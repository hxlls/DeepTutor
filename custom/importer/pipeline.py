# -*- coding: utf-8 -*-
"""统一导入流程：格式分流 → 解析 → 残留图片兜底 → 入库。

设计要点：
  · PDF / DOCX / PPTX / XLSX → MinerU 解析（公式转 LaTeX）
  · Markdown                 → 跳过解析
  · .doc 等旧格式             → 提示转换
  · 任何来源的残留图片引用     → MiMo 视觉兜底（复用 enhance_markdown）

兜底不是"异常处理"而是常规步骤 —— MinerU 解析后通常仍会残留
几何图、扫描页等图片，需要视觉模型补齐。
"""
from __future__ import annotations

from pathlib import Path

from . import mineru_runner
from .core import import_paths_sync, scan_files

# 可导入的文档类型
DOC_EXTS = ('.pdf', '.docx', '.pptx', '.xlsx', '.md')
CONVERT_HINT = {'.doc': '.docx', '.ppt': '.pptx', '.xls': '.xlsx'}

# 扫描时跳过的目录名。
# `_mineru_out` / `mineru_out` 是**我们自己**生成的解析产物目录，
# `images` 是导出的图片 —— 不跳过的话，导入一次之后再扫描，
# 会把上次的产物当成新的源文件重复导入。
SKIP_DIRS = frozenset({'_mineru_out', 'mineru_out', 'images', '__pycache__'})


def _is_derived(path: Path, root: Path) -> bool:
    """path 是否落在我们自己的产物目录里。"""
    try:
        parts = path.relative_to(root).parts[:-1]
    except ValueError:
        parts = path.parts[:-1]
    return any(p in SKIP_DIRS for p in parts)


def scan_documents(directory: str | Path) -> dict:
    """扫描目录，按类型分类文件。"""
    d = Path(directory)
    if not d.is_dir():
        return {'ok': False, 'msg': f'目录不存在: {d}'}

    docs, needs_convert, unknown = [], [], []
    for f in sorted(d.rglob('*')):
        if not f.is_file() or f.name.endswith('.enriched.md'):
            continue
        if f.name.startswith('$'):  # 编辑器锁/缓存/损坏文件，不导入
            continue
        if _is_derived(f, d):
            continue
        ext = f.suffix.lower()
        if ext in DOC_EXTS:
            docs.append(f)
        elif ext in CONVERT_HINT:
            needs_convert.append({'path': str(f), 'name': f.name,
                                  'hint': f'请另存为 {CONVERT_HINT[ext]}'})
        elif ext in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'):
            unknown.append({'path': str(f), 'name': f.name,
                            'hint': '图片文件，可单独走视觉识别'})

    return {
        'ok': True,
        'docs': [{'path': str(p), 'name': p.name,
                  'ext': p.suffix.lower(),
                  'engine': 'skip' if p.suffix.lower() == '.md' else 'mineru'}
                 for p in docs],
        'needs_convert': needs_convert,
        'images': unknown,
        'mineru_available': mineru_runner.is_available(),
    }


def _settings_dir() -> Path:
    # DeepTutor 运行时设置目录（容器内 /app/data/user/settings）
    return Path(__file__).resolve().parents[2] / 'data' / 'user' / 'settings'


def _default_mimo_key() -> str:
    # api_key 未显式传入时，回退读取用户已配置的 MiMo key
    # 兼容两种配置结构：旧版顶层 active_profile_id/models；新版 services.llm.profiles
    try:
        import json
        cfg = _settings_dir() / 'model_catalog.json'
        if not cfg.is_file():
            return ''
        data = json.loads(cfg.read_text(encoding='utf-8'))
        models = data.get('models', [])
        active = data.get('active_profile_id', '') or 'xiaomi_mimo-llm'
        if not models:
            llm = (data.get('services') or {}).get('llm') or {}
            active = llm.get('active_profile_id', '') or active
            models = llm.get('profiles', [])
        for m in models:
            if m.get('id') == active or m.get('provider') == 'xiaomi_mimo':
                key = (m.get('api_key') or '').strip()
                if key:
                    return key
        # 最后尝试 connections 里的 xiaomi_mimo
        for c in data.get('connections', []):
            if c.get('provider') == 'xiaomi_mimo':
                key = (c.get('api_key') or '').strip()
                if key:
                    return key
    except Exception:
        pass
    return ''
def import_documents(kb_name: str, directory: str | Path,
                     api_key: str = '', model: str = 'mimo-v2.6-flash',
                     use_fallback: bool = True,
                     on_progress=None) -> dict:
    # 完整导入流程
    if not api_key:
        api_key = _default_mimo_key()
    def log(msg):
        if on_progress:
            on_progress(str(msg))

    scan = scan_documents(directory)
    if not scan.get('ok'):
        return scan

    docs = scan['docs']
    if not docs:
        log('没有找到可导入的文档')
        return {'ok': False, 'msg': '没有可导入的文档'}

    log(f'发现 {len(docs)} 个文档待处理')

    if scan['needs_convert']:
        log(f'⚠ {len(scan["needs_convert"])} 个旧格式文件需要转换：')
        for item in scan['needs_convert'][:5]:
            log(f'    {item["name"]} → {item["hint"]}')

    # 是否需要 MinerU
    need_mineru = any(d['engine'] == 'mineru' for d in docs)
    if need_mineru and not scan['mineru_available']:
        log('需要 MinerU 但未安装，尝试自动安装...')
        if not mineru_runner.prepare_mineru(on_progress):
            return {'ok': False, 'msg': 'MinerU 准备失败，请查看日志'}

    # 兜底清扫：上一次导入若被强杀（容器重启、进程崩溃），
    # job 的 finally 走不到，文档库里会留下孤儿记录和临时文件。
    if need_mineru:
        mineru_runner.cleanup_doclib(on_progress)

    prepared: list[Path] = []
    skipped = 0
    total = len(docs)

    for i, doc in enumerate(docs, 1):
        path = Path(doc['path'])
        log(f'--- [{i}/{total}] {path.name} ---')

        md: Path | None = path

        # ① 非 Markdown → MinerU 解析
        if doc['engine'] == 'mineru':
            md = mineru_runner.parse(path, on_progress=on_progress)
            if md is None:
                skipped += 1
                continue
            # MinerU 4.x 的图片是文档库定位符，先导出成本地文件，
            # 后续视觉兜底与入库才能拿到真实图片
            md = mineru_runner.materialize_images(md, on_progress=on_progress)
            # 图片已落地、markdown 已生成，文档库里这份记录不再需要。
            # 不回收的话 MINERU_HOME/doclib 会随每份试卷单调增长 ——
            # MinerU 自己没有任何自动淘汰机制。
            mineru_runner.forget_source(path, on_progress)

        # ② 检查残留图片引用
        from custom.vision import count_images
        _, uniq = count_images(md)

        if uniq > 0:
            if use_fallback and api_key:
                log(f'  {uniq} 张残留图片，走云端视觉兜底')
                from custom.vision import enhance_markdown
                r = enhance_markdown(md, api_key, model, workers=2,
                                     on_progress=on_progress)
                if r.get('ok') and r.get('output'):
                    md = Path(r['output'])
                    log(f'  兜底完成：{r.get("done")}/{uniq} 张')
                else:
                    log(f'  兜底失败，将导入未增强版本')
            else:
                log(f'  有 {uniq} 张图片未处理（兜底已关闭）')

        prepared.append(md)

    if not prepared:
        return {'ok': False, 'msg': '没有成功处理的文档'}

    # ③ 批量入库
    log(f'开始导入 {len(prepared)} 个文件到「{kb_name}」...')
    from .core import kb_raw_dir
    try:
        raw_dir = kb_raw_dir(kb_name)
        raw_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:  # noqa: BLE001
        log(f'  定位知识库 raw 目录失败（图片不会随文档入库）：{e}')
        raw_dir = None

    ok_count = 0
    for i in range(0, len(prepared), 10):
        chunk = prepared[i:i + 10]
        try:
            import_paths_sync(kb_name, chunk)
            # 图片随 md 一起进 raw/：MinerU 导出的 images/ 在解析临时目录，
            # 只搬 md 的话 raw/ 里的相对引用 images/block_N.png 会悬空，
            # 前端渲染就变成 "Image block"。
            if raw_dir is not None:
                for md in chunk:
                    src_img = md.parent / 'images'
                    if not src_img.is_dir():
                        continue
                    dst_img = raw_dir / 'images'
                    dst_img.mkdir(parents=True, exist_ok=True)
                    for p in src_img.iterdir():
                        if p.is_file():
                            (dst_img / p.name).write_bytes(p.read_bytes())
            ok_count += len(chunk)
            log(f'  已导入 {ok_count}/{len(prepared)}')
        except Exception as e:
            log(f'  导入失败：{e}')

    # ④ 回收文档库
    # forget 只做标记，真正的磁盘回收要在这里统一做（见 mineru_runner 的说明）。
    # 放在批量结束而不是每份之后，是为了少起几次 CLI 进程。
    if need_mineru:
        mineru_runner.cleanup_doclib(on_progress)

    log(f'导入完成：成功 {ok_count}，跳过 {skipped}')
    return {'ok': True, 'imported': ok_count, 'skipped': skipped}
