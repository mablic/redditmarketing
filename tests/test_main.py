import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main

POST = dict(title='Choose your trip', body='Which city would you choose?', short_post='One city or two?', video_script='What is your ideal trip?', claims_to_verify=[])

class GenerationTests(unittest.TestCase):
    def test_api_output_is_saved_and_sources_are_attached_by_code(self):
        response = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(POST)}}], 'usage': {'total_tokens': 42}}
        candidate = {'topic': 'China trip', 'notes': 'Unverified source summary', 'source_url': 'https://example.org/source'}
        with patch.dict(main.os.environ, {'DEEPSEEK_API_KEY': 'test', 'LLM_BASE_URL': 'https://api.deepseek.com'}), patch.object(main, 'request_json', return_value=response) as call:
            post, usage = main.generate(candidate, 'en')
            self.assertEqual(call.call_args.args[1]['thinking'], {'type': 'disabled'})
        with tempfile.TemporaryDirectory() as directory:
            folder = main.save(post, candidate, usage, Path(directory))
            self.assertIn(candidate['source_url'], (folder / 'body.txt').read_text())
            self.assertEqual(json.loads((folder / 'post.json').read_text())['review_status'], 'needs_review')

    def test_truncated_response_is_rejected(self):
        response = {'choices': [{'finish_reason': 'length', 'message': {'content': json.dumps(POST)}}]}
        with patch.dict(main.os.environ, {'DEEPSEEK_API_KEY': 'test'}), patch.object(main, 'request_json', return_value=response):
            with self.assertRaises(ValueError):
                main.generate({'topic': 'China'}, 'en')

    def test_invented_links_and_wrong_claim_types_are_rejected(self):
        for bad in [dict(POST, body='See https://invented.example'), dict(POST, claims_to_verify='none')]:
            with self.assertRaises(ValueError):
                main.validate(bad)

    def test_rss_and_atom_candidates(self):
        feeds = [b'<rss><channel><item><title>Shanghai walks</title><link>https://example.org/a</link><description>Travel</description></item><item><title>Other news</title></item></channel></rss>',
                 b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Beijing trip</title><link href="https://example.org/b"/><summary>Travel</summary></entry></feed>']
        for feed in feeds:
            with patch.object(main, 'urlopen') as opening:
                opening.return_value.__enter__.return_value.read.return_value = feed
                items = main.rss_topics('https://example.org/feed')
                self.assertEqual(len(items), 1)
                self.assertTrue(items[0]['source_url'].startswith('https://example.org/'))

if __name__ == '__main__':
    unittest.main()
