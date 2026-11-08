"""配置：全部来自环境变量，与 `.env.example` / `docs/deployment.md §4` 保持一致。

**为什么每个字段都写死 `validation_alias`**：pydantic-settings 默认按字段名转大写去读，
即 `embedding_model` → `EMBEDDING_MODEL`。但 `.env.example` 与 `docker-compose.yml` 用的是
`EMBED_MODEL` / `RERANK_MODEL`（更短、社区习惯）。两者不一致的后果是**静默失效**：
你在 `.env` 里换了模型名，服务照旧用默认值加载，而日志里一个字都不会提。

所以这里给每个字段显式列出所有可接受的拼法（`AliasChoices`，先匹配先赢），
而不是依赖默认的字段名映射。新增字段时照抄这个模式。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _alias(*names: str) -> AliasChoices:
    return AliasChoices(*names)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"

    # ---------- Qdrant ----------
    vector_db_url: str = Field(
        default="http://qdrant:6333", validation_alias=_alias("VECTOR_DB_URL")
    )
    qdrant_collection: str = Field(
        default="sgu_notices", validation_alias=_alias("QDRANT_COLLECTION")
    )
    qdrant_api_key: str = Field(default="", validation_alias=_alias("QDRANT_API_KEY"))

    # ---------- 检索参数 ----------
    # 这些数字同时写在 eval/thresholds.json 的 runtime 段，有测试比对（改一处必须改另一处）。
    dense_top_k: int = Field(default=40, validation_alias=_alias("DENSE_TOP_K"))
    sparse_top_k: int = Field(default=40, validation_alias=_alias("SPARSE_TOP_K"))
    rrf_k: int = Field(default=2, validation_alias=_alias("RRF_K"))
    rerank_top_k: int = Field(default=20, validation_alias=_alias("RERANK_TOP_K", "RAG_TOP_K"))
    final_top_k: int = Field(default=8, validation_alias=_alias("FINAL_TOP_K", "RAG_FINAL_TOP_K"))
    stale_penalty: float = Field(default=0.5, validation_alias=_alias("STALE_PENALTY"))
    min_score: float = Field(default=0.35, validation_alias=_alias("MIN_SCORE", "MIN_ANSWER_SCORE"))

    # ---------- 模型 ----------
    embedding_model: str = Field(
        default="BAAI/bge-m3", validation_alias=_alias("EMBED_MODEL", "EMBEDDING_MODEL")
    )
    reranker_model: str = Field(
        default="BAAI/bge-reranker-v2-m3", validation_alias=_alias("RERANK_MODEL", "RERANKER_MODEL")
    )
    # 必须与 docker-compose.yml 里 `model-cache` 卷的挂载点一致，
    # 否则模型下到容器可写层，`docker compose up --build` 一次就要重下 2.3 GB。
    model_cache_dir: str = Field(default="/models", validation_alias=_alias("MODEL_CACHE_DIR"))
    use_fp16: bool = Field(default=False, validation_alias=_alias("USE_FP16"))

    # ---------- 骨架模式 ----------
    # true：不加载模型，用确定性哈希向量跑通全链路（CI、离线环境、D 联调用）。
    rag_stub_mode: bool = Field(default=True, validation_alias=_alias("RAG_STUB_MODE"))

    # ---------- 时间轴 ----------
    # 固定 Asia/Shanghai，避免容器 UTC 使"今天"错一天 —— 时效判定的基准日错一天，
    # 边界当天的通知就可能被判成过期。
    # 只认 APP_TIMEZONE，**不认容器的 TZ**：TZ 常被运维设成 UTC（日志时间戳习惯），
    # 而"今天"必须按北京时间算 —— 时效判定的基准日错一天，边界当天的通知就判错了。
    timezone: str = Field(default="Asia/Shanghai", validation_alias=_alias("APP_TIMEZONE"))

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
