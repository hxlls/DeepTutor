# -*- coding: utf-8 -*-
"""DeepTutor 试卷助手 —— 桌面程序（pywebview + HTML 界面）。

面向完全不懂配置的用户：一个窗口搞定环境检测、模型部署、导入、出题。

用法:
  python custom/gui/app.py
  python custom/gui/app.py --dev     # 开发模式（开调试台）
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _web_dir() -> Path:
    """界面目录：打包后在 PyInstaller 的临时解压目录里。"""
    if getattr(sys, 'frozen', False):
        return Path(getattr(sys, '_MEIPASS', Path(sys.executable).parent)) / 'web'
    return Path(__file__).resolve().parent / 'web'


WEB_DIR = _web_dir()


class Api:
    """暴露给前端的接口。所有方法都被 js_api 包装成 JS 可调函数。"""

    def __init__(self, window=None):
        self.window = window
        self._busy = False

    # ---------- 工具 ----------

    def _emit(self, kind, msg):
        """向前端推送进度。"""
        if self.window:
            try:
                payload = json.dumps({'kind': kind, 'msg': str(msg)})
                self.window.evaluate_js(f'window.__onProgress({payload})')
            except Exception:
                pass

    def _bg(self, fn):
        """后台线程执行，避免阻塞界面。"""
        def runner():
            try:
                fn()
            except Exception as e:
                self._emit('error', f'{e}\n{traceback.format_exc()[:500]}')
        threading.Thread(target=runner, daemon=True).start()

    # ---------- 环境 ----------

    def check_env(self):
        from custom.gui.backend import check_env
        return check_env()

    def save_key(self, api_key, model):
        from custom.gui.backend import save_mimo_key, test_mimo
        res = save_mimo_key(api_key, model)
        if res.get('ok'):
            t = test_mimo(api_key, model)
            res['test'] = t
        return res

    def test_key(self, api_key, model):
        from custom.gui.backend import test_mimo
        return test_mimo(api_key, model)

    # ---------- 部署 ----------

    def deploy_ollama(self):
        if self._busy:
            return {'ok': False, 'msg': '已有任务在运行'}
        self._busy = True

        def job():
            from custom.gui.backend import pull_embed_model, start_ollama
            try:
                self._emit('info', '=== 启动 Ollama ===')
                if not start_ollama(self._emit):
                    self._emit('error', 'Ollama 启动失败')
                    return
                self._emit('info', '=== 下载 embedding 模型 ===')
                ok = pull_embed_model(self._emit)
                self._emit('done' if ok else 'error',
                           '本地模型部署完成' if ok else '模型下载失败')
            finally:
                self._busy = False

        self._bg(job)
        return {'ok': True, 'msg': '任务已启动'}

    # ---------- 视觉增强 ----------

    def scan_dir(self, directory):
        """扫描目录，返回可增强的文件与预估成本。"""
        from custom.vision import count_images, estimate_cost
        d = Path(directory)
        if not d.is_dir():
            return {'ok': False, 'msg': f'目录不存在: {directory}'}
        items = []
        for md in sorted(d.rglob('*.md')):
            if md.name.endswith('.enriched.md'):
                continue
            refs, uniq = count_images(md)
            if uniq:
                est = estimate_cost(md)
                items.append({'path': str(md), 'name': md.name,
                              'images': uniq, 'cost': est['cost_yuan']})
        total_cost = round(sum(i['cost'] for i in items), 2)
        return {'ok': True, 'items': items, 'total_cost': total_cost}

    def enhance(self, md_paths, api_key, model, workers=4):
        if self._busy:
            return {'ok': False, 'msg': '已有任务在运行'}
        self._busy = True

        def job():
            from custom.vision import enhance_markdown
            try:
                total = len(md_paths)
                for i, p in enumerate(md_paths, 1):
                    self._emit('info', f'--- [{i}/{total}] {Path(p).name} ---')
                    r = enhance_markdown(Path(p), api_key, model,
                                         workers=workers, on_progress=self._emit)
                    if not r.get('ok'):
                        self._emit('error', r.get('msg', '失败'))
                self._emit('done', f'视觉增强完成（{total} 个文件）')
            finally:
                self._busy = False

        self._bg(job)
        return {'ok': True, 'msg': '任务已启动'}

    # ---------- 知识库 ----------

    def list_kbs(self):
        from custom.importer.core import list_kbs
        try:
            return {'ok': True, 'kbs': list_kbs()}
        except Exception as e:
            return {'ok': False, 'msg': str(e), 'kbs': []}

    def import_dir(self, kb_name, directory, use_enriched=True):
        """把目录导入知识库（优先用增强后的文件）。"""
        if self._busy:
            return {'ok': False, 'msg': '已有任务在运行'}
        self._busy = True

        def job():
            from custom.importer.core import import_paths_sync, scan_files
            try:
                files = scan_files(directory)
                if use_enriched:
                    # 若存在同名 .enriched.md，优先用它
                    picked, seen = [], set()
                    for f in files:
                        if f.suffix == '.md':
                            enriched = f.with_suffix('.enriched.md')
                            if enriched.exists():
                                picked.append(enriched)
                                seen.add(f.stem)
                                continue
                        if f.stem in seen:
                            continue
                        picked.append(f)
                    files = picked

                total = len(files)
                self._emit('info', f'共 {total} 个文件待导入')
                batch = 20
                for i in range(0, total, batch):
                    chunk = files[i:i + batch]
                    self._emit('info',
                               f'导入 {i+1}-{min(i+batch, total)}/{total} ...')
                    import_paths_sync(kb_name, chunk)
                self._emit('done', f'导入完成（{total} 个文件）')
            except Exception as e:
                self._emit('error', f'导入失败: {e}')
            finally:
                self._busy = False

        self._bg(job)
        return {'ok': True, 'msg': '任务已启动'}

    # ---------- 出题 ----------

    def generate(self, kb_name, topic, count=5, difficulty=''):
        if self._busy:
            return {'ok': False, 'msg': '已有任务在运行'}
        self._busy = True

        def job():
            try:
                from deeptutor.agents.question.capability import QuestionCapability
                self._emit('info', f'正在基于「{kb_name}」出题：{topic}')
                # 走 CLI 更稳（内部会处理会话与事件总线）
                import subprocess
                cmd = [sys.executable, '-m', 'deeptutor_cli', 'run',
                       'deep_question', topic, '--kb', kb_name,
                       '--config', f'num_questions={count}']
                if difficulty:
                    cmd += ['--config', f'difficulty={difficulty}']
                proc = subprocess.run(cmd, cwd=str(ROOT),
                                      capture_output=True, text=True,
                                      encoding='utf-8', errors='replace',
                                      timeout=900)
                out = (proc.stdout or '')[-4000:]
                self._emit('result', out or (proc.stderr or '')[-2000:])
            except Exception as e:
                self._emit('error', f'出题失败: {e}')
            finally:
                self._busy = False

        self._bg(job)
        return {'ok': True, 'msg': '任务已启动'}


def main():
    ap = argparse.ArgumentParser(description='DeepTutor 试卷助手')
    ap.add_argument('--dev', action='store_true', help='开发模式（开调试台）')
    ap.add_argument('--width', type=int, default=1100)
    ap.add_argument('--height', type=int, default=760)
    args = ap.parse_args()

    try:
        import webview
    except ImportError:
        print('缺少 pywebview，请执行: pip install pywebview')
        sys.exit(1)

    index = WEB_DIR / 'index.html'
    if not index.exists():
        print(f'界面文件缺失: {index}')
        sys.exit(1)

    api = Api()
    window = webview.create_window(
        'DeepTutor 试卷助手', str(index),
        width=args.width, height=args.height,
        min_size=(900, 620), js_api=api,
    )
    api.window = window
    webview.start(debug=args.dev)


if __name__ == '__main__':
    main()
