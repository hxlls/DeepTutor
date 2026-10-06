# -*- coding: utf-8 -*-
"""试卷助手后端端点。

把 custom/ 里的扩展能力（环境检测、模型部署、视觉增强、题目保存）
包装成 FastAPI 路由，供前端引导向导与增强导入按钮调用。

设计原则：
  · 只读/写 DeepTutor 已有的存储与配置，不修改其内部实现
  · 复用 custom/gui/backend.py 与 custom/vision/ 的既有逻辑
"""
from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

router = APIRouter()

# custom/ 目录在仓库根下。
# 绿色包（Windows 免安装版）里本文件位于 site-packages，parents[3] 会指向
# site-packages；启动器通过 DEEPTUTOR_ROOT 环境变量指回绿色包根，使
# 配置/预设写入 --home 数据目录而非包内。容器/源码树不设该变量，行为不变。
_REPO_ROOT = Path(os.environ.get('DEEPTUTOR_ROOT') or Path(__file__).resolve().parents[3])
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _backend():
    """延迟导入，避免循环依赖与启动期开销。"""
    from custom.gui import backend as B
    return B


# ---------- 请求模型 ----------

class SaveLlmRequest(BaseModel):
    api_key: str = Field(min_length=1)
    provider: str = "xiaomi_mimo"
    model: str = ""
    base_url: str = ""


class TestLlmRequest(BaseModel):
    api_key: str = Field(min_length=1)
    provider: str = "xiaomi_mimo"
    model: str = ""
    base_url: str = ""


class TestEmbeddingRequest(BaseModel):
    api_key: str = Field(min_length=1)
    provider: str = "zhipu"
    model: str = ""
    base_url: str = ""


class EmbeddingConfig(BaseModel):
    """「全云」档的向量服务配置。

    与大模型 Key 是**互相独立**的槽位 —— 可以用 MiMo 做对话、
    智谱做向量，不必是同一家服务商。
    """

    provider: str = ""
    api_key: str = ""
    model: str = ""
    base_url: str = ""
    dimension: int = 0


class DeployRequest(BaseModel):
    # cloud = 全云（不下任何本地模型，向量走云端 API）
    mode: Literal["cloud", "standard", "full"] = "standard"
    embedding: EmbeddingConfig | None = None


class EnhanceRequest(BaseModel):
    paths: list[str] = Field(default_factory=list)
    api_key: str = ""
    model: str = "mimo-v2.6-flash"


class SaveQuestionRequest(BaseModel):
    question: str = Field(min_length=1)
    question_type: str = ""
    options: dict[str, str] = Field(default_factory=dict)
    correct_answer: str = ""
    explanation: str = ""
    user_answer: str = ""
    category: str = ""
    target: Literal["bank", "mistakes"] = "bank"


class BackupRequest(BaseModel):
    keep: int = 7
    note: str = ""


# ---------- 环境与配置 ----------

def _ensure_preset_settings():
    """首次运行时写入预设配置。

    主要是界面语言：DeepTutor 默认值是 "auto"，但前端 normalizeLanguage()
    不认识 "auto"，会落到兜底 "en" —— 所以中文用户首次会看到英文界面。

    注意：DeepTutor 自身启动时可能已经生成 interface.json（把 auto 落成 en），
    所以不能只判断"文件不存在"，而要用标记文件判断是否首次。
    """
    try:
        settings = _REPO_ROOT / 'data' / 'user' / 'settings'
        settings.mkdir(parents=True, exist_ok=True)
        marker = settings / '.preset-applied'

        if marker.exists():
            return          # 已应用过，尊重用户后续的手动修改

        iface = settings / 'interface.json'
        preset = _REPO_ROOT / 'custom' / 'presets' / 'interface.json'

        # 读取现有内容（可能已被 DeepTutor 初始化为 en）
        data = {}
        if iface.is_file():
            try:
                data = json.loads(iface.read_text(encoding='utf-8'))
            except Exception:
                data = {}

        # 预设内容优先，但保留用户已有的其他键
        if preset.is_file():
            try:
                data.update(json.loads(preset.read_text(encoding='utf-8')))
            except Exception:
                data.setdefault('language', 'zh')
        else:
            data.setdefault('language', 'zh')

        iface.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                         encoding='utf-8')
        marker.touch()
    except Exception:
        pass          # 预设写入失败不应影响主流程


@router.get("/status")
async def status():
    """配置完整性 + 硬件信息（供引导向导决定推荐哪种部署方式）。"""
    _ensure_preset_settings()

    from custom.gui.backend import check_env, in_container

    env = check_env()
    env["in_container"] = in_container()

    # 硬件信息
    try:
        import os
        env["cpu_arch"] = platform.machine()
        env["is_x86"] = platform.machine().lower() in ("x86_64", "amd64")
        mem = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") \
            if hasattr(os, "sysconf") else 0
        env["mem_gb"] = round(mem / 1024 ** 3, 1) if mem else 0
        usage = shutil.disk_usage(str(_REPO_ROOT))
        env["disk_free_gb"] = round(usage.free / 1024 ** 3, 1)
    except Exception:
        env.setdefault("cpu_arch", platform.machine())
        env.setdefault("is_x86", False)
        env.setdefault("mem_gb", 0)
        env.setdefault("disk_free_gb", 0)

    # 引导是否已完成。
    # 用 embed_configured 而不是 embed_ready —— 后者只表示「本地 Ollama 有没有
    # bge-m3」，而「全云」档本来就不跑本地 embedding，用它会误判成未完成，
    # 让选全云的用户每次开页面都被弹向导。
    env["need_onboarding"] = not (
        env.get("mimo_key_set") and env.get("embed_configured")
    )

    # 推荐部署方式
    if env["is_x86"] and env["mem_gb"] >= 8 and env["disk_free_gb"] >= 10:
        env["recommended_mode"] = "full"
    else:
        env["recommended_mode"] = "standard"

    return env


@router.get("/providers")
async def providers():
    """可选的大模型服务商。"""
    from custom.gui.backend import LLM_PROVIDERS
    return {k: {"name": v["name"], "models": v["models"],
                "default": v["default"], "key_url": v["key_url"],
                "key_label": v.get("key_label", ""),
                "key_hint": v["key_hint"], "base_url": v["base_url"]}
            for k, v in LLM_PROVIDERS.items()}


@router.post("/save-llm")
async def save_llm(req: SaveLlmRequest):
    from custom.gui.backend import save_llm_config, test_llm
    res = save_llm_config(req.api_key, req.provider, req.model, req.base_url)
    if res.get("ok"):
        res["test"] = await asyncio.to_thread(
            test_llm, req.api_key, req.provider, req.model, req.base_url)
    return res


@router.post("/test-llm")
async def test_llm_route(req: TestLlmRequest):
    from custom.gui.backend import test_llm
    return await asyncio.to_thread(
        test_llm, req.api_key, req.provider, req.model, req.base_url)


# ---------- 云端向量服务（全云档） ----------

@router.get("/embedding-providers")
async def embedding_providers():
    """可选的云端向量服务列表。

    与大模型服务商是**两套独立的槽位** —— 全云档允许分别指定，
    所以这里单独开一个接口，不复用 /providers。
    """
    from custom.gui.backend import EMBEDDING_PROVIDERS
    return {
        k: {
            "name": v["name"],
            "models": v["models"],
            "default": v["default"],
            "key_url": v["key_url"],
            "key_label": v.get("key_label", ""),
            "key_hint": v.get("key_hint", ""),
            "base_url": v["base_url"],
        }
        for k, v in EMBEDDING_PROVIDERS.items()
    }


@router.post("/test-embedding")
async def test_embedding_route(req: TestEmbeddingRequest):
    """测试向量服务连通性，并返回探测到的向量维度。

    维度由实际调用反推，不硬编码 —— 各家默认值不同且大多可调，
    写错会让检索静默失效。
    """
    from custom.gui.backend import test_embedding
    return await asyncio.to_thread(
        test_embedding, req.api_key, req.provider, req.model, req.base_url)


@router.get("/model-links")
async def model_links():
    """模型的手动下载清单：文件直链 + 目标目录。

    给想用迅雷 / IDM 下载的用户。直链 302 到 modelscope 的 CDN，
    带 Content-Length 且支持 Range，多线程下载工具可直接用。
    """
    from custom.gui.backend import model_download_manifest
    return await asyncio.to_thread(model_download_manifest)


# ---------- 模型部署 ----------

_PROGRESS: list[dict] = []
# 最近一次后台任务的起始下标、运行状态与类型。
# 前端刷新页面后 cursor 会归零，若直接从头拉取会把上一次任务的日志
# （包括结尾的 done）也读进来，造成「刚打开就显示已完成」的误判。
_TASK_START = 0
_TASK_RUNNING = False
# "deploy"（装模型）/ "import"（导试卷）/ None。
# 两类任务的横幅文案完全不同，前端靠它决定显示哪一个。
_TASK_KIND: str | None = None


def _begin_task(kind: str) -> None:
    """标记一个后台任务开始。

    所有会跑很久的操作（部署模型、导入试卷）都必须走这里 ——
    前端靠 ``_TASK_RUNNING`` 决定要不要显示进度条。
    """
    global _TASK_START, _TASK_RUNNING, _TASK_KIND
    _TASK_START = len(_PROGRESS)
    _TASK_RUNNING = True
    _TASK_KIND = kind


def _end_task() -> None:
    global _TASK_RUNNING
    _TASK_RUNNING = False


def _emit(kind: str, msg: str = ""):
    """记录进度。

    兼容两种调用方式：
      _emit("info", "文本")   —— 本模块内部用
      _emit("文本")           —— custom/gui/backend.py 的回调约定（单参数）
    """
    if msg == "":
        kind, msg = "info", kind
    _PROGRESS.append({"kind": kind, "msg": str(msg)})
    if len(_PROGRESS) > 500:
        del _PROGRESS[:200]
        # 裁剪后下标整体前移，任务起点也要跟着修正
        global _TASK_START
        _TASK_START = max(0, _TASK_START - 200)


@router.get("/progress")
async def progress(since: int = 0):
    return {
        "logs": _PROGRESS[since:],
        "total": len(_PROGRESS),
        "task_start": _TASK_START,
        "running": _TASK_RUNNING,
        "kind": _TASK_KIND,
    }


@router.post("/deploy-local")
async def deploy_local(req: DeployRequest):
    """部署本地模型。standard = 仅 Ollama+bge-m3；full = 额外装 MinerU。"""
    _begin_task("deploy")

    async def job():
        try:
            await _deploy_job(req)
        finally:
            _end_task()

    asyncio.create_task(job())
    return {"ok": True, "msg": "任务已启动", "task_start": _TASK_START}


async def _deploy_job(req: DeployRequest):
    # 全云：不下载任何本地模型，只把云端向量配置落盘
    if req.mode == "cloud":
        await _deploy_cloud(req)
        return

    from custom.gui.backend import ollama_host, pull_embed_model, start_ollama

    _emit("info", f"Ollama 服务地址：{ollama_host()}")
    _emit("info", f"向量模型下载源：ollama.com/library/bge-m3（约 1.2GB）")
    _emit("info", "存放位置：Ollama 所在机器上的 ~/.ollama/models")

    _emit("info", "=== 启动 Ollama ===")
    ok = await asyncio.to_thread(start_ollama, _emit)
    if not ok:
        _emit("error", "Ollama 启动失败")
        _emit("info", "【手动启动】OLLAMA_HOST=0.0.0.0:11434 ollama serve")
        return

    _emit("info", "=== 准备向量模型 ===")
    ok = await asyncio.to_thread(pull_embed_model, _emit)
    if not ok:
        _emit("error", "向量模型准备失败")
        _emit("info", "【手动下载】ollama pull bge-m3")
        return

    if req.mode == "full":
        _emit("info", "=== 部署 MinerU 解析模型 ===")
        await asyncio.to_thread(_deploy_mineru, _emit)

    _emit("done", "本地模型部署完成，现在可以导入试卷了")


async def _deploy_cloud(req: DeployRequest):
    """全云档：不下载任何本地模型，只写入云端向量配置。

    这一档解决两类人的问题：
      · 磁盘/带宽紧张，不想为 1.2GB 的向量模型买单
      · 已经有现成的云端向量服务，没必要再跑一份本地的

    大模型 Key 在第 2 步就存过了，这里只补向量那一半。
    """
    from custom.gui.backend import kb_count, save_embedding_config

    emb = req.embedding
    if emb is None or not (emb.api_key or "").strip():
        _emit("error", "全云模式需要先填写向量服务的 API Key")
        _emit("info", "在第 3 步选「全云」后展开的向量服务里填写并测试")
        return

    _emit("info", "全云模式：不下载任何本地模型")
    _emit("info", "试卷解析走云端视觉模型，知识库检索走云端向量服务")

    res = await asyncio.to_thread(save_embedding_config, {
        "mode": "cloud",
        "provider": emb.provider,
        "api_key": emb.api_key,
        "model": emb.model,
        "base_url": emb.base_url,
        "dimension": emb.dimension,
    })
    if not res.get("ok"):
        _emit("error", res.get("msg", "向量配置写入失败"))
        return
    _emit("info", f"向量服务：{res.get('msg', '')}")

    # 换向量服务会让已建知识库的索引失配 —— 症状很隐蔽（不报错，
    # 只是检索结果不对），所以必须在这里明说。
    try:
        n_kb = await asyncio.to_thread(kb_count)
    except Exception:
        n_kb = 0
    if n_kb > 0:
        _emit("info", f"⚠ 检测到 {n_kb} 个已有知识库：向量服务换了以后，"
                      f"旧索引不再匹配，需要重新导入试卷才能正常检索")

    _emit("done", "配置完成，现在可以导入试卷了")


def _deploy_mineru(emit):
    """安装 MinerU 并下载模型（复用 custom/importer/mineru_runner 的统一实现）。

    完整链路：安装 CLI → 启动本地服务 → 下载模型 → 开启本地解析模式。
    其中「启动本地服务」是 MinerU 4.x 新增的必要步骤，缺了它 config/parse
    都会失败。
    """
    from custom.importer import mineru_runner

    home = Path(os.environ.get("MINERU_HOME", "") or (Path.home() / ".mineru"))
    emit("info", "MinerU 模型下载源：modelscope.cn（约 2.0GB）")
    emit("info", f"存放位置：{home / 'models'}")

    if mineru_runner.prepare_mineru(lambda m: emit("info", m)):
        emit("done", "MinerU 部署完成")
    else:
        emit("error", "MinerU 部署失败")
        emit("info", "【手动部署】在容器/终端内依次执行：")
        emit("info", "  pip install mineru")
        emit("info", "  mineru server start")
        emit("info", "  mineru-kit models download --tier standard --source modelscope")
        emit("info", "  mineru config set parse_server.local.mode managed")


# ---------- 增强导入 ----------

@router.post("/scan")
async def scan_dir(payload: dict):
    """扫描目录，返回含图文件与预估成本（Markdown 专用，保留兼容）。"""
    from custom.vision import count_images, estimate_cost

    d = Path(payload.get("dir", ""))
    if not d.is_dir():
        return {"ok": False, "msg": f"目录不存在: {d}"}

    items = []
    for md in sorted(d.rglob("*.md")):
        if md.name.endswith(".enriched.md"):
            continue
        _, uniq = count_images(md)
        if uniq:
            est = estimate_cost(md)
            items.append({"path": str(md), "name": md.name,
                          "images": uniq, "cost": est["cost_yuan"]})
    return {"ok": True, "items": items,
            "total_cost": round(sum(i["cost"] for i in items), 2)}


@router.post("/scan-docs")
async def scan_docs(payload: dict):
    """扫描目录，按文档类型分类（PDF/Word/PPT/Excel/Markdown）。"""
    from custom.importer import scan_documents
    return await asyncio.to_thread(scan_documents, payload.get("dir", ""))


@router.post("/import-docs")
async def import_docs(payload: dict):
    """统一导入：格式分流 → MinerU 解析 → 残留图片兜底 → 入库。"""
    from custom.importer import import_documents

    kb = payload.get("kb", "")
    d = payload.get("dir", "")
    api_key = payload.get("api_key", "")
    model = payload.get("model", "mimo-v2.6-flash")
    use_fallback = payload.get("use_fallback", True)

    if not kb or not d:
        return {"ok": False, "msg": "缺少知识库名或目录"}

    # 立刻返回，解析在后台跑。前端据此关闭弹窗，改由顶部任务条显示进度。
    _begin_task("import")

    async def job():
        try:
            r = await asyncio.to_thread(
                import_documents, kb, d, api_key, model, use_fallback, _emit)
            if r.get("ok"):
                _emit("done", f"导入完成：{r.get('imported', 0)} 个文件")
            else:
                _emit("error", r.get("msg", "导入失败"))
        except Exception as e:
            _emit("error", f"导入异常：{e}")
        finally:
            _end_task()

    asyncio.create_task(job())
    return {"ok": True, "msg": "任务已启动"}


# 单文件大小上限（前端也会拦一道，这里是服务端兜底）
_MAX_UPLOAD_BYTES = 512 * 1024 * 1024

# 上传临时目录的前缀与最长存活时间。
# 正常路径下 job 的 finally 会删掉自己的临时目录；但容器被强杀 / 进程崩时
# 走不到 finally，残留会一直占磁盘（一份试卷夹可能几百 MB）。
_UPLOAD_TMP_PREFIX = "deeptutor-upload-"
_UPLOAD_TMP_MAX_AGE = 6 * 3600


def _sweep_stale_uploads() -> int:
    """清掉上次被中断留下的上传临时目录，返回清掉几个。

    用 6 小时的年龄门槛，避免误删正在处理的目录。
    """
    import time

    now = time.time()
    removed = 0
    try:
        root = Path(tempfile.gettempdir())
        for d in root.glob(f"{_UPLOAD_TMP_PREFIX}*"):
            try:
                if not d.is_dir():
                    continue
                if now - d.stat().st_mtime <= _UPLOAD_TMP_MAX_AGE:
                    continue
                shutil.rmtree(d, ignore_errors=True)
                if not d.exists():
                    removed += 1
            except OSError:
                pass
    except Exception:  # noqa: BLE001 - 清扫失败绝不能影响导入
        pass
    return removed


@router.post("/import-upload")
async def import_upload(
    kb: str = Form(...),
    use_fallback: bool = Form(True),
    api_key: str = Form(""),
    model: str = Form("mimo-v2.6-flash"),
    files: list[UploadFile] = File(default=[]),
):
    """接收浏览器上传的试卷（可以是整个文件夹），走增强导入。

    为什么需要它：浏览器**拿不到本地文件的绝对路径**（安全限制），
    所以「选文件夹」只能把文件内容传上来。前端用 webkitdirectory 读目录，
    这里按 webkitRelativePath 的相对结构落盘到临时目录，再交给同一套
    ``import_documents`` —— 解析、兜底、入库的逻辑完全复用，不重复实现。
    """
    if not kb:
        return {"ok": False, "msg": "缺少知识库名"}
    if not files:
        return {"ok": False, "msg": "没有收到文件"}

    from custom.importer import import_documents

    swept = _sweep_stale_uploads()
    if swept:
        _emit("info", f"清理了 {swept} 个上次中断留下的临时目录")

    tmp = Path(tempfile.mkdtemp(prefix=_UPLOAD_TMP_PREFIX))
    saved = 0
    skipped: list[str] = []

    try:
        for f in files:
            # webkitdirectory 给的是 "文件夹/子目录/文件名"，也可能带反斜杠
            rel = (f.filename or "").replace("\\", "/").lstrip("/")
            parts = [p for p in rel.split("/") if p not in ("", ".")]
            if not parts or ".." in parts:          # 防目录穿越
                skipped.append(rel or "(空文件名)")
                continue

            dst = tmp.joinpath(*parts)
            dst.parent.mkdir(parents=True, exist_ok=True)
            size = 0
            try:
                with dst.open("wb") as out:
                    while True:
                        chunk = await f.read(1 << 20)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > _MAX_UPLOAD_BYTES:
                            raise ValueError("文件过大")
                        out.write(chunk)
                saved += 1
            except Exception as e:  # noqa: BLE001
                skipped.append(f"{rel}（{e}）")
    finally:
        await asyncio.gather(*(f.close() for f in files),
                             return_exceptions=True)

    if not saved:
        shutil.rmtree(tmp, ignore_errors=True)
        return {"ok": False, "msg": "没有成功接收任何文件"}

    _emit("info", f"已接收 {saved} 个上传文件，开始处理")
    if skipped:
        _emit("info", f"跳过 {len(skipped)} 个：{'、'.join(skipped[:5])}")

    # 上传已完成，解析交给后台。前端收到这个响应就关弹窗，不再阻塞。
    _begin_task("import")

    async def job():
        try:
            r = await asyncio.to_thread(
                import_documents, kb, str(tmp), api_key, model,
                use_fallback, _emit)
            if r.get("ok"):
                _emit("done", f"导入完成：{r.get('imported', 0)} 个文件")
            else:
                _emit("error", r.get("msg", "导入失败"))
        except Exception as e:  # noqa: BLE001
            _emit("error", f"导入异常：{e}")
        finally:
            # 临时目录用完即删 —— 原始文件用户本地还有一份
            shutil.rmtree(tmp, ignore_errors=True)
            _end_task()

    asyncio.create_task(job())
    return {"ok": True, "msg": f"已接收 {saved} 个文件，开始处理",
            "received": saved, "skipped": len(skipped)}


@router.post("/enhance")
async def enhance(req: EnhanceRequest):
    """对指定 Markdown 做视觉增强。"""
    from custom.vision import enhance_markdown

    paths = req.paths
    if not paths:
        return {"ok": False, "msg": "未指定文件"}

    async def job():
        total = len(paths)
        for i, p in enumerate(paths, 1):
            _emit("info", f"--- [{i}/{total}] {Path(p).name} ---")
            r = await asyncio.to_thread(
                enhance_markdown, Path(p), req.api_key, req.model,
                2, _emit)
            if not r.get("ok"):
                _emit("error", r.get("msg", "失败"))
        _emit("done", f"视觉增强完成（{total} 个文件）")

    asyncio.create_task(job())
    return {"ok": True, "msg": "任务已启动"}


# ---------- 题目保存 ----------

@router.post("/save-question")
async def save_question(req: SaveQuestionRequest):
    """保存题目到题库或错题本。

    题库与错题本是同一张表（notebook_entries），靠 is_correct 区分。
    """
    try:
        from deeptutor.services.session import get_sqlite_session_store
    except ImportError as e:
        raise HTTPException(500, f"无法访问题库存储: {e}") from e

    store = get_sqlite_session_store()

    # 选项拼进题面，保持与导入模板一致
    text = req.question.strip()
    if req.options:
        opts = "  ".join(f"{k}. {v}" for k, v in sorted(req.options.items()))
        text = f"{text}\n{opts}"

    item = {
        "origin_type": "external_import",
        "origin_ref": f"assistant:{req.category or 'manual'}",
        "question_id": str(abs(hash(text)) % (10 ** 16)),
        "question": text[:4000],
        "question_type": req.question_type[:100],
        "correct_answer": req.correct_answer[:1000],
        "explanation": req.explanation[:2000],
        "user_answer": req.user_answer[:1000],
        "source": "assistant",
        "material_title": req.category[:200] if req.category else "",
        # 关键：错题本 = is_correct False
        "is_correct": req.target != "mistakes",
    }

    try:
        n = await store.upsert_notebook_entries(None, [item])
    except Exception as e:
        raise HTTPException(500, f"写入失败: {e}") from e

    if not n:
        return {"ok": False, "msg": "存储层拒绝了该条目"}
    where = "错题本" if req.target == "mistakes" else "题库"
    return {"ok": True, "msg": f"已存入{where}"}


@router.get("/wrong-questions")
async def wrong_questions(topic: str = "", limit: int = 30):
    """聚合跨 session 的错题，供出题时参考。

    原生历史按 session 读，这里直接查 is_correct=False 拿到全部错题。
    """
    try:
        from deeptutor.services.session import get_sqlite_session_store
    except ImportError as e:
        raise HTTPException(500, f"无法访问题库: {e}") from e

    store = get_sqlite_session_store()
    try:
        result = await store.list_notebook_entries(
            session_id=None, limit=max(1, min(limit, 200)), offset=0,
        )
    except TypeError:
        # 某些版本不接受 session_id=None
        result = await store.list_notebook_entries(limit=limit, offset=0)
    except Exception as e:
        raise HTTPException(500, f"查询失败: {e}") from e

    entries = result.get("entries", result.get("items", [])) \
        if isinstance(result, dict) else []
    wrong = [e for e in entries if not e.get("is_correct")]

    # 有 topic 时按关键词粗筛
    if topic:
        kw = topic.strip()
        wrong = [e for e in wrong if kw in str(e.get("question", ""))] or wrong

    return {"ok": True, "total": len(wrong), "entries": wrong[:limit]}


# ---------- 备份 ----------

@router.post("/backup")
async def backup(req: BackupRequest):
    """备份 data/ 目录。"""
    async def job():
        _emit("info", "开始备份...")
        try:
            from custom.backup import run_backup
            r = await asyncio.to_thread(run_backup, keep=req.keep,
                                        note=req.note, on_progress=_emit)
            _emit("done", f"备份完成: {r}")
        except Exception as e:
            _emit("error", f"备份失败: {e}")

    asyncio.create_task(job())
    return {"ok": True, "msg": "任务已启动"}


@router.get("/backups")
async def list_backups():
    """列出已有备份。"""
    try:
        from custom.backup import list_backups as _list
        return {"ok": True, "backups": _list()}
    except Exception as e:
        return {"ok": False, "msg": str(e), "backups": []}
