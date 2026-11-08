#!/usr/bin/env bash
# 【F 所有】本地重置向量库（危险，仅限 development）
set -euo pipefail
if [ "${APP_ENV:-development}" = "production" ]; then
  echo "拒绝在 production 环境执行" >&2
  exit 1
fi
QDRANT="${VECTOR_DB_URL:-http://localhost:6333}"
COLL="${QDRANT_COLLECTION:-sgu_notices}"
read -r -p "确认删除 collection $COLL ? [y/N] " ans
[ "$ans" = "y" ] || exit 0
curl -sf -X DELETE "$QDRANT/collections/$COLL" || true
bash "$(dirname "$0")/init-qdrant.sh"
