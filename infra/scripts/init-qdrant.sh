#!/bin/sh
# 【F 所有】创建 Qdrant collection 与 payload 索引（幂等）
#
# 这个脚本由 compose 的 `qdrant-init` 一次性服务执行，所以：
#   - 必须能被 **POSIX sh** 跑（镜像基于 alpine，`/bin/sh` 是 busybox ash，
#     不是 bash；`set -o pipefail`、`[[ ]]` 之类都不能用）
#   - 必须**幂等**：每次 `docker compose up` 都会重跑一遍
#   - 失败必须 **exit 非 0**：rag-core 通过
#     `depends_on: qdrant-init: condition: service_completed_successfully`
#     挂在这里，脚本静默成功就等于让检索服务对着一个空库跑
#
# payload 字段与 docs/data-model.md §2.5 一致。
set -eu

QDRANT="${VECTOR_DB_URL:-http://qdrant:6333}"
COLL="${QDRANT_COLLECTION:-sgu_notices}"
WAIT_SECONDS="${QDRANT_WAIT_SECONDS:-60}"

# ---------- 1. 等 Qdrant HTTP 就绪 ----------
# depends_on 的 service_healthy 只保证容器里的 TCP 端口通了，Qdrant 在
# 恢复 storage 期间仍可能对 API 返回 503。这里自己再等一次，避免竞态。
i=0
while [ "$i" -lt "$WAIT_SECONDS" ]; do
    if curl -sf -o /dev/null "$QDRANT/collections"; then
        echo "→ qdrant is up at $QDRANT"
        break
    fi
    i=$((i + 1))
    sleep 1
done
if [ "$i" -ge "$WAIT_SECONDS" ]; then
    echo "✗ qdrant 在 ${WAIT_SECONDS}s 内没有就绪：$QDRANT" >&2
    exit 1
fi

# ---------- 2. 建 collection（已存在则跳过） ----------
# **不能无条件 PUT**：Qdrant 的 `PUT /collections/{name}` 在集合已存在时会
# 直接重建，等于每次 `docker compose up` 都把向量库清空一次。
# 所以先查再建 —— "幂等"的意思是不改变已有数据，不是"跑两次也没报错"。
if curl -sf -o /dev/null "$QDRANT/collections/$COLL"; then
    echo "→ collection $COLL 已存在，跳过创建"
else
    echo "→ create collection $COLL"
    if ! curl -sf -X PUT "$QDRANT/collections/$COLL" \
        -H 'Content-Type: application/json' \
        -d '{
          "vectors": { "dense": { "size": 1024, "distance": "Cosine" } },
          "sparse_vectors": { "sparse": {} },
          "optimizers_config": { "default_segment_number": 2 }
        }' > /dev/null; then
        echo "✗ 创建 collection $COLL 失败" >&2
        exit 1
    fi
fi

# ---------- 3. payload 索引 ----------
# 没有这些索引，时间硬过滤（SKILL.md §5 步骤 2）会退化成全表扫描。
# 而且 Qdrant 对未建索引的 range 条件**仍能查**——慢，但不报错，
# 所以在开发机上"能跑"不等于"索引齐了"。这里逐个建并逐个校验。
put_index() {
    field="$1"
    schema="$2"
    if curl -sf -X PUT "$QDRANT/collections/$COLL/index" \
        -H 'Content-Type: application/json' \
        -d "{\"field_name\":\"$field\",\"field_schema\":\"$schema\"}" > /dev/null; then
        echo "  ✓ index $field ($schema)"
    else
        echo "✗ 创建索引 $field 失败" >&2
        exit 1
    fi
}

put_index publish_date datetime
put_index effective_from datetime
put_index effective_to datetime
put_index is_current bool
put_index doc_version_rank integer
put_index content_type keyword

# ---------- 4. 自证：collection 真的在 ----------
if ! curl -sf "$QDRANT/collections/$COLL" > /dev/null; then
    echo "✗ collection $COLL 不存在（创建步骤没有生效）" >&2
    exit 1
fi

echo "✓ collection $COLL ready"
