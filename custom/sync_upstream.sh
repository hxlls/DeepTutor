#!/usr/bin/env bash
# ============================================
# 同步官方 DeepTutor 更新到本地 fork
# ============================================
# 设计前提（2026-10 起修订）
# --------------------------
# 早期版本写的是「不修改任何核心文件」。后来发现有两处**必须**改官方源码：
#
#   1. MinerU 4.x 兼容     官方用的还是 3.x 的 `mineru -p <f> -o <dir>` 语法
#   2. 图注内联回正文       官方在文本向量下会静默丢弃所有图片
#
# 这两处**不提交进仓库**，而是在 **Docker 构建时**由
# `custom/patches/apply_all.py` 施加（见 Dockerfile 里的 RUN 那一步）。
#
# 换来两个好处：
#   · 核心文件在 git 里与官方一致 → rebase 永不冲突
#   · 上游一改锚点 → 构建失败（fail-fast），而不是静默失效
#
# 本脚本在 rebase 之后跑一次 `apply_all.py --verify` 干跑校验锚点，
# 不写盘，所以不会把工作区弄脏。
#
# ⚠️ 仍有几处**手工核心改动**未纳入补丁机制（rebase 时可能冲突）：
#   deeptutor/api/main.py                                    挂载 assistant 路由
#   web/app/layout.tsx                                       挂载下载横幅/引导
#   web/components/knowledge/KbLinkedFoldersSection.tsx
#   web/components/quiz/QuizViewer.tsx
#   冲突时按提示逐个解决即可；这几个文件改动都很小（4~10 行）。
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
  echo "✓ 变基完成。"
else
  echo
  echo "✗ 出现冲突。"
  echo "  - 冲突在 custom/ 下      ：解决后 git rebase --continue"
  echo "  - 冲突在 deeptutor/ 或 web/ 下的核心文件："
  echo "      说明官方改了我们动过的地方，需要人工判断怎么合。"
  echo "      （注意：custom/patches/ 里的补丁目标文件**不该**出现在冲突里 ——"
  echo "        它们在 git 里应与官方一致。若出现，说明有人把补丁提交进去了。）"
  echo "  - 放弃：git rebase --abort"
  exit 1
fi

echo
echo "=== 干跑校验核心补丁锚点 ==="
echo "（核心文件在 git 里与官方一致，补丁由构建时施加）"
echo
if ! python3 custom/patches/apply_all.py --verify; then
  echo
  echo "✗ 上游改动了补丁依赖的代码，需要更新 custom/patches/ 下的补丁模块。"
  echo "  请按上面的提示核对锚点。在此之前**不要**构建发布镜像 ——"
  echo "  构建会在同一步失败（这是有意设计的 fail-fast）。"
  exit 1
fi

echo
echo "=== 同步完成 ==="
git log --oneline -5
echo
echo "下一步："
echo "  1. git push origin main --force-with-lease"
echo "  2. 重新构建镜像（会自动应用核心补丁）"
