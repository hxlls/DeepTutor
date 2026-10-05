# -*- coding: utf-8 -*-
"""MinerU 调用封装（适配 MinerU 4.x）。

把 PDF / DOCX / PPTX / XLSX 解析成 Markdown（公式转 LaTeX）。
MinerU 不支持的格式（如旧版 .doc）返回 None，由上层决定如何处理。

MinerU 4.x 与旧版（2.x/3.x）的差别很大，这里按 4.x 的真实行为实现：

  1. 解析是「本地服务 + 客户端」架构，`mineru parse` 依赖后台的
     `mineru server`，所以解析前必须确保服务在跑。
  2. 本地解析默认是关闭的（parse_server.local.mode = disabled），
     需要 `mineru config set parse_server.local.mode managed` 打开，
     且该命令要求本地模型文件已就绪。
  3. `mineru parse -o` 的语义是「输出文件路径」，不是输出目录。
  4. Markdown 里的图片不是本地文件，而是文档库定位符：
         ![Image block](doc:ab12cd3/tier:standard/page:1/block:2)
     需要 `mineru read <locator> -f image -o <png>` 才能导出成图片。

完整链路：
    pip install mineru
    mineru server start
    mineru-kit models download --tier standard --source modelscope
    mineru config set parse_server.local.mode managed
    mineru parse <file> -o <out.md> -p all --tier standard --wait N
    mineru read <locator> -f image -o <png>      （导出残留图片）
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from custom.winutil import IS_WINDOWS, no_window_kwargs, which_exe

# MinerU 官方支持的格式
SUPPORTED = {'.pdf', '.docx', '.pptx', '.xlsx'}

# 常见但不支持的格式，需要提示用户转换
NEEDS_CONVERT = {'.doc': '.docx', '.ppt': '.pptx', '.xls': '.xlsx'}

# 文档库定位符形式的图片引用（MinerU 4.x）
LOCATOR_RE = re.compile(r'!\[([^\]]*)\]\((doc:[^)\s]+)\)')

# 模型下载源：国内直连 modelscope 比 huggingface 快很多
DEFAULT_MODEL_SOURCE = 'modelscope'

# pip 源。实测 pypi.org 在国内单次响应要 6 秒以上，装 MinerU 会拖到十几分钟；
# 换清华源后是秒级。海外部署可用 PIP_INDEX_URL 环境变量覆盖。
DEFAULT_PIP_INDEX = 'https://pypi.tuna.tsinghua.edu.cn/simple'

# 解析档位：flash/basic/standard/advanced
#   PDF / 图片 → standard（含公式识别）
#   Office 文档 → **只能用 flash**，且不接受页范围参数（4.x 的硬限制）
DEFAULT_TIER = 'standard'
OFFICE_TIER = 'flash'

# 首次解析要加载模型，给足等待时间
DEFAULT_WAIT = 1800

# 本次进程内 parse-server 是否已确认就绪（避免每个文档都去探测一次）
_PARSE_READY = False


def is_available() -> bool:
    """MinerU 是否可用（CLI 已安装）。"""
    return _bin('mineru') is not None


def check_format(path: Path) -> tuple[bool, str]:
    """检查文件格式是否可处理。返回 (是否支持, 提示信息)。"""
    suffix = path.suffix.lower()
    if suffix in SUPPORTED:
        return True, ''
    if suffix in NEEDS_CONVERT:
        target = NEEDS_CONVERT[suffix]
        return False, f'{suffix} 是旧版格式，请用 Office 另存为 {target} 后重试'
    return False, f'{suffix} 不是 MinerU 支持的格式'


# ---------- 本地服务 ----------

def _run(cmd: list[str], timeout: int = 60,
         cwd: str | None = None) -> tuple[int, str]:
    """执行命令，返回 (returncode, 合并输出)。"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding='utf-8', errors='replace',
                           timeout=timeout, cwd=cwd,
                           **no_window_kwargs())
        return r.returncode, (r.stdout or '') + (r.stderr or '')
    except subprocess.TimeoutExpired:
        return -1, '命令超时'
    except Exception as e:  # noqa: BLE001
        return -1, str(e)


def mineru_home() -> Path:
    """MinerU 的数据目录（程序 / 模型 / 文档库）。"""
    env = os.environ.get('MINERU_HOME')
    if env:
        return Path(env)
    return Path.home() / '.mineru'


def mineru_venv() -> Path:
    """MinerU 的独立运行环境，放在 mineru_home() 下。

    为什么不装进系统 site-packages：容器是可抛弃的，运行时装的东西一次
    `compose down` 就没了，用户升级后会发现「增强导入突然失效」。
    放进挂载卷（MINERU_HOME 指向挂载点）就跟着数据一起留下来。

    为什么用 venv 而不是 `pip install --target`：venv 有完整的
    `bin/python` + `bin/mineru`，MinerU 内部用 `sys.executable` 拉起
    parse-server 子进程时能自动落到同一个环境；`--target` 只有一堆包目录，
    子进程还得靠外部注入 PYTHONPATH 才能 import 到。
    """
    return mineru_home() / 'venv'


def _venv_bindir(venv: Path) -> Path:
    """venv 的可执行文件目录（Windows 是 Scripts/）。"""
    return venv / ('Scripts' if IS_WINDOWS else 'bin')


def _settings_dir() -> Path:
    """DeepTutor 的运行时设置目录。

    本文件在 <仓库根>/custom/importer/ 下，往上三层就是仓库根。
    容器里对应 /app/data/user/settings。
    """
    return Path(__file__).resolve().parents[2] / 'data' / 'user' / 'settings'


def _bin(name: str) -> Path | None:
    """定位 MinerU 相关可执行文件。

    优先用挂载卷里那份 venv（用户选「全本地部署」时才装的），
    再退回系统/镜像里预装的版本（兼容老部署）。
    """
    bindir = _venv_bindir(mineru_venv())
    suffixes = ('.exe', '') if IS_WINDOWS else ('',)
    for suf in suffixes:
        cand = bindir / f'{name}{suf}'
        try:
            if cand.is_file():
                return cand
        except OSError:
            continue
    return which_exe(name)


def _server_cwd() -> str:
    """启动 MinerU 服务时使用的工作目录。

    必须是一个**可写**目录。MinerU 的 managed parse-server 在命令行没给
    `--upload-dir` 时，click 会拿「当前目录」去做可写校验；而后端进程的 cwd
    是 /app（root 所有），以 deeptutor 用户运行时直接报
        Error: Invalid value for '--upload-dir': Directory '' is not writable.
    parse-server 连续三次重启失败后被禁用，表现为 parse 一直提示
    "Local parse-server is not ready"。
    """
    home = mineru_home()
    try:
        if home.is_dir() and os.access(home, os.W_OK):
            return str(home)
    except OSError:
        pass
    return tempfile.gettempdir()


def _server_up(mineru: Path) -> bool:
    """`mineru server status` 是否显示服务进程存在。

    注意 "Server is still starting." 也算存在 —— 只看是否明确报
    "Server is not running"。否则启动过程中会误判为没起来，进而重复
    `server start`，两个实例抢同一个 socket。
    """
    rc, out = _run([str(mineru), 'server', 'status'], timeout=30)
    if rc != 0:
        return False
    return 'Server is not running' not in out


def _parse_server_ready(mineru: Path) -> bool:
    """本地 parse-server 是否真的可用（不只是 doclib 进程在跑）。

    `mineru server status` 的 Parse Server 表格里，Local 行的 Health 列
    为 yes 才表示解析后端已加载好模型。
    """
    rc, out = _run([str(mineru), 'server', 'status'], timeout=30)
    if rc != 0:
        return False

    in_table = False
    for line in out.splitlines():
        if 'Parse Server' in line:
            in_table = True
            continue
        if not in_table:
            continue
        stripped = line.strip()
        if stripped.startswith('╰') or stripped.startswith('└'):
            break
        if not stripped.startswith('│'):
            continue
        cells = [c.strip() for c in line.split('│')]
        # cells = ['', Target, Health, Endpoint, Managed, ...]
        if len(cells) > 2 and cells[1].startswith('Local'):
            return cells[2] == 'yes'
    return False


def wait_parse_ready(on_progress=None, timeout: int = 300) -> bool:
    """等待本地 parse-server 就绪（首次加载模型需要时间）。"""
    def log(msg):
        if on_progress:
            on_progress(str(msg))

    mineru = _bin('mineru')
    if mineru is None:
        return False

    deadline = time.time() + timeout
    announced = False
    while time.time() < deadline:
        if _parse_server_ready(mineru):
            return True
        if not announced:
            log('本地解析服务正在加载模型，请稍候...')
            announced = True
        time.sleep(5)
    return False


def ensure_server(on_progress=None, wait: int = 90) -> bool:
    """确保 MinerU 本地服务在运行，必要时启动并等待就绪。"""
    def log(msg):
        if on_progress:
            on_progress(str(msg))

    mineru = _bin('mineru')
    if mineru is None:
        log('未找到 MinerU CLI')
        return False

    if _server_up(mineru):
        return True

    cwd = _server_cwd()
    log(f'启动 MinerU 本地服务（工作目录 {cwd}）...')
    _run([str(mineru), 'server', 'start'], timeout=120, cwd=cwd)

    for _ in range(wait):
        if _server_up(mineru):
            log('MinerU 本地服务已就绪')
            return True
        time.sleep(1)

    log('MinerU 本地服务启动超时')
    return False


def restart_server(on_progress=None) -> bool:
    """重启本地服务，确保它以可写的 cwd 启动。"""
    def log(msg):
        if on_progress:
            on_progress(str(msg))

    mineru = _bin('mineru')
    if mineru is None:
        return False

    cwd = _server_cwd()
    log('重启 MinerU 本地服务以应用配置...')
    _run([str(mineru), 'server', 'stop'], timeout=60, cwd=cwd)
    time.sleep(2)
    _run([str(mineru), 'server', 'start'], timeout=120, cwd=cwd)

    for _ in range(30):
        if _server_up(mineru):
            return True
        time.sleep(1)
    return False


def local_mode() -> str:
    """当前本地解析模式：managed / disabled / ..."""
    mineru = _bin('mineru')
    if mineru is None:
        return ''
    _, out = _run([str(mineru), 'config', 'get', 'parse_server.local.mode'],
                  timeout=30)
    m = re.search(r'parse_server\.local\.mode\s*=\s*(\S+)', out)
    return m.group(1) if m else ''


def _enable_local_mode() -> tuple[bool, str]:
    """开启本地解析模式。

    返回 (是否成功, 输出)。若模型缺失，MinerU 会返回
    "requires model files that are not ready"，据此判断模型是否就绪。
    """
    mineru = _bin('mineru')
    if mineru is None:
        return False, '未找到 MinerU CLI'
    rc, out = _run([str(mineru), 'config', 'set',
                    'parse_server.local.mode', 'managed'], timeout=90)
    if rc != 0:
        return False, out
    if 'requires model files that are not ready' in out:
        return False, out
    return True, out


def models_ready() -> tuple[bool, str]:
    """本地模型是否就绪（通过开启 managed 模式来探测）。"""
    return _enable_local_mode()


# ---------- 准备 / 安装 ----------

def _install_mineru(on_progress=None) -> bool:
    """把 MinerU 装进挂载卷里的独立 venv（按需安装）。

    用户在第 3 步选「全本地部署」才会走到这里 —— 选「标准部署」的人
    不该为这 300MB 买单，所以镜像里不预装。

    用 `--system-site-packages` 复用镜像已有的 onnxruntime / fastapi /
    opencv 等，只补 MinerU 自己那份，省下重复下载。
    """
    def log(msg):
        if on_progress:
            on_progress(str(msg))

    venv = mineru_venv()
    bindir = _venv_bindir(venv)
    py = bindir / ('python.exe' if IS_WINDOWS else 'python')
    pip = bindir / ('pip.exe' if IS_WINDOWS else 'pip')

    if not py.is_file():
        log(f'创建 MinerU 运行环境（{venv}）...')
        rc, out = _run([sys.executable, '-m', 'venv',
                        '--system-site-packages', str(venv)],
                       timeout=300, cwd=_server_cwd())
        if rc != 0:
            log(f'创建运行环境失败：{out[-300:]}')
            return False
        if not pip.is_file():
            # 少数精简镜像不带 ensurepip，用解释器直接调 pip 模块兜底
            rc, out = _run([str(py), '-m', 'ensurepip', '--upgrade'],
                           timeout=300, cwd=_server_cwd())
            if rc != 0 or not pip.is_file():
                log(f'运行环境缺少 pip：{out[-200:]}')
                return False

    log('安装 MinerU（约 300MB，视网速 2-5 分钟）...')
    index = os.environ.get('PIP_INDEX_URL') or DEFAULT_PIP_INDEX
    cmd = [str(pip), 'install', '--upgrade']
    if index:
        cmd += ['-i', index]
    cmd.append('mineru')
    # cwd 同样要可写：pip 会在当前目录留临时文件
    rc, out = _run(cmd, timeout=1800, cwd=_server_cwd())
    if rc != 0:
        log(f'安装失败：{out[-300:]}')
        return False
    return True


def _link_models_for_official_engine(on_progress=None) -> None:
    """让官方的模型就绪检查能认到我们下载的模型。

    两边布局不一样：

      MinerU 整理后的：``MINERU_HOME/models/<repo>/``
      官方 readiness 查的：``$MODELSCOPE_CACHE/models/OpenDataLab/<mineru*>/``
                          （readiness.py 的 _is_mineru_model_dir）

    不接上的话，官方会说「模型未就绪」，用户一点解析就可能触发重复下载 2GB。
    这里建软链把两边接起来 —— 软链几乎不占空间，也不动原文件。
    """
    def log(msg):
        if on_progress:
            on_progress(str(msg))

    src_root = mineru_home() / 'models'
    if not src_root.is_dir():
        return

    ms_cache = os.environ.get('MODELSCOPE_CACHE')
    ms_root = (Path(ms_cache) if ms_cache
               else Path.home() / '.cache' / 'modelscope' / 'hub')
    org_dir = ms_root / 'models' / 'OpenDataLab'

    try:
        org_dir.mkdir(parents=True, exist_ok=True)
        linked = 0
        for repo in src_root.iterdir():
            if not repo.is_dir():
                continue
            link = org_dir / repo.name
            if link.exists() or link.is_symlink():
                continue
            os.symlink(repo, link)
            linked += 1
        if linked:
            log(f'已让官方引擎识别到 {linked} 个模型（软链到 {org_dir}）')
    except Exception as e:  # noqa: BLE001
        log(f'模型软链建立失败（不影响增强导入）：{e}')


def enable_official_engine(on_progress=None) -> bool:
    """把 DeepTutor 官方的「文档解析引擎」切到 MinerU。

    官方其实**内置**了 MinerU 引擎（deeptutor/services/parsing/engines/mineru/），
    但默认用不起来，因为：

      1. 引擎默认是 ``text_only``，MinerU 的配置项在设置页根本不展开 ——
         用户在「设置 → 知识库」里看不到任何 MinerU 相关的东西。

    这里只做一件事：把 ``engine`` 改成 ``mineru``，并打开公式/表格识别。
    **不填 ``local_cli_path``** —— venv 的 bin 已经进了 PATH
    （见 Dockerfile 的 ENV PATH），官方引擎用 ``shutil.which("mineru")``
    自己就能找到，不需要额外指向一个自定义路径。

    模型那边同理：``MODELSCOPE_CACHE`` 指向挂载卷，
    正好是官方 readiness 检查认的位置。

    只动 ``engine`` 和 ``engines.mineru`` 两个键，其余原样保留。
    """
    def log(msg):
        if on_progress:
            on_progress(str(msg))

    cfg = _settings_dir() / 'document_parsing.json'
    try:
        if cfg.is_file():
            data = json.loads(cfg.read_text(encoding='utf-8'))
        else:
            cfg.parent.mkdir(parents=True, exist_ok=True)
            data = {}
    except Exception as e:  # noqa: BLE001
        log(f'读取 document_parsing.json 失败：{e}')
        return False

    data.setdefault('version', 2)
    data['engine'] = 'mineru'

    engines = data.setdefault('engines', {})
    m = engines.setdefault('mineru', {})
    m['mode'] = 'local'
    # 试卷场景要的就是公式和表格
    m['enable_formula'] = True
    m['enable_table'] = True
    # 模型已经下好了，放开这个开关只是避免「未就绪却又不让下」的死锁
    m['allow_local_model_download'] = True
    # 用 pipeline 而不是 vlm：更省显存，试卷公式识别够用
    m.setdefault('model_version', 'pipeline')
    # 直接赋值而不是 setdefault —— 配置文件里本来就有这个键（默认 huggingface），
    # setdefault 不会覆盖它，结果就是官方仍按 huggingface 的路径去找模型
    m['model_download_source'] = DEFAULT_MODEL_SOURCE
    m.setdefault('language', 'auto')
    m.setdefault('is_ocr', False)
    m.setdefault('api_base_url', 'https://mineru.net')
    m.setdefault('api_token', '')
    m.setdefault('max_pages_per_part', 180)
    m.setdefault('local_cli_path', '')

    try:
        cfg.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding='utf-8')
    except Exception as e:  # noqa: BLE001
        log(f'写入 document_parsing.json 失败：{e}')
        return False

    # 官方 readiness 查的是 modelscope 缓存布局（$MODELSCOPE_CACHE/models/
    # OpenDataLab/<mineru*>），和 MinerU 整理后的 MINERU_HOME/models 不一样。
    # 不接上的话官方会说「模型未就绪」，还可能触发重复下载 2GB。
    _link_models_for_official_engine(on_progress)

    log('官方解析引擎已切到 MinerU（设置 → 知识库 可查看）')
    return True


def prepare_mineru(on_progress=None) -> bool:
    """确保 MinerU 可用：安装 CLI → 启动服务 → 下载模型 → 开启本地模式。"""
    def log(msg):
        if on_progress:
            on_progress(str(msg))

    # HOME 被指到挂载卷里的子目录（见 Dockerfile），首次运行时它还不存在。
    # modelscope 写凭证前不会自己建这个目录，直接报 Permission denied。
    try:
        (mineru_home() / 'home').mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        pass

    # ① 安装 CLI（装到挂载卷，重建容器不丢）
    if is_available():
        log('MinerU 已就绪')
    else:
        if not _install_mineru(on_progress):
            return False
        if not is_available():
            log('安装后仍未找到 MinerU 命令')
            return False
        log('MinerU 安装完成')

    # ② 启动本地服务（关键：config 与 parse 都依赖它）
    if not ensure_server(on_progress):
        log('本地服务启动失败，无法继续')
        return False

    # ③ 模型就绪检查 / 下载
    ready, out = models_ready()
    if not ready:
        log('下载 MinerU 模型（约 2.0GB，视网速 5-20 分钟）...')
        kit = _bin('mineru-kit')
        if kit is None:
            log('未找到 mineru-kit，无法下载模型')
            return False
        cmd = [str(kit), 'models', 'download',
               '--tier', DEFAULT_TIER, '--source', DEFAULT_MODEL_SOURCE]
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace',
                # cwd 必须可写：后端进程的 cwd 是 /app（root 所有），
                # modelscope SDK 会往当前目录写临时文件，不可写就直接失败。
                cwd=_server_cwd(),
                **no_window_kwargs(),
            )
            last = ''
            tail: list[str] = []
            for line in proc.stdout:
                # 进度条用 \r 刷新，拆开只保留有信息量的片段
                for piece in line.replace('\r', '\n').split('\n'):
                    piece = piece.strip()
                    if not piece:
                        continue
                    tail.append(piece)
                    if len(tail) > 40:
                        del tail[:20]
                    if piece != last and '%' in piece:
                        last = piece
                        log(f'  {piece[:110]}')
            proc.wait()
            if proc.returncode != 0:
                # 必须把真实错误打出来 —— 只写「模型下载失败」的话，
                # 排查时完全看不到原因（这个坑踩过一次了）
                log(f'模型下载失败（exit {proc.returncode}）')
                for line in tail[-8:]:
                    log(f'  {line[:150]}')
                return False
        except Exception as e:  # noqa: BLE001
            log(f'模型下载异常：{e}')
            return False
        log('模型下载完成')

    # ④ 开启本地解析模式
    ok, out = _enable_local_mode()
    if not ok:
        log(f'开启本地解析失败：{out[-200:]}')
        return False
    log(f'本地解析模式：{local_mode()}')

    # ⑤ 重启服务并等 parse-server 就绪
    #    切换 managed 模式后需要重新拉起解析后端；同时这一步保证它是用
    #    可写的 cwd 启动的（见 _server_cwd 的说明）。
    if not restart_server(on_progress):
        log('本地服务重启失败')
        return False
    if not wait_parse_ready(on_progress, timeout=600):
        log('本地解析服务未能就绪')
        return False

    # 顺带把官方「文档解析引擎」也指向这份 MinerU —— 否则官方设置页
    # 和「知识库上传文件」都不知道我们装过 MinerU，两边互相看不见
    enable_official_engine(on_progress)

    log('MinerU 准备完成')
    return True


# ---------- 解析 ----------

def parse(path: Path, out_dir: Path | None = None,
          on_progress=None, timeout: int = DEFAULT_WAIT) -> Path | None:
    """解析单个文档，返回生成的 Markdown 路径（失败返回 None）。

    产物：<out_dir>/<文件名>.md（图片是文档库定位符，见 materialize_images）
    """
    def log(msg):
        if on_progress:
            on_progress(str(msg))

    mineru = _bin('mineru')
    if mineru is None:
        log('未找到 MinerU CLI，请先执行：pip install mineru')
        return None

    ok, hint = check_format(path)
    if not ok:
        log(f'跳过 {path.name}：{hint}')
        return None

    # 解析依赖本地服务
    if not ensure_server(on_progress):
        log('MinerU 本地服务未就绪，无法解析')
        return None

    # 还要等 parse-server 把模型加载完，否则会报 "not ready"
    global _PARSE_READY
    if not _PARSE_READY:
        if not wait_parse_ready(on_progress, timeout=300):
            log('本地解析服务未就绪（可能仍在加载模型），请稍后重试')
            return None
        _PARSE_READY = True

    out_dir = Path(out_dir) if out_dir else path.parent / '_mineru_out'
    out_dir.mkdir(parents=True, exist_ok=True)
    out_md = out_dir / f'{path.stem}.md'

    # MinerU 4.x 的参数按文件类型分流（实测，报错信息很明确）：
    #   PDF  → "-p all --tier standard"
    #          （-p 是页范围，默认只解析前 10 页，必须显式 all）
    #   docx → 不能带 -p（"Page range is only supported for PDF files"），
    #          且只接受 --tier flash（"'standard' is only supported for
    #          PDF and image files; 'docx' files use --tier flash"）
    # 一开始对两种文件用了同一套参数，结果 docx 全军覆没。
    cmd = [str(mineru), 'parse', str(path), '-o', str(out_md)]
    if path.suffix.lower() == '.pdf':
        cmd += ['-p', 'all', '--tier', DEFAULT_TIER]
    else:
        cmd += ['--tier', OFFICE_TIER]
    cmd += ['--wait', str(timeout)]
    log(f'MinerU 解析中：{path.name}')

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace',
            cwd=_server_cwd(),
            **no_window_kwargs(),
        )
        last = ''
        for line in proc.stdout:
            line = line.strip()
            if line and line != last:
                last = line
                log(f'  {line[:110]}')
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        log(f'解析超时：{path.name}')
        return None
    except Exception as e:  # noqa: BLE001
        log(f'解析异常：{e}')
        return None

    if proc.returncode != 0 or not out_md.is_file():
        # 解析后端可能刚崩过，下次重新探测一次就绪状态
        _PARSE_READY = False

    if proc.returncode != 0:
        log(f'解析失败（exit {proc.returncode}）')
        return None

    if not out_md.is_file():
        log(f'未找到解析产物：{out_md.name}')
        return None

    log(f'解析完成：{out_md.name}')
    return out_md


# ---------- 图片落地 ----------

def count_locators(md_path: Path) -> int:
    """Markdown 里文档库定位符图片的数量。"""
    if not md_path.is_file():
        return 0
    return len(LOCATOR_RE.findall(md_path.read_text(encoding='utf-8')))


def materialize_images(md_path: Path, on_progress=None,
                       workers: int = 4) -> Path:
    """把文档库定位符图片导出成本地文件，并改写 Markdown 引用。

    MinerU 4.x 的图片存在文档库里，Markdown 只写定位符：
        ![Image block](doc:ab12cd3/tier:standard/page:1/block:2)
    这里用 `mineru read -f image` 导出到 <md 同级>/images/，
    并把引用改写成标准相对路径，供后续视觉兜底与入库使用。
    """
    def log(msg):
        if on_progress:
            on_progress(str(msg))

    if not md_path.is_file():
        return md_path

    text = md_path.read_text(encoding='utf-8')
    # 注意用 finditer + group(2)：正则有两个捕获组，findall 会返回元组
    locators = list(dict.fromkeys(
        mo.group(2) for mo in LOCATOR_RE.finditer(text)))  # 去重保序
    if not locators:
        return md_path

    mineru = _bin('mineru')
    if mineru is None:
        log('未找到 MinerU CLI，无法导出图片')
        return md_path

    log(f'导出 {len(locators)} 张图片...')
    img_dir = md_path.parent / 'images'
    img_dir.mkdir(parents=True, exist_ok=True)

    from concurrent.futures import ThreadPoolExecutor

    mapping: dict[str, str] = {}

    def export(item: tuple[int, str]) -> None:
        idx, loc = item
        dst = img_dir / f'block_{idx}.png'
        _run([str(mineru), 'read', loc, '-f', 'image', '-o', str(dst)],
             timeout=180, cwd=_server_cwd())
        if dst.is_file() and dst.stat().st_size > 0:
            mapping[loc] = dst.name

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(export, enumerate(locators, 1)))

    if not mapping:
        log('图片导出失败，保留原引用')
        return md_path

    def repl(m: re.Match) -> str:
        name = mapping.get(m.group(2))
        return f'![{m.group(1)}](images/{name})' if name else m.group(1)

    out = LOCATOR_RE.sub(repl, text)
    md_path.write_text(out, encoding='utf-8')
    log(f'图片已导出：{len(mapping)}/{len(locators)}')
    return md_path
