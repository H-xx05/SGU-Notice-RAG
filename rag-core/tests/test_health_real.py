"""故障上报用例：向量库挂掉不能被伪装成"没有命中"。

返回空列表时 C 会判成 `no_hit` → 拒答话术"未找到依据"。那是把运维故障说成
"学校没发过这条通知"，比 5xx 有害得多。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app import main
from app.store import FixtureStore, VectorStoreUnavailable


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
