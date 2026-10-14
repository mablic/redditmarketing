"""Local draft history. SQLite is the authority for publication status."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import sqlite3
import unicodedata
from urllib.parse import urlsplit, urlunsplit


def topic_hash(topic):
    normalized = ' '.join(unicodedata.normalize('NFKC', topic).casefold().split())
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()


def source_key(url):
    if not url:
        return ''
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(),
                       parts.path.rstrip('/'), parts.query, ''))


class Storage:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute('''CREATE TABLE IF NOT EXISTS drafts (
            id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
            topic TEXT NOT NULL, topic_hash TEXT NOT NULL,
            source_key TEXT NOT NULL, folder TEXT NOT NULL,
            demo INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'draft',
            published_at TEXT, post_url TEXT)''')
        self.db.execute('CREATE INDEX IF NOT EXISTS drafts_recent ON drafts(created_at)')
        self.db.commit()

    def close(self):
        self.db.close()

    @contextmanager
    def generation(self, candidate, days=30, allow_duplicate=False, demo=False):
        # Reserve the writer before checking, so concurrent runs cannot both pass.
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if not demo and not allow_duplicate:
                cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
                duplicate = self.db.execute('''SELECT id FROM drafts
                    WHERE demo = 0 AND created_at >= ? AND
                    (topic_hash = ? OR (source_key != '' AND source_key = ?))
                    ORDER BY created_at DESC LIMIT 1''',
                    (cutoff, topic_hash(candidate['topic']), source_key(candidate.get('source_url', '')))).fetchone()
                if duplicate:
                    raise ValueError(f"Duplicate topic/source: {duplicate['id']}; use --allow-duplicate to regenerate.")
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def add(self, folder, record):
        material = record['material']
        self.db.execute('''INSERT INTO drafts
            (id, created_at, topic, topic_hash, source_key, folder, demo)
            VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (folder.name, record['created_at'], material['topic'], topic_hash(material['topic']),
             source_key(material.get('source_url', '')), str(folder.resolve()), int(record['demo'])))

    def list(self, status=None, limit=20):
        return self.db.execute('''SELECT * FROM drafts WHERE (? IS NULL OR status = ?)
            ORDER BY created_at DESC, id DESC LIMIT ?''', (status, status, limit)).fetchall()

    def mark_published(self, draft_id, post_url=None):
        if post_url:
            parts = urlsplit(post_url)
            if parts.scheme not in ('http', 'https') or not parts.hostname:
                raise ValueError('Post URL must be an absolute HTTP(S) URL.')
        with self.db:
            row = self.db.execute('SELECT * FROM drafts WHERE id = ?', (draft_id,)).fetchone()
            if row is None:
                raise ValueError(f'Unknown draft ID: {draft_id}')
            if row['demo']:
                raise ValueError('Demo drafts cannot be marked published.')
            self.db.execute('''UPDATE drafts SET status = 'published',
                published_at = COALESCE(published_at, ?), post_url = COALESCE(?, post_url)
                WHERE id = ?''', (datetime.now(timezone.utc).isoformat(), post_url, draft_id))
