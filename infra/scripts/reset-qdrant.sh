#!/usr/bin/env bash
# 【F 所有】本地重置向量库（危险，仅限 development）
#
# 破坏性操作，所以刻意做成"两道闸门 + 一次确认"：
#   1. APP_ENV=production 直接拒绝
#   2. 非交互环境（没有 TTY）拒绝 —— 防止被某个脚本不知不觉调起来
#   3. 必须手动输入 collection 名，而不是 y/N
#
# 为什么是输名字而不是 y/N：`reset-qdrant.sh` 最可能被误触发的方式是
# "上箭头 + 回车"，而 y/N 的默认答案是 N 却只需要多按一个键。输名字要打字，
# 打字这个动作本身就能让人意识到自己在删库。
set -euo pipefail

if [ "${APP_ENV:-development}" = "production" ]; then
  echo "✗ 拒绝在 production 环境执行" >&2
  exit 1
fi

if [ ! -t 0 ]; then
  echo "✗ 非交互环境，拒绝执行（这是破坏性操作，必须有 TTY 手动确认）" >&2
  echo "  确认要在 CI/脚本里跑，请显式设置 FORCE_RESET=1" >&2
  [ "${FORCE_RESET:-0}" = "1" ] || exit 1
fi

QDRANT="${VECTOR_DB_URL:-http://localhost:6333}"
COLL="${QDRANT_COLLECTION:-sgu_notices}"

if [ "${FORCE_RESET:-0}" != "1" ]; then
  printf '即将删除 collection %s（宿主机操作，请确认 VECTOR_DB_URL=%s）。\n输入 collection 名以确认: ' "$COLL" "$QDRANT"
  read -r ans
  [ "$ans" = "$COLL" ] || { echo "输入不匹配，已取消。"; exit 0; }
fi

echo "→ delete collection $COLL"
# 404 属于正常情况（本来就没建过），其他错误要暴露出来
code=$(curl -s -o /dev/null -w '%{http_code}' -X DELETE "$QDRANT/collections/$COLL" || echo 000)
case "$code" in
  200|404) echo "  ✓ deleted (HTTP $code)" ;;
  *) echo "✗ 删除失败（HTTP $code）" >&2; exit 1 ;;
esac

echo "→ 重新初始化"
exec bash "$(dirname "$0")/init-qdrant.sh"
