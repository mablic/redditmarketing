"""Read KEY=value settings from the project .env; process environment wins."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def read_env(path=ROOT / '.env'):
    values = {}
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            key, sep, value = line.strip().partition('=')
            if sep and key and not key.startswith('#'):
                values[key.strip()] = value.strip().strip('"\'')
    values.update({key: value for key, value in os.environ.items() if key in values and value})
    return values


def setting(key, default='', path=ROOT / '.env'):
    return os.environ.get(key) or read_env(path).get(key) or default
