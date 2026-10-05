# custom/ —— DeepTutor 扩展层

本目录下的所有代码都是**外挂**，不修改 DeepTutor 任何核心文件。
因此 `git rebase upstream/main` 同步官方更新时**不会产生冲突**。

## 目录结构

```
custom/
├── importer/               # 导入增强
│   ├── core.py             # 核心：扫描 + 直接调用内部导入 API
│   ├── batch.py            # 批量导入目录（CLI）
│   └── watcher.py          # 监控文件夹自动导入（CLI）
├── launcher/
│   └── app.py              # 桌面启动器（pywebview 原生窗口）
├── sync_upstream.sh        # 一键同步官方更新
└── README.md
```

## 一、导入增强

### 原理

直接 import DeepTutor 内部函数 `deeptutor.knowledge.add_documents.add_documents`，
**绕过后端 HTTP 上传**：

- 不需要服务在运行
- 不受浏览器文件选择限制，可整目录导入
- 少一次「磁盘 → 网络 → 磁盘」搬运

### 批量导入

```bash
# 先看有哪些知识库
python3 custom/importer/batch.py --list

# 试运行：只列出会导入哪些文件
python3 custom/importer/batch.py 试卷库 ~/试卷 --exts .pdf,.docx --dry-run

# 实际导入
python3 custom/importer/batch.py 试卷库 ~/试卷 --exts .pdf,.docx

# 只导入某个文件
python3 custom/importer/batch.py 试卷库 ~/试卷/海淀数学.pdf
```

常用参数：

| 参数 | 说明 |
|---|---|
| `--exts .pdf,.docx` | 只导入这些扩展名 |
| `--no-recursive` | 不递归子目录 |
| `--batch 20` | 每批文件数，默认 20 |
| `--kb-dir PATH` | 知识库根目录（默认 `<仓库>/data/knowledge_bases`） |
| `--dry-run` | 只列出不导入 |
| `--duplicates` | 允许重复导入（默认跳过已入库的） |

### 监控文件夹自动导入

指定一个目录，往里丢文件就自动入库：

```bash
python3 custom/importer/watcher.py 试卷库 ~/试卷 --exts .pdf,.docx
```

- 用轮询实现，无额外依赖
- 文件连续 `--settle`（默认 10）秒未被修改才导入，避免写入未完成
- `Ctrl+C` 退出

### 环境变量

| 变量 | 说明 |
|---|---|
| `DEEPTUTOR_KB_DIR` | 知识库根目录，覆盖默认值 |

## 二、桌面启动器

```bash
pip install pywebview
python3 custom/launcher/app.py
```

它会：启动后端 → 启动前端 → 等就绪 → 打开原生窗口 → 关窗时清理全部子进程。

| 参数 | 默认 | 说明 |
|---|---|---|
| `--port` | 3782 | 前端端口 |
| `--backend-port` | 8001 | 后端端口 |
| `--python` | 当前解释器 | 用于跑后端的 Python |
| `--no-frontend` | — | 前端已 build 时跳过 dev server |
| `--timeout` | 240 | 就绪等待上限（秒） |

Windows 需要 WebView2 运行时（Win10 1809+/Win11 通常已预装）。

## 三、同步官方更新

```bash
bash custom/sync_upstream.sh --check   # 只看差异
bash custom/sync_upstream.sh           # 拉取并变基
```

脚本会先检查工作区是否干净，再 `git rebase upstream/main`。

**如果冲突发生在核心文件里，说明改动越界了**——应该改成外挂实现，
把逻辑挪回 `custom/` 下，保持核心零改动。

## 设计约束

1. **不修改核心文件**——任何改动都放在 `custom/` 下
2. **只 import、不 patch**——用内部 API 而非改写它
3. **依赖内部 API 的风险**——若官方改了 `add_documents` 签名，只需改本目录的
   `core.py`，核心仓库仍可无冲突同步
