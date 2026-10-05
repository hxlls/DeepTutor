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


def scan_documents(directory: str | Path) -> dict:
    """扫描目录，按类型分类文件。"""
    d = Path(directory)
    if not d.is_dir():
        return {'ok': False, 'msg': f'目录不存在: {d}'}

    docs, needs_convert, unknown = [], [], []
    for f in sorted(d.rglob('*')):
        if not f.is_file() or f.name.endswith('.enriched.md'):
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


def import_documents(kb_name: str, directory: str | Path,
                     api_key: str = '', model: str = 'mimo-v2.6-flash',
                     use_fallback: bool = True,
                     on_progress=None) -> dict:
    """完整导入流程。

    参数：
      kb_name       目标知识库
      directory     源目录
      api_key       MiMo Key（用于残留图片兜底）
      use_fallback  是否启用 MiMo 兜底
      on_progress   进度回调 (msg) -> None
    """
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
    ok_count = 0
    for i in range(0, len(prepared), 10):
        chunk = prepared[i:i + 10]
        try:
            import_paths_sync(kb_name, chunk)
            ok_count += len(chunk)
            log(f'  已导入 {ok_count}/{len(prepared)}')
        except Exception as e:
            log(f'  导入失败：{e}')

    log(f'导入完成：成功 {ok_count}，跳过 {skipped}')
    return {'ok': True, 'imported': ok_count, 'skipped': skipped}
