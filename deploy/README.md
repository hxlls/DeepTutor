# DeepTutor 部署套件

面向学校信息管理员 / 老师的零参数部署包 —— 复制到服务器上三条命令跑起来。

## 快速开始

```bash
# 1. 把本目录整个拷到服务器（例如 /opt/deeptutor）
cd /opt/deeptutor

# 2. 生成配置（默认端口 80，按需改）
cp .env.example .env

# 3. 启动
docker compose up -d
```

浏览器打开 `http://<服务器IP>`，跟着开箱引导走：选部署档 → 填 API Key → 完成。

## 目录结构

```
deploy/
├── docker-compose.yml   主 compose（自带内置 ollama 服务，零外部依赖）
├── .env.example         配置模板（端口 / 时区 / 镜像版本）
├── upgrade.sh           五步升级（备份 → 拉镜像 → 重建 → 验证，失败自动回滚）
├── data/                业务数据与知识库（运行时生成，不进仓库）
├── models/              本地模型（运行时生成，0~4GB，看选的档位）
└── backups/             升级前自动备份
```

## 三档部署

第一次打开会走开箱引导，三选一：

| 档位 | 下载量 | 需要准备什么 |
|---|---|---|
| **全云** | 0 GB | 两个 API：大模型 + 向量，**可以是两家不同服务商** |
| **标准部署** | ~1.2 GB | 一个 API（大模型）+ 本地向量模型 bge-m3 |
| **全本地部署** | ~4 GB | 一个 API（视觉）+ bge-m3 + MinerU 解析引擎 |

模型全部**按需下载**：选全云不会下载任何东西；下载量跟选择走，不预装。

下载横幅里有「手动下载」面板 —— 嫌慢可以用迅雷等工具，
面板给出直链和对应的存放目录。

## 升级

```bash
./upgrade.sh            # 升到 latest
./upgrade.sh v1.6.13    # 升到指定版本
```

流程：备份 `data/` → 拉新镜像 → 重建容器 → 健康检查。
任一步失败会自动回滚到旧镜像。

## 回滚

```bash
./upgrade.sh <旧版本号>
```

升级前备份在 `backups/pre-upgrade-*.tar.gz`，只保留最近 5 份。
备份会**排除模型目录**（`.mineru` / `.ollama` / `.cache` 合计约 3.2GB，可重建）。

## 常见问题

| 现象 | 处理 |
|---|---|
| 端口 80 被占用 | 改 `.env` 的 `DEEPTUTOR_PORT` |
| 时间不对 / AI 回答「今天几号」差一天 | `.env` 的 `DEEPTUTOR_TZ` 必须是 `Asia/Shanghai`（官方镜像默认 UTC） |
| 模型下载太慢 | 用下载横幅里的「手动下载」面板拿直链 |
| 换过向量服务后检索变差 | 向量维度变了，旧索引不再匹配 —— 需要重新导入文档 |
| 试卷里的公式图搜不到 | 见仓库根目录 `已实现-试卷助手.md` 的 2.9 节 |

## 注意

- `data/`、`models/`、`backups/`、`.env` 已写进本目录的 `.gitignore`，**不要提交**
- compose 里的镜像地址是 `ghcr.io/hxlls/deeptutor:${DEEPTUTOR_TAG}`，
  版本由 `.env` 的 `DEEPTUTOR_TAG` 控制
- `docker-compose.yml` **必须叫这个名字**，改名会报
  `no configuration file provided`
