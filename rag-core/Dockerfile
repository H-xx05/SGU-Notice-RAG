# syntax=docker/dockerfile:1
# B（rag-core）：两阶段构建。模型不打进镜像（见 docs/deployment.md §5），
# 由 model-puller 服务挂载到 /models，镜像本身保持轻量。
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build

COPY pyproject.toml ./
# 骨架期只装运行必需依赖；FlagEmbedding 在 RAG_STUB_MODE=true 时不会被 import，
# 单独放在 requirements-models.txt 里，接真实模型时再装（见同目录 README）。
RUN pip install --prefix=/install fastapi==0.115.5 uvicorn[standard]==0.32.1 \
    pydantic==2.10.3 pydantic-settings==2.7.0 httpx==0.28.1 qdrant-client==1.12.1

FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MODEL_CACHE_DIR=/models \
    HF_HOME=/models

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -r -u 10001 -s /usr/sbin/nologin ragncore

COPY --from=builder /install /usr/local
WORKDIR /app
COPY app ./app

USER 10001
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/internal/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
