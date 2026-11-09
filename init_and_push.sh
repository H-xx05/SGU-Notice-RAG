#!/usr/bin/env bash
# 一键把当前目录推到远端。
#
# 注意：这个脚本只在**第一次建仓**时用。项目已经在
# https://github.com/A1muE4aY/SGU-Notice-RAG 上跑起来了，
# 日常开发走分支 + PR（见 CONTRIBUTING.md），不要用它直推 main。
set -euo pipefail

REMOTE_URL="${REMOTE_URL:-https://github.com/A1muE4aY/SGU-Notice-RAG.git}"
BRANCH="${BRANCH:-main}"

echo "==> 目标仓库：$REMOTE_URL（分支 $BRANCH）"

# 防御 1：已经是 git 仓库就退出，避免把已有历史覆盖掉
if [ -d .git ]; then
    echo "✗ 当前目录已经是 git 仓库。"
    echo "  如果你只是想推送改动：git push origin $BRANCH"
    echo "  如果你确实要重新初始化：先备份 .git 再手动删除。"
    exit 1
fi

# 防御 2：.env 必须被忽略。真提交一次密钥就得全部轮换。
if ! grep -qE '^\.env$' .gitignore 2>/dev/null; then
    echo "✗ .gitignore 里没有 '.env' 规则。"
    echo "  先补上再跑这个脚本 —— 泄露一次密钥，六个人都要轮换。"
    exit 1
fi

git init -b "$BRANCH"

# 四份非交付物（元提示词、外来的两份说明、一个脚本）与空目录不入库。
if [ -d .git/info ]; then
    cat >> .git/info/exclude <<'EOF'

# 本地材料，不属于仓库内容
/0.txt
/git-ready.txt
/group-ready.txt
/tools-common.js
/source/
EOF
fi

git add -A

# 防御 3：没什么可提交的就别建空提交
if git diff --cached --quiet; then
    echo "✗ 没有可提交的内容。"
    exit 1
fi

git commit -m "chore: 初始化项目骨架，建立 API 契约、Skill 约束与文档基线"
git remote add origin "$REMOTE_URL"
git push -u origin "$BRANCH"

echo
echo "==> 完成。接下来两件事："
echo "    1. 到 GitHub 仓库设置里开启分支保护（main 要求 PR + 1 个 approve）"
echo "    2. 把 Issue 面板链接发到群里"
