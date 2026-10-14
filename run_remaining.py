"""Sequential local generation of every unfinished city; never publishes."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
import argparse

from city_pipeline import ROOT, lock, write_json

DEFAULT_PROMPT = '清新明亮的水彩旅行插画，温暖的阳光，绿色山林与土黄色建筑。英文文案简短自然，加入有来源的本地美食。'


def run(overrides=None):
    with lock(ROOT / 'work' / 'remaining_batch.lock'):
        queue = json.loads((ROOT / 'city_queue.json').read_text(encoding='utf-8'))
        cities = [c['name_zh'] for c in queue['cities'] if not c['run_time']]
        state = {'status': 'running', 'total': len(cities), 'current': '', 'completed': [], 'failed': [],
                 'started_at': datetime.now(timezone.utc).isoformat()}
        path = ROOT / 'work' / 'remaining_batch.json'
        write_json(path, state)
        for i, city in enumerate(cities, 1):
            state['current'] = city
            write_json(path, state)
            print(f'批量进度 {i}/{len(cities)}：{city}', flush=True)
            work = ROOT / 'work' / 'city_pipeline' / city
            option_path = work / 'run_options.json'
            options = json.loads(option_path.read_text(encoding='utf-8')) if option_path.exists() else {'prompt':DEFAULT_PROMPT, 'include_food':True, 'source':'public'}
            if overrides and not option_path.exists():
                options.update(prompt=overrides.get('prompt') or DEFAULT_PROMPT,
                               include_food=overrides.get('include_food', True))
            command = [sys.executable, '-u', str(ROOT / 'city_pipeline.py'), '--city', city, '--source', 'public',
                       '--prompt', options.get('prompt',DEFAULT_PROMPT), '--run-id', uuid.uuid4().hex]
            if options.get('include_food',True):
                command.append('--include-food')
            log_path = ROOT / 'work' / 'batch_logs' / (city + '.log')
            log_path.parent.mkdir(exist_ok=True)
            lines = []
            with log_path.open('a', encoding='utf-8') as log:
                process = subprocess.Popen(command, cwd=ROOT, env=dict(os.environ,PYTHONIOENCODING='utf-8'),
                                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                                           creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                for line in process.stdout:
                    print(line,end='',flush=True);log.write(line);log.flush();lines.append(line)
                code = process.wait()
            if code == 0:
                state['completed'].append(city)
            else:
                error = ''.join(lines)[-2500:]
                state['failed'].append({'city':city,'error':error})
                if any(message in error for message in ['Another city run holds','Manual login/search did not expose','needs login/verification','BrowserType.launch_persistent_context']):
                    state['status']='needs_attention';write_json(path,state)
                    print('批量流程暂停：需要处理浏览器登录、验证或另一个运行中的任务。没有标记失败城市完成。',flush=True)
                    return 1
            write_json(path,state)
            if i < len(cities):
                time.sleep(10)
        state.update(status='finished',current='',finished_at=datetime.now(timezone.utc).isoformat())
        write_json(path,state)
        print(f"批量结束：成功 {len(state['completed'])}；失败 {len(state['failed'])}。",flush=True)
        return 0 if not state['failed'] else 1


if __name__=='__main__':
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--options', type=Path)
    parser.add_argument('--province', default=None)
    args = parser.parse_args()
    overrides = json.loads(args.options.read_text(encoding='utf-8')) if args.options else None
    from scenic_pipeline import run as run_scenic
    sys.exit(run_scenic(args.province, prompt=(overrides or {}).get('prompt', '')))
