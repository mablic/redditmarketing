"""Deterministic location labels shared by export and Reddit packaging."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def location(city):
    queue = json.loads((ROOT / 'city_queue.json').read_text(encoding='utf-8'))
    item = next((c for c in queue['cities'] if c['name_zh'] == city), None)
    if not item or not all(item.get(k) for k in ('province_en', 'province_full', 'prefecture', 'prefecture_en', 'administrative_name')):
        raise ValueError('Missing verified location fields for ' + city)
    chinese = f"所在地：{item['province_full']} · {item['prefecture']} · {item['administrative_name']}。"
    english = f"Location: {item['name_en']} ({item['administrative_name']}), {item['prefecture_en']} ({item['prefecture']}), {item['province_en']} ({item['province_full']}), China."
    return english, chinese


def city_from_folder(folder):
    cities = json.loads((ROOT / 'city_queue.json').read_text(encoding='utf-8'))['cities']
    return next((c['name_zh'] for c in sorted(cities, key=lambda c: len(c['name_zh']), reverse=True)
                 if Path(folder).name == c['name_zh'] or Path(folder).name.startswith(c['name_zh'] + '_')), None)
