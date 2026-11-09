"""真实模式下的 embedder / reranker 适配层测试。

用假模型验证**适配层**，不验证模型效果（那是 E 的事）：归一化、形态转换、分数尺度。
这些才是"接错就静默出错"的地方——维度不对会乱排，logits 不压会击穿拒答阈值。
"""

from __future__ import annotations

from typing import Any

import pytest

from app import embedder
from app.embedder import DENSE_DIM
from app.settings import settings


class _FakeBGEM3:
    """假的 bge-m3：只验证适配层，不加载任何权重。"""

    def __init__(self, dense: list[float] | None = None, weights: dict | None = None) -> None:
        self._dense = dense
        self._weights = weights if weights is not None else {"101": 0.8, "202": 0.3}
        self.calls: list[dict] = []

    def encode(self, _texts, **kwargs: Any) -> dict:
        self.calls.append(kwargs)
        out: dict[str, Any] = {}
        if kwargs.get("return_dense"):
            vec = self._dense
            if vec is None:
                vec = [3.0, 4.0] + [0.0] * (DENSE_DIM - 2)  # 模长 5，归一化后 0.6 / 0.8
            out["dense_vecs"] = [vec]
        if kwargs.get("return_sparse"):
            out["lexical_weights"] = [self._weights]
        return out


@pytest.fixture()
def real_mode(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "rag_stub_mode", False)


class TestRealEmbedder:
    def test_dense_is_normalised_1024(self, real_mode, monkeypatch):
        monkeypatch.setattr(embedder, "_embedder", lambda: _FakeBGEM3())
        vec = embedder.dense_embed("转专业")

        assert len(vec) == DENSE_DIM
        assert vec[0] == pytest.approx(0.6)
        assert vec[1] == pytest.approx(0.8)
        assert sum(v * v for v in vec) == pytest.approx(1.0)

    def test_dense_dimension_mismatch_raises(self, real_mode, monkeypatch):
        """换了模型却没重建 collection 时必须吵，而不是静默乱排。"""
        monkeypatch.setattr(embedder, "_embedder", lambda: _FakeBGEM3(dense=[1.0, 0.0]))
        with pytest.raises(ValueError, match="1024"):
            embedder.dense_embed("转专业")

    def test_sparse_converts_lexical_weights(self, real_mode, monkeypatch):
        fake = _FakeBGEM3(weights={"202": 0.3, "101": 0.8, "not-a-token": 0.5, "303": 0.0})
        monkeypatch.setattr(embedder, "_embedder", lambda: fake)
        indices, values = embedder.sparse_embed("转专业")

        # 非数字键与 0 权重被丢弃，indices 升序
        assert indices == [101, 202]
        assert values == [pytest.approx(0.8), pytest.approx(0.3)]

    def test_rerank_outputs_probability(self, real_mode, monkeypatch):
        """cross-encoder 的 logits 必须压到 [0,1]，否则 C 的 0.35 阈值失效。"""

        class _FakeReranker:
            def __init__(self) -> None:
                self.kwargs: dict = {}

            def compute_score(self, pairs, **kwargs):
                self.kwargs = kwargs
                assert isinstance(pairs, list) and isinstance(pairs[0], list)
                return [-3.0, 0.0, 3.0]

        fake = _FakeReranker()
        monkeypatch.setattr(embedder, "_reranker", lambda: fake)
        scores = embedder.rerank("转专业", ["a", "b", "c"])

        assert fake.kwargs.get("normalize") is False, "应自己算 sigmoid，而不是依赖库默认值"
        assert scores[0] == pytest.approx(0.0474, abs=1e-3)
        assert scores[1] == pytest.approx(0.5)
        assert scores[2] == pytest.approx(0.9526, abs=1e-3)
        assert all(0.0 <= s <= 1.0 for s in scores)

    def test_stub_mode_untouched(self, monkeypatch):
        """stub 分支的行为一个字都不能变——既有用例依赖它。"""
        monkeypatch.setattr(settings, "rag_stub_mode", True)
        assert len(embedder.dense_embed("转专业")) == DENSE_DIM
        idx, val = embedder.sparse_embed("转专业")
        assert len(idx) == len(val) > 0
        assert 0.0 <= embedder.rerank("转专业条件", ["转专业条件"])[0] <= 1.0


class TestModelsReady:
    def test_stub_mode_is_always_ready(self, monkeypatch):
        monkeypatch.setattr(settings, "rag_stub_mode", True)
        assert embedder.models_ready() == (True, True)

    def test_missing_weights_report_not_ready(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "rag_stub_mode", False)
        monkeypatch.setattr(settings, "model_cache_dir", str(tmp_path))
        monkeypatch.setattr(settings, "embedding_model", "BAAI/bge-m3")
        monkeypatch.setattr(settings, "reranker_model", "BAAI/bge-reranker-v2-m3")
        assert embedder.models_ready() == (False, False)

    def test_hf_cache_layout_detected(self, monkeypatch, tmp_path):
        """按 HuggingFace Hub 的缓存布局判断权重是否已下好。"""
        for model in ("BAAI/bge-m3", "BAAI/bge-reranker-v2-m3"):
            snap = tmp_path / f"models--{model.replace('/', '--')}" / "snapshots" / "rev1"
            snap.mkdir(parents=True)
            (snap / "config.json").write_text("{}", encoding="utf-8")

        monkeypatch.setattr(settings, "rag_stub_mode", False)
        monkeypatch.setattr(settings, "model_cache_dir", str(tmp_path))
        monkeypatch.setattr(settings, "embedding_model", "BAAI/bge-m3")
        monkeypatch.setattr(settings, "reranker_model", "BAAI/bge-reranker-v2-m3")
        assert embedder.models_ready() == (True, True)
