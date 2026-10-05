# -*- coding: utf-8 -*-
"""监控文件夹，新文件自动导入知识库。

用法:
  python3 custom/importer/watcher.py <知识库名> <监控目录> [选项]

选项:
  --exts .pdf,.docx   只关注这些扩展名
  --interval N        轮询间隔秒数（默认 5）
  --settle N          文件稳定等待秒数（默认 10，避免写入未完成就导入）
  --kb-dir PATH       知识库根目录

说明:
  用轮询而非 watchdog，避免额外依赖，也方便在容器/远程环境长期运行。
  文件必须连续 settle 秒未被修改，才认为写入完成并触发导入。

示例:
  python3 custom/importer/watcher.py 试卷库 ~/试卷 --exts .pdf,.docx
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from custom.importer.core import (  # noqa: E402
    import_paths_sync, resolve_base_dir, scan_files,
)


def main():
    ap = argparse.ArgumentParser(description='监控文件夹自动导入知识库')
    ap.add_argument('kb', help='知识库名称')
    ap.add_argument('watch_dir', help='要监控的目录')
    ap.add_argument('--exts', help='只关注这些扩展名，逗号分隔')
    ap.add_argument('--interval', type=int, default=5, help='轮询间隔秒，默认 5')
    ap.add_argument('--settle', type=int, default=10, help='稳定等待秒，默认 10')
    ap.add_argument('--kb-dir', help='知识库根目录')
    args = ap.parse_args()

    exts = None
    if args.exts:
        exts = set()
        for e in args.exts.split(','):
            e = e.strip()
            if e:
                exts.add(e if e.startswith('.') else '.' + e)

    watch = Path(args.watch_dir).expanduser().resolve()
    if not watch.is_dir():
        print(f'错误: 目录不存在 {watch}')
        sys.exit(1)

    print(f'知识库  : {args.kb}')
    print(f'数据目录: {resolve_base_dir(args.kb_dir)}')
    print(f'监控目录: {watch}')
    print(f'轮询 {args.interval}s / 稳定 {args.settle}s')
    print('Ctrl+C 退出\n')

    seen = set()
    pending = {}

    while True:
        try:
            for f in scan_files(watch, exts=exts):
                key = str(f)
                if key in seen:
                    continue

                now = time.time()
                if key not in pending:
                    pending[key] = now
                    print(f'[发现] {f.name}', flush=True)
                    continue

                try:
                    mtime = f.stat().st_mtime
                except OSError:
                    pending.pop(key, None)
                    continue

                # 文件仍在写入 → 重置计时
                if now - mtime < args.settle:
                    pending[key] = now
                    continue

                try:
                    import_paths_sync(args.kb, [f], base_dir=args.kb_dir)
                    seen.add(key)
                    pending.pop(key, None)
                    print(f'[导入] {f.name}', flush=True)
                except Exception as e:
                    print(f'[失败] {f.name}: {str(e)[:120]}', flush=True)
                    pending[key] = now

            time.sleep(args.interval)

        except KeyboardInterrupt:
            print('\n已停止')
            break
        except Exception as e:
            print(f'[异常] {str(e)[:120]}', flush=True)
            time.sleep(args.interval)


if __name__ == '__main__':
    main()
