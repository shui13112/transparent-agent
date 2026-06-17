from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from threading import Lock, RLock
from typing import Any

from utils.compression import KnowledgeBaseManager


class SessionManager:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.sessions_dir = base_dir / "sessions"
        self.archive_dir = self.sessions_dir / "archive"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, RLock] = {}
        self._locks_guard = Lock()

    def _session_path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.json"

    @contextmanager
    def _session_lock(self, session_id: str):
        with self._locks_guard:
            lock = self._locks.setdefault(session_id, RLock())
        with lock:
            yield

    def _default_record(self, session_id: str, title: str = "新会话") -> dict[str, Any]:
        now = time.time()
        return {
            "id": session_id,
            "title": title,
            "created_at": now,
            "updated_at": now,
            "compressed_context": "",
            "messages": [],
        }

    def _read_session_file(self, session_id: str) -> dict[str, Any]:
        with self._session_lock(session_id):
            path = self._session_path(session_id)
            if not path.exists():
                record = self._default_record(session_id)
                self._write_session(record)
                return record

            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                record = self._default_record(session_id)
                record["messages"] = raw
                self._write_session(record)
                return record

            raw.setdefault("id", session_id)
            raw.setdefault("title", "新会话")
            raw.setdefault("created_at", time.time())
            raw.setdefault("updated_at", raw["created_at"])
            raw.setdefault("compressed_context", "")
            raw.setdefault("messages", [])
            return raw

    def _write_session(self, record: dict[str, Any]) -> None:
        session_id = str(record["id"])
        record["updated_at"] = time.time()
        path = self._session_path(session_id)
        tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        tmp_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)

    def create_session(self, title: str = "新会话") -> dict[str, Any]:
        session_id = uuid.uuid4().hex
        record = self._default_record(session_id, title=title)
        self._write_session(record)
        return record

    def list_sessions(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for path in self.sessions_dir.glob("*.json"):
            if path.parent == self.archive_dir:
                continue
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            records.append(
                {
                    "id": record.get("id", path.stem),
                    "title": record.get("title", "新会话"),
                    "created_at": record.get("created_at"),
                    "updated_at": record.get("updated_at"),
                    "message_count": len(record.get("messages", [])),
                }
            )
        return sorted(records, key=lambda item: item.get("updated_at") or 0, reverse=True)

    def load_session_record(self, session_id: str) -> dict[str, Any]:
        return self._read_session_file(session_id)

    def load_session(self, session_id: str) -> list[dict[str, Any]]:
        return self._read_session_file(session_id)["messages"]

    def load_session_for_agent(self, session_id: str) -> list[dict[str, str]]:
        record = self._read_session_file(session_id)
        merged: list[dict[str, str]] = []

        compressed_context = record.get("compressed_context", "").strip()
        if compressed_context:
            merged.append(
                {
                    "role": "assistant",
                    "content": f"[以下是之前对话的摘要]\n{compressed_context}",
                }
            )

        for message in record.get("messages", []):
            role = message.get("role", "")
            content = str(message.get("content", "") or "")
            if role == "assistant" and merged and merged[-1]["role"] == "assistant":
                if content:
                    if merged[-1]["content"]:
                        merged[-1]["content"] += "\n\n" + content
                    else:
                        merged[-1]["content"] = content
                continue

            merged.append({"role": role, "content": content})

        return [item for item in merged if item["role"] in {"user", "assistant"}]

    def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
        tool_status: str | None = None,
    ) -> dict[str, Any]:
        with self._session_lock(session_id):
            record = self._read_session_file(session_id)
            message: dict[str, Any] = {"role": role, "content": content}
            if tool_calls:
                message["tool_calls"] = tool_calls
            if tool_status:
                message["tool_status"] = tool_status
            record["messages"].append(message)
            self._write_session(record)
            return message

    def get_history(self, session_id: str) -> dict[str, Any]:
        return self._read_session_file(session_id)

    def rename_session(self, session_id: str, title: str) -> dict[str, Any]:
        with self._session_lock(session_id):
            record = self._read_session_file(session_id)
            record["title"] = title.strip() or "新会话"
            self._write_session(record)
            return record

    def set_title(self, session_id: str, title: str) -> dict[str, Any]:
        return self.rename_session(session_id, title)

    def delete_session(self, session_id: str) -> None:
        with self._session_lock(session_id):
            path = self._session_path(session_id)
            if path.exists():
                path.unlink()
            KnowledgeBaseManager.delete_session_index(self.base_dir, session_id)

    def compress_history(self, session_id: str, summary: str, n_messages: int) -> dict[str, int]:
        with self._session_lock(session_id):
            record = self._read_session_file(session_id)
            messages = record.get("messages", [])
            archived = messages[:n_messages]
            remaining = messages[n_messages:]

            archive_path = self.archive_dir / f"{session_id}_{int(time.time())}.json"
            archive_payload = {
                "session_id": session_id,
                "archived_at": time.time(),
                "messages": archived,
            }
            tmp_archive_path = archive_path.with_name(
                f".{archive_path.name}.{uuid.uuid4().hex}.tmp"
            )
            tmp_archive_path.write_text(
                json.dumps(archive_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp_archive_path.replace(archive_path)

            existing_summary = record.get("compressed_context", "").strip()
            if existing_summary:
                record["compressed_context"] = f"{existing_summary}\n---\n{summary.strip()}"
            else:
                record["compressed_context"] = summary.strip()
            record["messages"] = remaining
            self._write_session(record)
            return {
                "archived_count": len(archived),
                "remaining_count": len(remaining),
            }

    def get_compressed_context(self, session_id: str) -> str:
        return self._read_session_file(session_id).get("compressed_context", "")
