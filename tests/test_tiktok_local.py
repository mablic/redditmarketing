from pathlib import Path
import unittest
from tiktok_local import make_video, TikTokPublisher


class TikTokTests(unittest.TestCase):
    def test_outside_folder_is_not_exported(self):
        with self.assertRaises(ValueError):
            make_video(Path('work'))

    def test_posting_requires_a_prepared_reviewed_payload(self):
        with self.assertRaises(ValueError):
            TikTokPublisher().dispatch('publish', {'prepared_id': 'unknown'})
