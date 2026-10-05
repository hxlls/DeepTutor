# -*- coding: utf-8 -*-
"""视觉增强：把 Markdown 中的图片转成 LaTeX / 文字描述。

原理：RAG 只对文本做向量索引，图片检索不到。导入前把图片「翻译」成
文字，让公式、几何图的内容也进入文本索引。

依赖 MiMo 的视觉能力（OpenAI 兼容接口），不需要本地 GPU。
"""
from __future__ import annotations

import base64
import json
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

IMG_RE = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')

MIME = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
        '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp'}

PROMPT = """这是一份中国中学试卷中的图片。请判断内容：
- 数学公式/表达式：输出 LaTeX（行内 $...$，独立公式 $$...$$）
- 几何图形/函数图像：一句话说明图形类型、标注字母与已知条件
- 表格：转成 Markdown 表格
- 装饰/水印/logo/空白：输出 EMPTY
只输出结果本身，不要解释，不要引号。"""


def _describe(api_key: str, model: str, img: Path, timeout: int = 120) -> str:
    """单张图片 → 文字。"""
    mime = MIME.get(img.suffix.lower())
    if not mime:
        return ''
    try:
        b64 = base64.b64encode(img.read_bytes()).decode()
        payload = {
            'model': model,
            'messages': [{
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': PROMPT},
                    {'type': 'image_url',
                     'image_url': {'url': f'data:{mime};base64,{b64}'}},
                ],
            }],
            'max_tokens': 400,
        }
        req = urllib.request.Request(
            'https://api.xiaomimimo.com/v1/chat/completions',
            data=json.dumps(payload).encode(),
            headers={'Authorization': f'Bearer {api_key}',
                     'Content-Type': 'application/json'},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        txt = (data['choices'][0]['message'].get('content') or '').strip()
        txt = txt.replace('\n', ' ')
        return '' if txt.upper() == 'EMPTY' else txt
    except Exception:
        return ''


def count_images(md_path: Path) -> tuple[int, int]:
    """返回 (引用总数, 唯一图片数)。"""
    if not md_path.exists():
        return 0, 0
    text = md_path.read_text(encoding='utf-8')
    refs = IMG_RE.findall(text)
    base = md_path.parent
    uniq = set()
    for _, rel in refs:
        p = (base / rel.split('?')[0]).resolve()
        if p.exists():
            uniq.add(p)
    return len(refs), len(uniq)


def enhance_markdown(md_path: Path, api_key: str,
                     model: str = 'mimo-v2.6-flash',
                     workers: int = 4,
                     on_progress=None) -> dict:
    """增强单个 Markdown，输出 <原名>.enriched.md。"""
    def log(m):
        if on_progress:
            on_progress(str(m))

    if not md_path.exists():
        return {'ok': False, 'msg': f'文件不存在: {md_path}'}

    text = md_path.read_text(encoding='utf-8')
    base = md_path.parent
    matches = list(IMG_RE.finditer(text))

    # 去重：同一张图只识别一次
    uniq, seen = [], set()
    for m in matches:
        p = (base / m.group(2).split('?')[0]).resolve()
        if p.exists() and p not in seen:
            seen.add(p)
            uniq.append(p)

    if not uniq:
        log('没有需要识别的图片')
        return {'ok': True, 'msg': '无图片', 'total': 0, 'done': 0}

    log(f'共 {len(matches)} 处引用，去重后 {len(uniq)} 张图片待识别')

    results, done = {}, 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_describe, api_key, model, p): p for p in uniq}
        for fut in futures:
            pass
        for fut, p in futures.items():
            results[str(p)] = fut.result()
            done += 1
            if done % 5 == 0 or done == len(uniq):
                log(f'识别进度 {done}/{len(uniq)}')

    hit = sum(1 for v in results.values() if v)
    log(f'识别完成：{hit}/{len(uniq)} 张有内容')

    def repl(m):
        p = (base / m.group(2).split('?')[0]).resolve()
        desc = results.get(str(p), '')
        return desc if desc else m.group(1)

    out = IMG_RE.sub(repl, text)
    dst = md_path.with_suffix('.enriched.md')
    dst.write_text(out, encoding='utf-8')
    log(f'已生成 {dst.name}')
    return {'ok': True, 'msg': str(dst), 'total': len(uniq),
            'done': hit, 'output': str(dst)}


def estimate_cost(md_path: Path, price_in=1.0, price_out=2.0) -> dict:
    """粗估增强成本（元）。按每张图约 1200 输入 / 150 输出 token 计。"""
    _, uniq = count_images(md_path)
    in_tok = uniq * 1200
    out_tok = uniq * 150
    cost = in_tok / 1_000_000 * price_in + out_tok / 1_000_000 * price_out
    return {'images': uniq, 'cost_yuan': round(cost, 3)}
