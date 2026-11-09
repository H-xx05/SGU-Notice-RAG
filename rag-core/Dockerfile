# syntax=docker/dockerfile:1
# B（rag-core）：两阶段构建。模型权重不打进镜像（见 docs/deployment.md §5），
# 由 model-puller 服务挂载到 /models，镜像本身保持轻量。
#
# 但**推理依赖必须进镜像**：docker-compose.yml 里 rag-core 的默认值是
# `RAG_STUB_MODE=false`，也就是说按 README 一键启动走的就是真实模型路径。
# 骨架期镜像只装了 FastAPI 那一套，真起容器会卡在 `from FlagEmbedding import ...`
# 的 ImportError 上，而 healthcheck 只会告诉你"容器不健康"。
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build

COPY pyproject.toml ./
RUN pip install --prefix=/install fastapi==0.115.5 uvicorn[standard]==0.32.1 \
    pydantic==2.10.3 pydantic-settings==2.7.0 httpx==0.28.1 qdrant-client==1.12.1

# 模型推理依赖（约 1.5 GB）。torch 走 CPU 官方轮子：
# 默认 pypi 的 torch 2.5.1 是 CUDA 版，会多拉 ~2 GB 的 nvidia-* 包，
# 而本服务只跑 bge-m3 / reranker 的推理，用不上显卡。
COPY requirements-models.txt ./
RUN pip install --prefix=/install \
    --index-url https://download.pytorch.org/whl/cpu \
    --extra-index-url https://pypi.org/simple \
    -r requirements-models.txt

FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MODEL_CACHE_DIR=/models \
    HF_HOME=/models

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -r -u 10001 -s /usr/sbin/nologin ragncore \
    # 必须先把挂载点建出来并交给运行用户：命名卷首次挂载会继承镜像里该目录的属主，
    # 否则非 root 的 10001 在 /models 下建缓存目录会 permission denied，
    # 而报错只出现在 model-puller 的日志里（compose 里它是 profile=models，很容易漏看）。
    && mkdir -p /models && chown -R 10001:10001 /models

COPY --from=builder /install /usr/local
WORKDIR /app
COPY app ./app

USER 10001
EXPOSE 8000

# start_period 给 120s：真实模式首次请求要加载 2.3 GB 权重进内存（CPU 上十几秒到一分钟）。
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/internal/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
