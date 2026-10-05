# -*- coding: utf-8 -*-
"""导入增强：批量导入、目录监控、视觉预处理。

主要能力：
  · pipeline.import_documents  —— 统一流程（格式分流 + MinerU + MiMo 兜底）
  · core.import_paths_sync     —— 底层批量入库
  · watcher                    —— 目录监控自动导入
  · mineru_runner              —— MinerU 解析封装
"""
from .core import (  # noqa: F401
    import_paths_sync, kb_raw_dir, list_kbs, scan_files,
)
from .pipeline import (  # noqa: F401
    import_documents, scan_documents,
)
