# -*- coding: utf-8 -*-
"""核心补丁的公共工具：仓库根定位 + 幂等锚点替换。

三条设计原则
------------
1. **幂等** —— 重复执行安全。这样「同步上游 → 重跑补丁」可以无条件执行。
2. **锚点严格** —— 锚点必须出现且**仅出现一次**。出现 0 次说明官方改过这里；
   出现多次说明上下文不够长。两种情况都直接失败，不猜。
3. **fail-fast** —— 打不上补丁时**返回非零退出码**。构建脚本据此中断，
   而不是产出一个「看起来能跑、但公式搜不到」的镜像。

为什么补丁在构建时打、而不提交进仓库
------------------------------------
提交进仓库的话，每次 `git rebase upstream/main` 都会在这些核心文件上冲突，
`custom/sync_upstream.sh` 的「只改 custom/」前提就废了。
改成构建时应用之后：
  · 核心文件在 git 里保持与官方一致 → rebase 永不冲突
  · 上游一旦改了锚点 → **构建失败**，立刻暴露（比静默失效好得多）
  · 官方将来自己适配了 → 删掉对应补丁模块即可
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MARK = '[试卷助手补丁]'


def rel(path: str) -> Path:
    """仓库根下的相对路径 -> 绝对路径。"""
    return REPO_ROOT / path


def apply(path: Path, steps, *, mark: str = MARK, dry: bool = False) -> bool:
    """按顺序幂等地打补丁。

    ``steps`` 是 ``[(标签, 锚点原文, 替换后原文), ...]``。
    返回 ``True`` 表示文件最终处于「已打补丁」状态。
    """
    if not path.exists():
        print(f'  ✗ 目标文件不存在: {path}')
        return False

    src = path.read_text(encoding='utf-8')
    if mark in src:
        print(f'  = {path.name} 已打过补丁，跳过')
        return True

    for label, old, new in steps:
        count = src.count(old)
        if count == 0:
            print(f'  ✗ {label}: 未找到锚点。')
            print(f'    官方很可能改过这里，请人工核对后更新补丁：{path}')
            return False
        if count > 1:
            print(f'  ✗ {label}: 锚点在文件里出现 {count} 次，上下文不够长。')
            print(f'    请把锚点扩展到唯一可识别后重试：{path}')
            return False
        src = src.replace(old, new, 1)
        print(f'  ✓ {label}')

    try:
        compile(src, str(path), 'exec')
    except SyntaxError as exc:
        print(f'  ✗ 打补丁后语法错误: {exc}')
        return False

    if not dry:
        path.write_text(src, encoding='utf-8')
    print(f'  ✓ {path.name} 补丁完成')
    return True


def show(path: Path, needles) -> None:
    """回显命中行，便于人工核对。"""
    if not path.exists():
        return
    for i, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if any(n in line for n in needles):
            print(f'    {path.name}:{i}: {line.strip()[:88]}')
