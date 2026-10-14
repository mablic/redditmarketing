import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import city_pipeline as pipeline


class CityWorkflowTests(unittest.TestCase):
    def test_other_city_sight_rejected_even_if_model_accepts(self):
        item = {'name_zh': '老阴山', 'description_zh': '个旧的老阴山可以俯瞰全城。'}
        with patch.object(pipeline, 'ai', return_value={'belongs': True}) as model:
            self.assertFalse(pipeline.verify_city_source('石屏', item, '石屏有异龙湖。个旧的主要景点是老阴山。', 'test'))
            model.assert_not_called()

    def test_verified_evidence_is_copied_from_source(self):
        item = {'name_zh': '观音寺巷', 'description_zh': '石屏古城的一条街巷。'}
        source = '石屏赶集地点：石屏古城及观音寺巷等街巷。'
        with patch.object(pipeline, 'ai', return_value={'belongs': True}):
            self.assertTrue(pipeline.verify_city_source('石屏', item, source, 'test'))
        self.assertIn(item['city_evidence'], source)
        self.assertEqual(item['verified_city'], '石屏')

    def test_queue_uses_first_empty_time(self):
        queue = {'cities': [{'name_zh': '建水', 'run_time': '2026-10-13'},
                            {'name_zh': '靖西', 'run_time': ''}, {'name_zh': '松阳', 'run_time': ''}]}
        self.assertEqual(pipeline.first_city(queue)['name_zh'], '靖西')

    def test_controller_cannot_switch_city(self):
        with patch.object(pipeline, 'ai', return_value={'city': '靖西', 'query': '靖西旅游', 'candidate_limit': 5}):
            with self.assertRaises(ValueError):
                pipeline.plan({'name_zh': '建水', 'run_time': ''}, 'test')

    def test_lock_survives_old_lock_file_and_prevents_concurrency(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'lock'
            path.write_text('old process')
            with pipeline.lock(path):
                with self.assertRaises(ValueError):
                    with pipeline.lock(path):
                        self.fail('Second writer acquired the lock')
            with pipeline.lock(path):
                pass

    def test_hallucinated_selection_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            candidates = [{'id': i, 'name_zh': str(i), 'images': []} for i in range(1, 6)]
            with patch.object(pipeline, 'ai', return_value={'selected_ids': [1, 99], 'reason_zh': 'test'}):
                with self.assertRaises(ValueError):
                    pipeline.select(Path(directory), candidates, 'test')

    def test_missing_image_does_not_complete_or_delete_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / 'work' / 'city_pipeline' / '建水'
            work.mkdir(parents=True)
            pipeline.write_json(work / 'selected.json', [{'id': 1}])
            pipeline.write_json(work / 'image_tasks.json', {'tasks': [{'id': 1, 'output_file': str(work / 'generated' / '01.png')}]})
            queue_path = root / 'city_queue.json'
            queue = {'cities': [{'name_zh': '建水', 'run_time': ''}]}
            pipeline.write_json(queue_path, queue)
            with self.assertRaises(ValueError):
                pipeline.finalize(work, queue_path, 'test', root / 'city_posts')
            self.assertEqual(json.loads(queue_path.read_text(encoding='utf-8')), queue)
            self.assertTrue(work.exists())

    def test_cleanup_is_limited_to_exact_city_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / '建水'
            other = root / '靖西'
            work.mkdir()
            other.mkdir()
            with self.assertRaises(ValueError):
                pipeline.cleanup_city(root, root)
            pipeline.cleanup_city(work, root)
            self.assertTrue(other.exists())

    def test_final_success_exports_only_text_and_images(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / 'work' / 'city_pipeline' / '建水'
            (work / 'generated').mkdir(parents=True)
            selected = [{'id': 1, 'name_zh': '测试景点', 'source_url': 'https://example.gov.cn/article'}]
            pipeline.write_json(work / 'selected.json', selected)
            image = work / 'generated' / '01.png'
            Image.new('RGB', (32, 32), 'blue').save(image)
            pipeline.write_json(work / 'image_tasks.json', {'tasks': [{'id': 1, 'output_file': str(image)}]})
            queue_path = root / 'city_queue.json'
            pipeline.write_json(queue_path, {'cities': [{'name_zh': '建水', 'run_time': ''}]})
            with patch.object(pipeline, 'ROOT', root), patch.object(pipeline, 'write_post', return_value={'title': 'Explore Jianshui', 'body': 'A discussion draft.', 'chinese_text': '中文说明'}):
                destination = pipeline.finalize(work, queue_path, 'test', root / 'city_posts')
            self.assertEqual({p.suffix for p in destination.iterdir()}, {'.txt', '.png'})
            self.assertIn(selected[0]['source_url'], (destination / 'body.txt').read_text(encoding='utf-8'))
            self.assertTrue(json.loads(queue_path.read_text(encoding='utf-8'))['cities'][0]['run_time'])
            self.assertFalse(work.exists())

    def test_chinese_final_post_rejected_before_export(self):
        with patch.object(pipeline, 'ai', return_value={'title': '建水', 'body': '中文正文'}):
            with self.assertRaises(ValueError):
                pipeline.write_post('建水', [], 'test')

    def test_invented_trip_experience_rejected_before_export(self):
        with patch.object(pipeline, 'ai', return_value={'title': 'Jianshui', 'body': 'I visited these places last week.'}):
            with self.assertRaises(ValueError):
                pipeline.write_post('建水', [], 'test')


if __name__ == '__main__':
    unittest.main()
