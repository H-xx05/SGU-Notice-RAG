"""预下载 bge-m3 与 bge-reranker-v2-m3 到 model-cache 卷。

用法（compose profile）：

    docker compose --profile models run --rm model-puller

为什么单独做成一个命令而不是在 rag-core 启动时下模型：

  - 首次下载约 2.3 GB，放在启动路径里会让 `/internal/health` 长时间不绿，
    compose 的 healthcheck 会因为超时把容器反复重启；
  - 模型下载要网，而 rag-core 的**运行**不一定要网。把这两件事拆开，
    离线环境才能只跑 `RAG_STUB_MODE=true` 验证链路。

下载失败**不抛异常**（只打日志并返回 1）：这个命令是给 F 排障用的，
不该因为镜像源抖动就把 `docker compose --profile models run` 整个搞崩。
"""

from __future__ import annotations

import argparse
import os
import sys

from ..settings import get_settings


def _download(model_id: str, cache_dir: str) -> bool:
    """下载一个模型。**任何没能真正下载成功的情况都必须返回 False。**

    这里曾经有个坑：huggingface_hub 没装时返回 True（"跳过预下载"），
    于是 `main()` 打印"模型预下载完成，可以设 RAG_STUB_MODE=false 了"，
    退出码 0 —— 而实际上一份权重都没有。照这句话去关 stub，
    rag-core 会在 import FlagEmbedding 时直接崩。

    "未安装工具"与"网络抖动"是两回事：
      - 网络抖动：模型以后还有机会自动下载，不该阻断 profile run；
      - 没装工具：这条命令**根本不可能**成功，必须让人知道。
    两者都返回非 0，但报错措辞不同，好让 F 一眼看出该装东西还是该重试。
    """
    try:
        from huggingface_hub import snapshot_download  # type: ignore[import-not-found]
    except ImportError:
        print(
            f"✗ 未安装 huggingface_hub，无法预下载 {model_id}。\n"
            f"  这个镜像（WITH_MODELS=0）刻意不含模型依赖。要预下载请先构建带模型依赖的镜像：\n"
            f"    WITH_MODELS=1 docker compose build model-puller\n"
            f"    docker compose --profile models run --rm model-puller",
            file=sys.stderr,
        )
        return False

    try:
        path = snapshot_download(repo_id=model_id, cache_dir=cache_dir)
    except Exception as exc:  # noqa: BLE001 —— 网络错误只报告不抛栈
        print(f"✗ {model_id} 下载失败：{exc}", file=sys.stderr)
        return False

    print(f"✓ {model_id} → {path}")
    return True


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="预下载嵌入与重排模型")
    parser.add_argument("--cache-dir", default=os.environ.get("HF_HOME") or settings.model_cache_dir)
    parser.add_argument("--embed", default=settings.embedding_model)
    parser.add_argument("--rerank", default=settings.reranker_model)
    args = parser.parse_args(argv)

    os.makedirs(args.cache_dir, exist_ok=True)
    print(f"cache dir: {args.cache_dir}")

    ok = _download(args.embed, args.cache_dir)
    ok = _download(args.rerank, args.cache_dir) and ok

    if ok:
        print("模型预下载完成。rag-core 可以设 RAG_STUB_MODE=false 了。")
    else:
        print(
            "模型未备齐。可以先用 RAG_STUB_MODE=true 启动，链路与契约不受影响。",
            file=sys.stderr,
        )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
