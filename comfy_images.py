"""Local ComfyUI image inpainting and illustration adapter. No cloud nodes."""
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
import uuid
import os

ROOT = Path(__file__).resolve().parent
CONFIG_KEYS = {'url': 'COMFY_URL', 'python': 'COMFY_PYTHON', 'checkpoint': 'COMFY_CHECKPOINT',
               'person_model': 'COMFY_PERSON_MODEL', 'input_dir': 'COMFY_INPUT_DIR', 'output_dir': 'COMFY_OUTPUT_DIR'}


def load_config(env_path=ROOT / '.env'):
    """Read COMFY_* settings; process environment variables override .env."""
    from local_env import read_env
    values = read_env(env_path)
    values.update({key: os.environ[key] for key in CONFIG_KEYS.values() if os.environ.get(key)})
    defaults = {'COMFY_URL': 'http://127.0.0.1:8188', 'COMFY_INPUT_DIR': str(ROOT / 'work' / 'comfy-input'),
                'COMFY_OUTPUT_DIR': str(ROOT / 'work' / 'comfy-output')}
    for key, default in defaults.items():
        values[key] = values.get(key) or default
    missing = [key for key in CONFIG_KEYS.values() if not values.get(key)]
    if missing:
        raise ValueError('Set these ComfyUI settings in .env first: ' + ', '.join(missing))
    return {name: values[key] for name, key in CONFIG_KEYS.items()}


class Comfy:
    def __init__(self, config):
        self.config = config
        self.base = config['url'].rstrip('/')
        parsed = urlparse(self.base)
        if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1'):
            raise ValueError('ComfyUI must be a localhost HTTP service for this pipeline.')

    def request(self, route, payload=None):
        headers = {}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            headers['Content-Type'] = 'application/json'
        with urlopen(Request(self.base + route, data=data, headers=headers), timeout=60) as response:
            raw = response.read()
        return json.loads(raw) if raw.strip() else {}

    def upload(self, path, name):
        boundary = 'city-' + uuid.uuid4().hex
        data = (f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{name}"\r\nContent-Type: image/png\r\n\r\n'.encode() + path.read_bytes() +
                f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="type"\r\n\r\ninput\r\n--{boundary}--\r\n'.encode())
        request = Request(self.base + '/upload/image', data=data,
                          headers={'Content-Type': 'multipart/form-data; boundary=' + boundary})
        with urlopen(request, timeout=60) as response:
            uploaded = json.load(response)
        return (uploaded.get('subfolder', '') + '/' + uploaded['name']).lstrip('/')

    def graph(self, uploaded, prompt, seed, prefix):
        node = lambda kind, **inputs: {'class_type': kind, 'inputs': inputs}
        style = self.config.get('illustration', {})
        positive = style.get('prompt', 'empty landscape, no people, original editorial travel illustration, painted gouache, coherent architecture, ') + prompt
        negative = 'person, people, human, man, woman, child, crowd, face, body, portrait, text, watermark, logo, distorted architecture'
        return {
            '1': node('LoadImage', image=uploaded),
            '2': node('CheckpointLoaderSimple', ckpt_name=self.config['checkpoint']),
            '3': node('CLIPTextEncode', text='empty landscape and architecture, restore background, nobody', clip=['2', 1]),
            '4': node('CLIPTextEncode', text=negative, clip=['2', 1]),
            '5': node('VAEEncodeForInpaint', pixels=['1', 0], vae=['2', 2], mask=['1', 1], grow_mask_by=12),
            '6': node('KSampler', model=['2', 0], positive=['3', 0], negative=['4', 0], latent_image=['5', 0],
                      seed=seed, steps=24, cfg=6.5, sampler_name='euler', scheduler='normal', denoise=1.0),
            '7': node('VAEDecode', samples=['6', 0], vae=['2', 2]),
            '8': node('ImageCompositeMasked', destination=['1', 0], source=['7', 0], mask=['1', 1], x=0, y=0, resize_source=False),
            '9': node('VAEEncode', pixels=['8', 0], vae=['2', 2]),
            '10': node('CLIPTextEncode', text=positive, clip=['2', 1]),
            '11': node('KSampler', model=['2', 0], positive=['10', 0], negative=['4', 0], latent_image=['9', 0],
                       seed=seed+1, steps=style.get('steps', 24), cfg=style.get('cfg', 6.0),
                       sampler_name=style.get('sampler', 'euler'), scheduler=style.get('scheduler', 'normal'),
                       denoise=style.get('denoise', 0.3)),
            '12': node('VAEDecode', samples=['11', 0], vae=['2', 2]),
            '13': node('SaveImage', images=['12', 0], filename_prefix=prefix),
        }

    def detect(self, path, output=None):
        command = [self.config['python'], '-s', str(ROOT / 'person_mask.py'), '--input', str(path),
                   '--model', self.config['person_model']]
        command += ['--output', str(output)] if output else ['--check']
        environment = dict(os.environ, YOLO_CONFIG_DIR=str(Path(self.config['person_model']).parent / 'yolo-config'))
        result = subprocess.run(command, check=True, capture_output=True, text=True, encoding='utf-8', env=environment)
        for line in reversed(result.stdout.splitlines()):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                pass
        raise ValueError('Local person detector returned no result.')

    def generate(self, task, work):
        target = Path(task['output_file']).resolve()
        if target.parent != (work / 'generated').resolve():
            raise ValueError('Unexpected generated image destination.')
        record_path = work / f"comfy-{task['id']:02d}.json"
        old = json.loads(record_path.read_text(encoding='utf-8')) if record_path.exists() else None
        import hashlib
        reference = Path(task['reference_image']).resolve()
        if reference.parent != (work / 'source_images').resolve():
            raise ValueError('Reference image must be a downloaded image for this city.')
        fingerprint = hashlib.sha256(reference.read_bytes() + task['prompt'].encode('utf-8')).hexdigest()
        if old and old.get('task_fingerprint') != fingerprint:
            old = None
        if old and old.get('completed'):
            if target.exists():
                return
        reference = Path(task['reference_image']).resolve()
        if reference.parent != (work / 'source_images').resolve():
            raise ValueError('Reference image must be a downloaded image for this city.')
        if old and old.get('prompt_id'):
            record = old
        else:
            record = self.submit(task, reference, work, fingerprint, record_path)
        prompt_id = record['prompt_id']
        resubmissions = 0
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            if self.queued(prompt_id):
                time.sleep(2)
                continue
            history = self.request('/history/' + prompt_id)
            entry = history.get(prompt_id)
            if not entry:
                if resubmissions >= 2:
                    raise ValueError('ComfyUI keeps losing the render (server restarted?); intermediate data retained.')
                resubmissions += 1
                print(f"ComfyUI 已无此任务（可能重启过），重新提交：{task['name_zh']}", flush=True)
                self.remove_upload(record)
                record = self.submit(task, reference, work, fingerprint, record_path)
                prompt_id = record['prompt_id']
                deadline = time.monotonic() + 900
                continue
            if entry.get('status', {}).get('status_str') == 'error':
                raise ValueError('ComfyUI workflow failed; intermediate data retained.')
            outputs = entry.get('outputs', {}).get('13', {}).get('images', [])
            if outputs:
                image = outputs[0]
                with urlopen(self.base + '/view?' + urlencode(image), timeout=60) as response:
                    raw = response.read(30_000_001)
                if len(raw) > 30_000_000:
                    raise ValueError('Generated image is unexpectedly large.')
                target.write_bytes(raw)
                checked = self.detect(target)
                record.update(server_image=image, detected_people_in_output=checked['person_count'])
                if checked['person_count']:
                    record_path.write_text(json.dumps(record, indent=2), encoding='utf-8')
                    raise ValueError('A person may remain in the generated image; do not finalize. Inspect or regenerate this image.')
                record['completed'] = True
                record_path.write_text(json.dumps(record, indent=2), encoding='utf-8')
                return
            time.sleep(2)
        raise ValueError('ComfyUI render timed out; prompt ID retained for recovery, not automatically resubmitted.')

    def submit(self, task, reference, work, fingerprint, record_path):
        masked = work / f"mask-{task['id']:02d}.png"
        mask_result = self.detect(reference, masked)
        prefix = 'city_pipeline_' + uuid.uuid4().hex
        uploaded = self.upload(masked, prefix + '.png')
        response = self.request('/prompt', {'prompt': self.graph(uploaded, task['prompt'], 42 + task['id'], prefix), 'client_id': prefix})
        if response.get('node_errors') or not response.get('prompt_id'):
            raise ValueError('ComfyUI rejected the local workflow: ' + str(response.get('node_errors', response)))
        record = {'prompt_id': response['prompt_id'], 'uploaded': uploaded, 'prefix': prefix,
                  'people_detected_in_reference': mask_result['person_count'], 'completed': False,
                  'task_fingerprint': fingerprint}
        record_path.write_text(json.dumps(record, indent=2), encoding='utf-8')
        return record

    def queued(self, prompt_id):
        """Queue must be checked before history: finished prompts land in history before leaving the queue."""
        queue = self.request('/queue')
        return any(len(item) > 1 and item[1] == prompt_id
                   for item in queue.get('queue_running', []) + queue.get('queue_pending', []))

    def remove_owned(self, root, relative, prefix):
        if not prefix.startswith('city_pipeline_'):
            raise ValueError('Unexpected Comfy cleanup prefix.')
        if not relative:
            return
        path = (root / relative).resolve()
        if not path.is_relative_to(root.resolve()) or not path.name.startswith(prefix):
            raise ValueError('Unexpected Comfy cleanup path.')
        path.unlink(missing_ok=True)

    def remove_upload(self, record):
        self.remove_owned(Path(self.config['input_dir']), record.get('uploaded', ''), record['prefix'])

    def cleanup(self, work):
        """Remove only our uploaded/rendered files after the final folder is safe."""
        for record_path in work.glob('comfy-*.json'):
            record = json.loads(record_path.read_text(encoding='utf-8'))
            prefix = record['prefix']
            server_image = record.get('server_image', {})
            self.remove_owned(Path(self.config['input_dir']), record['uploaded'], prefix)
            self.remove_owned(Path(self.config['output_dir']),
                              (server_image.get('subfolder', '') + '/' + server_image.get('filename', '')).lstrip('/'), prefix)
            self.request('/history', {'delete': [record['prompt_id']]})


def generate_tasks(work, config):
    client = Comfy(config)
    client.request('/system_stats')
    tasks = json.loads((work / 'image_tasks.json').read_text(encoding='utf-8'))['tasks']
    for task in tasks:
        print('ComfyUI 本地图像生成：' + task['name_zh'], flush=True)
        client.generate(task, work)
