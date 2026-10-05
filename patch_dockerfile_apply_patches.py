# -*- coding: utf-8 -*-
"""在官方 Dockerfile 里插入「应用核心补丁」这一步。

背景
----
我们有 2 处必须改官方源码的补丁（MinerU 4.x 兼容、图注内联回正文），
见 `custom/patches/`。

如果把补丁**提交进仓库**，每次 `git rebase upstream/main` 都会在这两个
核心文件上冲突，`custom/sync_upstream.sh` 的「只改 custom/」前提就废了。

所以改成**构建时应用**：

  · 核心文件在 git 里保持与官方一致 → rebase 永不冲突
  · 上游一改锚点 → 这一步失败 → 构建中断（fail-fast）
    比产出一个「看起来能跑、但公式搜不到」的镜像好得多

插入位置
--------
`COPY custom/ ./custom/` 之后、`ENV TZ=...` 之前。

这一层只依赖 `deeptutor/`（第 175 行）和 `custom/`（第 190 行）两个廉价
层，不会让上面昂贵的 apt/pip 层缓存失效。

用法
----
    python3 patch_dockerfile_apply_patches.py
"""
import sys
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parent / 'Dockerfile'

ANCHOR = '''COPY custom/ ./custom/

# 时区与 MinerU 数据目录。放在最后，避免让上面昂贵的 apt/pip 层缓存失效。
'''

NEW = '''COPY custom/ ./custom/

# 应用核心补丁（MinerU 4.x 兼容 + 图注内联回正文），见 custom/patches/。
#
# 补丁**不提交进仓库** —— 核心文件在 git 里保持与官方一致，这样
# `custom/sync_upstream.sh` 的 rebase 永不冲突。
# 上游一旦改了锚点，这一步直接失败、构建中断（fail-fast），
# 而不是产出一个「看起来能跑、但试卷公式搜不到」的镜像。
#
# 这一层只依赖上面的 deeptutor/ 与 custom/ 两个廉价层，
# 不会让更早的 apt/pip 层缓存失效。
RUN python3 custom/patches/apply_all.py

# 时区与 MinerU 数据目录。放在最后，避免让上面昂贵的 apt/pip 层缓存失效。
'''

MARK = 'custom/patches/apply_all.py'


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')

    if not DOCKERFILE.exists():
        print(f'✗ 找不到 {DOCKERFILE}')
        sys.exit(1)

    src = DOCKERFILE.read_text(encoding='utf-8')

    if MARK in src:
        print(f'= {DOCKERFILE.name} 已包含补丁应用步骤')
        _show(src)
        return

    if ANCHOR not in src:
        print(f'✗ 未找到锚点 —— Dockerfile 结构可能变了，请人工检查 {DOCKERFILE}')
        sys.exit(1)

    if src.count(ANCHOR) != 1:
        print(f'✗ 锚点出现 {src.count(ANCHOR)} 次，不敢改')
        sys.exit(1)

    DOCKERFILE.write_text(src.replace(ANCHOR, NEW, 1), encoding='utf-8')
    print(f'✓ {DOCKERFILE.name} 已插入「应用核心补丁」步骤')
    _show(DOCKERFILE.read_text(encoding='utf-8'))


def _show(src: str) -> None:
    print('\n--- 回显校验 ---')
    for i, line in enumerate(src.splitlines(), 1):
        if 'apply_all.py' in line or 'COPY custom/' in line:
            print(f'  Dockerfile:{i}: {line.strip()[:88]}')


if __name__ == '__main__':
    main()
