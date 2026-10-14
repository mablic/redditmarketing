import contextlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import scenic_pipeline as pipeline
import reddit_local


class DummyBrowser(contextlib.nullcontext):
    def baike(self, *args):
        return None


class ScenicTests(unittest.TestCase):
    def test_catalogue_has_all_regions_and_first_beijing(self):
        regions = pipeline.catalogue()
        self.assertEqual(len(regions), 32)
        self.assertEqual(sum(len(p['attractions']) for p in regions), 359)
        self.assertEqual(regions[0]['province'], '北京')
        self.assertEqual([(c['city'], len(c['attraction_ids'])) for c in regions[0]['cities']], [('北京', 9)])

    def test_every_attraction_resolves_to_a_city(self):
        unresolved = [(p['province'], x['name_zh']) for p in pipeline.catalogue() for x in p['attractions'] if x['city'] == '其他']
        self.assertEqual(unresolved, [])

    def test_pending_cities_skips_finished_drafts(self):
        data = {'province': '测试', 'attractions': [
            {'id': '1', 'name_zh': '金华市双龙风景旅游区'}, {'id': '2', 'name_zh': '丽水市云和梯田景区'}],
                'city_runs': {'金华市': {'status': 'draft_ready'}}}
        with patch.object(pipeline, 'catalogue', return_value=[{'province': '测试'}]), \
                patch.object(pipeline, 'get_province', return_value=(Path('province.json'), data)):
            pending = pipeline.pending_cities()
        self.assertEqual([city for _, _, city in pending], ['丽水市'])

    def test_city_rules(self):
        cases = [('浙江', '金华市双龙风景旅游区', '金华市'), ('湖北', '恩施州腾龙洞景区', '恩施州'),
                 ('海南', '保亭县呀诺达雨林文化旅游区', '保亭县'), ('重庆', '大足石刻景区', '重庆'),
                 ('山西', '黄河壶口瀑布旅游区(陕西省延安市·山西省临汾市)', '临汾市'),
                 ('陕西', '黄河壶口瀑布旅游区(陕西省延安市·山西省临汾市)', '延安市'),
                 ('湖北', '湖北省武汉市黄鹤楼公园', '武汉市'), ('吉林', '延边朝鲜族自治州长白山景区', '延边州')]
        for province, name, city in cases:
            self.assertEqual(pipeline.city_of(province, {'name_zh': name}), city)
        self.assertEqual(pipeline.short_name('金华市双龙风景旅游区'), '双龙')

    def test_photo_match_needs_distinctive_name(self):
        from scenic_images import keywords, matches
        cave = keywords('Shuanglong Cave', '金华市双龙风景旅游区')
        self.assertFalse(matches('fog covered mountains, karst cave, china', cave))
        self.assertTrue(matches('Entrance of Shuanglong scenic area, Jinhua', cave))
        self.assertTrue(matches('Boats near the Summer Palace', keywords('Summer Palace', '颐和园')))
        self.assertFalse(matches('summer day at a lake', keywords('Summer Palace', '颐和园')))

    def test_clean_text_rejects_visits_and_strips_chinese_names(self):
        self.assertEqual(pipeline.clean_text('The Hall of Prayer (祈年殿) is central.'), 'The Hall of Prayer is central.')
        self.assertEqual(pipeline.clean_text('Prince Gong, 奕訢, lived in this mansion for decades.'),
                         'Prince Gong, lived in this mansion for decades.')
        with self.assertRaises(ValueError):
            pipeline.clean_text('When I visited, it rained.')

    def test_compose_explains_5a_without_ai_statement(self):
        items = [{'id': 'a', 'name_zh': '颐和园', 'rating_year': '2007'},
                 {'id': 'b', 'name_zh': '圆明园遗址公园景区', 'rating_year': '2019', 'rating_year_disputed': True}]
        researched = {'a': {'name_en': 'Summer Palace', 'sources': [{'url': 'https://en.wikipedia.org/wiki/Summer_Palace'}]},
                      'b': {'name_en': 'Old Summer Palace', 'sources': [{'url': 'https://en.wikipedia.org/wiki/Old_Summer_Palace'}]}}
        sections = {x: {'history_en': 'History text.', 'why_5a_en': 'Why text.', 'highlights_en': ['Long Corridor - painted beams']} for x in 'ab'}
        photos = [{'source': 'unsplash', 'credit': 'Photo by A on Unsplash'}, {'source': 'ai_local'}]
        title, body = pipeline.compose('北京', 'Beijing', items, researched, sections, photos, '2025-03-11', 'https://example.gov.cn')
        self.assertTrue(title.startswith('Beijing: 2 of China'))
        self.assertIn('GB/T 17775', body)
        self.assertIn('Rated 5A in 2007.', body)
        self.assertNotIn('2019', body)
        self.assertIn('Photo 1: Photo by A on Unsplash', body)
        self.assertNotIn('Photo 2', body)
        self.assertNotIn('AI', body.replace('MAIN', ''))

    def test_city_run_without_llm_or_images_writes_one_post(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, data = pipeline.get_province('北京')
            data = json.loads(json.dumps(data))
            researched = {'sources': [], 'name_en': '', 'errors': []}
            with patch.object(pipeline, 'ROOT', root), patch.object(pipeline, 'WORK', root / 'work'), \
                    patch('scenic_images.BaiduBrowser', DummyBrowser), \
                    patch.object(pipeline, 'research', side_effect=lambda *a: dict(researched)):
                output, count = pipeline.run_city(root / 'p.json', data, '北京', use_llm=False, search_images=False)
            meta = json.loads((output / 'post.json').read_text(encoding='utf-8'))
            self.assertEqual(count, 0)
            self.assertEqual(meta['kind'], '5a_city')
            self.assertEqual(len(meta['attractions']), 9)
            self.assertEqual(data['city_runs']['北京']['status'], 'draft_without_images')
            self.assertIn('Beijing', (output / 'title.txt').read_text(encoding='utf-8'))

    def test_city_post_with_ai_image_has_no_disclosure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); folder = root / 'city_posts/5a/北京/北京_v1'; folder.mkdir(parents=True)
            (folder / 'title.txt').write_text('Beijing: 9 of China\'s top-rated (5A) scenic areas', encoding='utf-8')
            (folder / 'body.txt').write_text('Location: Beijing, China.\n\nHistory.', encoding='utf-8')
            (folder / '01.png').write_bytes(b'image')
            (folder / 'post.json').write_text(json.dumps({'kind': '5a_city', 'images': [{'source': 'ai_local', 'file': '01.png'}]}), encoding='utf-8')
            with patch.object(reddit_local, 'ROOT', root):
                result = reddit_local.package(folder, 'chinatravelhelp')
            self.assertFalse(result['title'].startswith('[AI'))
            self.assertNotIn('AI-generated', result['body'])

    def test_licensed_photo_missing_license_cannot_be_prepared(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); folder = root / 'city_posts/5a/test'; folder.mkdir(parents=True)
            (folder / 'title.txt').write_text('Test', encoding='utf-8')
            (folder / 'post.json').write_text(json.dumps({'kind': '5a_city', 'images': [{'source': 'unsplash'}]}), encoding='utf-8')
            with patch.object(reddit_local, 'ROOT', root):
                with self.assertRaisesRegex(ValueError, '许可'):
                    reddit_local.package(folder, 'chinatravelhelp')


if __name__ == '__main__':
    unittest.main()
