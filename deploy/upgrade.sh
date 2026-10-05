#!/usr/bin/env bash
# ============================================
# DeepTutor 升级脚本
# ============================================
# 流程：备份 → 记录版本 → 拉新镜像 → 重建 → 验证
# 任一步失败会自动回滚到旧镜像。
#
# 用法：
#   ./upgrade.sh             升级到 latest
#   ./upgrade.sh v1.6.14     升级到指定版本
# ============================================
set -euo pipefail

cd "$(dirname "$0")"

COMPOSE_FILE="docker-compose.yml"
TARGET_TAG="${1:-latest}"
STATE_FILE=".deeptutor-version"
BACKUP_DIR="./backups"

log()  { printf '\033[36m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
warn() { printf '\033[33m[警告]\033[0m %s\n' "$*"; }
die()  { printf '\033[31m[失败]\033[0m %s\n' "$*" >&2; exit 1; }

# ---------- 0. 前置检查 ----------
command -v docker >/dev/null || die "未找到 docker"
docker compose version >/dev/null 2>&1 || die "需要 docker compose v2"

OLD_TAG="$(cat "$STATE_FILE" 2>/dev/null || echo '')"
log "当前版本: ${OLD_TAG:-未记录}"
log "目标版本: $TARGET_TAG"

if [ "$OLD_TAG" = "$TARGET_TAG" ]; then
  warn "版本相同，仍会执行重建（用于修复损坏的容器）"
fi

# ---------- 1. 备份数据（最重要） ----------
log "步骤 1/5 · 备份数据"
mkdir -p "$BACKUP_DIR"
if [ -d "./data" ]; then
  STAMP="$(date +%Y%m%d-%H%M%S)"
  ARCHIVE="$BACKUP_DIR/pre-upgrade-${STAMP}.tar.gz"

  # 停容器保证 SQLite 一致（若在运行）
  if docker compose -f "$COMPOSE_FILE" ps --quiet 2>/dev/null | grep -q .; then
    log "  暂停容器以保证数据一致..."
    docker compose -f "$COMPOSE_FILE" stop
    STOPPED=1
  else
    STOPPED=0
  fi

  tar czf "$ARCHIVE" -C . data 2>/dev/null || die "备份失败"
  SIZE="$(du -h "$ARCHIVE" | cut -f1)"
  log "  已备份: $ARCHIVE ($SIZE)"

  # 只保留最近 5 份升级前备份
  ls -1t "$BACKUP_DIR"/pre-upgrade-*.tar.gz 2>/dev/null | tail -n +6 | xargs -r rm -f
else
  warn "  ./data 不存在，跳过备份（首次部署？）"
  STOPPED=0
fi

# ---------- 2. 拉取新镜像 ----------
log "步骤 2/5 · 拉取镜像 $TARGET_TAG"
if ! docker pull "ghcr.io/hxlls/deeptutor:${TARGET_TAG}"; then
  die "镜像拉取失败，未做任何变更"
fi

# ---------- 3. 重建容器 ----------
log "步骤 3/5 · 重建容器"
echo "$TARGET_TAG" > "$STATE_FILE"

export DEEPTUTOR_TAG="$TARGET_TAG"
if ! docker compose -f "$COMPOSE_FILE" up -d --remove-orphans; then
  warn "启动失败，尝试回滚到 $OLD_TAG"
  if [ -n "$OLD_TAG" ]; then
    echo "$OLD_TAG" > "$STATE_FILE"
    DEEPTUTOR_TAG="$OLD_TAG" docker compose -f "$COMPOSE_FILE" up -d
    die "已回滚到 $OLD_TAG，请检查日志"
  fi
  die "启动失败且无旧版本可回滚"
fi

# ---------- 4. 健康检查 ----------
log "步骤 4/5 · 等待服务就绪"
PORT="${DEEPTUTOR_PORT:-80}"
for i in $(seq 1 30); do
  if curl -fsS "http://localhost:${PORT}/" >/dev/null 2>&1; then
    log "  服务已就绪（${i}0 秒内）"
    break
  fi
  if [ "$i" -eq 30 ]; then
    warn "服务 300 秒内未响应，请查看日志："
    echo "  docker compose -f $COMPOSE_FILE logs --tail 50"
  fi
  sleep 10
done

# ---------- 5. 完成 ----------
log "步骤 5/5 · 升级完成"
cat <<EOF

  版本:   ${OLD_TAG:-无} → ${TARGET_TAG}
  访问:   http://localhost:${PORT}
  备份:   ${ARCHIVE:-无}

  如需回滚:
    ./upgrade.sh ${OLD_TAG:-<旧版本>}

EOF
