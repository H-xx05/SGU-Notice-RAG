"""启动预热用例。

守两件事：
1. 预热真的把状态推到 ok（成功）/ down（权重缺失或加载失败），
   让 `/internal/health` 能把"正在加载"和"加载失败"分开说；
2. 预热线程与第一个用户请求同时到达时，**模型只加载一次**——lru_cache 在并发未命中
   时会重复执行被装饰的函数，两个线程各加载一份 2.3 GB 权重就是 OOM。
"""

from __future__ import annotations

import threading
import time
from functools import lru_cache

import pytest

from app import embedder
from app.settings import settings


@pytest.fixture(autouse=True)
def _reset_component_state():
    embedder._STATE.update({"embedder": "idle", "reranker": "idle"})
    yield
    embedder._STATE.update({"embedder": "idle", "reranker": "idle"})


@pytest.fixture()
def real_mode(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "rag_stub_mode", False)


def _weights_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embedder, "model_available", lambda _model_id: True)


class TestWarmupStates:
    def test_stub_mode_returns_no_thread(self, monkeypatch):
        """stub 模式不加载模型，也不该起线程。"""
        monkeypatch.setattr(settings, "rag_stub_mode", True)
        assert embedder.start_warmup() is None
        assert embedder.components_state() == ("ok", "ok")

    def test_success_marks_both_ok(self, real_mode, monkeypatch):
        _weights_present(monkeypatch)
        monkeypatch.setattr(embedder, "_probe_embedder", lambda: None)
        monkeypatch.setattr(embedder, "_probe_reranker", lambda: None)

        embedder.warmup()

        assert embedder._STATE == {"embedder": "ok", "reranker": "ok"}

    def test_missing_weights_marks_down(self, real_mode, monkeypatch):
        monkeypatch.setattr(embedder, "model_available", lambda _model_id: False)
        monkeypatch.setattr(embedder, "_probe_embedder", lambda: pytest.fail("不该探测"))
        monkeypatch.setattr(embedder, "_probe_reranker", lambda: pytest.fail("不该探测"))

        embedder.warmup()

        assert embedder._STATE == {"embedder": "down", "reranker": "down"}

    def test_probe_exception_marks_down_not_raising(self, real_mode, monkeypatch):
        """后台线程里的异常必须被吞掉并降级，不能把进程带崩。"""
        _weights_present(monkeypatch)

        def boom() -> None:
            raise RuntimeError("tokenizer 缺文件")

        monkeypatch.setattr(embedder, "_probe_embedder", boom)
        monkeypatch.setattr(embedder, "_probe_reranker", lambda: None)

        embedder.warmup()  # 不抛

        assert embedder._STATE == {"embedder": "down", "reranker": "ok"}

    def test_loading_state_before_probe_finishes(self, real_mode, monkeypatch):
        """探测还在跑的时候，health 应看到 loading 而不是 down。"""
        _weights_present(monkeypatch)
        observed: list[str] = []

        def slow_probe() -> None:
            observed.append(embedder.components_state()[0])

        monkeypatch.setattr(embedder, "_probe_embedder", slow_probe)
        monkeypatch.setattr(embedder, "_probe_reranker", lambda: None)

        embedder.warmup()

        assert observed == ["loading"]


class TestFailFastWhenDown:
    """预热已判死刑之后，请求必须快速失败，不能把一次故障放大成雪崩。

    `lru_cache` **不缓存异常**：加载失败时每个请求都会重新走一遍几十秒的加载。
    因此 `_embedder()` / `_reranker()` 在 `_STATE == "down"` 时直接抛 `ModelUnavailable`。
    """

    def test_embedder_raises_without_retrying_load(self, real_mode, monkeypatch):
        embedder._STATE["embedder"] = "down"
        monkeypatch.setattr(
            embedder, "_embedder_cached", lambda: pytest.fail("已判定 down 就不该再加载")
        )

        with pytest.raises(embedder.ModelUnavailable):
            embedder._embedder()

    def test_reranker_raises_without_retrying_load(self, real_mode, monkeypatch):
        embedder._STATE["reranker"] = "down"
        monkeypatch.setattr(
            embedder, "_reranker_cached", lambda: pytest.fail("已判定 down 就不该再加载")
        )

        with pytest.raises(embedder.ModelUnavailable):
            embedder._reranker()


class TestWarmupThread:
    def test_thread_is_daemon(self, real_mode, monkeypatch):
        _weights_present(monkeypatch)
        done = threading.Event()

        def fake_warmup() -> None:
            done.set()

        monkeypatch.setattr(embedder, "warmup", fake_warmup)
        th = embedder.start_warmup()

        assert th is not None
        assert th.daemon is True, "非 daemon 线程会把 SIGTERM 后的优雅退出钉住"
        assert done.wait(5), "预热线程没跑起来"

    def test_concurrent_load_happens_once(self, monkeypatch):
        """预热线程 + 首个请求同时到达时，模型只能加载一次。"""
        calls: list[int] = []

        @lru_cache(maxsize=1)
        def fake_cached():
            calls.append(1)
            time.sleep(0.15)  # 放大并发窗口
            return object()

        monkeypatch.setattr(embedder, "_embedder_cached", fake_cached)

        results: list[object] = []

        def worker() -> None:
            results.append(embedder._embedder())

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(calls) == 1, f"模型被加载了 {len(calls)} 次"
        assert len(results) == 4
        assert results[0] is results[1] is results[2] is results[3]
