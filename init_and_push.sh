#!/usr/bin/env bash
# =============================================================================
# SGU-Notice-RAG —— Git 初始化与首次推送
#
# 用途：把当前工作区初始化成一个独立的 git 仓库，打一个基线提交，推到 GitHub。
# 用法：
#   1) 先把 REMOTE_URL 改成你们自己的仓库地址（见文件末尾的提示）
#   2) bash init_and_push.sh
#
# 为什么是"独立仓库"而不是塞进某个已有仓库的子目录：
#   GitHub Actions 只执行仓库根目录下的 .github/workflows/。
#   本项目把 CI 放在 .github/workflows/ci.yml，若作为子目录推送，CI 永远不会跑。
#   同理 .github/PULL_REQUEST_TEMPLATE.md 也只有位于仓库根才对 PR 生效。
# =============================================================================
set -euo pipefail

REMOTE_URL="${REMOTE_URL:-https://github.com/A1muE4aY/SGU-Notice-RAG.git}"
BASELINE_MSG="chore: 初始化项目骨架，建立 API 契约、Skill 约束与文档基线"

# 0) 前置检查：不要在一个已有仓库里重复初始化
if [ -d .git ]; then
  echo "✗ 当前目录已经是 git 仓库（存在 .git）。"
  echo "  如果你要重新初始化，请先自行确认并删除 .git（这一步不可撤销，脚本不代做）。"
  exit 1
fi

# 1) 初始化 git 仓库
#    -b main：直接以 main 为主分支，避免之后再 rename 时远端默认分支对不上
git init -b main

# 2) 确认忽略规则生效：.env 与构建产物绝不能进仓库
#    （.gitignore 已经写好；这里再核一次，防止有人删过它）
if ! grep -q '^\.env$' .gitignore 2>/dev/null; then
  echo "✗ .gitignore 里没有 .env 规则，先修好再提交（密钥泄露不可回滚）。"
  exit 1
fi

# 3) 添加所有文件
git add -A

# 4) 提交初始基线
#    - 用 -m 而不是打开编辑器，保证脚本可无人值守执行
#    - 若仓库里没有任何可提交内容会失败，这里显式提示，而不是抛一个看不懂的 git 错误
if git diff --cached --quiet; then
  echo "✗ 没有可提交的内容（检查 .gitignore 是否把一切都忽略了）。"
  exit 1
fi
git commit -m "$BASELINE_MSG"

# 5) 主分支名兜底
#    -b main 已经设好；这一步只兜住"旧版 git 不认 -b"的环境
git branch -M main

# 6) 关联远程仓库并推送
#    --set-upstream 让之后可以直接 git push / git pull 而不必带参数
echo
echo "即将关联远程仓库：$REMOTE_URL"
echo "（如果这不是你们的仓库地址，按 Ctrl+C 中止，修改本文件顶部的 REMOTE_URL 后重跑）"
git remote add origin "$REMOTE_URL" 2>/dev/null || git remote set-url origin "$REMOTE_URL"
git push -u origin main

echo
echo "✓ 基线已推送到 $REMOTE_URL （分支 main）"
echo
echo "接下来（不要跳过，否则 CI 与 PR 模板都不生效）："
echo "  1. 到 GitHub → Settings → Branches → 把 main 设为 Protected："
echo "     禁止直接 push、要求 1 个以上 Reviewer、要求 CI 通过才能合并。"
echo "  2. 到 GitHub → Issues → 为 A–F 六人分配第一周任务（已创建）。"
echo "  3. 把 README.md 里的 <仓库地址> 占位替换成真实地址。"
echo
echo "提示："
echo "  - 每个人拉自己的分支：git checkout -b feat/A-scraper"
echo "  - 直接 push 到 main 会被保护规则拒绝 —— 这是设计如此，不是配置错误。"
