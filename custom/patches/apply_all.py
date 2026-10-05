# -*- coding: utf-8 -*-
"""一次性应用全部核心补丁。

用法
----
    python3 custom/patches/apply_all.py            # 打补丁（幂等）
    python3 custom/patches/apply_all.py --check    # 报告是否「已应用」
    python3 custom/patches/apply_all.py --verify   # 干跑：只验证锚点还在，不写盘

在哪儿用
--------
1. **Docker 构建** —— Dockerfile 里 `COPY custom/` 之后：
       RUN python3 custom/patches/apply_all.py
   上游一改锚点，构建就失败（fail-fast），不会产出一个
   「看起来能跑、但公式搜不到」的镜像。

2. **同步上游之后** —— `custom/sync_upstream.sh` 会跑 `--verify`：
   核心文件在 git 里保持与官方一致，所以 rebase 不会冲突；
   补丁全部由本脚本在**构建时**重新施加。
   `--verify` 不写盘，因此不会把工作区弄脏。

3. **本地开发** —— 从源码直跑（`deeptutor start`）前先跑一次。
   注意：`git checkout -- deeptutor/` 之类的操作会抹掉补丁，重跑即可。

三种状态的判读
--------------
    pristine 树（同步上游后）  → --check 报「未应用」，--verify 通过
    构建镜像时                 → apply_all.py 打上补丁
    本地已打补丁               → --check 报「已应用」，--verify 通过

为什么不用「提交补丁进仓库」
----------------------------
那样每次 rebase 都会在这些核心文件上冲突，`sync_upstream.sh` 的
「只改 custom/」前提就废了。构建时应用换来的是：rebase 永不冲突 +
上游漂移立刻可见。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import patch_image_caption_text as p_caption
import patch_mineru4_engine as p_mineru4

PATCHES = (
    ('MinerU 4.x 兼容', p_mineru4.TARGET, p_mineru4.ANCHORS),
    ('图注内联回正文', p_caption.TARGET, p_caption.ANCHORS),
)


def check() -> int:
    """报告每个补丁是否**已应用**（针对已打过补丁的树）。"""
    from _patchlib import MARK, rel

    print('=== 核心补丁状态 ===')
    missing = 0
    for label, target, _anchors in PATCHES:
        path = rel(target)
        if not path.exists():
            print(f'  ✗ {label:16s} 目标文件不存在: {target}')
            missing += 1
        elif MARK in path.read_text(encoding='utf-8'):
            print(f'  ✓ {label:16s} 已应用')
        else:
            print(f'  ⬜ {label:16s} 未应用（pristine 树属正常）')
            missing += 1
    return missing


def verify() -> int:
    """干跑：验证锚点仍能匹配，**不写盘**。"""
    from _patchlib import apply, rel

    print('=== 干跑验证核心补丁（不写盘）===')
    failed = 0
    for label, target, anchors in PATCHES:
        path = rel(target)
        print(f'\n--- {label} ---')
        if not apply(path, anchors, dry=True):
            failed += 1
    return failed


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')

    if '--check' in sys.argv:
        sys.exit(0 if check() == 0 else 1)

    if '--verify' in sys.argv:
        failed = verify()
        print()
        if failed:
            print(f'✗ {failed} 个补丁的锚点已失效 —— 官方改了这部分代码。')
            print('  请核对后更新 custom/patches/ 下对应的补丁模块。')
            sys.exit(1)
        print('✓ 全部补丁的锚点仍然有效，构建时可以正常应用')
        sys.exit(0)

    print('=== 应用核心补丁（幂等，可重复执行）===\n')
    results = [p_mineru4.run(), p_caption.run()]

    print()
    if all(results):
        print('✓ 全部补丁已就绪')
        sys.exit(0)

    print('✗ 有补丁未能应用 —— 官方代码结构可能变了。')
    print('  请按上面的提示核对锚点后更新 custom/patches/ 下的对应模块。')
    sys.exit(1)


if __name__ == '__main__':
    main()
