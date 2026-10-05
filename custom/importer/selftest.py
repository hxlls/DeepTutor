# -*- coding: utf-8 -*-
"""环境自检：确认导入增强所需的接口都可用。

用法:
  python3 custom/importer/selftest.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OK = '  ✓'
NO = '  ✗'


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    print('=' * 56)
    print('DeepTutor 导入增强 · 环境自检')
    print('=' * 56)
    print(f'仓库根目录: {ROOT}')
    print()

    failures = []

    # 1. 核心导入接口
    print('[1] 核心导入接口')
    try:
        from deeptutor.knowledge.add_documents import (  # noqa: F401
            DEFAULT_BASE_DIR, DocumentAdder, add_documents,
        )
        import inspect
        sig = inspect.signature(add_documents)
        print(f'{OK} add_documents 可用')
        print(f'     参数: {list(sig.parameters)}')
        print(f'{OK} DocumentAdder 可用')
        print(f'{OK} DEFAULT_BASE_DIR = {DEFAULT_BASE_DIR}')
    except Exception as e:
        print(f'{NO} 导入失败: {e}')
        failures.append('add_documents')

    # 2. 知识库管理
    print()
    print('[2] 知识库管理')
    try:
        from deeptutor.knowledge.manager import KnowledgeBaseManager
        print(f'{OK} KnowledgeBaseManager 可用')
    except Exception as e:
        print(f'{NO} 导入失败: {e}')
        failures.append('KnowledgeBaseManager')

    # 3. 数据目录
    print()
    print('[3] 数据目录')
    try:
        from custom.importer.core import resolve_base_dir, list_kbs
        bd = resolve_base_dir()
        print(f'     知识库根目录: {bd}')
        if bd.exists():
            print(f'{OK} 目录存在')
            kbs = list_kbs()
            if kbs:
                print(f'{OK} 现有 {len(kbs)} 个知识库:')
                for k in kbs:
                    print(f'       - {k}')
            else:
                print('  · 还没有知识库（首次导入时会自动创建目录结构）')
        else:
            print(f'  · 目录尚未创建，首次导入时自动生成')
    except Exception as e:
        print(f'{NO} 失败: {e}')
        failures.append('数据目录')

    # 4. 扫描功能
    print()
    print('[4] 文件扫描')
    try:
        from custom.importer.core import DEFAULT_EXTS, scan_files
        print(f'{OK} 支持 {len(DEFAULT_EXTS)} 种扩展名')
        print(f'     示例: {", ".join(sorted(DEFAULT_EXTS)[:8])} ...')
        sample = scan_files(ROOT, recursive=False)
        print(f'{OK} 仓库根目录扫到 {len(sample)} 个可导入文件')
    except Exception as e:
        print(f'{NO} 失败: {e}')
        failures.append('扫描')

    # 5. 桌面启动器依赖
    print()
    print('[5] 桌面启动器（可选）')
    try:
        import webview  # noqa: F401
        print(f'{OK} pywebview 可用')
    except ImportError:
        print('  · pywebview 未安装（需要桌面窗口时: pip install pywebview）')

    # 汇总
    print()
    print('=' * 56)
    if failures:
        print(f'✗ 有 {len(failures)} 项未通过: {", ".join(failures)}')
        sys.exit(1)
    print('✓ 全部通过，可以开始导入')


if __name__ == '__main__':
    main()
