"""Province -> city -> every official 5A area in that city -> one researched Reddit post per city."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import sys
import uuid
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from city_pipeline import ROOT, ai, ensure_services, lock, write_json
from local_env import setting
from public_sources import fetch

CATALOGUE = ROOT / 'provinces_5a'
STATE_FILE = ROOT / 'work' / 'scenic_batch.json'
WORK = ROOT / 'work' / 'scenic'
MUNICIPALITIES = {'北京': 'Beijing', '天津': 'Tianjin', '上海': 'Shanghai', '重庆': 'Chongqing'}
PROVINCE_EN = {'河北': 'Hebei', '山西': 'Shanxi', '内蒙古': 'Inner Mongolia', '辽宁': 'Liaoning', '吉林': 'Jilin',
               '黑龙江': 'Heilongjiang', '江苏': 'Jiangsu', '浙江': 'Zhejiang', '安徽': 'Anhui', '福建': 'Fujian',
               '江西': 'Jiangxi', '山东': 'Shandong', '河南': 'Henan', '湖北': 'Hubei', '湖南': 'Hunan',
               '广东': 'Guangdong', '广西': 'Guangxi', '海南': 'Hainan', '四川': 'Sichuan', '贵州': 'Guizhou',
               '云南': 'Yunnan', '西藏': 'Tibet', '陕西': 'Shaanxi', '甘肃': 'Gansu', '青海': 'Qinghai',
               '宁夏': 'Ningxia', '新疆': 'Xinjiang', '兵团': 'Xinjiang', **MUNICIPALITIES}
# Areas whose catalogue name carries no city prefix, or spans two provinces.
CITY_OVERRIDES = {'黄河壶口瀑布旅游区(陕西省延安市·山西省临汾市)': {'山西': '临汾市', '陕西': '延安市'},
                  '福建土楼（永定•南靖）旅游景区': '龙岩市', '沂蒙山旅游区': '临沂市',
                  '神农架生态旅游区': '神农架林区', '青海湖风景区': '海南州',
                  '兵团十师185团白沙湖景区（阿勒泰地区）': '第十师'}
CITY_ALIASES = {'延边朝鲜族自治州': '延边州'}
CITY_PATTERN = re.compile(r'^(.{2,12}?(?:市|自治州|地区|盟|林区|州|县))')
SUFFIX = re.compile(r'(文化旅游景区|文化旅游区|生态旅游区|风景旅游区|风景名胜区|旅游景区|旅游区|风景区|景区)$')
FIVE_A = ("5A is the top tier of China's national A-level tourist attraction rating, overseen by the Ministry of Culture "
          "and Tourism under the national standard GB/T 17775. To earn it, a place has to stand out for what it protects - "
          "its history, culture or natural landscape - and also meet demanding standards for looking after visitors: "
          "transport access, paths and signage, safety, sanitation, services and visitor satisfaction.")
WRITER_ROLE = ("You are a knowledgeable travel writer introducing one of China's officially rated 5A scenic areas to "
               "English-speaking Reddit readers planning a trip. Write with care and warmth, like a well-read local guide. "
               "Use ONLY facts found in the supplied sources; never invent dates, names, legends, numbers, prices or opening hours. "
               "history_en: one paragraph of 3-6 sentences on how the place came to be and the key periods, people or events in its history. "
               "why_5a_en: 2-3 sentences on what makes it exceptional enough for China's top tourism rating - its cultural, historical "
               "or natural significance and any heritage listings - only as supported by the sources. Do not merely restate "
               "that it is a 5A site; name the specific qualities. "
               "highlights_en: 3-5 specific things to see that the sources name, each written as 'Name - why it is worth seeing'. "
               "English only. Prefer English names given in the sources; otherwise use pinyin rather than guessing a translation. "
               "Do not claim a personal visit and do not mention AI. "
               "Follow tone_request for style only.")


def model():
    return setting('SCENIC_MODEL', 'qwen2.5:3b')


def province_files():
    return sorted(CATALOGUE.glob('[0-9][0-9]_*.json'))


def city_of(province, item):
    if item.get('city'):
        return item['city']
    if province in MUNICIPALITIES:
        return province
    override = CITY_OVERRIDES.get(item['name_zh'])
    if isinstance(override, dict):
        override = override.get(province)
    if override:
        return override
    match = CITY_PATTERN.match(re.sub(f'^{province}(?:省|[^省]{{0,4}}自治区)?', '', item['name_zh']))
    return CITY_ALIASES.get(match.group(1), match.group(1)) if match else '其他'


def group_cities(data):
    groups = {}
    for item in data['attractions']:
        groups.setdefault(city_of(data['province'], item), []).append(item)
    return groups


def catalogue():
    result = []
    for path in province_files():
        data = json.loads(path.read_text(encoding='utf-8'))
        runs = data.get('city_runs', {})
        for item in data['attractions']:
            item['city'] = city_of(data['province'], item)
        data['cities'] = [{'city': city, 'attraction_ids': [x['id'] for x in items], **runs.get(city, {})}
                          for city, items in group_cities(data).items()]
        result.append(data)
    return result


def get_province(name=None):
    paths = province_files()
    if not paths:
        raise ValueError('缺少各省5A景区目录。')
    for path in paths:
        data = json.loads(path.read_text(encoding='utf-8'))
        if name is None or data['province'] == name:
            return path, data
    raise ValueError('省份不在官方目录中。')


def progress():
    if not STATE_FILE.exists():
        return None
    result = json.loads(STATE_FILE.read_text(encoding='utf-8'))
    if result['status'] == 'running':
        try:
            with lock(ROOT / 'work' / 'scenic_pipeline.lock'):
                result['status'] = 'interrupted'
        except ValueError:
            pass
    log = ROOT / 'work' / 'scenic_batch.log'
    if log.exists():
        with log.open('rb') as stream:
            stream.seek(max(0, stream.seek(0, 2) - 40000))
            result['log'] = stream.read().decode('utf-8', errors='replace')
    return result


def short_name(name_zh):
    name = re.sub(r'[（(].*?[)）]', '', name_zh)
    if CITY_PATTERN.match(name):
        name = CITY_PATTERN.sub('', name, count=1)
    return SUFFIX.sub('', name) or name_zh


def wiki(lang, **params):
    params.update(action='query', format='json')
    return json.loads(fetch(f'https://{lang}.wikipedia.org/w/api.php?' + urlencode(params)))


def research(province, city, item, work, baidu=None):
    """Collect public encyclopedia text about the area; cached per attraction."""
    cache = work / f"sources-{item['id']}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding='utf-8'))
    short, place = short_name(item['name_zh']), re.sub(r'(市|自治州|地区|盟|林区|州|县)$', '', city)
    result = {'sources': [], 'name_en': item.get('name_en', ''), 'errors': []}
    try:
        hits = wiki('zh', list='search', srsearch=f'{short} {place}', srlimit=6)['query']['search']
        title = next((h['title'] for h in hits if short in h['title'] or short[:3] in h['title']), None)
        if title:
            page = next(iter(wiki('zh', prop='extracts|langlinks', explaintext=1, lllang='en', redirects=1,
                                  titles=title)['query']['pages'].values()))
            text = page.get('extract', '')
            if place in text or province in text:
                result['sources'].append({'lang': 'zh', 'title': page['title'], 'text': text[:3500],
                                          'url': 'https://zh.wikipedia.org/wiki/' + quote(page['title'])})
                english = next((link['*'] for link in page.get('langlinks', []) if link.get('lang') == 'en'), '')
                if english:
                    en_page = next(iter(wiki('en', prop='extracts', explaintext=1, redirects=1, titles=english)['query']['pages'].values()))
                    result['sources'].append({'lang': 'en', 'title': en_page['title'], 'text': en_page.get('extract', '')[:3500],
                                              'url': 'https://en.wikipedia.org/wiki/' + quote(en_page['title'].replace(' ', '_'))})
                    result['name_en'] = result['name_en'] or en_page['title']
    except (ValueError, OSError, KeyError) as error:
        result['errors'].append('wikipedia: ' + str(error))
    if not result['sources'] and baidu:
        try:
            baike = baidu.baike(item['name_zh'], short)
            if baike:
                result['sources'].append({'lang': 'zh', **baike})
        except Exception as error:
            result['errors'].append('baike: ' + str(error))
    write_json(cache, result)
    return result


def wiki_english_title(title):
    try:
        page = next(iter(wiki('zh', prop='langlinks', lllang='en', redirects=1, titles=title)['query']['pages'].values()))
    except (ValueError, OSError, KeyError):
        return ''
    english = next((link['*'] for link in page.get('langlinks', []) if link.get('lang') == 'en'), '')
    return re.sub(r'\s*\(.*\)$', '', english).strip()


def english_names(province, city, items, researched, run):
    """English city name from Wikipedia when possible; the local model only fills gaps."""
    city_en = run.get('city_en') or MUNICIPALITIES.get(province) or wiki_english_title(city)
    missing = [x for x in items if not researched[x['id']]['name_en']]
    if city_en and not missing:
        return city_en
    properties = {}
    if not city_en:
        properties['city_en'] = {'type': 'string', 'maxLength': 80, 'pattern': '^[ -~]+$'}
    if missing:
        properties['attractions_en'] = {'type': 'array', 'minItems': len(missing), 'maxItems': len(missing),
                                        'items': {'type': 'string', 'maxLength': 150, 'pattern': '^[ -~]+$'}}
    result = ai('Translate Chinese place names into the English names travellers would recognise; use pinyin when there is no '
                'established English name. Keep the same order. Latin letters only.',
                {'province': province, 'city': city, 'attractions': [x['name_zh'] for x in missing]},
                properties, list(properties), model())
    for item, name in zip(missing, result.get('attractions_en', [])):
        researched[item['id']]['name_en'] = name.strip()
    return city_en or result['city_en'].strip()


def clean_text(text):
    """Drop Chinese names the local model copies from the source, then keep the English."""
    text = re.sub(r'\s*[（(][^)）]*[\u4e00-\u9fff][^)）]*[)）]', '', text)
    text = re.sub(r'[\u4e00-\u9fff]+', '', text)
    text = re.sub(r'\s+([,.;:])', r'\1', text)
    text = re.sub(r'([,.;:])\s*([,.;:])', r'\1', text)
    text = re.sub(r'\s{2,}', ' ', text).strip()
    if len(re.findall(r'[A-Za-z]', text)) < 20:
        raise ValueError('English section is too short after removing Chinese names.')
    if re.search(r'\b(?:I|we) (?:visited|went|saw|travelled|traveled|stayed)\b|\bas an AI\b', text, re.I):
        raise ValueError('Section claims a personal visit or mentions AI.')
    return text


def write_section(item, info, city_en, province_en, prompt, use_llm):
    if not use_llm or not info['sources']:
        if item.get('facts_en'):
            return {'history_en': item['facts_en'], 'why_5a_en': '', 'highlights_en': []}
        return None
    material = {'attraction_zh': item['name_zh'], 'attraction_en': info['name_en'], 'city': city_en,
                'province': province_en, 'rated_5a_in': None if item.get('rating_year_disputed') else item.get('rating_year'),
                'tone_request': prompt, 'sources': [{'title': s['title'], 'text': s['text']} for s in info['sources']]}
    schema = {'history_en': {'type': 'string', 'minLength': 200, 'maxLength': 1600},
              'why_5a_en': {'type': 'string', 'minLength': 80, 'maxLength': 700},
              'highlights_en': {'type': 'array', 'minItems': 2, 'maxItems': 5, 'items': {'type': 'string', 'maxLength': 260}}}
    error = None
    for _ in range(2):
        try:
            draft = ai(WRITER_ROLE, material, schema, list(schema), model())
            return {'history_en': clean_text(draft['history_en']), 'why_5a_en': clean_text(draft['why_5a_en']),
                    'highlights_en': [clean_text(h) for h in draft['highlights_en'] if h.strip()]}
        except ValueError as failure:
            error = failure
            material['rejected_draft'] = str(failure)
    print(f"{item['name_zh']}：英文介绍未通过检查，本篇只列名称：{error}", flush=True)
    return None


def compose(province, city_en, items, researched, sections, photos, source_as_of, source_url):
    province_en = PROVINCE_EN.get(province, province)
    place = city_en if city_en == province_en else f'{city_en}, {province_en}'
    written = [x for x in items if sections.get(x['id'])]
    lead = researched[written[0]['id']]['name_en'] if len(written) == 1 else ''
    title = (f'{lead}, {place}: the story behind this 5A scenic area and what to see' if lead else
             f'{place}: {len(written)} of China\'s top-rated (5A) scenic areas - their history and what to see')
    count = 'one scenic area' if len(items) == 1 else f'{len(items)} scenic areas'
    parts = [f'Location: {place}, China.',
             f'{city_en} has {count} on China\'s national 5A list. {FIVE_A} Here is a closer look at '
             + ('it' if len(items) == 1 else 'each of them') + ': the history behind it, why it earned the top rating, and what to look for when you go.']
    for number, item in enumerate(written, 1):
        info, section = researched[item['id']], sections[item['id']]
        block = [f"{number}. {info['name_en']} ({item['name_zh']})"]
        if item.get('rating_year') and not item.get('rating_year_disputed'):
            block.append(f"Rated 5A in {item['rating_year']}.")
        block.append(section['history_en'])
        if section['why_5a_en']:
            block.append('Why it is rated 5A: ' + section['why_5a_en'])
        if section['highlights_en']:
            block.append('What to see:\n' + '\n'.join('- ' + h for h in section['highlights_en']))
        parts.append('\n\n'.join(block))
    others = [x for x in items if not sections.get(x['id'])]
    if others:
        parts.append('Also on the 5A list here: ' + '; '.join(f"{researched[x['id']]['name_en'] or x['name_zh']} ({x['name_zh']})" for x in others) + '.')
    sources = [f'5A list: Ministry of Culture and Tourism catalogue, dated {source_as_of} ({source_url})']
    sources += [f"{researched[x['id']]['name_en']}: {s['url']}" for x in written for s in researched[x['id']]['sources']]
    parts.append('Sources:\n' + '\n'.join('- ' + s for s in sources))
    credits = [f"Photo {i}: {p['credit']}" for i, p in enumerate(photos, 1) if p.get('credit')]
    if credits:
        parts.append('Photo credits:\n' + '\n'.join(credits))
    return title, '\n\n'.join(parts) + '\n'


def unload_ollama():
    try:
        with urlopen(Request('http://127.0.0.1:11434/api/generate', data=json.dumps({'model': model(), 'keep_alive': 0}).encode(),
                             headers={'Content-Type': 'application/json'}), timeout=60):
            pass
    except OSError:
        pass


def collect_images(items, researched, city, city_en, work, output, baidu):
    """One image per attraction: Unsplash, then Commons, then a local AI illustration from a Baidu reference."""
    from scenic_images import Unsplash, commons, keywords
    unsplash, chosen, ai_tasks, log = Unsplash(), {}, [], []
    for index, item in enumerate(items, 1):
        info = researched[item['id']]
        words, target = keywords(info['name_en'], item['name_zh']), output / f'{index:02d}.png'
        attempts = [('unsplash', lambda: unsplash.find(info['name_en'] + ' China', words, target)),
                    ('commons', lambda: commons(info['name_en'], words, target)),
                    ('commons', lambda: commons(short_name(item['name_zh']), words, target))]
        for source, attempt in attempts:
            if not (words['phrase'] or words['zh']):
                break
            try:
                photo = attempt()
            except Exception as error:
                log.append({'id': item['id'], 'source': source, 'error': str(error)})
                continue
            if photo:
                chosen[item['id']] = dict(photo, file=target.name, attraction_id=item['id'], name_zh=item['name_zh'],
                                          match_status='human_review_required')
                print(f"已取得 {source} 照片：{item['name_zh']}", flush=True)
                break
        if item['id'] in chosen:
            continue
        reference = work / 'source_images' / f'{index:02d}.png'
        reference.parent.mkdir(parents=True, exist_ok=True)
        try:
            found = baidu.find(f"{re.sub(r'(市|州|县)$', '', city)}{short_name(item['name_zh'])}", reference)
        except Exception as error:
            found = None
            log.append({'id': item['id'], 'source': 'baidu', 'error': str(error)})
        if found:
            ai_tasks.append({'id': index, 'attraction_id': item['id'], 'name_zh': item['name_zh'], 'reference': found,
                             'reference_image': str(reference.resolve()),
                             'output_file': str((work / 'generated' / f'{index:02d}.png').resolve()),
                             'prompt': f"{info['name_en']}, {city_en}, China, scenic landmark, landscape"})
            print('未找到可授权照片，将用本地 AI 依据参考图绘制：' + item['name_zh'], flush=True)
    if ai_tasks:
        try:
            render(ai_tasks, work, output, chosen, log)
        except Exception as error:
            log.append({'source': 'ai_local', 'error': str(error)})
            print('本地 AI 绘图未完成，相关景区暂缺图片：' + str(error), flush=True)
    write_json(output / 'image_search.json', {'errors': log})
    return [chosen[x['id']] for x in items if x['id'] in chosen]


def render(tasks, work, output, chosen, log):
    from comfy_images import Comfy, load_config
    config = load_config()
    config['illustration'] = {'prompt': 'beautiful hand painted travel illustration, harmonious colors, warm natural light, '
                                        'clean architecture, no people, ', 'steps': 32, 'cfg': 7, 'sampler': 'dpmpp_2m',
                              'scheduler': 'karras', 'denoise': 0.6}
    client = Comfy(config)
    (work / 'generated').mkdir(exist_ok=True)
    try:
        unload_ollama()
        ensure_services(comfy=True)
        for task in tasks:
            print('ComfyUI 本地图像生成：' + task['name_zh'], flush=True)
            try:
                client.generate(task, work)
            except Exception as error:
                log.append({'id': task['attraction_id'], 'source': 'ai_local', 'error': str(error)})
                continue
            target = output / Path(task['output_file']).name
            shutil.copyfile(task['output_file'], target)
            chosen[task['attraction_id']] = {'source': 'ai_local', 'file': target.name, 'attraction_id': task['attraction_id'],
                                             'name_zh': task['name_zh'], 'reference': task['reference'],
                                             'match_status': 'human_review_required'}
    finally:
        try:
            client.request('/free', {'unload_models': True, 'free_memory': True})
            client.cleanup(work)
        except Exception as error:
            log.append({'source': 'ai_local', 'error': 'cleanup: ' + str(error)})
        for path in list(work.glob('comfy-*.json')) + list(work.glob('mask-*.png')):
            path.unlink(missing_ok=True)
        for folder in ('generated', 'source_images'):
            shutil.rmtree(work / folder, ignore_errors=True)


def run_city(path, data, city, prompt='', use_llm=True, search_images=True):
    province = data['province']
    items = group_cities(data).get(city)
    if not items:
        raise ValueError('城市不在该省5A目录中。')
    run = data.setdefault('city_runs', {}).setdefault(city, {})
    work = WORK / province / city
    work.mkdir(parents=True, exist_ok=True)
    output = ROOT / 'city_posts' / '5a' / province / f'{city}_{uuid.uuid4().hex[:8]}'
    output.mkdir(parents=True)
    try:
        return write_city(path, data, city, items, run, work, output, prompt, use_llm, search_images)
    except BaseException:
        shutil.rmtree(output, ignore_errors=True)
        raise


def write_city(path, data, city, items, run, work, output, prompt, use_llm, search_images):
    province = data['province']
    from scenic_images import BaiduBrowser
    with BaiduBrowser() as baidu:
        researched = {}
        for item in items:
            print('查阅景区资料：' + item['name_zh'], flush=True)
            researched[item['id']] = research(province, city, item, work, baidu)
        if use_llm:
            ensure_services()
            run['city_en'] = english_names(province, city, items, researched, run)
        else:
            run['city_en'] = run.get('city_en') or MUNICIPALITIES.get(province) or city
            for item in items:
                researched[item['id']]['name_en'] = researched[item['id']]['name_en'] or item['name_zh']
        photos = collect_images(items, researched, city, run['city_en'], work, output, baidu) if search_images else []
    province_en = PROVINCE_EN.get(province, province)
    sections = {}
    for item in items:
        print('撰写英文介绍：' + item['name_zh'], flush=True)
        sections[item['id']] = write_section(item, researched[item['id']], run['city_en'], province_en, prompt, use_llm)
    if not any(sections.values()):
        raise ValueError('该城市的景区都没有找到可核对的公开资料，未生成帖子。')
    title, body = compose(province, run['city_en'], items, researched, sections, photos, data['source_as_of'], data['source_url'])
    (output / 'title.txt').write_text(title + '\n', encoding='utf-8')
    (output / 'body.txt').write_text(body, encoding='utf-8')
    (output / '中文说明.txt').write_text(
        f'省份：{province}\n城市：{city}\n5A景区：{"、".join(x["name_zh"] for x in items)}\n'
        f'官方目录截止日：{data["source_as_of"]}\n图片：{len(photos)} / {len(items)}\n'
        '图片来源依次为 Unsplash、Wikimedia Commons，均无则用本地 AI 依据百度参考图绘制。画面是否对应景区需人工核对。', encoding='utf-8')
    write_json(output / 'post.json', {'kind': '5a_city', 'province': province, 'city': city, 'city_en': run['city_en'],
                                      'attractions': [{'id': x['id'], 'name_zh': x['name_zh'], 'name_en': researched[x['id']]['name_en'],
                                                       'written': bool(sections[x['id']])} for x in items],
                                      'images': photos, 'review_status': 'needs_human_review', 'source_as_of': data['source_as_of']})
    status = 'draft_ready' if len(photos) == len(items) else ('draft_partial_images' if photos else 'draft_without_images')
    run.update(status=status, run_time=datetime.now(timezone.utc).isoformat(), last_error='',
               output=str(output.relative_to(ROOT / 'city_posts')).replace('\\', '/'))
    write_json(path, data)
    print('最终文件夹：' + str(output), flush=True)
    return output, len(photos)


def pending_cities(province=None):
    """Cities that do not yet have a complete draft, in catalogue order."""
    names = [province] if province else [item['province'] for item in catalogue()]
    pending = []
    for name in names:
        path, data = get_province(name)
        runs = data.get('city_runs', {})
        pending += [(path, data, city) for city in group_cities(data) if runs.get(city, {}).get('status') != 'draft_ready']
    return pending


def run_entries(entries, prompt='', use_llm=True, search_images=True):
    state = {'status': 'running', 'province': '', 'total': len(entries), 'current': '',
             'completed': 0, 'failed': 0, 'failures': [], 'log': ''}
    write_json(STATE_FILE, state)
    loaded = {}
    for index, (path, data, name) in enumerate(entries, 1):
        data = loaded.get(path, data)
        loaded[path] = data
        state.update(province=data['province'], current=name)
        write_json(STATE_FILE, state)
        count = len(group_cities(data)[name])
        scope = '全部' if len({item['province'] for _, item, _ in entries}) > 1 else data['province']
        print(f'{scope} {index}/{len(entries)} · {data["province"]} {name}（{count} 个5A景区）', flush=True)
        try:
            run_city(path, data, name, prompt, use_llm, search_images)
            state['completed'] += 1
        except Exception as error:
            data.setdefault('city_runs', {}).setdefault(name, {}).update(status='failed', last_error=str(error))
            write_json(path, data)
            state['failed'] += 1
            state['failures'].append({'city': f"{data['province']} {name}", 'error': str(error)})
            print('城市暂未完成：' + str(error), flush=True)
        write_json(STATE_FILE, state)
    state.update(status='finished', current='')
    write_json(STATE_FILE, state)
    print(f'批次完成：草稿 {state["completed"]}，失败 {state["failed"]}。', flush=True)
    return 1 if state['failed'] else 0


def run(province=None, city=None, prompt='', use_llm=True, search_images=True, all_cities=False):
    with lock(ROOT / 'work/scenic_pipeline.lock'), lock(ROOT / 'work/city_pipeline.lock'):
        if all_cities:
            return run_entries(pending_cities(), prompt, use_llm, search_images)
        path, data = get_province(province)
        groups = group_cities(data)
        if city and city not in groups:
            raise ValueError('城市不属于所选省份。')
        cities = [city] if city else [name for name in groups if data.get('city_runs', {}).get(name, {}).get('status') != 'draft_ready']
        return run_entries([(path, data, name) for name in cities], prompt, use_llm, search_images)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--province')
    parser.add_argument('--city')
    parser.add_argument('--all', action='store_true', help='run every city that does not yet have a complete draft')
    parser.add_argument('--prompt', default='')
    parser.add_argument('--no-llm', action='store_true')
    parser.add_argument('--no-images', action='store_true')
    args = parser.parse_args()
    try:
        if args.all and (args.province or args.city):
            raise ValueError('跑全部城市时不要再指定省份或城市。')
        sys.exit(run(args.province, args.city, args.prompt, not args.no_llm, not args.no_images, args.all))
    except Exception as error:
        print('流程暂停：' + str(error), flush=True)
        sys.exit(1)
