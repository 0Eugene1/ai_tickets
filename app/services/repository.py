from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    ticket_id: int
    version_hash: str
    status: str
    input_json: str
    result_json: str | None
    error: str | None
    created_at: str
    updated_at: str


class TaskRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    ticket_id INTEGER NOT NULL,
                    version_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    input_json TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(ticket_id, version_hash)
                )
                """
            )

    def create_or_get(self, ticket_id: int, version_hash: str, payload: dict[str, Any]) -> tuple[TaskRecord, bool]:
        now = datetime.now(timezone.utc).isoformat()
        task_id = str(uuid.uuid4())
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
        created = True
        with self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO tasks
                    (task_id, ticket_id, version_hash, status, input_json, created_at, updated_at)
                    VALUES (?, ?, ?, 'queued', ?, ?, ?)
                    """,
                    (task_id, ticket_id, version_hash, serialized, now, now),
                )
            except sqlite3.IntegrityError:
                created = False
            row = connection.execute(
                "SELECT * FROM tasks WHERE ticket_id = ? AND version_hash = ?",
                (ticket_id, version_hash),
            ).fetchone()
        if row is None:
            raise RuntimeError("Task insert failed")
        return self._record(row), created

    def get(self, task_id: str) -> TaskRecord | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return self._record(row) if row else None

    def set_processing(self, task_id: str) -> None:
        self._update(task_id, status="processing", error=None)

    def set_completed(self, task_id: str, result: dict[str, Any]) -> None:
        self._update(
            task_id,
            status="completed",
            result_json=json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=str),
            error=None,
        )

    def set_error(self, task_id: str, error: str) -> None:
        self._update(task_id, status="error", error=error[:2000])

    def _update(self, task_id: str, **fields: Any) -> None:
        fields["updated_at"] = datetime.now(timezone.utc).isoformat()
        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = [*fields.values(), task_id]
        with self._connection() as connection:
            connection.execute(f"UPDATE tasks SET {assignments} WHERE task_id = ?", values)

    @staticmethod
    def _record(row: sqlite3.Row) -> TaskRecord:
        return TaskRecord(**dict(row))
