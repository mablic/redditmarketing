"""Image sources for 5A city posts: Unsplash, Wikimedia Commons, then a Baidu reference for local AI."""
import io
import json
import os
import re
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

from PIL import Image

from local_env import ROOT, setting
from public_sources import fetch, image_reference

UTM = 'utm_source=china_travel_mcp&utm_medium=referral'
GENERIC_WORDS = {'scenic', 'area', 'areas', 'tourist', 'tourism', 'park', 'the', 'of', 'and', 'cultural', 'culture',
                 'resort', 'zone', 'site', 'spot', 'national', 'china', 'chinese', 'city', 'ancient', 'old', 'travel',
                 'attraction', 'heritage', 'world', 'museum', 'mountain', 'lake', 'river', 'town', 'village', 'temple',
                 'great', 'grand', 'new', 'west', 'east', 'north', 'south', 'garden', 'gardens', 'forest', 'ecological',
                 'cave', 'caves', 'palace', 'hall', 'tower', 'pagoda', 'bridge', 'island', 'islands', 'canyon', 'valley',
                 'waterfall', 'falls', 'gorge', 'gorges', 'studios', 'studio', 'film', 'street', 'square', 'wall', 'tomb',
                 'tombs', 'grottoes', 'grotto', 'peak', 'peaks', 'hill', 'hills', 'cliff', 'bay', 'beach', 'sea', 'wetland',
                 'botanical', 'zoo', 'safari', 'ruins', 'memorial', 'center', 'centre', 'base', 'county', 'district',
                 'province', 'heaven', 'water', 'stone', 'rock', 'dragon', 'tea', 'snow', 'grassland', 'desert', 'scenery'}


PINYIN = re.compile(r'^(?:(?:zh|ch|sh|[bpmfdtnlgkhjqxrzcsyw])?(?:iang|iong|uang|ang|eng|ing|ong|uan|ian|iao|uai|ai|ei|ao|ou'
                    r'|an|en|in|un|ua|uo|ia|ie|iu|ui|ue|er|a|e|i|o|u|v))+$')


def keywords(name_en, name_zh=''):
    """A candidate must contain the full English name, all pinyin proper-name words, or the Chinese short name."""
    words = [w for w in re.findall(r'[a-z]+', name_en.casefold())
             if len(w) > 2 and w not in GENERIC_WORDS and PINYIN.match(w)]
    short = re.sub(r'(旅游景区|风景名胜区|旅游区|景区|文化旅游区|风景区)$', '', re.split(r'市|自治州|地区|盟|州|县', name_zh)[-1])
    return {'phrase': name_en.casefold().strip(), 'words': words, 'zh': short if len(short) >= 2 else ''}


def matches(text, keys):
    text = text.casefold()
    return bool((keys['phrase'] and keys['phrase'] in text) or (keys['words'] and all(w in text for w in keys['words']))
                or (keys['zh'] and keys['zh'] in text))


def save_png(raw, path, min_width=800, max_side=None):
    with Image.open(io.BytesIO(raw)) as image:
        if image.width * image.height > 60_000_000:
            raise ValueError('Image is too large to process safely.')
        if image.width < min_width:
            raise ValueError('Image is too small.')
        converted = image.convert('RGB')
        if max_side:
            converted.thumbnail((max_side, max_side))
        converted.save(path)


class Unsplash:
    def __init__(self):
        self.key = setting('UNSPLASH_ACCESS_KEY') or setting('UPSPLASH_ACCESS_KEY')
        self.remaining = None

    def get(self, path, params=None):
        if self.remaining is not None and self.remaining < 3:
            raise ValueError('Unsplash hourly request limit nearly used; skipped.')
        url = path if path.startswith('https://api.unsplash.com/') else 'https://api.unsplash.com' + path
        url += ('?' + urlencode(params)) if params else ''
        try:
            with urlopen(Request(url, headers={'Authorization': 'Client-ID ' + self.key, 'Accept-Version': 'v1'}), timeout=30) as response:
                remaining = response.headers.get('X-Ratelimit-Remaining', '')
                self.remaining = int(remaining) if remaining.isdigit() else self.remaining
                return json.load(response)
        except HTTPError as error:
            if error.code in (403, 429):
                self.remaining = 0
            raise ValueError(f'Unsplash HTTP {error.code}') from None

    def find(self, query, words, path):
        if not self.key:
            raise ValueError('UNSPLASH_ACCESS_KEY is not set in .env.')
        results = self.get('/search/photos', {'query': query, 'per_page': 8, 'orientation': 'landscape', 'content_filter': 'high'})['results']
        def text_of(photo):
            return ' '.join(str(photo.get(k) or '') for k in ('description', 'alt_description', 'slug')).casefold()
        chosen = next((p for p in results if matches(text_of(p), words)), None)
        for photo in ([] if chosen else results[:2]):
            detail = self.get('/photos/' + photo['id'])
            location = ' '.join(str(v or '') for v in (detail.get('location') or {}).values() if not isinstance(v, dict))
            tags = ' '.join(t.get('title', '') for t in detail.get('tags', []))
            if matches(location + ' ' + tags + ' ' + text_of(detail), words):
                chosen = photo
                break
        if not chosen:
            return None
        # Unsplash API guidelines: report the download before using the file.
        self.get(chosen['links']['download_location'])
        image_url = chosen['urls']['regular']
        if urlparse(image_url).hostname != 'images.unsplash.com':
            raise ValueError('Unexpected Unsplash image host.')
        save_png(fetch(image_url, limit=30_000_000), path)
        user = chosen['user']
        return {'source': 'unsplash', 'author': user['name'], 'license': 'Unsplash License',
                'license_url': 'https://unsplash.com/license', 'page': chosen['links']['html'] + '?' + UTM,
                'author_url': user['links']['html'] + '?' + UTM,
                'credit': f"Photo by {user['name']} on Unsplash ({chosen['links']['html']}?{UTM})"}


def commons(query, words, path):
    params = urlencode({'action': 'query', 'list': 'search', 'srsearch': query, 'srnamespace': 6, 'srlimit': 10, 'format': 'json'})
    for hit in json.loads(fetch('https://commons.wikimedia.org/w/api.php?' + params)).get('query', {}).get('search', []):
        title = hit['title']
        if not matches(title.replace('_', ' '), words):
            continue
        if re.search(r'\.(svg|gif|webm|ogv|pdf|tif|tiff)$|\b(map|plan|painting|drawing|portrait|logo)\b', title, re.I):
            continue
        try:
            reference = image_reference(title)
            save_png(fetch(reference['url'], limit=30_000_000), path, max_side=2400)
        except (ValueError, OSError):
            continue
        return {'source': 'commons', 'author': reference['author'], 'license': reference['license'],
                'license_url': reference['license_url'], 'page': reference['page'],
                'credit': f"Photo: {reference['author']}, {reference['license']} ({reference['page']})"}
    return None


class BaiduBrowser:
    """Headless Chrome for public Baidu pages. Baidu pictures are only AI references and are never published."""

    def __init__(self):
        self.playwright = self.browser = self.page = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()

    def open(self):
        if self.page:
            return
        os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(ROOT / 'work' / 'browsers'))
        from playwright.sync_api import sync_playwright
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(channel='chrome', headless=True)
        self.page = self.browser.new_page(locale='zh-CN', viewport={'width': 1280, 'height': 900})

    def find(self, query, path):
        self.open()
        self.page.goto('https://image.baidu.com/search/index?tn=baiduimage&word=' + quote(query), wait_until='domcontentloaded', timeout=60000)
        self.page.wait_for_timeout(4000)
        found = self.page.evaluate('''() => [...document.querySelectorAll('img')].map(i => ({
            src: i.currentSrc || i.src, width: i.naturalWidth, height: i.naturalHeight,
            link: i.closest('a') ? i.closest('a').href : ''})).filter(x => x.width >= 600 && x.width >= x.height)''')
        for item in found:
            host = urlparse(item['src']).hostname or ''
            if not re.fullmatch(r'img\d?\.baidu\.com', host) or 'image.baidu.com/search/detail' not in item['link']:
                continue
            response = self.page.request.get(item['src'], headers={'Referer': 'https://image.baidu.com/'}, timeout=30000)
            if not response.ok:
                continue
            try:
                save_png(response.body(), path, min_width=600, max_side=896)
            except ValueError:
                continue
            return {'query': query, 'image_url': item['src'], 'result_page': item['link']}
        return None

    def baike(self, name_zh, short):
        self.open()
        url = 'https://baike.baidu.com/item/' + quote(name_zh)
        self.page.goto(url, wait_until='domcontentloaded', timeout=60000)
        self.page.wait_for_timeout(3000)
        text = self.page.evaluate('''() => {
            const main = document.querySelector('[class*="mainContent"], .main-content, .lemma-summary');
            return (main || document.body).innerText; }''')
        text = re.sub(r'\[\d+(?:-\d+)?\]', '', text or '')
        if short not in text or len(text) < 300:
            return None
        return {'title': name_zh, 'url': self.page.url if 'baike.baidu.com' in self.page.url else url, 'text': text[:3500]}
