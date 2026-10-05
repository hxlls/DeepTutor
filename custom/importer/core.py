# -*- coding: utf-8 -*-
"""DeepTutor 导入增强 —— 核心逻辑。

直接调用 DeepTutor 内部 API（add_documents），绕过 HTTP 上传：
  - 不需要后端服务在运行
  - 不受浏览器文件选择限制，可整目录导入
  - 少一次磁盘→网络→磁盘的搬运

本模块只 import、不修改 DeepTutor 任何文件，因此官方升级不会冲突。
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

# 与 DeepTutor 解析器对齐的默认扩展名
DEFAULT_EXTS = frozenset({
    '.pdf', '.docx', '.doc', '.pptx', '.ppt', '.xlsx', '.xls',
    '.md', '.markdown', '.txt', '.csv', '.tsv', '.html', '.htm',
    '.epub', '.json',
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp',
})

# 系统/临时文件前缀，跳过
SKIP_PREFIX = ('~$', '.~', '.DS_Store')


def repo_root() -> Path:
    """DeepTutor 仓库根目录（本文件位于 <root>/custom/importer/core.py）。"""
    return Path(__file__).resolve().parents[2]


def resolve_base_dir(explicit: str | None = None) -> Path:
    """知识库根目录：显式参数 > 环境变量 > 仓库内默认位置。"""
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get('DEEPTUTOR_KB_DIR')
    if env:
        return Path(env).expanduser().resolve()
    return (repo_root() / 'data' / 'knowledge_bases').resolve()


def scan_files(target, exts=None, recursive: bool = True) -> list[Path]:
    """扫描文件或目录，返回待导入的文件列表（已排序、已去重）。"""
    p = Path(target).expanduser()
    if p.is_file():
        return [p.resolve()]
    if not p.is_dir():
        raise FileNotFoundError(f'路径不存在: {p}')

    allow = {e.lower() for e in (exts or DEFAULT_EXTS)}
    it = p.rglob('*') if recursive else p.glob('*')
    out = []
    for f in it:
        if not f.is_file():
            continue
        if any(f.name.startswith(x) for x in SKIP_PREFIX):
            continue
        if f.suffix.lower() in allow:
            out.append(f.resolve())
    return sorted(set(out))


def list_kbs(base_dir=None) -> list[str]:
    """列出全部知识库名称。"""
    from deeptutor.knowledge.manager import KnowledgeBaseManager
    mgr = KnowledgeBaseManager(base_dir=str(resolve_base_dir(base_dir)))
    return sorted(mgr.list_knowledge_bases())


def kb_raw_dir(kb_name: str, base_dir=None) -> Path:
    """知识库的 raw 目录。"""
    from deeptutor.knowledge.manager import KnowledgeBaseManager
    mgr = KnowledgeBaseManager(base_dir=str(resolve_base_dir(base_dir)))
    return Path(mgr.get_raw_path(kb_name))


async def import_paths(kb_name: str, paths, base_dir=None,
                       allow_duplicates: bool = False) -> int:
    """把一批文件导入指定知识库，返回处理的文件数。"""
    from deeptutor.knowledge.add_documents import add_documents

    files = [str(Path(p).resolve()) for p in paths]
    if not files:
        return 0

    # add_documents 的 base_dir 默认是相对路径 "./data/knowledge_bases"，
    # 必须显式传绝对路径，否则会跟着当前工作目录跑偏。
    return await add_documents(
        kb_name=kb_name,
        source_files=files,
        base_dir=str(resolve_base_dir(base_dir)),
        allow_duplicates=allow_duplicates,
    )


def import_paths_sync(kb_name, paths, base_dir=None, allow_duplicates=False) -> int:
    """同步封装，供 CLI 与 watcher 使用。"""
    return asyncio.run(import_paths(kb_name, paths, base_dir, allow_duplicates))
