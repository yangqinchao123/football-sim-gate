#!/usr/bin/env bash
# 把版本化的 git hooks 安装到当前仓库的 .git/hooks
set -euo pipefail
REPO_ROOT="$(git rev-parse --show-toplevel)"
SRC_DIR="$REPO_ROOT/tools/git_hooks"

for hook in pre-push; do
  cp "$SRC_DIR/$hook" "$REPO_ROOT/.git/hooks/$hook"
  chmod +x "$REPO_ROOT/.git/hooks/$hook"
  echo "已安装 $hook"
done
echo "git 钩子安装完成"
