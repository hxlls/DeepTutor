# -*- coding: utf-8 -*-
"""补丁：让官方 MinerU 引擎兼容 MinerU 4.x。

问题
----
官方 `deeptutor/services/parsing/engines/mineru/local.py` 用的是 **MinerU 3.x**
的调用语法：

    mineru -p <file> -o <output_dir>

4.x 改成了子命令式，且 PDF 与 Office 文档参数不同：

    mineru parse <file> -o <out.md> -p all --tier standard   # PDF
    mineru parse <file> -o <out.md> --tier flash             # docx/pptx/xlsx

结果：官方路径下每个文件都报 `No such option: -p`，界面上表现为一直刷错误。

改动
----
**只有一处** —— `local.py` 里的命令构造。

产物查找不用改：官方 `load_ir()` 用 `glob("*.md")` 找 markdown，
4.x 输出的单文件正好能被它捡到。

细节
----
· `-o` 在 4.x 里是**输出文件**，不是目录
· `-p` 是 pages，**默认只解析前 10 页** —— 试卷必须显式给 `all`
· docx/pptx/xlsx **不能带 `-p`**，且只接受 `--tier flash`
· `--wait` 必须显式给：4.x 默认只等 60 秒，首次解析要加载模型必然超时
"""
from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _patchlib as lib
else:
    from . import _patchlib as lib

TARGET = 'deeptutor/services/parsing/engines/mineru/local.py'

ANCHOR = '''        cmd = [mineru_cmd, "-p", str(source_file), "-o", str(temp_output)]
'''

REPLACEMENT = '''        # [试卷助手补丁] 兼容 MinerU 4.x —— 见 custom/patches/patch_mineru4_engine.py
        # 官方原代码是 3.x 语法 `mineru -p <file> -o <dir>`，在 4.x 上会直接
        # 报 "No such option: -p"。4.x 改成了子命令式，而且 PDF 与 Office
        # 文档的参数不同（4.x 的硬限制，见报错信息）：
        #   PDF            → -p all + --tier standard（standard 才含公式识别）
        #   docx/pptx/xlsx → 不能带 -p，且只接受 --tier flash
        # 产物写成单文件；官方下游的 load_ir() 用 glob("*.md") 找，正好能捡到，
        # 所以这里不用改产物查找逻辑。
        # --wait 必须显式给：4.x 默认只等 60 秒，而首次解析要加载模型，
        # 一份试卷很容易超时。
        _suffix = source_file.suffix.lower()
        _out_md = temp_output / f"{source_file.stem}.md"
        if _suffix == ".pdf":
            cmd = [mineru_cmd, "parse", str(source_file), "-o", str(_out_md),
                   "-p", "all", "--tier", "standard", "--wait", "1800"]
        else:
            cmd = [mineru_cmd, "parse", str(source_file), "-o", str(_out_md),
                   "--tier", "flash", "--wait", "900"]
'''


ANCHORS = (
    ('命令构造改为 4.x 子命令式', ANCHOR, REPLACEMENT),
)


def run() -> bool:
    print('--- 补丁 1/2 · MinerU 4.x 兼容 ---')
    path = lib.rel(TARGET)
    ok = lib.apply(path, ANCHORS)
    if ok:
        lib.show(path, ('试卷助手补丁', '"parse", str(source_file)'))
    return ok


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(0 if run() else 1)
