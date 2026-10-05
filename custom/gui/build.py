# -*- coding: utf-8 -*-
"""把 GUI 打包成 Windows 可执行文件。

在 Windows 上运行（或由 GitHub Actions 的 windows-latest 执行）。

用法:
  python custom/gui/build.py                # 单目录模式（推荐，启动快）
  python custom/gui/build.py --onefile      # 单文件模式（体积大、启动慢）
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# Windows 控制台默认 cp1252，输出中文/符号会抛 UnicodeEncodeError
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--onefile', action='store_true')
    ap.add_argument('--name', default='DeepTutor-Assistant')
    args = ap.parse_args()

    sep = ';' if sys.platform == 'win32' else ':'

    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--noconfirm', '--clean',
        '--name', args.name,
        '--onefile' if args.onefile else '--onedir',
        '--windowed',
        '--paths', str(ROOT),
        # 界面文件
        '--add-data', f'{HERE / "web"}{sep}web',
        # pywebview 的 Windows 后端
        '--hidden-import', 'webview',
        '--hidden-import', 'webview.platforms.edgechromium',
        '--hidden-import', 'clr_loader',
        # 我们自己的包
        '--hidden-import', 'custom',
        '--hidden-import', 'custom.gui',
        '--hidden-import', 'custom.gui.backend',
        '--hidden-import', 'custom.vision',
        '--hidden-import', 'custom.vision.enhance',
        '--hidden-import', 'custom.importer',
        '--hidden-import', 'custom.importer.core',
        # 排除用不到的大块依赖，显著减小体积
        '--exclude-module', 'tkinter',
        '--exclude-module', 'matplotlib',
        '--exclude-module', 'torch',
        '--exclude-module', 'transformers',
        '--exclude-module', 'pytest',
        '--exclude-module', 'IPython',
        str(HERE / 'app.py'),
    ]

    print('执行:', ' '.join(cmd[:8]), '...')
    result = subprocess.run(cmd, cwd=str(HERE))
    if result.returncode != 0:
        print('\n✗ 打包失败')
        sys.exit(result.returncode)

    out = HERE / 'dist'
    print(f'\n✓ 打包完成: {out}')

    # 附带一份说明
    readme = out / '使用说明.txt'
    if out.exists():
        readme.write_text(
            'DeepTutor 试卷助手\n'
            '==================\n\n'
            '1. 双击 DeepTutor-Assistant.exe 启动\n'
            '2. 首次使用请在「配置」页：\n'
            '   - 点「一键部署本地模型」（自动下载 Ollama 与 bge-m3）\n'
            '   - 填入 MiMo API Key（小米开放平台获取）\n'
            '3. 切到「导入试卷」页，选择文件夹开始导入\n\n'
            '注意：本程序需要 DeepTutor 源码目录可用，\n'
            '     请把它放在 DeepTutor 仓库根目录下运行。\n',
            encoding='utf-8')

    for d in ('build', f'{args.name}.spec'):
        p = HERE / d
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.is_file():
            p.unlink()


if __name__ == '__main__':
    main()
