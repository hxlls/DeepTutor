#!/usr/bin/env bash
# 同步官方 DeepTutor 更新到本地 fork。
#
# 设计前提：所有自定义代码都在 custom/ 目录，不修改任何核心文件，
# 因此 rebase 官方更新时不会产生冲突。
#
# 用法:
#   bash custom/sync_upstream.sh          # 拉取并变基
#   bash custom/sync_upstream.sh --check  # 只看差异，不改动
set -euo pipefail

cd "$(dirname "$0")/.."

if ! git remote get-url upstream >/dev/null 2>&1; then
  echo "未配置 upstream，正在添加..."
  git remote add upstream https://github.com/HKUDS/DeepTutor.git
fi

echo "=== 检查工作区 ==="
if [ -n "$(git status --porcelain)" ]; then
  echo "⚠️  工作区有未提交改动，请先提交或 stash："
  git status --short
  exit 1
fi
echo "干净"

echo
echo "=== 拉取官方更新 ==="
git fetch upstream --tags

LOCAL=$(git rev-parse --short HEAD)
REMOTE=$(git rev-parse --short upstream/main)
echo "本地  : $LOCAL $(git log -1 --format=%s HEAD)"
echo "官方  : $REMOTE $(git log -1 --format=%s upstream/main)"

if [ "$LOCAL" = "$REMOTE" ]; then
  echo
  echo "已是最新，无需同步。"
  exit 0
fi

echo
echo "=== 待合入的官方提交 ==="
git log --oneline HEAD..upstream/main | head -20
COUNT=$(git rev-list --count HEAD..upstream/main)
echo "共 $COUNT 个提交"

if [ "${1:-}" = "--check" ]; then
  echo
  echo "[--check] 未做任何改动。"
  exit 0
fi

echo
echo "=== 变基到官方 main ==="
if git rebase upstream/main; then
  echo
  echo "✓ 同步完成。"
  git log --oneline -5
  echo
  echo "下一步：git push origin main --force-with-lease"
else
  echo
  echo "✗ 出现冲突。"
  echo "  - 若冲突在 custom/ 下：直接解决后 git rebase --continue"
  echo "  - 若冲突在核心文件：说明有改动越界到了核心，建议改为外挂实现"
  echo "  - 放弃：git rebase --abort"
  exit 1
fi
