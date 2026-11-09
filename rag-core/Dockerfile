# syntax=docker/dockerfile:1
# B（rag-core）：两阶段构建。模型**权重**不打进镜像（见 docs/deployment.md §5），
# 由 model-puller 服务下载到 /models 卷，镜像本身保持轻量。
#
# 但"模型权重"和"模型依赖库"是两件事。torch + FlagEmbedding 约 4.5 GB 的依赖，
# 只有 RAG_STUB_MODE=false 才需要。默认不装（镜像小、CI 快、离线可跑），
# 需要时用构建参数打开：
#   WITH_MODELS=1 docker compose build rag-core
#   WITH_MODELS=1 docker compose --profile models run --rm model-puller
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build

ARG WITH_MODELS=0

COPY pyproject.toml ./
COPY requirements-models.txt ./
# 骨架期只装运行必需依赖；FlagEmbedding 在 RAG_STUB_MODE=true 时不会被 import。
RUN pip install --prefix=/install fastapi==0.115.5 uvicorn[standard]==0.32.1 \
    pydantic==2.10.3 pydantic-settings==2.7.0 httpx==0.28.1 qdrant-client==1.12.1 \
    && if [ "$WITH_MODELS" = "1" ]; then \
         echo "==> 安装模型依赖（torch + FlagEmbedding，约 4.5 GB）" && \
         pip install --prefix=/install -r requirements-models.txt huggingface_hub; \
       else \
         echo "==> 跳过模型依赖（WITH_MODELS=0，RAG_STUB_MODE 必须为 true）"; \
       fi

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

# /models 是挂载点。这里先建出来并交给运行用户，
# 否则首次以 uid 10001 写缓存目录会 permission denied。
RUN mkdir -p /models && chown 10001:10001 /models

USER 10001
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/internal/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
