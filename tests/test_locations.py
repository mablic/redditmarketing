import json
import unittest
from locations import ROOT, city_from_folder, location


class LocationTests(unittest.TestCase):
    def test_every_queued_city_has_full_location(self):
        cities = json.loads((ROOT / 'city_queue.json').read_text(encoding='utf-8'))['cities']
        for city in cities:
            english, chinese = location(city['name_zh'])
            self.assertIn(city['province_en'], english)
            self.assertIn(city['prefecture_en'], english)
            self.assertIn(city['administrative_name'], chinese)

    def test_autonomous_prefecture_is_not_mislabeled_as_city(self):
        _, chinese = location('建水')
        self.assertIn('红河哈尼族彝族自治州', chinese)
        self.assertNotIn('红河市', chinese)

    def test_illustrated_version_resolves_to_same_city(self):
        self.assertEqual(city_from_folder(ROOT / 'city_posts' / '松阳_美化版'), '松阳')

    def test_unknown_location_is_not_invented(self):
        with self.assertRaises(ValueError):
            location('不存在的城市')
