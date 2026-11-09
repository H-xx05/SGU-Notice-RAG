"""FastAPI 入口：只暴露两个内部端点（docs/api.md §6）。

**不暴露任何 /api/v1/*** ——对外契约由 C（Go）单点承载。B 在 compose 网络内，
宿主机不映射端口（见 docker-compose.yml 中 rag-core 的 expose 而非 ports）。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from . import __version__, time_filter
from .embedder import ModelUnavailable, components_state, start_warmup
from .retriever import build_health, retrieve
from .schemas import RetrieveRequest, RetrieveResponse
from .settings import settings
from .store import VectorStore, VectorStoreUnavailable, build_store

logging.basicConfig(
    level=logging.INFO if settings.is_production else logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("rag-core")

_store: VectorStore | None = None


def get_store() -> VectorStore:
    global _store
    if _store is None:
        _store = build_store()
    return _store


def set_store(store: VectorStore) -> None:
    """测试注入点。"""
    global _store
    _store = store


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    log.info(
        "starting rag-core version=%s stub_mode=%s qdrant=%s",
        __version__,
        settings.rag_stub_mode,
        settings.vector_db_url,
    )
    get_store()
    # 后台预热模型，不阻塞启动：C 侧超时 8s，等模型加载完再 listen 会让网关先超时。
    # 预热期间 /internal/health 会报 loading（503），compose 的 start_period 负责等它。
    start_warmup()
    yield
    log.info("stopping rag-core")


app = FastAPI(
    title="SGU-Notice-RAG core",
    version=__version__,
    lifespan=lifespan,
    # 内部服务不暴露任何文档端点：openapi_url 与 docs_url 都要关掉。
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/internal/health")
def health():
    # 真实模式下 embedder / reranker 是两个独立加载的模型，各自上报 ok/loading/down；
    # stub 模式下 components_state() 直接给 ("ok","ok")，与骨架期行为一致。
    embedder_state, reranker_state = components_state()
    payload, ok = build_health(
        get_store(),
        __version__,
        embedder_state=embedder_state,
        reranker_state=reranker_state,
    )
    # 契约要求：任一组件不是 ok → HTTP 503 + status=degraded
    return JSONResponse(status_code=200 if ok else 503, content=payload)


@app.post("/internal/retrieve", response_model=RetrieveResponse)
def retrieve_endpoint(req: RetrieveRequest) -> RetrieveResponse:
    try:
        return retrieve(req, get_store())
    except ValueError as exc:
        # 参数越界（例如 range 缺日期）：这是调用方错误，400 而不是 500
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except VectorStoreUnavailable as exc:
        # 契约里的 50301「依赖未就绪」：向量库不可用是**故障**，不是"没有命中"。
        # 必须显式 5xx，否则 C 会把它当 no_hit 变成"未找到依据"的拒答。
        log.error("vector store unavailable: %s", exc)
        return JSONResponse(  # type: ignore[return-value]
            status_code=503, content={"detail": "vector store unavailable"}
        )
    except ModelUnavailable as exc:
        # 模型预热已判定失败（权重缺失 / 加载崩）。同样是"依赖未就绪"，503 而不是 500：
        # 500 会让 C 归为后端 bug，503 才能让运维看到"是该去跑 model-puller 了"。
        # 快速失败也很重要——lru_cache 不缓存异常，不拦住就会每个请求重试几十秒的加载。
        log.error("model unavailable: %s", exc)
        return JSONResponse(  # type: ignore[return-value]
            status_code=503, content={"detail": "model unavailable"}
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("retrieve failed")
        return JSONResponse(status_code=500, content={"detail": f"retrieve failed: {exc}"})  # type: ignore[return-value]


# 供测试与 E 侧直接复用的判定函数（不属于 HTTP 契约）
__all__ = ["app", "get_store", "set_store", "retrieve", "retrieve_endpoint", "time_filter"]
