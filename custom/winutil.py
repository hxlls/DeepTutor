# -*- coding: utf-8 -*-
"""跨平台子进程辅助 —— 主要是 Windows 上会踩的两个坑。

坑 1：**黑框闪烁**
Windows 上 `subprocess` 默认会为每个子进程创建控制台窗口。批量解析试卷时
每个文档、每张图都弹一个黑框，满屏闪。用 `CREATE_NO_WINDOW` 抑制。

坑 2：**找不到 CLI**
`pip install` 出来的命令行工具，Windows 上落在 `<python>/Scripts/` 且带
`.exe` 后缀；Linux/macOS 上则与解释器同级、无后缀。只查
`Path(sys.executable).parent / name` 在 Windows 上必然扑空。

这两件事散落在各处容易漏，统一放这里。
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

IS_WINDOWS = os.name == 'nt'

# Win32 常量：不为子进程创建控制台窗口
_CREATE_NO_WINDOW = 0x08000000


def no_window_kwargs() -> dict:
    """传给 subprocess 的「不弹黑框」参数；非 Windows 返回空 dict。

    用法：
        subprocess.run(cmd, **no_window_kwargs())
        subprocess.Popen(cmd, **no_window_kwargs())
    """
    if not IS_WINDOWS:
        return {}
    return {'creationflags': _CREATE_NO_WINDOW}


def which_exe(name: str) -> Path | None:
    """定位可执行文件，兼容 Windows 的 Scripts/ 目录与 .exe 后缀。

    查找顺序：
      1. 与当前解释器同级：`<exe_dir>/<name>`、`<exe_dir>/<name>.exe`
      2. 同级的 Scripts/：`<exe_dir>/Scripts/<name>[.exe]`  ← Windows pip 的位置
      3. PATH

    每一步都吞异常：容器里 `/root` 之类不可访问的目录会让 `is_file()`
    抛 PermissionError，不该因此让整个调用链崩掉。
    """
    exe_dir = Path(sys.executable).parent
    suffixes = ('', '.exe') if IS_WINDOWS else ('',)
    for base in (exe_dir, exe_dir / 'Scripts'):
        for suf in suffixes:
            cand = base / f'{name}{suf}'
            try:
                if cand.is_file():
                    return cand
            except OSError:
                continue
    try:
        found = shutil.which(name)
    except Exception:  # noqa: BLE001
        found = None
    return Path(found) if found else None
