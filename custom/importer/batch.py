# -*- coding: utf-8 -*-
"""批量导入本地目录到 DeepTutor 知识库。

用法:
  python3 custom/importer/batch.py <知识库名> <目录或文件> [选项]

选项:
  --exts .pdf,.docx   只导入这些扩展名（逗号分隔）
  --no-recursive      不递归子目录
  --batch N           每批导入 N 个文件，默认 20
  --kb-dir PATH       知识库根目录（默认 <仓库>/data/knowledge_bases）
  --dry-run           只列出待导入文件，不实际导入
  --duplicates        允许重复导入（默认跳过已入库的）
  --list              列出所有知识库

示例:
  python3 custom/importer/batch.py --list
  python3 custom/importer/batch.py 试卷库 ~/试卷 --exts .pdf,.docx
  python3 custom/importer/batch.py 试卷库 ~/试卷 --dry-run
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from custom.importer.core import (  # noqa: E402
    import_paths_sync, list_kbs, resolve_base_dir, scan_files,
)


def main():
    ap = argparse.ArgumentParser(
        description='批量导入本地目录到 DeepTutor 知识库',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument('kb', nargs='?', help='知识库名称')
    ap.add_argument('target', nargs='?', help='目录或文件路径')
    ap.add_argument('--exts', help='只导入这些扩展名，逗号分隔')
    ap.add_argument('--no-recursive', action='store_true', help='不递归子目录')
    ap.add_argument('--batch', type=int, default=20, help='每批文件数，默认 20')
    ap.add_argument('--kb-dir', help='知识库根目录')
    ap.add_argument('--dry-run', action='store_true', help='只列出不导入')
    ap.add_argument('--duplicates', action='store_true', help='允许重复导入')
    ap.add_argument('--list', action='store_true', help='列出所有知识库')
    args = ap.parse_args()

    if args.list:
        kbs = list_kbs(args.kb_dir)
        if kbs:
            print(f'共 {len(kbs)} 个知识库:')
            for k in kbs:
                print('  -', k)
        else:
            print('（还没有知识库）')
        return

    if not args.kb or not args.target:
        ap.print_help()
        return

    exts = None
    if args.exts:
        exts = set()
        for e in args.exts.split(','):
            e = e.strip()
            if e:
                exts.add(e if e.startswith('.') else '.' + e)

    try:
        files = scan_files(args.target, exts=exts,
                           recursive=not args.no_recursive)
    except FileNotFoundError as e:
        print(f'错误: {e}')
        sys.exit(1)

    if not files:
        print('没有找到匹配的文件')
        return

    total = len(files)
    print(f'知识库  : {args.kb}')
    print(f'数据目录: {resolve_base_dir(args.kb_dir)}')
    print(f'待导入  : {total} 个文件')
    print()

    if args.dry_run:
        for i, f in enumerate(files, 1):
            print(f'  {i:>4}. {f}')
        print(f'\n[dry-run] 共 {total} 个文件，未实际导入')
        return

    ok = fail = 0
    t0 = time.time()
    for start in range(0, total, args.batch):
        chunk = files[start:start + args.batch]
        idx = f'{start + 1}-{min(start + len(chunk), total)}'
        print(f'[{idx}/{total}] 处理中...', flush=True)
        try:
            import_paths_sync(args.kb, chunk, base_dir=args.kb_dir,
                              allow_duplicates=args.duplicates)
            ok += len(chunk)
            print(f'          ✓ {len(chunk)} 个', flush=True)
        except Exception as e:
            fail += len(chunk)
            print(f'          ✗ {str(e)[:140]}', flush=True)

    dt = time.time() - t0
    print(f'\n完成: 成功 {ok}, 失败 {fail}, 用时 {dt:.1f}s')


if __name__ == '__main__':
    main()
