import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
from storage import Storage
from test_main import POST


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Storage(self.root / 'data' / 'drafts.sqlite3')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def add(self, topic='China trip', url='', demo=False):
        candidate = dict(topic=topic, source_url=url, notes='')
        with self.store.generation(candidate, demo=demo):
            folder = main.save(POST, candidate, {}, self.root / 'drafts', demo)
            self.store.add(folder, json.loads((folder / 'post.json').read_text(encoding='utf-8')))
        return folder.name

    def test_history_survives_reopen_and_publication_is_idempotent(self):
        draft_id = self.add()
        self.store.close()
        self.store = Storage(self.root / 'data' / 'drafts.sqlite3')
        self.store.mark_published(draft_id, 'https://example.org/post')
        first = self.store.list('published')[0]
        self.store.mark_published(draft_id)
        second = self.store.list('published')[0]
        self.assertEqual(first['published_at'], second['published_at'])
        self.assertEqual(second['post_url'], 'https://example.org/post')
        self.assertEqual(self.store.list('draft'), [])

    def test_normalized_topic_and_source_are_duplicates(self):
        self.add(url='https://example.org/article/#section')
        for candidate in [dict(topic='  CHINA   trip ', source_url=''),
                          dict(topic='Different title', source_url='https://EXAMPLE.org/article')]:
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                with self.store.generation(candidate):
                    self.fail('Duplicate passed')
        with self.store.generation(dict(topic='China trip'), allow_duplicate=True):
            pass

    def test_old_and_demo_drafts_do_not_block(self):
        self.add(demo=True)
        draft_id = self.add()
        with self.store.db:
            self.store.db.execute("UPDATE drafts SET created_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (draft_id,))
        self.add()

    def test_unknown_demo_and_invalid_url_are_rejected(self):
        demo_id = self.add(demo=True)
        for draft_id, url in [('missing', None), (demo_id, None), (demo_id, 'file:///secret')]:
            with self.assertRaises(ValueError):
                self.store.mark_published(draft_id, url)

    def test_cli_duplicate_is_blocked_before_model_call(self):
        self.add()
        argv = ['main.py', '--topic', 'China trip', '--db', str(self.root / 'data' / 'drafts.sqlite3')]
        with patch.object(sys, 'argv', argv), patch.object(main, 'generate') as generate:
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                main.main()
            generate.assert_not_called()

    def test_failed_generation_does_not_reserve_topic(self):
        with self.assertRaises(RuntimeError):
            with self.store.generation(dict(topic='New trip')):
                raise RuntimeError('Model failed')
        self.add(topic='New trip')

    def test_cli_generate_list_and_mark(self):
        db = str(self.root / 'cli.sqlite3')
        argv = ['main.py', '--topic', 'Shanghai walks', '--db', db,
                '--out', str(self.root / 'cli-drafts')]
        with patch.object(sys, 'argv', argv), patch.object(main, 'generate', return_value=(POST, {})), patch('builtins.print'):
            main.main()
        cli_store = Storage(db)
        try:
            draft_id = cli_store.list()[0]['id']
            with patch('builtins.print'):
                main.history_command(['mark-published', draft_id, 'https://example.org/post', '--db', db])
            with patch('builtins.print') as output:
                main.history_command(['list-drafts', '--status', 'published', '--db', db])
                self.assertIn(draft_id, output.call_args_list[0].args[0])
            self.assertEqual(cli_store.list('published')[0]['post_url'], 'https://example.org/post')
        finally:
            cli_store.close()


if __name__ == '__main__':
    unittest.main()
