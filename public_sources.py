"""Public Ctrip + official source adapter, with licensed image references."""
import io
import json
from pathlib import Path
import sys
import time
import re
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse, urljoin
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'work' / 'runtime'))
from bs4 import BeautifulSoup
from PIL import Image


def fetch(url, limit=5_000_000):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname:
        raise ValueError('Public sources must use absolute HTTPS URLs.')
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={'User-Agent': 'ChinaTravelMCP/0.3 (local travel review tool)'}), timeout=45) as response:
                raw = response.read(limit + 1)
            break
        except HTTPError as error:
            if error.code != 429 or attempt == 2:
                raise ValueError(f'Public source HTTP {error.code}: {parsed.hostname}; saved city progress is retained.') from None
            retry = error.headers.get('Retry-After', '')
            delay = int(retry) if retry.isdigit() else 20 * (attempt + 1)
            if delay > 60:
                raise ValueError(f'{parsed.hostname} requested waiting {delay} seconds. Retry this city later.') from None
            print(f'{parsed.hostname} 限流，按要求等待 {delay} 秒后重试。', flush=True)
            time.sleep(delay)
    if len(raw) > limit:
        raise ValueError('Source exceeded the download limit.')
    return raw


def article(raw, kind):
    soup = BeautifulSoup(raw.decode('utf-8', errors='replace'), 'html.parser')
    selector = '.travels_detail' if kind == 'ctrip' else '.v_news_content, .TRS_Editor, #zoom, .article-content, .article-cont, .zhengwen, #zoomcon, .content-info, article, .content'
    content = next((soup.select_one(part.strip()) for part in selector.split(',') if soup.select_one(part.strip()) is not None), None)
    if content is None:
        raise ValueError(f'Public {kind} page has no readable article; no login or hidden API bypass attempted.')
    for tag in content.select('script, style, nav'):
        tag.decompose()
    return content.get_text(' ', strip=True)


PROVINCES = {'建水': '云南', '靖西': '广西', '松阳': '浙江', '屏南': '福建',
             '歙县': '安徽', '寿县': '安徽', '阆中': '四川', '景宁': '浙江',
             '兴义': '贵州', '桐庐': '浙江', '邵武': '福建', '宁国': '安徽'}


def catalogue_documents(name):
    """Read the ministry's ordinary public route catalogue, not search RSS."""
    province = PROVINCES.get(name)
    queue_path = ROOT / 'city_queue.json'
    if queue_path.exists():
        province = next((c.get('province') or province for c in json.loads(queue_path.read_text(encoding='utf-8'))['cities'] if c['name_zh'] == name), province)
    if not province:
        raise ValueError(f'{name} 未设置省份，无法定位官方旅游线路目录。')
    hub = 'https://zhuanti.mct.gov.cn/csxz2022.html'
    soup = BeautifulSoup(fetch(hub), 'html.parser')
    link = next((urljoin(hub, a['href']) for a in soup.select('a[href]')
                 if province in a.get_text(strip=True) and '/csxz2022/' in a['href']), None)
    if not link or urlparse(link).hostname != 'zhuanti.mct.gov.cn':
        raise ValueError(f'官方旅游目录未找到{province}入口，需更新目录适配器。')
    listing = fetch(link).decode('utf-8', errors='replace')
    match = re.search(r'category_id\s*:\s*(\d+)', listing)
    if not match:
        raise ValueError('官方旅游目录页面结构已改变，需更新目录适配器。')
    documents = []
    for page in range(1, 11):
        params = urlencode({'category_id': match.group(1), 'pagesize': 6, 'page': page})
        data = json.loads(fetch('https://zhuanti.mct.gov.cn/getDataList.html?' + params))
        for item in data.get('list', []):
            url = urljoin(hub, item.get('link', ''))
            if urlparse(url).hostname != 'zhuanti.mct.gov.cn' or '/detail/' not in url:
                continue
            try:
                text = article(fetch(url), 'official')
            except (ValueError, OSError):
                continue
            if name in text:
                documents.append(dict(kind='official', url=url, text=text[:18000]))
                print('官方目录找到城市介绍：' + url, flush=True)
        if not data.get('more'):
            break
    return documents


def discover_city(name, model, ai, work=None, write_json=None):
    """Discover real links first; the model may only select supplied evidence."""
    documents = []
    if work is not None and (work / 'public_documents.json').exists():
        documents = json.loads((work / 'public_documents.json').read_text(encoding='utf-8'))
    if not documents:
        documents = catalogue_documents(name)
    if not any(d['kind'] == 'official' for d in documents):
        raise ValueError(f'{name}：官方线路目录暂未覆盖此城市，需要补充其他官方来源；重复运行不会解决目录覆盖不足。')
    if work is not None:
        write_json(work / 'public_documents.json', documents)
    result = ai('Extract 4 or 5 distinct visitor attractions located in the supplied city. Names must occur verbatim in official documents. Exclude other cities. Give English translations and one short English factual description from supplied text. Never generate URLs.',
                {'city': name, 'documents': documents},
                {'attractions': {'type': 'array', 'minItems': 4, 'maxItems': 5, 'items': {'type': 'object', 'properties': {'name_zh': {'type': 'string'}, 'name_en': {'type': 'string'}, 'facts_en': {'type': 'string'}}, 'required': ['name_zh', 'name_en', 'facts_en']}}}, ['attractions'], model)
    official = ' '.join(d['text'] for d in documents if d['kind'] == 'official')
    attractions = result['attractions']
    if len({a['name_zh'] for a in attractions}) != len(attractions) or any(not a['name_zh'] or a['name_zh'] not in official for a in attractions):
        raise ValueError('自动检索的景点未通过官方原文核验，未记录城市完成。')
    for attraction in attractions:
        params = urlencode({'action': 'query', 'list': 'search', 'srnamespace': 6, 'srsearch': name + ' ' + attraction['name_zh'], 'srlimit': 8, 'format': 'json'})
        hits = json.loads(fetch('https://commons.wikimedia.org/w/api.php?' + params))['query']['search']
        matches = [h for h in hits if attraction['name_zh'] in h['title'] or attraction['name_zh'] in BeautifulSoup(h.get('snippet', ''), 'html.parser').get_text()]
        if not matches:
            raise ValueError(f"{name}：已找到景点介绍，但尚未找到 {attraction['name_zh']} 的可复用参考照片。来源检索记录保留。")
        attraction['commons_file'] = matches[0]['title']
        image_reference(attraction['commons_file'])
    return dict(official_urls=[d['url'] for d in documents if d['kind'] == 'official'], ctrip_urls=[d['url'] for d in documents if d['kind'] == 'ctrip'], attractions=attractions)


def image_reference(title):
    params = urlencode({'action': 'query', 'titles': title, 'prop': 'imageinfo',
                        'iiprop': 'url|extmetadata', 'format': 'json'})
    result = json.loads(fetch('https://commons.wikimedia.org/w/api.php?' + params))
    pages = list(result['query']['pages'].values())
    info = pages[0].get('imageinfo', [])
    if not info:
        raise ValueError('Licensed reference not found: ' + title)
    info = info[0]
    meta = info['extmetadata']
    def value(key):
        text = str(meta.get(key, {}).get('value', ''))
        return BeautifulSoup(text, 'html.parser').get_text(' ', strip=True) if '<' in text else text
    license_name, license_url = value('LicenseShortName'), value('LicenseUrl')
    if not (license_name.startswith(('CC BY ', 'CC BY-SA ', 'CC0')) or license_name == 'Public domain'):
        raise ValueError('Reference has no supported reusable image license: ' + license_name)
    if (urlparse(info['url']).hostname or '') != 'upload.wikimedia.org':
        raise ValueError('Unexpected licensed image download host.')
    return dict(url=info['url'], page=info['descriptionurl'], author=value('Artist'),
                license=license_name, license_url=license_url,
                modifications='Person removal, local inpainting, and AI illustration adaptation.')


def collect_public(work, controller, model, ai, write_json, registry_path):
    registry = json.loads(registry_path.read_text(encoding='utf-8'))
    city = registry.get(controller['city'])
    if not city:
        print('未缓存城市来源，正在检索公开介绍和参考图片：' + controller['city'], flush=True)
        city = discover_city(controller['city'], model, ai, work, write_json)
        registry[controller['city']] = city
        write_json(registry_path, registry)
    document_path = work / 'public_documents.json'
    documents = json.loads(document_path.read_text(encoding='utf-8')) if document_path.exists() else []
    if not documents:
        for kind, key in [('ctrip', 'ctrip_urls'), ('official', 'official_urls')]:
            for url in city[key]:
                host = urlparse(url).hostname or ''
                if (kind == 'ctrip' and host != 'gs.ctrip.com') or (kind == 'official' and not host.endswith('.gov.cn')):
                    raise ValueError('Unexpected source host for ' + kind)
                try:
                    text = article(fetch(url), kind)
                except (ValueError, OSError) as error:
                    print(f'来源暂不可读，尝试其他来源：{url}（{error}）', flush=True)
                    continue
                documents.append({'kind': kind, 'url': url, 'text': text})
                write_json(document_path, documents)
    write_json(work / 'public_documents.json', documents)
    official = ' '.join(item['text'] for item in documents if item['kind'] == 'official')
    candidate_path = work / 'candidates.json'
    candidates = json.loads(candidate_path.read_text(encoding='utf-8')) if candidate_path.exists() else []
    for index, attraction in enumerate(city['attractions'][:5], 1):
        name = attraction['name_zh']
        if any(item['name_zh'] == name and item.get('source_provider') == 'ctrip-official' and all(Path(path).exists() for path in item['images']) for item in candidates):
            continue
        if name not in official:
            raise ValueError('Attraction not corroborated in official page: ' + name)
        passages = []
        for document in documents:
            position = document['text'].find(name)
            if position >= 0:
                passages.append({'source': document['url'], 'excerpt': document['text'][max(0,position-80):position+350]})
        description = ai('You are the travel researcher. Write a concise original Chinese introduction (one sentence, at most 70 Chinese characters) for the named attraction using only supplied source excerpts. Avoid superlatives, uncertain legends, dates, visitor numbers, distances, admission prices and opening times. Do not copy a passage.',
                         {'city': controller['city'], 'attraction': name, 'source_excerpts': passages},
                         {'description_zh': {'type': 'string', 'minLength': 10, 'maxLength': 90}},
                         ['description_zh'], model)['description_zh']
        reference = image_reference(attraction['commons_file'])
        raw = fetch(reference['url'], limit=25_000_000)
        with Image.open(io.BytesIO(raw)) as image:
            if image.width * image.height > 40_000_000:
                raise ValueError('Reference image is too large to process safely.')
            converted = image.convert('RGB')
            converted.thumbnail((896, 896))
        buffer = io.BytesIO()
        converted.save(buffer, format='PNG')
        path = work / 'source_images' / (f'{index:02d}' + '.png')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(buffer.getvalue())
        candidates.append(dict(id=index, name_zh=name, name_en=attraction['name_en'],
                               description_zh=description, source_url=next(d['url'] for d in documents if d['kind'] == 'official' and name in d['text']),
                               facts_en=attraction.get('facts_en', ''),
                               discovery_urls=city['ctrip_urls'], images=[str(path.resolve())],
                               image_credit=reference, source_provider='ctrip-official',
                               image_match_status='Commons file describes this attraction; review generated geography'))
        write_json(work / 'candidates.json', candidates)
        print('公开来源收集：' + name, flush=True)
        time.sleep(2)
    if not 4 <= len(candidates) <= 5:
        raise ValueError('Need four or five corroborated attractions with licensed images.')
    return candidates
