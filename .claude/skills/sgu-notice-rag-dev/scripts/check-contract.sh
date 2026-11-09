#!/usr/bin/env bash
# SGU-Notice-RAG 契约自检 —— PR 前必跑。
# 检查三件事：
#   1. 前端 src/api 里出现的 /api/v1/* 路径，是否都在 references/api-contract.md 登记；
#   2. 前端 src/api 里出现的字段名，是否都在契约文件里出现过（防自造字段）；
#   3. docs/api.md 与 references/api-contract.md 是否同步（后者是前者的摘要，路径集合必须一致）。
# 退出码：0 = 通过（可能有 ! 提醒）；1 = 有 ✗ 级问题。

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
cd "$ROOT" || exit 1

CONTRACT=".claude/skills/sgu-notice-rag-dev/references/api-contract.md"
DOCAPI="docs/api.md"
API_DIR="frontend/src/api"

fail=0
note() { printf '  ! %s\n' "$*"; }
ok()   { printf '  ✓ %s\n' "$*"; }
bad()  { printf '  ✗ %s\n' "$*"; fail=1; }

echo "== 1. 契约文件存在性 =="
for f in "$CONTRACT" "$DOCAPI"; do
  if [ -f "$f" ]; then ok "$f"; else bad "$f 缺失"; fi
done
[ "$fail" -ne 0 ] && { echo "契约文件缺失，终止检查"; exit 1; }

echo "== 2. 前端请求路径是否都在契约中 =="
if [ -d "$API_DIR" ]; then
  # 抓取三种写法，统一成契约里的绝对路径再比对：
  #   '/api/v1/xxx'      —— 已经带前缀（少见，但允许）
  #   '/v1/xxx'          —— BASE_URL 前缀由 client.ts 统一拼接（本项目写法）
  #   '/internal/xxx'    —— 内部接口（前端不应出现，出现即告警）
  # 排除测试文件：里面的 /v1/x 是故意造的假路径，不是真实调用
  paths=$(grep -rhoE --exclude-dir=__tests__ --exclude="*.spec.ts" \
    "/(api/v1|internal|v1)/[a-zA-Z0-9_./-]+" "$API_DIR" 2>/dev/null \
    | sed 's/[.,'"'"'\"\`)]*$//' \
    | sed -E 's#^/v1/#/api/v1/#' \
    | sort -u)
  if [ -z "$paths" ]; then
    note "未在 $API_DIR 找到任何 API 路径字面量"
  else
    while IFS= read -r p; do
      [ -z "$p" ] && continue
      if grep -qF -- "$p" "$CONTRACT"; then ok "$p"
      else bad "前端使用了契约中不存在的路径: $p"; fi
    done <<< "$paths"
  fi
else
  note "目录 $API_DIR 不存在（D 尚未开始）"
fi

echo "== 3. docs/api.md 与 references/api-contract.md 的路径集合一致性 =="
# 只认**端点形状**的路径：
#   /api/v1/<段>                 —— 对外端点
#   /internal/(retrieve|health)  —— 内部端点，只有这两个
# 不能放宽成 /internal/<任意>：文档里会写 `api-server/internal/model/types.go`
# 这类**代码路径**，宽松匹配会把它当成端点，产生假失败。假失败比不检查更糟 ——
# 一旦大家习惯性忽略检查结果，这道门就等于没有了。
PATH_RE='/(api/v1/[a-zA-Z0-9_/-]+|internal/(retrieve|health))'
paths_doc=$(grep -oE "$PATH_RE" "$DOCAPI" 2>/dev/null | sed 's/[.,'"'"'"\`)]*$//' | sort -u)
paths_ref=$(grep -oE "$PATH_RE" "$CONTRACT" 2>/dev/null | sed 's/[.,'"'"'"\`)]*$//' | sort -u)
if [ "$paths_doc" = "$paths_ref" ]; then
  ok "路径集合一致（$(printf '%s\n' "$paths_doc" | grep -c . ) 条）"
else
  while IFS= read -r p; do
    [ -z "$p" ] && continue
    printf '%s\n' "$paths_ref" | grep -qxF -- "$p" || bad "docs/api.md 有、契约无: $p"
  done <<< "$paths_doc"
  while IFS= read -r p; do
    [ -z "$p" ] && continue
    printf '%s\n' "$paths_doc" | grep -qxF -- "$p" || bad "契约有、docs/api.md 无: $p"
  done <<< "$paths_ref"
fi

echo "== 4. 关键字段抽查（防自造字段） =="
FIELDS="doc_id title publish_date effective_period doc_version section_path content_type source_url chunk_id recency_bucket is_current refusal_rate stale_recall_rate"
for f in $FIELDS; do
  if grep -qF -- "\`$f\`" "$CONTRACT"; then ok "字段 $f"
  else note "字段 $f 未在契约文件中以反引号登记"; fi
done

echo "== 5. 禁用项抽查 =="
if grep -rn "localhost:[0-9]\{2,5\}/api" frontend/src 2>/dev/null | grep -v "vite.config" | grep -q .; then
  bad "前端出现硬编码后端地址（localhost:PORT/api）"
else
  ok "前端未硬编码后端地址"
fi

if grep -rn "proxy_pass" infra/nginx/*.conf 2>/dev/null | grep -q .; then
  ok "Nginx 反代配置存在"
else
  note "未找到 infra/nginx 反代配置"
fi

echo
if [ "$fail" -ne 0 ]; then
  echo "检查未通过：先修 ✗ 级问题，再提交 PR。"
  echo "若是合法的契约变更，请按 SKILL.md §1 的三步流程同步 docs/api.md 与本 references。"
  exit 1
fi
echo "检查通过。"
