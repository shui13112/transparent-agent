from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path


class KnowledgeFileDB:
    """轻量级 SQLite 数据库，追踪本地知识库文件信息。"""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._init_table()

    def _init_table(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_files (
                    id TEXT PRIMARY KEY,
                    file_name TEXT UNIQUE NOT NULL,
                    file_path TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            conn.commit()

    def get_all(self) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM knowledge_files ORDER BY file_name"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_by_name(self, file_name: str) -> dict | None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM knowledge_files WHERE file_name = ?", (file_name,)
            ).fetchone()
            return dict(row) if row else None

    def get_existing_names(self) -> set[str]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT file_name FROM knowledge_files").fetchall()
            return {r[0] for r in rows}

    def insert(self, file_name: str, file_path: str) -> str:
        file_id = uuid.uuid4().hex
        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO knowledge_files (id, file_name, file_path, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (file_id, file_name, file_path, now, now),
            )
            conn.commit()
        return file_id

    def delete(self, file_id: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM knowledge_files WHERE id = ?", (file_id,))
            conn.commit()

    def find_deleted(self, current_names: set[str]) -> list[dict]:
        """返回数据库中存在但磁盘上已不存在的文件记录。"""
        return [r for r in self.get_all() if r["file_name"] not in current_names]
