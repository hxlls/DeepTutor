# -*- coding: utf-8 -*-
"""DeepTutor 数据备份与恢复。

备份内容：`data/` 目录（知识库、配置、会话、题库）。
不含模型文件（~/.ollama、~/.mineru）—— 那些可重新下载。

SQLite 处理：服务运行时文件处于打开状态，直接复制可能拿到不一致数据，
所以对每个 .db 先执行 `.backup`（SQLite 官方在线备份），再打包。

用法：
  python custom/backup.py                    备份到 ./backups/
  python custom/backup.py --keep 7           保留最近 7 份
  python custom/backup.py --out /path/dir    指定输出目录
  python custom/backup.py --list             列出已有备份
  python custom/backup.py --restore x.tar.gz 从备份恢复
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
DEFAULT_OUT = ROOT / 'backups'

# 备份时排除的内容（可重新生成，体积大）
#   .mineru / .ollama —— 模型文件（合计约 3.2GB），重新下载即可，不该进备份
#   .cache / parse_cache / __pycache__ —— 解析与 pip 缓存
EXCLUDE_DIRS = {'.mineru', '.ollama', '.cache', 'parse_cache', '__pycache__'}
EXCLUDE_SUFFIX = {'.pyc', '.tmp'}


def _log(msg, on_progress=None):
    print(msg, flush=True)
    if on_progress:
        try:
            on_progress('info', msg)
        except Exception:
            pass


def _safe_copy_sqlite(src: Path, dst: Path) -> bool:
    """用 SQLite 的在线备份 API 复制，避免读写冲突。"""
    try:
        con = sqlite3.connect(f'file:{src}?mode=ro', uri=True)
        out = sqlite3.connect(str(dst))
        with out:
            con.backup(out)
        out.close()
        con.close()
        return True
    except Exception as e:
        _log(f'  警告: {src.name} 在线备份失败（{e}），改用直接复制')
        try:
            shutil.copy2(src, dst)
            return True
        except Exception:
            return False


def _should_skip(p: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in p.parts):
        return True
    return p.suffix in EXCLUDE_SUFFIX


def run_backup(out_dir: Path | None = None, keep: int = 7,
               note: str = '', on_progress=None) -> str:
    """执行备份，返回生成的压缩包路径。"""
    out_dir = Path(out_dir) if out_dir else DEFAULT_OUT
    out_dir.mkdir(parents=True, exist_ok=True)

    if not DATA.exists():
        raise FileNotFoundError(f'数据目录不存在: {DATA}')

    stamp = time.strftime('%Y%m%d-%H%M%S')
    tag = f'-{note}' if note else ''
    archive = out_dir / f'deeptutor-data-{stamp}{tag}.tar.gz'

    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / 'data'

        # 1. 复制文件（SQLite 用在线备份）
        total = 0
        for src in DATA.rglob('*'):
            if _should_skip(src):
                continue
            rel = src.relative_to(DATA)
            dst = staging / rel
            if src.is_dir():
                dst.mkdir(parents=True, exist_ok=True)
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.suffix in ('.db', '.sqlite', '.sqlite3'):
                _safe_copy_sqlite(src, dst)
            else:
                try:
                    shutil.copy2(src, dst)
                except Exception as e:
                    _log(f'  跳过 {rel}: {e}', on_progress)
            total += 1

        _log(f'已复制 {total} 个文件，开始打包...', on_progress)

        # 2. 打包
        with tarfile.open(archive, 'w:gz') as tar:
            tar.add(staging, arcname='data')

    size_mb = archive.stat().st_size / 1024 / 1024
    _log(f'备份完成: {archive.name} ({size_mb:.1f} MB)', on_progress)

    # 3. 清理旧备份
    if keep > 0:
        backups = sorted(out_dir.glob('deeptutor-data-*.tar.gz'),
                         key=lambda p: p.stat().st_mtime, reverse=True)
        for old in backups[keep:]:
            old.unlink()
            _log(f'已清理旧备份: {old.name}', on_progress)

    return str(archive)


def list_backups(out_dir: Path | None = None) -> list[dict]:
    """列出已有备份，按时间倒序。"""
    out_dir = Path(out_dir) if out_dir else DEFAULT_OUT
    if not out_dir.exists():
        return []
    items = []
    for p in sorted(out_dir.glob('deeptutor-data-*.tar.gz'),
                    key=lambda x: x.stat().st_mtime, reverse=True):
        st = p.stat()
        items.append({
            'name': p.name,
            'path': str(p),
            'size_mb': round(st.st_size / 1024 / 1024, 1),
            'created': time.strftime('%Y-%m-%d %H:%M:%S',
                                     time.localtime(st.st_mtime)),
        })
    return items


def restore(archive: Path, target: Path | None = None) -> str:
    """从备份恢复。会先把现有 data/ 改名保留，再解压。"""
    archive = Path(archive)
    if not archive.exists():
        raise FileNotFoundError(f'备份文件不存在: {archive}')

    target = Path(target) if target else ROOT
    data_dir = target / 'data'

    # 保底：把现有数据改名，而不是直接删
    if data_dir.exists():
        backup_name = data_dir.with_name(
            f'data.before-restore-{time.strftime("%Y%m%d-%H%M%S")}')
        data_dir.rename(backup_name)
        _log(f'现有数据已改名保留: {backup_name.name}')

    with tarfile.open(archive, 'r:gz') as tar:
        tar.extractall(target)

    _log(f'已从 {archive.name} 恢复')
    return str(data_dir)


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser(description='DeepTutor 数据备份')
    ap.add_argument('--out', help='备份输出目录')
    ap.add_argument('--keep', type=int, default=7, help='保留份数')
    ap.add_argument('--note', default='', help='备注（会加到文件名）')
    ap.add_argument('--list', action='store_true', help='列出已有备份')
    ap.add_argument('--restore', help='从指定备份恢复')
    args = ap.parse_args()

    out_dir = Path(args.out) if args.out else None

    if args.list:
        items = list_backups(out_dir)
        if not items:
            print('暂无备份')
            return
        print(f'{"文件名":<42}{"大小":>10}  创建时间')
        for it in items:
            print(f'{it["name"]:<42}{it["size_mb"]:>8.1f}MB  {it["created"]}')
        return

    if args.restore:
        restore(Path(args.restore))
        return

    run_backup(out_dir, args.keep, args.note)


if __name__ == '__main__':
    main()
