import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from comfy_images import Comfy, load_config


class ComfyConfigTests(unittest.TestCase):
    def test_reads_env_file_and_fills_blank_defaults(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict('comfy_images.os.environ', {}, clear=True):
            env = Path(temp) / '.env'
            env.write_text('DEEPSEEK_API_KEY=x\n# comment\nCOMFY_PYTHON="C:/comfy/python.exe"\n'
                           'COMFY_CHECKPOINT=sd15.safetensors\nCOMFY_PERSON_MODEL=C:/m/yolo.pt\nCOMFY_INPUT_DIR=\n', encoding='utf-8')
            config = load_config(env)
        self.assertEqual(config['python'], 'C:/comfy/python.exe')
        self.assertEqual(config['url'], 'http://127.0.0.1:8188')
        self.assertTrue(config['input_dir'].endswith('comfy-input'))

    def test_missing_required_setting_is_reported(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict('comfy_images.os.environ', {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'COMFY_PYTHON'):
                load_config(Path(temp) / '.env')


class FakeComfy(Comfy):
    """Simulates a ComfyUI server that was restarted and lost all earlier prompts."""

    def __init__(self, config, queued_polls=0):
        super().__init__(config)
        self.known = {}
        self.submitted = []
        self.queued_polls = queued_polls

    def detect(self, path, output=None):
        if output:
            Path(output).write_bytes(b'mask')
        return {'person_count': 0}

    def upload(self, path, name):
        (Path(self.config['input_dir']) / name).write_bytes(b'mask')
        return name

    def request(self, route, payload=None):
        if route == '/prompt':
            prompt_id = f'new-{len(self.submitted)}'
            self.submitted.append(prompt_id)
            self.known[prompt_id] = {'status': {'status_str': 'success'},
                                     'outputs': {'13': {'images': [{'filename': 'out.png', 'subfolder': '', 'type': 'output'}]}}}
            return {'prompt_id': prompt_id}
        if route == '/queue':
            running = []
            if self.queued_polls and self.submitted:
                self.queued_polls -= 1
                running = [[0, self.submitted[-1], {}, {}, []]]
            return {'queue_running': running, 'queue_pending': []}
        if route.startswith('/history/'):
            prompt_id = route.rsplit('/', 1)[1]
            return {prompt_id: self.known[prompt_id]} if prompt_id in self.known else {}
        raise AssertionError(route)


class ComfyRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.work = root / 'city'
        (self.work / 'source_images').mkdir(parents=True)
        (self.work / 'generated').mkdir()
        self.input_dir = root / 'comfy_input'
        self.input_dir.mkdir()
        reference = self.work / 'source_images' / 'ref.jpg'
        reference.write_bytes(b'reference')
        self.task = {'id': 4, 'name_zh': '中天楼', 'prompt': 'tower',
                     'reference_image': str(reference), 'output_file': str(self.work / 'generated' / '04.png')}
        self.config = {'url': 'http://127.0.0.1:8188', 'checkpoint': 'model.safetensors', 'input_dir': str(self.input_dir), 'output_dir': str(root / 'out')}

    def tearDown(self):
        self.temp.cleanup()

    def stale_record(self, client):
        import hashlib
        fingerprint = hashlib.sha256(b'reference' + b'tower').hexdigest()
        stale_upload = 'city_pipeline_old.png'
        (self.input_dir / stale_upload).write_bytes(b'old')
        (self.work / 'comfy-04.json').write_text(json.dumps({
            'prompt_id': 'lost-before-restart', 'uploaded': stale_upload, 'prefix': 'city_pipeline_old',
            'completed': False, 'task_fingerprint': fingerprint}), encoding='utf-8')

    def run_generate(self, client):
        with patch('comfy_images.urlopen') as opening, patch('comfy_images.time.sleep'):
            opening.return_value.__enter__.return_value.read.return_value = b'png'
            client.generate(self.task, self.work)

    def test_prompt_lost_after_restart_is_resubmitted(self):
        client = FakeComfy(self.config)
        self.stale_record(client)
        self.run_generate(client)
        record = json.loads((self.work / 'comfy-04.json').read_text(encoding='utf-8'))
        self.assertEqual(client.submitted, ['new-0'])
        self.assertEqual(record['prompt_id'], 'new-0')
        self.assertTrue(record['completed'])
        self.assertFalse((self.input_dir / 'city_pipeline_old.png').exists())
        self.assertTrue(Path(self.task['output_file']).exists())

    def test_queued_prompt_is_awaited_not_resubmitted(self):
        client = FakeComfy(self.config, queued_polls=3)
        self.run_generate(client)
        self.assertEqual(client.submitted, ['new-0'])


class ComfyResponseTests(unittest.TestCase):
    def test_empty_success_response_from_free_and_cleanup_is_accepted(self):
        with patch('comfy_images.urlopen') as opening:
            opening.return_value.__enter__.return_value.read.return_value = b''
            client = Comfy({'url': 'http://127.0.0.1:8188'})
            self.assertEqual(client.request('/free', {'unload_models': True}), {})

    def test_remote_comfy_endpoint_rejected(self):
        with self.assertRaises(ValueError):
            Comfy({'url': 'https://example.org'})
