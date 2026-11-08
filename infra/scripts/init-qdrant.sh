#!/usr/bin/env bash
# 【B/F 共用】创建 Qdrant collection（幂等）
# payload 字段与 docs/data-model.md §2.5 一致
set -euo pipefail
QDRANT="${VECTOR_DB_URL:-http://localhost:6333}"
COLL="${QDRANT_COLLECTION:-sgu_notices}"

echo "→ create collection $COLL at $QDRANT"
curl -sf -X PUT "$QDRANT/collections/$COLL" \
  -H 'Content-Type: application/json' \
  -d '{
    "vectors": { "dense": { "size": 1024, "distance": "Cosine" } },
    "sparse_vectors": { "sparse": {} },
    "optimizers_config": { "default_segment_number": 2 }
  }' || echo "  (collection may already exist)"

for field in publish_date effective_to; do
  curl -sf -X PUT "$QDRANT/collections/$COLL/index" \
    -H 'Content-Type: application/json' \
    -d "{\"field_name\":\"$field\",\"field_schema\":\"datetime\"}" || true
done
for field in content_type; do
  curl -sf -X PUT "$QDRANT/collections/$COLL/index" \
    -H 'Content-Type: application/json' \
    -d "{\"field_name\":\"$field\",\"field_schema\":\"keyword\"}" || true
done
for field in is_current; do
  curl -sf -X PUT "$QDRANT/collections/$COLL/index" \
    -H 'Content-Type: application/json' \
    -d "{\"field_name\":\"$field\",\"field_schema\":\"bool\"}" || true
done
for field in doc_version_rank; do
  curl -sf -X PUT "$QDRANT/collections/$COLL/index" \
    -H 'Content-Type: application/json' \
    -d "{\"field_name\":\"$field\",\"field_schema\":\"integer\"}" || true
done

echo "✓ collection ready"
curl -sf "$QDRANT/collections/$COLL" | head -c 400; echo
