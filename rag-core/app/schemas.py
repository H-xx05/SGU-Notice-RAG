"""与 docs/api.md §6 内部契约逐字对应的 Pydantic 模型。

**这个文件是 B 与 C 的唯一交界处**：字段名改动必须走 SKILL.md §1 的契约三步流程，
即 docs/api.md、references/api-contract.md 与本文件同步修改，并在 PR 标题加 `contract-change:`。

对照关系（改了这里必须改那边）：
- `Chunk`            ↔ api-server/internal/client/ragcore.go `Chunk`
- `RetrieveRequest`  ↔ `RetrieveRequest` / `TimeFilterBody`
- `RetrieveResponse` ↔ `RetrieveResponse`（`chunks` 与 `scores` **等长同序**）
- `HealthResponse`   ↔ `HealthResponse`（`components` 为 map[string]string）
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TimeMode = Literal["auto", "range", "off"]
ComponentState = Literal["ok", "down", "loading"]


class TimeFilterBody(BaseModel):
    """时效过滤条件，由 C 侧 TimeFilter 转换而来（字段名与对外契约一致）。"""

    model_config = ConfigDict(extra="forbid")

    mode: TimeMode = "auto"
    date_from: str | None = None
    date_to: str | None = None
    as_of: str | None = None


class RetrieveRequest(BaseModel):
    """POST /internal/retrieve 请求体（docs/api.md §6.1）。"""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=512)
    time_filter: TimeFilterBody = Field(default_factory=TimeFilterBody)
    top_k: int = Field(default=8, ge=1, le=20)
    rerank: bool = True


class Chunk(BaseModel):
    """检索返回的切片，字段与 docs/data-model.md §2 的 chunk 元数据一一对应。

    注意这里**不含**任何打分子段：分数走响应顶层的 `scores[]`（等长同序），
    这样 C 侧可以只映射 `scores[i]`，不必关心 B 内部用了 dense/sparse/rerank 三种分。
    """

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    doc_id: str
    title: str
    text: str
    publish_date: str = ""
    effective_from: str | None = None
    effective_to: str | None = None
    doc_version: str = ""
    section_path: str = ""
    content_type: str = "notice_body"
    source_url: str = ""
    is_current: bool = True
    supersedes_doc_id: str | None = None


class RetrieveResponse(BaseModel):
    """POST /internal/retrieve 响应体（docs/api.md §6.1）。

    `applied_filters` 必须如实反映实际生效的过滤条件：E 的口径依赖它区分 auto/range/off。
    """

    model_config = ConfigDict(extra="forbid")

    chunks: list[Chunk] = Field(default_factory=list)
    scores: list[float] = Field(default_factory=list)
    applied_filters: list[str] = Field(default_factory=list)
    retrieve_ms: int = 0


class HealthResponse(BaseModel):
    """GET /internal/health（docs/api.md §6.2）。任一组件 down 时 HTTP 503 + status=degraded。"""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "degraded"] = "ok"
    components: dict[str, ComponentState] = Field(default_factory=dict)
    version: str = "0.1.0"
