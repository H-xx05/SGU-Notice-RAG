"""HTTP 层测试：验证 /internal/* 两个端点与 C 侧客户端逐字对齐（docs/api.md §6）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.store import FixtureStore

FIXTURE = Path(__file__).parent / "fixtures" / "chunks.jsonl"


@pytest.fixture()
def client() -> TestClient:
    main.set_store(FixtureStore.from_jsonl(FIXTURE))
    return TestClient(main.app)


def test_health_shape(client: TestClient):
    r = client.get("/internal/health")
    assert r.status_code == 200
    body = r.json()
    # 与 api-server/internal/client/ragcore.go HealthResponse 完全一致
    assert set(body) == {"status", "components", "version"}
    assert body["status"] == "ok"
    assert body["components"] == {"qdrant": "ok", "embedder": "ok", "reranker": "ok"}


def test_retrieve_shape(client: TestClient):
    r = client.post(
        "/internal/retrieve",
        json={"query": "转专业申请条件", "top_k": 5, "time_filter": {"mode": "auto"}},
    )
    assert r.status_code == 200
    body = r.json()

    # 顶层四字段，与 Go 侧 RetrieveResponse 一一对应
    assert set(body) == {"chunks", "scores", "applied_filters", "retrieve_ms"}
    assert isinstance(body["chunks"], list)
    assert isinstance(body["scores"], list)
    assert isinstance(body["applied_filters"], list)
    assert isinstance(body["retrieve_ms"], int)
    assert len(body["chunks"]) == len(body["scores"]), "chunks 与 scores 必须等长"

    if body["chunks"]:
        ch = body["chunks"][0]
        # 与 Go 侧 Chunk 结构体的 json tag 完全一致（多一个少一个都是契约漂移）
        assert set(ch) == {
            "chunk_id",
            "doc_id",
            "title",
            "text",
            "publish_date",
            "effective_from",
            "effective_to",
            "doc_version",
            "section_path",
            "content_type",
            "source_url",
            "is_current",
            "supersedes_doc_id",
        }


def test_retrieve_rejects_extra_field(client: TestClient):
    """extra=forbid：契约外字段必须 422，避免 C 侧悄悄塞字段而契约漂移。"""
    r = client.post("/internal/retrieve", json={"query": "x", "unknown_field": 1})
    assert r.status_code == 422


def test_retrieve_rejects_empty_query(client: TestClient):
    r = client.post("/internal/retrieve", json={"query": ""})
    assert r.status_code == 422


def test_retrieve_rejects_top_k_over_limit(client: TestClient):
    r = client.post("/internal/retrieve", json={"query": "x", "top_k": 99})
    assert r.status_code == 422


def test_retrieve_range_missing_bound_is_400(client: TestClient):
    r = client.post(
        "/internal/retrieve",
        json={"query": "转专业", "time_filter": {"mode": "range", "date_from": "2025-01-01"}},
    )
    assert r.status_code == 400


def test_openapi_not_exposed(client: TestClient):
    """内部服务不暴露文档端点，减少攻击面。"""
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_no_api_v1_routes():
    """B 绝不能暴露对外契约路径（SKILL.md §6）。"""
    paths = {getattr(r, "path", "") for r in main.app.routes}
    assert not any(p.startswith("/api/v1") for p in paths), f"rag-core 暴露了对外路径: {paths}"
