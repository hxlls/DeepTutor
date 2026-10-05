# -*- coding: utf-8 -*-
"""设置 `image_description_model`（图片描述用的视觉模型）。

⚠️ 作用范围（2026-10 实测源码后更正）
------------------------------------
`document_parsing.json` 里有两个字段，**用途完全不同**，不要混淆：

  · `image_description_model` —— **知识库上传也用它**。官方
    `document_loader._load_image_nodes()` 与 `get_image_description_client()`
    都读这个字段来选视觉模型。这是本脚本真正有价值的部分。

  · `image_caption`（布尔）—— **只被 `deeptutor/reading/captions.py` 读取**，
    服务的是「沉浸式阅读 / 书架」功能（`/learning/books`），
    **对知识库上传没有任何作用**。

早先本文件的注释写成「开启 image_caption 就能让上传试卷的公式图被写进索引」，
这是**错的**：知识库上传路径里，图片描述只会进 `ImageNode`，
而 `ImageNode` 需要**多模态 embedding**（见下），文本向量下会被整批丢弃。

真正的瓶颈
----------
`document_loader._load_image_nodes()` 的第一道门是：

    if not embedding_client.supports_multimodal_contents():
        self._log_skipped_images(...); return []

而三档部署用的 embedding 全是文本向量，实测能力表：

  · Ollama（bge-m3）      → `adapters/ollama.py:143`  "multimodal": False
  · OpenAI 兼容（智谱 / OpenAI）
                          → `adapters/openai_compatible.py:396`
                            `looks_like_multimodal_embedding_model(model)`
                            `embedding-3` / `text-embedding-3-*` 均判 False

=> 官方路径下试卷里的公式图、几何图**全被静默丢弃**，正文只剩
   `![Image block](images/block_1.png)` 这样的死链接。

因此本脚本**只负责把 `image_description_model` 配好**（让官方在
支持多模态 embedding 时有视觉模型可用）；要让文本向量也能命中公式，
还需要 `patch_image_caption_text.py` 补上「图注内联回正文」那一环。

用法:
  python3 custom/config/setup_image_caption.py             # 设置视觉模型
  python3 custom/config/setup_image_caption.py --check     # 查看当前状态
  python3 custom/config/setup_image_caption.py --off       # 清空视觉模型
  python3 custom/config/setup_image_caption.py --engine pymupdf4llm
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = ROOT / 'data' / 'user' / 'settings'
PARSING = SETTINGS / 'document_parsing.json'
CATALOG = SETTINGS / 'model_catalog.json'

# 支持图片提取的解析引擎（text_only 没有图片，无法描述）
IMAGE_CAPABLE_ENGINES = ('markitdown', 'pymupdf4llm', 'mineru', 'docling')
DEFAULT_ENGINE = 'markitdown'


def find_vision_model():
    """从 model_catalog 里找出可用于图片描述的模型（任意支持视觉的服务商）。"""
    if not CATALOG.exists():
        return None
    cat = json.loads(CATALOG.read_text(encoding='utf-8'))
    for svc_name in ('llm', 'task'):
        svc = cat.get('services', {}).get(svc_name, {})
        for prof in svc.get('profiles', []):
            for m in prof.get('models', []):
                caps = m.get('capabilities') or {}
                if caps.get('vision'):
                    return {'profile_id': prof['id'], 'model_id': m['id']}
    return None


def embedding_is_multimodal():
    """查 embedding 是否支持多模态 —— 这决定官方路径会不会保留图片。

    读不到配置时返回 None（未知），不要让检查本身失败。
    """
    cfg = SETTINGS / 'embedding.json'
    if not cfg.exists():
        return None
    try:
        data = json.loads(cfg.read_text(encoding='utf-8'))
        model = str(data.get('active_model_id') or '')
    except Exception:
        return None
    # 与官方 adapters/base.py:looks_like_multimodal_embedding_model 同一判据
    lowered = model.lower()
    return any(token in lowered for token in
               ('multimodal', 'vl-embed', 'qwen3-vl', 'jina-clip', 'clip'))


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser(description='设置图片描述用的视觉模型')
    ap.add_argument('--check', action='store_true', help='只查看状态')
    ap.add_argument('--off', action='store_true', help='清空视觉模型')
    ap.add_argument('--engine', default=DEFAULT_ENGINE,
                    choices=IMAGE_CAPABLE_ENGINES, help='解析引擎')
    args = ap.parse_args()

    if not PARSING.exists():
        print(f'配置文件不存在: {PARSING}')
        print('请先执行: python3 custom/setup_guide.py')
        sys.exit(1)

    cfg = json.loads(PARSING.read_text(encoding='utf-8'))

    if args.check:
        print('=== 当前文档解析配置 ===')
        print(f"engine                  : {cfg.get('engine')}")
        print(f"image_description_model : {cfg.get('image_description_model')}")
        print(f"image_caption（只影响阅读模块）: {cfg.get('image_caption')}")
        vm = find_vision_model()
        print(f"可用视觉模型            : {vm or '未找到（需先配置一个带 vision 的模型）'}")
        mm = embedding_is_multimodal()
        print()
        print('=== 决定图片能否被检索的关键 ===')
        if mm is None:
            print('embedding 能力            : 读不到 embedding.json，无法判断')
        elif mm:
            print('embedding 支持多模态      : 是 → 官方路径会保留图片（ImageNode）')
        else:
            print('embedding 支持多模态      : 否 → 官方路径会**静默丢弃所有图片**')
            print('                            需要 patch_image_caption_text.py 兜底')
        return

    if args.off:
        cfg['image_caption'] = False
        cfg['image_description_model'] = None
        PARSING.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                           encoding='utf-8')
        print('✓ 已清空 image_description_model')
        return

    vm = find_vision_model()
    if not vm:
        print('✗ 未找到可用的视觉模型')
        print('  请先配置一个支持图片输入的模型（custom/config/setup_mimo_llm.py 等）')
        sys.exit(1)

    # 1. 切换解析引擎（text_only 没有图片可描述）
    old_engine = cfg.get('engine')
    cfg['engine'] = args.engine

    # 2. 指向视觉模型（知识库上传路径读的就是这个字段）
    cfg['image_description_model'] = vm
    # image_caption 只服务「沉浸式阅读」模块，顺手一起打开无副作用
    cfg['image_caption'] = True

    # 3. 引擎级开关（markitdown 需要单独打开）
    engines = cfg.setdefault('engines', {})
    engines.setdefault('markitdown', {})['enable_llm_image_description'] = True

    PARSING.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                       encoding='utf-8')

    print('✓ 已设置图片描述模型')
    print(f'  解析引擎 : {old_engine} -> {args.engine}')
    print(f'  视觉模型 : {vm["profile_id"]} / {vm["model_id"]}')
    print()
    print('注意：仅配置视觉模型**不足以**让试卷里的公式可检索。')
    print('      官方路径把图片描述放进 ImageNode，而 ImageNode 需要')
    print('      **多模态 embedding**；文本向量（bge-m3 / 智谱 / OpenAI）下')
    print('      图片会被整批丢弃。要补上这一环请执行：')
    print('          python3 patch_image_caption_text.py')
    print('      已建好的知识库需要重新导入才会应用新配置。')


if __name__ == '__main__':
    main()
