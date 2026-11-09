"""真实模式下的 health 与故障上报用例。

三件事：
1. 模型权重没就绪 → `/internal/health` 必须 503 + degraded，而不是假装 ok；
2. 正在加载（loading）要与"加载失败（down）"分开上报，否则排障的人得多猜一步；
3. 向量库两路都挂 → `/internal/retrieve` 必须 5xx（C 侧映射 50201），而不是返回空列表
   让 C 判成 `no_hit` 拒答——把故障伪装成"没有这条通知"，是本项目最不该犯的错。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app import main
from app.retriever import build_health
from app.store import FixtureStore, VectorStoreUnavailable


def _client() -> TestClient:
    main.set_store(FixtureStore())
    return TestClient(main.app)


class TestHealthStates:
    def test_missing_model_turns_health_red(self, monkeypatch):
        """真实模式但权重没下完 → 503 + degraded，而不是假装 ok。"""
        monkeypatch.setattr(main, "components_state", lambda: ("down", "ok"))
        r = _client().get("/internal/health")

        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "degraded"
        assert body["components"] == {"qdrant": "ok", "embedder": "down", "reranker": "ok"}

    def test_components_are_reported_separately(self, monkeypatch):
        """embedder 好了、reranker 没好，不能一起显示成全绿或全红。"""
        monkeypatch.setattr(main, "components_state", lambda: ("ok", "down"))
        body = _client().get("/internal/health").json()

        assert body["components"]["embedder"] == "ok"
        assert body["components"]["reranker"] == "down"

    def test_loading_is_not_reported_as_ok(self, monkeypatch):
        """预热期间必须是 503 + loading：容器还没热，网关不该把流量放进来。"""
        monkeypatch.setattr(main, "components_state", lambda: ("loading", "ok"))
        r = _client().get("/internal/health")

        assert r.status_code == 503
        assert r.json()["components"]["embedder"] == "loading"

    def test_invalid_state_falls_back_to_down(self):
        """契约枚举外的值宁可报红，也不能报假绿。"""
        payload, ok = build_health(FixtureStore(), "0.1.0", embedder_state="weird")

        assert payload["components"]["embedder"] == "down"
        assert ok is False


class TestStoreFailureIsNotNoHit:
    def test_retrieve_endpoint_returns_503(self):
        """向量库不可用 → 503（C 侧映射 50201），不能变成 no_hit 拒答。"""

        class _BrokenStore:
            def hybrid_search(self, **_kw: Any) -> Any:
                raise VectorStoreUnavailable("boom")

            def ping(self) -> bool:
                return False

        main.set_store(_BrokenStore())
        try:
            r = TestClient(main.app).post("/internal/retrieve", json={"query": "转专业"})
        finally:
            main.set_store(FixtureStore())

        assert r.status_code == 503
        assert r.json()["detail"] == "vector store unavailable"


class TestModelFailureIsNotServerError:
    def test_retrieve_endpoint_returns_503(self, monkeypatch):
        """模型未就绪 → 503（依赖未就绪），不是 500（后端 bug）。

        500 会让 C 归到"我方写错了"，503 才能指向"去跑 model-puller / 等预热"。
        """
        from app import retriever
        from app.embedder import ModelUnavailable

        def boom(_text: str) -> list[float]:
            raise ModelUnavailable("embedder 未就绪")

        monkeypatch.setattr(retriever, "dense_embed", boom)
        main.set_store(FixtureStore())
        try:
            r = TestClient(main.app).post("/internal/retrieve", json={"query": "转专业"})
        finally:
            main.set_store(FixtureStore())

        assert r.status_code == 503
        assert r.json()["detail"] == "model unavailable"
