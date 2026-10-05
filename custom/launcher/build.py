# -*- coding: utf-8 -*-
"""把 DeepTutor 桌面启动器打包成 Windows 可执行文件。

⚠️ 必须在 Windows 上运行（PyInstaller 不支持交叉编译）。

用法:
  python custom/launcher/build.py              # 打包成单文件 exe
  python custom/launcher/build.py --onedir     # 打包成目录（启动更快）

前置:
  pip install pyinstaller pywebview

产物:
  dist/DeepTutor.exe
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def main():
    ap = argparse.ArgumentParser(description='打包 DeepTutor 桌面启动器')
    ap.add_argument('--onedir', action='store_true',
                    help='打包成目录而非单文件（启动更快）')
    ap.add_argument('--name', default='DeepTutor', help='产物名称')
    ap.add_argument('--icon', help='图标文件路径（.ico）')
    ap.add_argument('--console', action='store_true',
                    help='保留控制台窗口（调试用）')
    args = ap.parse_args()

    if sys.platform != 'win32':
        print('⚠️  当前不是 Windows，PyInstaller 无法交叉编译出 exe。')
        print('   请在 Windows 机器上运行本脚本。')
        return

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print('缺少 pyinstaller，请先执行: pip install pyinstaller')
        return

    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--noconfirm', '--clean',
        '--name', args.name,
        '--onedir' if args.onedir else '--onefile',
        '--paths', str(ROOT),
    ]
    if not args.console:
        cmd.append('--windowed')
    if args.icon:
        cmd += ['--icon', args.icon]

    # pywebview 在 Windows 上依赖这些隐藏导入
    for hidden in ('webview', 'webview.platforms.edgechromium',
                   'clr_loader', 'pythonnet'):
        cmd += ['--hidden-import', hidden]

    cmd.append(str(HERE / 'app.py'))

    print('执行:', ' '.join(cmd))
    print()
    result = subprocess.run(cmd, cwd=str(HERE))
    if result.returncode != 0:
        print('\n✗ 打包失败')
        sys.exit(result.returncode)

    exe = HERE / 'dist' / f'{args.name}.exe'
    print(f'\n✓ 打包完成: {exe}')
    print()
    print('注意：exe 只是启动器，仍需要 DeepTutor 源码与 .venv 在其可访问的位置。')
    print('如需「解压即用」，还要把 runtime/（Python + Node）一并打包 ——')
    print('可参考 DeepTutor-Desktop 的做法。')

    # 清理中间产物
    for d in ('build', f'{args.name}.spec'):
        p = HERE / d
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.is_file():
            p.unlink()


if __name__ == '__main__':
    main()
