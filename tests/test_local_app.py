import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import city_pipeline
import local_app
import reddit_local


class LocalAppTests(unittest.TestCase):
    def test_cancel_preserves_completed_files_and_marks_batch_cancelled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'city_posts' / '松阳' / 'body.txt'
            output.parent.mkdir(parents=True)
            output.write_text('existing draft', encoding='utf-8')
            city_pipeline.write_json(root / 'work' / 'remaining_batch.json', {'status':'running', 'current':'阆中', 'completed':['松阳'], 'failed':[], 'total':2})
            from subprocess import CompletedProcess
            result = CompletedProcess([], 0, '{"stopped":3}', '')
            state = {'status':'running'}
            with patch.object(local_app, 'ROOT', root), patch.object(local_app, 'STATE', state), patch.object(local_app.subprocess, 'run', return_value=result):
                self.assertEqual(local_app.cancel_tasks()['stopped'], 3)
                self.assertEqual(state['status'], 'idle')
            batch = json.loads((root / 'work' / 'remaining_batch.json').read_text(encoding='utf-8'))
            self.assertEqual(batch['status'], 'cancelled')
            self.assertEqual(batch['completed'], ['松阳'])
            self.assertEqual(output.read_text(encoding='utf-8'), 'existing draft')

    def test_cancel_failure_does_not_claim_idle(self):
        from subprocess import CompletedProcess
        state = {'status':'running'}
        with patch.object(local_app, 'STATE', state), patch.object(local_app.subprocess, 'run', return_value=CompletedProcess([], 1, '', 'blocked')):
            with self.assertRaisesRegex(ValueError, 'blocked'):
                local_app.cancel_tasks()
        self.assertEqual(state['status'], 'failed')

    def test_batch_rejects_duplicate_or_concurrent_run(self):
        with patch.object(local_app, 'batch_progress', return_value={'status': 'running'}):
            with self.assertRaisesRegex(ValueError, '已有生成任务'):
                local_app.start_remaining({})

    def test_batch_rejects_oversized_prompt(self):
        with self.assertRaisesRegex(ValueError, '2000'):
            local_app.start_remaining({'prompt': 'x' * 2001})

    def test_batch_with_no_remaining_does_not_spawn(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            city_pipeline.write_json(root / 'city_queue.json', {'cities': [{'name_zh': '松阳', 'run_time': 'completed'}]})
            with patch.object(local_app, 'ROOT', root), patch.object(local_app, 'STATE', {'status': 'idle'}), patch.object(local_app, 'batch_progress', return_value=None), patch.object(local_app.subprocess, 'Popen') as spawn:
                with self.assertRaisesRegex(ValueError, '没有剩余任务'):
                    local_app.start_remaining({})
                spawn.assert_not_called()

    def test_post_folder_cannot_escape_outputs(self):
        with self.assertRaises(ValueError):
            local_app.post_folder('../work')

    def test_reddit_payload_always_discloses_illustrations_and_requires_images(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / 'city_posts' / '松阳'
            folder.mkdir(parents=True)
            (folder / 'title.txt').write_text('Songyang', encoding='utf-8')
            (folder / 'body.txt').write_text('Travel draft.', encoding='utf-8')
            with patch.object(reddit_local, 'ROOT', root):
                with self.assertRaises(ValueError):
                    reddit_local.package(folder, 'chinatravelhelp')
                (folder / '01.png').write_bytes(b'test-image')
                item = reddit_local.package(folder, 'r/chinatravelhelp')
                self.assertTrue(item['title'].startswith('[AI illustrations]'))
                self.assertIn('not real photographs', item['body'])
                with self.assertRaises(ValueError):
                    reddit_local.package(folder, '../other')

    def test_uncertain_submission_blocks_duplicate_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            item = {'fingerprint': 'hash', 'folder': 'draft', 'subreddit': 'chinatravelhelp'}
            with patch.object(reddit_local, 'ROOT', root):
                client = reddit_local.Publisher()
                client.record(item, 'unknown')
                restarted = reddit_local.Publisher()
                with self.assertRaises(ValueError):
                    restarted.duplicate(item)

    def test_publish_without_review_is_rejected(self):
        client = reddit_local.Publisher()
        with self.assertRaises(ValueError):
            client.dispatch('publish', {'prepared_id': 'invented'})

    def test_food_not_in_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / '松阳'
            work.mkdir()
            city_pipeline.write_json(work / 'public_documents.json', [{'url': 'https://example.gov.cn/article', 'text': '松阳特色美食米面'}])
            result = {'foods': [{'name_zh': '假美食', 'name_en': 'Fake', 'description_en': 'Fake', 'source_url': 'https://example.gov.cn/article'}]}
            with patch.object(city_pipeline, 'ROOT', Path(directory)), patch.object(city_pipeline, 'ai', return_value=result):
                with self.assertRaises(ValueError):
                    city_pipeline.local_foods(work, 'test')

    def test_food_keeps_verified_source(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / '松阳'
            work.mkdir()
            city_pipeline.write_json(work / 'public_documents.json', [{'url': 'https://example.gov.cn/article', 'text': '松阳特色美食米面'}])
            result = {'foods': [{'name_zh': '米面', 'name_en': 'Rice noodles', 'description_en': 'A local food.', 'source_url': 'https://example.gov.cn/article'}]}
            with patch.object(city_pipeline, 'ROOT', Path(directory)), patch.object(city_pipeline, 'ai', return_value=result):
                self.assertIn('https://example.gov.cn/article', city_pipeline.local_foods(work, 'test'))

    def test_attraction_cannot_be_recommended_as_food(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / '松阳'
            work.mkdir()
            city_pipeline.write_json(work / 'public_documents.json', [{'url': 'https://example.gov.cn/article', 'text': '松阳特色美食，黄家大院附近'}])
            result = {'foods': [{'name_zh': '黄家大院', 'name_en': 'Courtyard', 'source_url': 'https://example.gov.cn/article'}]}
            with patch.object(city_pipeline, 'ROOT', Path(directory)), patch.object(city_pipeline, 'ai', return_value=result):
                with self.assertRaisesRegex(ValueError, 'incorrectly'):
                    city_pipeline.local_foods(work, 'test')

    def test_scenery_draft_cannot_add_unsupported_dumplings(self):
        with patch.object(city_pipeline, 'ai', return_value={'title': 'Songyang', 'body': 'Try local dumplings.'}):
            with self.assertRaisesRegex(ValueError, 'unsupported food'):
                city_pipeline.write_post('松阳', [], 'test')


if __name__ == '__main__':
    unittest.main()
