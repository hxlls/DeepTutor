# -*- coding: utf-8 -*-
"""补丁：把图注内联回正文，让官方上传路径也能检索到试卷里的公式。

问题
----
官方 `llamaindex/document_loader.py` 里，解析出的图片走 `_load_image_nodes()`，
而它的第一道门是：

    if not embedding_client.supports_multimodal_contents():
        self._log_skipped_images(...)
        return []                      # ← 图片全丢

实测三档部署用的 embedding **全是文本向量**：

  · Ollama（bge-m3）      → adapters/ollama.py:143  "multimodal": False
  · OpenAI 兼容（智谱 / OpenAI）
                          → adapters/openai_compatible.py:396
                            looks_like_multimodal_embedding_model(model)
                            `embedding-3` / `text-embedding-3-*` 均判 False

=> 试卷里的公式图、几何图被**静默丢弃**，正文只剩
   `![Image block](images/block_1.png)` 这种死链接，搜"tan∠GBO"永远搜不到。

修法
----
不改上传流程、不碰任务队列与 SSE 进度。只在 `load()` 里插一步：

    text = await self._inline_image_captions(text, extracted_images)

把 markdown 里的 `![](images/x.png)` **就地替换**成图注文本
（数学公式→LaTeX，几何图→图形类型+标注字母，表格→Markdown 表，装饰图→删掉）。
公式由此进入**正文**，走文本向量也能命中。

为什么内联，而不是像官方那样另开 Document
-----------------------------------------
官方 `_append_visual_documents()` 那个形状是给**独立视觉资产**（带 bbox/页码）
用的；而 MinerU 吐出来的是**正文流里的插图**，位置本身就是信息。内联能保住
"这张图属于哪道题"，chunk 划分时公式和题干在一起、互相增强。

为什么不直接用官方那句 prompt
-----------------------------
官方 `IMAGE_DESCRIPTION_PROMPT` 没要求输出 LaTeX，VLM 会把 `$x^2-5x+6=0$`
写成 "a quadratic equation"，检索照样命不中 —— 兜底等于白做。
所以这里用**试卷专用 prompt**，但**复用官方客户端与缓存**：

  · `get_image_description_client()` → 尊重用户配的视觉模型
                                       （MiMo / 智谱 / 通义 / OpenAI 都行）
  · `complete_image_caption()`       → 自带磁盘缓存，重复导入不重复花钱

只在 embedding 不支持多模态时启用（支持时官方 ImageNode 已覆盖，避免重复）。

开关（默认开，因为本构建就是试卷助手场景）
-----------------------------------------
  1. 环境变量 `DEEPTUTOR_INLINE_IMAGE_CAPTION=0` 关闭（优先级最高）
  2. `data/user/settings/assistant.json` 的 `inline_image_caption`
  3. 都没有 → 默认开
"""
from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _patchlib as lib
else:
    from . import _patchlib as lib

TARGET = 'deeptutor/services/rag/pipelines/llamaindex/document_loader.py'

# ---------- 锚点 1：顶部常量 ----------
ANCHOR_CONST = '''_PDF_OCR_FALLBACK_ENGINE = DOCUMENT_PARSING_ENGINE_LITEPARSE
'''

NEW_CONST = '''_PDF_OCR_FALLBACK_ENGINE = DOCUMENT_PARSING_ENGINE_LITEPARSE

# [试卷助手补丁] 图片描述内联 —— 见 custom/patches/patch_image_caption_text.py
# 为什么不用官方的 IMAGE_DESCRIPTION_PROMPT：那句没要求输出 LaTeX，
# VLM 会把 $x^2-5x+6=0$ 写成 "a quadratic equation"，检索照样命不中。
_INLINE_CAPTION_PROMPT = (
    "这是一份中国中学试卷中的图片。请判断内容：\\n"
    "- 数学公式/表达式：输出 LaTeX（行内 $...$，独立公式 $$...$$）\\n"
    "- 几何图形/函数图像：一句话说明图形类型、标注字母与已知条件\\n"
    "- 表格：转成 Markdown 表格\\n"
    "- 装饰/水印/logo/空白：输出 EMPTY\\n"
    "只输出结果本身，不要解释，不要引号。"
)

_INLINE_CAPTION_SYSTEM_PROMPT = (
    "You transcribe figures from Chinese middle-school exam papers. "
    "Output only the requested content, with no commentary."
)


def _inline_caption_enabled() -> bool:
    """[试卷助手补丁] 是否把图注内联进正文。

    优先级：环境变量 > data/user/settings/assistant.json > 默认开。
    读不到任何配置时返回 True —— 本构建就是试卷助手场景。
    """
    import json
    import os

    env = os.environ.get("DEEPTUTOR_INLINE_IMAGE_CAPTION")
    if env is not None and env.strip():
        return env.strip().lower() not in ("0", "false", "no", "off")
    try:
        # .../llamaindex/document_loader.py -> parents[5] 是仓库根（容器里是 /app）
        root = Path(__file__).resolve().parents[5]
        path = root / "data" / "user" / "settings" / "assistant.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        return bool(data.get("inline_image_caption", True))
    except Exception:  # noqa: BLE001 - 配置读不到不能拖垮导入
        return True
'''

# ---------- 锚点 2：load() 里的调用 ----------
ANCHOR_CALL = '''            if not scanned_pdf_needs_ocr:
                self._append_if_nonempty(
                    documents,
                    file_path,
                    text,
                    parse_engine=parse_engine,
                    extracted_image_count=len(extracted_images),
                )
'''

NEW_CALL = '''            if not scanned_pdf_needs_ocr:
                # [试卷助手补丁] 见 custom/patches/patch_image_caption_text.py
                # 官方在 embedding 不支持多模态时会整批丢弃图片，导致试卷里的
                # 公式图、几何图彻底搜不到。这里先把图注内联回正文。
                text = await self._inline_image_captions(text, extracted_images)
                self._append_if_nonempty(
                    documents,
                    file_path,
                    text,
                    parse_engine=parse_engine,
                    extracted_image_count=len(extracted_images),
                )
'''

# ---------- 锚点 3：方法插入点 ----------
ANCHOR_METHOD = '''    def _parse_document(
        self,
        file_path: Path,
        parse_service=None,  # noqa: ANN001
'''

NEW_METHOD = '''    async def _inline_image_captions(self, text: str, images: list[_ImageSource]) -> str:
        """[试卷助手补丁] 把正文里的图片引用就地换成图注文本。

        官方 `_load_image_nodes()` 在 embedding 不支持多模态时直接 `return []`，
        图片连同描述一起丢掉。这里改成：解析出的图片先用视觉模型转成
        LaTeX / 文字，再**就地替换掉 markdown 里的 `![](...)` 引用**，
        让文本向量也能命中公式内容。

        只在 embedding 不支持多模态时启用 —— 支持时官方 ImageNode 已经覆盖，
        再做一遍是重复花钱。开关见 `_inline_caption_enabled()`。

        不是本次解析产物的图片（外链、用户自留的图）原样保留，不动。
        """
        import re

        if not text or not images:
            return text
        if not _inline_caption_enabled():
            return text
        try:
            if get_embedding_client().supports_multimodal_contents():
                return text
        except Exception:  # noqa: BLE001 - 探测失败时按"不支持"处理，兜底更安全
            pass

        try:
            llm_client = get_image_description_client()
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(
                "Inline image caption skipped: LLM client is unavailable (%s)", exc
            )
            return text
        if not llm_client.supports_multimodal_images():
            self.logger.warning(
                "Inline image caption skipped: the configured LLM does not accept "
                "image input; figures in this document stay unsearchable."
            )
            return text

        pattern = re.compile(r'!\\[([^\\]]*)\\]\\(([^)\\s]+)(?:\\s+"[^"]*")?\\)')
        by_name = {image.path.name: image.path for image in images}
        names: list[str] = []
        for _alt, url in pattern.findall(text):
            name = url.rsplit("/", 1)[-1].split("?")[0]
            if name in by_name and name not in names:
                names.append(name)
        if not names:
            return text

        concurrency, timeout_seconds = image_description_limits()
        semaphore = asyncio.Semaphore(concurrency)
        cache: dict[str, str] = {}

        async def _caption(name: str) -> None:
            path = by_name[name]
            try:
                payload = await asyncio.to_thread(self._load_image_payload, path)
                async with semaphore:
                    caption = await asyncio.wait_for(
                        complete_image_caption(
                            llm_client,
                            _INLINE_CAPTION_PROMPT,
                            system_prompt=_INLINE_CAPTION_SYSTEM_PROMPT,
                            image_data=payload["base64"],
                            image_mime_type=payload["mimetype"],
                            image_filename=name,
                        ),
                        timeout=timeout_seconds,
                    )
            except asyncio.TimeoutError:
                self.logger.warning(
                    "Inline image caption timed out after %ss: %s", timeout_seconds, name
                )
                cache[name] = ""
                return
            except Exception as exc:  # noqa: BLE001 - 单张失败不能拖垮整份文档
                self.logger.warning("Inline image caption failed for %s: %s", name, exc)
                cache[name] = ""
                return
            caption = str(caption or "").strip()
            # 装饰图 / 水印 / logo —— 直接删掉引用，不留死链接
            cache[name] = "" if caption.upper() == "EMPTY" else caption

        await asyncio.gather(*(_caption(name) for name in names))

        def _replace(match: "re.Match[str]") -> str:
            url = match.group(2)
            name = url.rsplit("/", 1)[-1].split("?")[0]
            if name not in by_name:
                # 不是本次解析的产物，原样保留
                return match.group(0)
            caption = cache.get(name, "")
            return f"\\n\\n{caption}\\n\\n" if caption else ""

        new_text = pattern.sub(_replace, text)
        filled = sum(1 for name in names if cache.get(name))
        self.logger.info(
            "Inlined %d/%d figure caption(s) into the document text",
            filled,
            len(names),
        )
        return new_text

    def _parse_document(
        self,
        file_path: Path,
        parse_service=None,  # noqa: ANN001
'''


ANCHORS = (
    ('顶部：试卷专用 prompt + 开关函数', ANCHOR_CONST, NEW_CONST),
    ('load()：插入内联调用', ANCHOR_CALL, NEW_CALL),
    ('类内：插入 _inline_image_captions()', ANCHOR_METHOD, NEW_METHOD),
)


def run() -> bool:
    print('--- 补丁 2/2 · 图注内联回正文 ---')
    path = lib.rel(TARGET)
    ok = lib.apply(path, ANCHORS)
    if ok:
        lib.show(path, ('_inline_image_captions', '_INLINE_CAPTION_PROMPT ='))
    return ok


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(0 if run() else 1)
