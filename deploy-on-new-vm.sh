#!/usr/bin/env bash
# ============================================
# DeepTutor 试卷助手 · 新虚拟机源码构建部署脚本
# 适用：Ubuntu 20.04/22.04/24.04 (amd64/arm64)
# 前置：fork 仓库公开、可访问外网
# 用法：sudo bash deploy-on-new-vm.sh
# 每步带验证，失败即停（set -e）
# ============================================
set -euo pipefail

GITHUB_USER="<你的GitHub用户名>"        # ← 改成你的 GitHub 用户名
REPO_URL="https://github.com/${GITHUB_USER}/DeepTutor.git"

log() { printf '\033[36m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf '\033[31m[失败]\033[0m %s\n' "$*" >&2; exit 1; }

# ---------- 1. 检测 + 安装 Docker 与 Compose 插件 ----------
log "检测 Docker..."
if ! command -v docker >/dev/null 2>&1; then
  log "未找到 docker，开始安装（官方安装脚本）..."
  curl -fsSL https://get.docker.com | sh \
    || die "Docker 安装失败（可能网络/权限问题），请手动安装后重跑：curl -fsSL https://get.docker.com | sh"
  systemctl enable --now docker
fi

# 检测 daemon 是否真正可用（不只是命令存在）
if ! docker info >/dev/null 2>&1; then
  log "docker 已安装但 daemon 未运行，尝试启动..."
  systemctl start docker 2>/dev/null || service docker start 2>/dev/null || true
  sleep 3
  docker info >/dev/null 2>&1 || die "docker daemon 无法启动，请检查：systemctl status docker"
fi
log "docker 就绪: $(docker --version)"

# 检测 Compose 插件（docker compose v2，build 必需）
if ! docker compose version >/dev/null 2>&1; then
  log "缺少 compose 插件，尝试安装 docker-compose-plugin..."
  apt-get update -y && apt-get install -y docker-compose-plugin \
    || die "compose 插件安装失败，请手动安装：apt-get install -y docker-compose-plugin"
fi
log "compose 就绪: $(docker compose version)"

# ---------- 2. 配置 Docker Hub 镜像加速器（国内拉基础镜像/ollama 必需） ----------
log "配置 Docker 镜像加速器（daocloud，拉 node/python/ollama 基础镜像更快）..."
if ! docker info 2>/dev/null | grep -q "docker.m.daocloud.io"; then
  mkdir -p /etc/docker
  cat > /etc/docker/daemon.json <<'EOF'
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io/",
    "https://docker.1ms.run/"
  ]
}
EOF
  systemctl restart docker
fi
docker info | grep -A2 "Registry Mirrors"

# ---------- 3. 克隆 / 更新公开 fork 源码 ----------
log "获取源码 ${REPO_URL} ..."
if [ ! -d DeepTutor/.git ]; then
  git clone --depth 1 "${REPO_URL}"
else
  log "源码已存在，git pull 更新..."
  # --ff-only 只做快进合并；本地有未提交改动（如修复补丁）时 pull 会失败，
  # 此时不中断，继续用现有源码构建，避免覆盖本地修改
  git -C DeepTutor pull --ff-only 2>/dev/null \
    || log "⚠ git pull 失败（网络或本地有改动），继续用现有源码构建"
fi
cd DeepTutor
git log --oneline -1

# ---------- 4. （可选）npm/pip 换国内源 ----------
# GitHub 正常直连的话可不做；构建超时再启用：
# log "npm 换 npmmirror、pip 换清华源（应对国内拉包慢）..."
# sed -i 's|npm ci --legacy-peer-deps|npm config set registry https://registry.npmmirror.com \&\& npm ci --legacy-peer-deps|' Dockerfile
# sed -i 's|RUN pip install --upgrade pip|RUN pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple|' Dockerfile

# ---------- 5. 放好源码构建版 compose（build.yml 更新时自动同步） ----------
log "部署 compose（build 版）..."
cd ..
SRC="DeepTutor/docker-compose.build.yml"
[ -f "$SRC" ] || SRC="docker-compose.build.yml"
[ -f "$SRC" ] || SRC="/root/deploy/docker-compose.build.yml"
if [ -f "$SRC" ]; then
  if [ ! -f docker-compose.yml ] || [ "$SRC" -nt docker-compose.yml ]; then
    cp "$SRC" ./docker-compose.yml
    log "已同步 docker-compose.build.yml → docker-compose.yml"
  else
    log "docker-compose.yml 已是最新，无需同步"
  fi
else
  log "警告：未找到 docker-compose.build.yml，请把它放到本目录后手动 docker compose up -d --build"
fi
grep -q "build:" docker-compose.yml || log "警告：docker-compose.yml 不含 build 段，请使用源码构建版 compose"

# ---------- 5b. （可选）预建数据目录，属主归当前用户 ----------
# 不执行也行：Docker 会自动创建缺失目录（容器以 root 运行，写入无权限问题）。
# 想让宿主侧直接浏览 data/models/backups 时再取消注释：
mkdir -p data data/redis models/ollama models/mineru backups
# 宿主侧目录归当前用户便于浏览；data/redis 必须归 redis 标准 UID 999，
# 否则 Redis 写 RDB 快照 Permission denied → MISCONF 只读（协调全挂）。
chown -R "$(id -u)":"$(id -g)" models backups
chown -R 999:999 data/redis

# ---------- 6. 构建 + 启动 ----------
if docker compose ps -q 2>/dev/null | grep -q .; then
  log "检测到已有容器在运行，请选择："
  echo ""
  echo "  1) 重建更新 —— 重新构建镜像并重建容器（代码/配置改动生效，约 5-15 分钟）"
  echo "  2) 直接启动 —— 用现有镜像，不重新构建（快）"
  echo "  3) 退出"
  echo ""
  read -r -p "输入 1/2/3（默认 2）：" choice
  choice=${choice:-2}
  case "$choice" in
    1)
      log "重建镜像 + 重建容器..."
      docker compose up -d --build
      ;;
    2)
      log "直接启动（不重新构建）..."
      docker compose up -d
      ;;
    3)
      die "已退出，未做任何改动"
      ;;
    *)
      log "无效输入，按默认直接启动（不重新构建）..."
      docker compose up -d
      ;;
  esac
else
  log "首次部署，开始构建（首次约 20-40 分钟，之后秒级）..."
  docker compose up -d --build
fi

# ---------- 7. 验证 ----------
log "等待服务健康（最多 120s）..."
for i in $(seq 1 24); do
  sleep 5
  S=$(docker inspect deeptutor --format '{{.State.Health.Status}}' 2>/dev/null || echo none)
  log "deeptutor 状态: $S"
  if [ "$S" = "healthy" ]; then break; fi
done
docker ps --format '{{.Names}}  {{.Status}}'
log "前端: http://<本机IP>:${DEEPTUTOR_PORT:-80}  （浏览器打开，走开箱引导）"
log "后端 API: http://<本机IP>:${DEEPTUTOR_API_PORT:-8001}（可选）"
log "完成！首次引导会自动下载 bge-m3(1.2GB)；用增强导入会再下 MinerU(2.8GB)。"
