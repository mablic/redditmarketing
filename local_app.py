"""Loopback-only control panel for local generation and reviewed Reddit posting."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
from urllib.parse import parse_qs, unquote, urlparse
import uuid
import webbrowser

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'work' / 'runtime'))
from city_pipeline import write_json

TOKEN = secrets.token_urlsafe(32)
STATE_LOCK = threading.Lock()
STATE = {'status': 'idle', 'log': '', 'error': '', 'output': ''}
JOB_GENERATION = 0


def cancel_tasks():
    global JOB_GENERATION
    with STATE_LOCK:
        if STATE['status'] == 'cancelling':
            raise ValueError('正在取消任务，请稍候。')
        JOB_GENERATION += 1
        STATE.update(status='cancelling')
    try:
        if os.name != 'nt':
            raise ValueError('取消任务功能当前支持这台 Windows 电脑。')
        result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                                 str(ROOT / 'scripts' / 'cancel_tasks.ps1'), '-ProjectRoot', str(ROOT)],
                                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise ValueError(result.stderr.strip() or '未能停止所有任务，请查看进程状态。')
        stopped = json.loads(result.stdout)['stopped']
        for path in (ROOT/'work/remaining_batch.json',ROOT/'work/scenic_batch.json'):
            if path.exists():
                batch = json.loads(path.read_text(encoding='utf-8'))
                batch.update(status='cancelled', current='')
                write_json(path, batch)
        with STATE_LOCK:
            STATE.update(status='idle', kind='', error='', output='', log='已取消所有项目生成任务，保留已有结果。')
        return {'stopped': stopped, 'message': '已取消所有生成任务，网页继续运行。'}
    except Exception as error:
        with STATE_LOCK:
            STATE.update(status='failed', error=str(error))
        raise


def batch_progress():
    from scenic_pipeline import progress
    scenic = progress()
    if scenic:
        return scenic
    path = ROOT / 'work' / 'remaining_batch.json'
    if not path.exists():
        return None
    state = json.loads(path.read_text(encoding='utf-8'))
    if state.get('status') == 'running':
        from city_pipeline import lock
        try:
            with lock(ROOT / 'work' / 'remaining_batch.lock'):
                state['status'] = 'interrupted'
        except ValueError:
            pass
    log_path = ROOT / 'work' / 'remaining_batch.log'
    if not log_path.exists() and state.get('current'):
        log_path = ROOT / 'work' / 'batch_logs' / (state['current'] + '.log')
    tail = ''
    if log_path.exists():
        with log_path.open('rb') as log:
            log.seek(max(0, log.seek(0, 2) - 40000))
            tail = log.read().decode('utf-8', errors='replace')
    return {'status': state['status'], 'total': state['total'], 'current': state['current'],
            'completed': len(state['completed']), 'failed': len(state['failed']),
            'failures': state['failed'], 'log': tail}


def start_remaining(payload):
    """Launch a batch independently of the web server's lifetime."""
    prompt = payload.get('prompt', '')
    if not isinstance(prompt, str) or len(prompt) > 2000:
        raise ValueError('Prompt 最多 2000 字。')
    with STATE_LOCK:
        batch = batch_progress()
        if STATE['status'] in ('running', 'cancelling') or (batch and batch['status'] == 'running'):
            raise ValueError('已有生成任务在运行，请等待结束。')
        from city_pipeline import lock
        with lock(ROOT / 'work' / 'city_pipeline.lock'), lock(ROOT / 'work' / 'remaining_batch.lock'):
            queue = json.loads((ROOT / 'city_queue.json').read_text(encoding='utf-8'))
            remaining = sum(not c['run_time'] for c in queue['cities'])
            if not remaining:
                raise ValueError('所有城市已完成，没有剩余任务。')
            options_path = ROOT / 'work' / ('batch-options-' + uuid.uuid4().hex + '.json')
            write_json(options_path, {'prompt': prompt, 'include_food': bool(payload.get('include_food', True))})
        STATE.update(status='running', kind='batch', log='', error='', output='')
        generation = JOB_GENERATION
        try:
            with (ROOT / 'work' / 'remaining_batch.log').open('w', encoding='utf-8') as log:
                process = subprocess.Popen([sys.executable, '-u', str(ROOT / 'run_remaining.py'), '--options', str(options_path)],
                    cwd=ROOT, env=dict(os.environ, PYTHONIOENCODING='utf-8'), stdout=log, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL, creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == 'nt' else 0)
        except Exception:
            STATE.update(status='failed', error='批量进程未能启动。')
            raise
    def monitor():
        code = process.wait()
        with STATE_LOCK:
            if generation != JOB_GENERATION:
                return
            STATE.update(status='done' if code == 0 else 'failed', error='' if code == 0 else '部分城市未完成，请查看批量进度和日志。')
    threading.Thread(target=monitor, daemon=True).start()
    return {'remaining': remaining, 'pid': process.pid}


def start_scenic(payload):
    from scenic_pipeline import get_province, group_cities
    prompt = payload.get('prompt', '')
    if not isinstance(prompt, str) or len(prompt) > 2000:
        raise ValueError('Prompt 最多2000字。')
    run_all = bool(payload.get('all'))
    province = city = None
    if not run_all:
        _, province = get_province(payload.get('province'))
        city = payload.get('city')
        if city and city not in group_cities(province):
            raise ValueError('城市不属于所选省份。')
    with STATE_LOCK:
        if STATE['status'] in ('running','cancelling') or (batch_progress() or {}).get('status') == 'running':
            raise ValueError('已有任务在运行，请先取消或等待。')
        from city_pipeline import lock
        with lock(ROOT/'work/city_pipeline.lock'), lock(ROOT/'work/scenic_pipeline.lock'):
            pass
        command = [sys.executable,'-u',str(ROOT/'scenic_pipeline.py'),'--prompt',prompt]
        command += ['--all'] if run_all else ['--province', province['province']]
        if city:
            command += ['--city',city]
        log_path=ROOT/'work/scenic_batch.log'
        STATE.update(status='running',kind='scenic',error='',output='',log='正在启动5A景区任务。')
        generation=JOB_GENERATION
        try:
            with log_path.open('w',encoding='utf-8') as log:
                process=subprocess.Popen(command,cwd=ROOT,env=dict(os.environ,PYTHONIOENCODING='utf-8'),stdin=subprocess.DEVNULL,
                    stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        except Exception:
            STATE.update(status='failed',error='5A任务启动失败。');raise
    def monitor():
        code=process.wait()
        with STATE_LOCK:
            if generation != JOB_GENERATION:
                return
            STATE.update(status='done' if code==0 else 'failed',error='' if code==0 else '部分景区未完成，请查看日志。')
    threading.Thread(target=monitor,daemon=True).start()
    return {'province': '全部' if run_all else province['province'], 'pid': process.pid}


def post_folder(relative):
    root = (ROOT / 'city_posts').resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path == root or not path.is_dir():
        raise ValueError('Invalid post folder.')
    return path


def posts():
    root = ROOT / 'city_posts'
    if not root.exists():
        return []
    return [{'folder': str(p.parent.relative_to(root)).replace('\\', '/'), 'title': p.read_text(encoding='utf-8').strip()}
            for p in sorted(root.rglob('title.txt'), key=lambda p: p.stat().st_mtime, reverse=True)
            if post_folder(str(p.parent.relative_to(root)))]


def read_post(folder):
    p = post_folder(folder)
    metadata = json.loads((p/'post.json').read_text(encoding='utf-8')) if (p/'post.json').exists() else {}
    if (p/'photo_candidates.json').exists():
        metadata['photo_candidates']=json.loads((p/'photo_candidates.json').read_text(encoding='utf-8')).get('photos',[])
    return {'folder': folder, 'title': (p / 'title.txt').read_text(encoding='utf-8').strip(),
            'metadata': metadata,
            'body': (p / 'body.txt').read_text(encoding='utf-8'),
            'foods': (p / '美食推荐.txt').read_text(encoding='utf-8') if (p / '美食推荐.txt').exists() else '',
            'images': [str(x.relative_to(ROOT / 'city_posts')).replace('\\', '/') for x in sorted(p.glob('*.png'))]}


def generate(payload):
    batch = batch_progress()
    if batch and batch['status'] == 'running':
        raise ValueError('所有剩余城市正在批量生成，请等待批量任务结束。')
    city = payload.get('city', '')
    queue = json.loads((ROOT / 'city_queue.json').read_text(encoding='utf-8'))
    if city not in [c['name_zh'] for c in queue['cities']]:
        raise ValueError('请选择队列中的城市。')
    prompt = payload.get('prompt', '')
    if not isinstance(prompt, str) or len(prompt) > 2000:
        raise ValueError('Prompt 最多 2000 字。')
    with STATE_LOCK:
        if STATE['status'] in ('running', 'cancelling'):
            raise ValueError('已有任务在运行。')
        STATE.update(status='running', log='', error='', output='')
        generation = JOB_GENERATION
    run_id = uuid.uuid4().hex
    def worker():
        try:
            command = [sys.executable, '-u', str(ROOT / 'city_pipeline.py'), '--city', city,
                       '--run-id', run_id, '--prompt', prompt, '--source', 'public']
            if payload.get('include_food', True):
                command.append('--include-food')
            environment = dict(os.environ, PYTHONIOENCODING='utf-8')
            with STATE_LOCK:
                if generation != JOB_GENERATION:
                    return
                process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            for line in process.stdout:
                with STATE_LOCK:
                    if generation != JOB_GENERATION:
                        continue
                    STATE['log'] = (STATE['log'] + line)[-40000:]
            code = process.wait()
            output = f'runs/{run_id}/{city}'
            success = code == 0 and (ROOT / 'city_posts' / output).is_dir()
            with STATE_LOCK:
                if generation != JOB_GENERATION:
                    return
                STATE.update(status='done' if success else 'failed', output=output if success else '',
                             error='' if success else '生成失败，请查看运行日志。已完成进度保留。')
        except Exception as error:
            with STATE_LOCK:
                if generation != JOB_GENERATION:
                    return
                STATE.update(status='failed', error=str(error))
    threading.Thread(target=worker, daemon=True).start()
    return {'run_id': run_id}


class Handler(BaseHTTPRequestHandler):
    def send(self, data, code=200, content_type='application/json; charset=utf-8'):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        try:
            if self.headers.get('Host') not in ('127.0.0.1:8765', 'localhost:8765'):
                return self.send({'error': 'Invalid host'}, 403)
            route = urlparse(self.path)
            if route.path == '/api/config':
                return self.send({'token': TOKEN, 'version': 2})
            if route.path == '/':
                index = ROOT / 'dist' / 'index.html'
                html = index.read_text(encoding='utf-8') if index.exists() else '<p>请运行 npm run dev，并打开 http://127.0.0.1:5173</p>'
                return self.send(html.encode(), content_type='text/html; charset=utf-8')
            if route.path.startswith('/assets/'):
                root = (ROOT / 'dist').resolve()
                asset = (root / unquote(route.path.lstrip('/'))).resolve()
                if not asset.is_relative_to(root / 'assets') or not asset.is_file():
                    return self.send({'error': 'Not found'}, 404)
                return self.send(asset.read_bytes(), content_type=mimetypes.guess_type(str(asset))[0] or 'application/octet-stream')
            if route.path == '/api/state':
                from scenic_pipeline import catalogue
                from reddit_local import publisher
                from tiktok_local import publisher as tiktok
                with STATE_LOCK:
                    state = dict(STATE)
                return self.send({'job': state, 'batch': batch_progress(), 'reddit': publisher.snapshot(), 'tiktok': tiktok.snapshot(), 'posts': posts(),
                                  'provinces': catalogue(),
                                  'cities': json.loads((ROOT / 'city_queue.json').read_text(encoding='utf-8'))['cities']})
            if route.path == '/api/post':
                return self.send(read_post(parse_qs(route.query).get('folder', [''])[0]))
            if route.path.startswith('/media/'):
                root = (ROOT / 'city_posts').resolve()
                path = (root / unquote(route.path[len('/media/'):])).resolve()
                if not path.is_relative_to(root) or path.suffix.lower() != '.png':
                    raise ValueError('Invalid image path')
                return self.send(path.read_bytes(), content_type='image/png')
            self.send({'error': 'Not found'}, 404)
        except Exception as error:
            self.send({'error': str(error)}, 400)

    def do_POST(self):
        try:
            host = self.headers.get('Host')
            if host not in ('127.0.0.1:8765', 'localhost:8765') or self.headers.get('X-Local-Token') != TOKEN:
                return self.send({'error': 'Invalid local request'}, 403)
            if self.headers.get('Origin') not in (None, 'http://' + host, 'http://127.0.0.1:5173', 'http://localhost:5173'):
                return self.send({'error': 'Invalid origin'}, 403)
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 100000:
                raise ValueError('Invalid request size')
            data = json.loads(self.rfile.read(length))
            from reddit_local import publisher
            from tiktok_local import publisher as tiktok
            if self.path == '/api/start':
                result = start_scenic(data)
            elif self.path == '/api/batch/start':
                result = start_scenic(data)
            elif self.path == '/api/tasks/cancel':
                result = cancel_tasks()
            elif self.path == '/api/reddit/login':
                result = publisher.dispatch('login', {})
            elif self.path == '/api/reddit/prepare':
                with STATE_LOCK:
                    if STATE['status'] == 'running' or (batch_progress() or {}).get('status') == 'running':
                        raise ValueError('请等待图像生成结束，再运行本地 AI 发帖审核，以免争用显存。')
                result = publisher.dispatch('prepare', {'folder': str(post_folder(data['folder'])), 'subreddit': data.get('subreddit', 'chinatravelhelp')})
            elif self.path == '/api/reddit/publish':
                result = publisher.dispatch('publish', {'prepared_id': data['prepared_id']})
            elif self.path == '/api/reddit/mark':
                result = publisher.mark_manual(str(post_folder(data['folder'])), data['url'])
            elif self.path == '/api/tiktok/login':
                result = tiktok.dispatch('login', {})
            elif self.path == '/api/tiktok/prepare':
                if STATE['status'] == 'running' or (batch_progress() or {}).get('status') == 'running':
                    raise ValueError('请等待生成任务结束，再准备视频。')
                result = tiktok.dispatch('prepare', {'folder': str(post_folder(data['folder']))})
            elif self.path == '/api/tiktok/publish':
                result = tiktok.dispatch('publish', {'prepared_id': data['prepared_id']})
            else:
                return self.send({'error': 'Not found'}, 404)
            self.send(result)
        except Exception as error:
            self.send({'error': str(error)}, 400)

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    from city_pipeline import lock
    with lock(ROOT / 'work' / 'local_app.lock'):
        server = ThreadingHTTPServer(('127.0.0.1', 8765), Handler)
        print('本地主页：http://127.0.0.1:8765', flush=True)
        if '--no-browser' not in sys.argv:
            webbrowser.open('http://127.0.0.1:8765')
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.server_close()
