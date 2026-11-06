from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class AssignmentEvent:
    event_id: int
    performer: str
    ticket_id: int
    generation: int
    attempts: int
    task_id: str | None
    detected_at: datetime
    content_notice_sent: bool


@dataclass(frozen=True)
class SnapshotResult:
    baseline_created: bool
    current_count: int
    detected_count: int


class MonitorRepository:
    """Persistent assignment snapshot and retry queue for the Metabase poller."""

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
                CREATE TABLE IF NOT EXISTS metabase_monitor_runs (
                    performer TEXT PRIMARY KEY,
                    initialized_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS metabase_assignment_state (
                    performer TEXT NOT NULL,
                    ticket_id INTEGER NOT NULL,
                    generation INTEGER NOT NULL,
                    is_current INTEGER NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    PRIMARY KEY (performer, ticket_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS metabase_assignment_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    performer TEXT NOT NULL,
                    ticket_id INTEGER NOT NULL,
                    generation INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    task_id TEXT,
                    error TEXT,
                    content_notice_sent INTEGER NOT NULL DEFAULT 0,
                    detected_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (performer, ticket_id, generation)
                )
                """
            )
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(metabase_assignment_events)")
            }
            if "content_notice_sent" not in columns:
                connection.execute(
                    "ALTER TABLE metabase_assignment_events "
                    "ADD COLUMN content_notice_sent INTEGER NOT NULL DEFAULT 0"
                )

    def sync_snapshot(
        self,
        performer: str,
        current_ticket_ids: set[int],
        *,
        baseline_on_first_run: bool,
    ) -> SnapshotResult:
        now = datetime.now(timezone.utc).isoformat()
        detected = 0
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            initialized = connection.execute(
                "SELECT 1 FROM metabase_monitor_runs WHERE performer = ?",
                (performer,),
            ).fetchone()
            if initialized is None:
                for ticket_id in current_ticket_ids:
                    connection.execute(
                        """
                        INSERT INTO metabase_assignment_state
                        (performer, ticket_id, generation, is_current, first_seen_at, last_seen_at)
                        VALUES (?, ?, 1, 1, ?, ?)
                        """,
                        (performer, ticket_id, now, now),
                    )
                    if not baseline_on_first_run:
                        self._insert_event(connection, performer, ticket_id, 1, now)
                        detected += 1
                connection.execute(
                    "INSERT INTO metabase_monitor_runs (performer, initialized_at, updated_at) VALUES (?, ?, ?)",
                    (performer, now, now),
                )
                return SnapshotResult(True, len(current_ticket_ids), detected)

            previous_rows = connection.execute(
                "SELECT ticket_id FROM metabase_assignment_state WHERE performer = ? AND is_current = 1",
                (performer,),
            ).fetchall()
            previous = {int(row["ticket_id"]) for row in previous_rows}
            entered = current_ticket_ids - previous
            departed = previous - current_ticket_ids
            continuing = current_ticket_ids & previous

            if continuing:
                connection.executemany(
                    "UPDATE metabase_assignment_state SET last_seen_at = ? WHERE performer = ? AND ticket_id = ?",
                    ((now, performer, ticket_id) for ticket_id in continuing),
                )
            if departed:
                connection.executemany(
                    "UPDATE metabase_assignment_state SET is_current = 0, last_seen_at = ? WHERE performer = ? AND ticket_id = ?",
                    ((now, performer, ticket_id) for ticket_id in departed),
                )
            for ticket_id in entered:
                row = connection.execute(
                    "SELECT generation FROM metabase_assignment_state WHERE performer = ? AND ticket_id = ?",
                    (performer, ticket_id),
                ).fetchone()
                if row is None:
                    generation = 1
                    connection.execute(
                        """
                        INSERT INTO metabase_assignment_state
                        (performer, ticket_id, generation, is_current, first_seen_at, last_seen_at)
                        VALUES (?, ?, ?, 1, ?, ?)
                        """,
                        (performer, ticket_id, generation, now, now),
                    )
                else:
                    generation = int(row["generation"]) + 1
                    connection.execute(
                        """
                        UPDATE metabase_assignment_state
                        SET generation = ?, is_current = 1, first_seen_at = ?, last_seen_at = ?
                        WHERE performer = ? AND ticket_id = ?
                        """,
                        (generation, now, now, performer, ticket_id),
                    )
                self._insert_event(connection, performer, ticket_id, generation, now)
                detected += 1
            connection.execute(
                "UPDATE metabase_monitor_runs SET updated_at = ? WHERE performer = ?",
                (now, performer),
            )
        return SnapshotResult(False, len(current_ticket_ids), detected)

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        performer: str,
        ticket_id: int,
        generation: int,
        now: str,
    ) -> None:
        connection.execute(
            """
            INSERT OR IGNORE INTO metabase_assignment_events
            (performer, ticket_id, generation, status, detected_at, updated_at)
            VALUES (?, ?, ?, 'pending', ?, ?)
            """,
            (performer, ticket_id, generation, now, now),
        )

    def claim_pending(self, performer: str, *, limit: int = 20) -> list[AssignmentEvent]:
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        stale_before = (now_dt - timedelta(minutes=5)).isoformat()
        claimed: list[AssignmentEvent] = []
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE metabase_assignment_events
                SET status = 'pending', updated_at = ?
                WHERE performer = ? AND status = 'processing' AND updated_at < ?
                """,
                (now, performer, stale_before),
            )
            rows = connection.execute(
                """
                SELECT event_id, performer, ticket_id, generation, attempts, task_id,
                       detected_at, content_notice_sent
                FROM metabase_assignment_events
                WHERE performer = ? AND status = 'pending'
                ORDER BY event_id
                LIMIT ?
                """,
                (performer, limit),
            ).fetchall()
            for row in rows:
                updated = connection.execute(
                    """
                    UPDATE metabase_assignment_events
                    SET status = 'processing', attempts = attempts + 1, updated_at = ?
                    WHERE event_id = ? AND status = 'pending'
                    """,
                    (now, row["event_id"]),
                )
                if updated.rowcount:
                    claimed.append(
                        AssignmentEvent(
                            event_id=int(row["event_id"]),
                            performer=str(row["performer"]),
                            ticket_id=int(row["ticket_id"]),
                            generation=int(row["generation"]),
                            attempts=int(row["attempts"]) + 1,
                            task_id=str(row["task_id"]) if row["task_id"] else None,
                            detected_at=datetime.fromisoformat(str(row["detected_at"])),
                            content_notice_sent=bool(row["content_notice_sent"]),
                        )
                    )
        return claimed

    def mark_delivered(self, event_id: int, task_id: str) -> None:
        self._finish(event_id, status="delivered", task_id=task_id, error=None)

    def mark_analyzed(self, event_id: int, task_id: str) -> None:
        """Persist the paid/expensive analysis before attempting Telegram delivery."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE metabase_assignment_events
                SET task_id = ?, error = NULL, updated_at = ?
                WHERE event_id = ? AND status = 'processing'
                """,
                (task_id, now, event_id),
            )

    def mark_retry(self, event_id: int, error: str) -> None:
        self._finish(event_id, status="pending", task_id=None, error=error[:1000])

    def mark_waiting_for_content(self, event_id: int) -> None:
        """Remember that the short notice was sent while polling for real content."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE metabase_assignment_events
                SET status = 'pending', content_notice_sent = 1,
                    error = 'Waiting for CallCenter comments to become available',
                    updated_at = ?
                WHERE event_id = ?
                """,
                (now, event_id),
            )

    def _finish(self, event_id: int, *, status: str, task_id: str | None, error: str | None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE metabase_assignment_events
                SET status = ?, task_id = COALESCE(?, task_id), error = ?, updated_at = ?
                WHERE event_id = ?
                """,
                (status, task_id, error, now, event_id),
            )
