from __future__ import annotations

import sqlite3
import time
from pathlib import Path


class WebCacheDB:
    """SQLite 数据库，储存网页抓取内容，支持按 URL 查询完整文本。"""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._init_table()

    def _init_table(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS web_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT UNIQUE NOT NULL,
                    title TEXT DEFAULT '',
                    content TEXT NOT NULL,
                    content_length INTEGER DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_url ON web_cache(url)")
            conn.commit()

    def upsert(self, url: str, title: str, content: str) -> None:
        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO web_cache (url, title, content, content_length, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    title = excluded.title,
                    content = excluded.content,
                    content_length = excluded.content_length,
                    updated_at = excluded.updated_at
            """, (url, title, content, len(content), now, now))
            conn.commit()

    def get_by_url(self, url: str) -> dict | None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM web_cache WHERE url = ?", (url,)
            ).fetchone()
            return dict(row) if row else None


