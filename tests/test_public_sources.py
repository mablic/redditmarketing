import json
import unittest
from unittest.mock import patch
import city_pipeline
import public_sources


class PublicSourcesTests(unittest.TestCase):
    def test_discovery_rejects_attractions_not_in_official_evidence(self):
        result = {'attractions': [{'name_zh': '不存在的景点', 'name_en': 'Fake', 'facts_en': ''}]}
        with patch.object(public_sources, 'catalogue_documents', return_value=[{'kind': 'official', 'url': 'https://example.gov.cn/article', 'text': '靖西 鹅泉'}]):
            with self.assertRaisesRegex(ValueError, '核验'):
                public_sources.discover_city('靖西', 'test', lambda *args: result)

    def test_catalogue_does_not_fetch_unapproved_hosts(self):
        hub = '<a href="https://untrusted.example/csxz2022/zhejiang.html">浙江</a>'.encode()
        with patch.object(public_sources, 'fetch', return_value=hub) as read:
            with self.assertRaisesRegex(ValueError, '入口'):
                public_sources.catalogue_documents('松阳')
        self.assertEqual(read.call_count, 1)

    def test_catalogue_resolves_live_page_category_and_filters_city(self):
        replies = [ '<a href="/csxz2022/zhejiang.html">浙江</a>'.encode(),
                    b'<script>var x = {category_id : 171};</script>',
                    json.dumps({'more': False, 'list': [{'link': '/csxz2022/zhejiang/detail/1645.html'}, {'link': 'https://untrusted.example/detail/x'}]}).encode(),
                    '<div class="article-cont">松阳 杨家堂村</div>'.encode() ]
        with patch.object(public_sources, 'fetch', side_effect=replies) as read:
            docs = public_sources.catalogue_documents('松阳')
        self.assertEqual(len(docs), 1)
        self.assertIn('category_id=171', read.call_args_list[2].args[0])

    def test_official_parses_hangzhou_article_template(self):
        self.assertEqual(public_sources.article('<div class="zhengwen">黄家大院介绍</div>'.encode(), 'official'), '黄家大院介绍')

    def test_specific_article_container_wins_over_navigation_content(self):
        html = '<div class="content">导航首页</div><div class="content-info">松阳煨盐鸡介绍</div>'.encode()
        self.assertEqual(public_sources.article(html, 'official'), '松阳煨盐鸡介绍')

    def test_article_extracts_content_without_login_chrome(self):
        raw = '<nav>登录</nav><div class="travels_detail"><p>建水文庙介绍</p><script>secret</script></div>'.encode()
        self.assertEqual(public_sources.article(raw, 'ctrip'), '建水文庙介绍')

    def test_login_only_page_is_rejected(self):
        with self.assertRaises(ValueError):
            public_sources.article(b'<html>login required</html>', 'ctrip')

    def test_unlicensed_image_not_used_for_adaptation(self):
        record = {'query': {'pages': {'1': {'imageinfo': [{
            'url': 'https://upload.wikimedia.org/example.jpg', 'descriptionurl': 'https://commons.wikimedia.org/wiki/File:Test',
            'extmetadata': {'LicenseShortName': {'value': 'All rights reserved'}}}]}}}}
        with patch.object(public_sources, 'fetch', return_value=json.dumps(record).encode()):
            with self.assertRaises(ValueError):
                public_sources.image_reference('File:Test')

    def test_image_credit_preserves_author_and_license(self):
        record = {'query': {'pages': {'1': {'imageinfo': [{
            'url': 'https://upload.wikimedia.org/example.jpg', 'descriptionurl': 'https://commons.wikimedia.org/wiki/File:Test',
            'extmetadata': {'LicenseShortName': {'value': 'CC BY-SA 3.0'},
                            'LicenseUrl': {'value': 'https://creativecommons.org/licenses/by-sa/3.0/'},
                            'Artist': {'value': '<a>Photo author</a>'}}}]}}}}
        with patch.object(public_sources, 'fetch', return_value=json.dumps(record).encode()):
            credit = public_sources.image_reference('File:Test')
        self.assertEqual(credit['author'], 'Photo author')
        self.assertEqual(credit['license'], 'CC BY-SA 3.0')


if __name__ == '__main__':
    unittest.main()
