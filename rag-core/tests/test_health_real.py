"""真实模式下的 health 与故障上报用例。

两件事：
1. 模型权重没就绪 → `/internal/health` 必须 503 + degraded，而不是假装 ok；
2. 向量库两路都挂 → `/internal/retrieve` 必须 5xx（C 侧映射 50201），而不是返回空列表
   让 C 判成 `no_hit` 拒答——把故障伪装成"没有这条通知"，是本项目最不该犯的错。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app import main
from app.store import FixtureStore, VectorStoreUnavailable


def _client() -> TestClient:
    main.set_store(FixtureStore())
    return TestClient(main.app)


class TestHealthInRealMode:
    def test_missing_model_turns_health_red(self, monkeypatch):
        """真实模式但权重没下完 → 503 + degraded，而不是假装 ok。"""
        monkeypatch.setattr(main, "models_ready", lambda: (False, True))
        r = _client().get("/internal/health")

        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "degraded"
        assert body["components"] == {"qdrant": "ok", "embedder": "down", "reranker": "ok"}

    def test_components_are_reported_separately(self, monkeypatch):
        """embedder 好了、reranker 没好，不能一起显示成全绿或全红。"""
        monkeypatch.setattr(main, "models_ready", lambda: (True, False))
        body = _client().get("/internal/health").json()

        assert body["components"]["embedder"] == "ok"
        assert body["components"]["reranker"] == "down"


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
