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
    quarantined_count: int = 0
    cancelled_count: int = 0
    snapshot_ignored: bool = False
    baseline_reason: str | None = None


class MonitorRepository:
    """Persistent assignment snapshot and bounded retry queue for the Metabase poller."""

    def __init__(
        self,
        database_path: Path,
        *,
        departure_confirmation_polls: int = 3,
        snapshot_drop_guard_fraction: float = 0.5,
        snapshot_drop_guard_min_size: int = 10,
        max_new_events_per_poll: int = 10,
        rebaseline_after_seconds: float = 3600.0,
        pending_ttl_hours: float = 24.0,
        retry_delays_seconds: tuple[float, ...] = (300.0, 900.0, 1800.0, 3600.0),
    ) -> None:
        self.database_path = database_path
        self.departure_confirmation_polls = max(1, departure_confirmation_polls)
        self.snapshot_drop_guard_fraction = min(max(snapshot_drop_guard_fraction, 0.0), 1.0)
        self.snapshot_drop_guard_min_size = max(1, snapshot_drop_guard_min_size)
        self.max_new_events_per_poll = max(1, max_new_events_per_poll)
        self.rebaseline_after_seconds = max(0.0, rebaseline_after_seconds)
        self.pending_ttl = timedelta(hours=max(0.1, pending_ttl_hours))
        delays = tuple(max(0.0, value) for value in retry_delays_seconds)
        self.retry_delays_seconds = delays or (300.0,)
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

    @staticmethod
    def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
        return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS metabase_monitor_runs (
                    performer TEXT PRIMARY KEY,
                    initialized_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    snapshot_key TEXT
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
                    missing_polls INTEGER NOT NULL DEFAULT 0,
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
                    next_attempt_at TEXT,
                    expires_at TEXT,
                    UNIQUE (performer, ticket_id, generation)
                )
                """
            )
            migrations = (
                ("metabase_monitor_runs", "snapshot_key", "TEXT"),
                ("metabase_assignment_state", "missing_polls", "INTEGER NOT NULL DEFAULT 0"),
                (
                    "metabase_assignment_events",
                    "content_notice_sent",
                    "INTEGER NOT NULL DEFAULT 0",
                ),
                ("metabase_assignment_events", "next_attempt_at", "TEXT"),
                ("metabase_assignment_events", "expires_at", "TEXT"),
            )
            for table, column, definition in migrations:
                if column not in self._columns(connection, table):
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

            connection.execute(
                """
                UPDATE metabase_assignment_events
                SET next_attempt_at = COALESCE(next_attempt_at, updated_at, detected_at)
                WHERE next_attempt_at IS NULL
                """
            )
            legacy = connection.execute(
                "SELECT event_id, detected_at FROM metabase_assignment_events WHERE expires_at IS NULL"
            ).fetchall()
            for row in legacy:
                detected = datetime.fromisoformat(str(row["detected_at"]))
                connection.execute(
                    "UPDATE metabase_assignment_events SET expires_at = ? WHERE event_id = ?",
                    ((detected + self.pending_ttl).isoformat(), row["event_id"]),
                )

    def sync_snapshot(
        self,
        performer: str,
        current_ticket_ids: set[int],
        *,
        baseline_on_first_run: bool,
        snapshot_key: str = "default",
    ) -> SnapshotResult:
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                "SELECT initialized_at, updated_at, snapshot_key FROM metabase_monitor_runs WHERE performer = ?",
                (performer,),
            ).fetchone()
            if run is None:
                self._replace_baseline(connection, performer, current_ticket_ids, now, snapshot_key)
                detected = 0
                if not baseline_on_first_run:
                    for ticket_id in current_ticket_ids:
                        self._insert_event(connection, performer, ticket_id, 1, now, "pending")
                        detected += 1
                return SnapshotResult(True, len(current_ticket_ids), detected, baseline_reason="first-run")

            stored_key = str(run["snapshot_key"] or "")
            last_update = datetime.fromisoformat(str(run["updated_at"]))
            stale = (
                self.rebaseline_after_seconds > 0
                and (now_dt - last_update.astimezone(timezone.utc)).total_seconds()
                > self.rebaseline_after_seconds
            )
            if stored_key != snapshot_key or stale:
                cancelled = self._replace_baseline(
                    connection, performer, current_ticket_ids, now, snapshot_key
                )
                reason = "snapshot-filter-changed" if stored_key != snapshot_key else "stale-monitor-recovery"
                return SnapshotResult(
                    True,
                    len(current_ticket_ids),
                    0,
                    cancelled_count=cancelled,
                    baseline_reason=reason,
                )

            previous_rows = connection.execute(
                """
                SELECT ticket_id, missing_polls
                FROM metabase_assignment_state
                WHERE performer = ? AND is_current = 1
                """,
                (performer,),
            ).fetchall()
            previous_missing = {int(row["ticket_id"]): int(row["missing_polls"]) for row in previous_rows}
            previous = set(previous_missing)
            if len(previous) >= self.snapshot_drop_guard_min_size and len(current_ticket_ids) < len(previous):
                drop_fraction = (len(previous) - len(current_ticket_ids)) / len(previous)
                if drop_fraction > self.snapshot_drop_guard_fraction:
                    connection.execute(
                        "UPDATE metabase_monitor_runs SET updated_at = ?, snapshot_key = ? WHERE performer = ?",
                        (now, snapshot_key, performer),
                    )
                    return SnapshotResult(
                        False,
                        len(current_ticket_ids),
                        0,
                        snapshot_ignored=True,
                    )

            entered = current_ticket_ids - previous
            continuing = current_ticket_ids & previous
            departed = previous - current_ticket_ids
            cancelled = 0

            if continuing:
                connection.executemany(
                    """
                    UPDATE metabase_assignment_state
                    SET last_seen_at = ?, missing_polls = 0
                    WHERE performer = ? AND ticket_id = ?
                    """,
                    ((now, performer, ticket_id) for ticket_id in continuing),
                )
            for ticket_id in departed:
                missing_polls = previous_missing[ticket_id] + 1
                if missing_polls >= self.departure_confirmation_polls:
                    connection.execute(
                        """
                        UPDATE metabase_assignment_state
                        SET is_current = 0, missing_polls = 0, last_seen_at = ?
                        WHERE performer = ? AND ticket_id = ?
                        """,
                        (now, performer, ticket_id),
                    )
                    cancelled += self._cancel_active_events(
                        connection, performer, ticket_id, now, "Ticket left active snapshot"
                    )
                else:
                    connection.execute(
                        """
                        UPDATE metabase_assignment_state
                        SET missing_polls = ?, last_seen_at = ?
                        WHERE performer = ? AND ticket_id = ?
                        """,
                        (missing_polls, now, performer, ticket_id),
                    )

            quarantine = len(entered) > self.max_new_events_per_poll
            detected = 0
            quarantined = 0
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
                        (performer, ticket_id, generation, is_current, first_seen_at, last_seen_at, missing_polls)
                        VALUES (?, ?, ?, 1, ?, ?, 0)
                        """,
                        (performer, ticket_id, generation, now, now),
                    )
                else:
                    generation = int(row["generation"]) + 1
                    connection.execute(
                        """
                        UPDATE metabase_assignment_state
                        SET generation = ?, is_current = 1, first_seen_at = ?, last_seen_at = ?, missing_polls = 0
                        WHERE performer = ? AND ticket_id = ?
                        """,
                        (generation, now, now, performer, ticket_id),
                    )
                status = "quarantined" if quarantine else "pending"
                self._insert_event(connection, performer, ticket_id, generation, now, status)
                if quarantine:
                    quarantined += 1
                else:
                    detected += 1

            connection.execute(
                "UPDATE metabase_monitor_runs SET updated_at = ?, snapshot_key = ? WHERE performer = ?",
                (now, snapshot_key, performer),
            )
            return SnapshotResult(
                False,
                len(current_ticket_ids),
                detected,
                quarantined_count=quarantined,
                cancelled_count=cancelled,
            )

    def _replace_baseline(
        self,
        connection: sqlite3.Connection,
        performer: str,
        current_ticket_ids: set[int],
        now: str,
        snapshot_key: str,
    ) -> int:
        connection.execute(
            """
            UPDATE metabase_assignment_state
            SET is_current = 0, missing_polls = 0, last_seen_at = ?
            WHERE performer = ?
            """,
            (now, performer),
        )
        for ticket_id in current_ticket_ids:
            row = connection.execute(
                "SELECT generation FROM metabase_assignment_state WHERE performer = ? AND ticket_id = ?",
                (performer, ticket_id),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO metabase_assignment_state
                    (performer, ticket_id, generation, is_current, first_seen_at, last_seen_at, missing_polls)
                    VALUES (?, ?, 1, 1, ?, ?, 0)
                    """,
                    (performer, ticket_id, now, now),
                )
            else:
                connection.execute(
                    """
                    UPDATE metabase_assignment_state
                    SET is_current = 1, first_seen_at = ?, last_seen_at = ?, missing_polls = 0
                    WHERE performer = ? AND ticket_id = ?
                    """,
                    (now, now, performer, ticket_id),
                )
        cancelled = connection.execute(
            """
            UPDATE metabase_assignment_events
            SET status = 'cancelled', error = 'Cancelled during safe baseline reconciliation', updated_at = ?
            WHERE performer = ? AND status IN ('pending', 'processing')
            """,
            (now, performer),
        ).rowcount
        connection.execute(
            """
            INSERT INTO metabase_monitor_runs (performer, initialized_at, updated_at, snapshot_key)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(performer) DO UPDATE SET updated_at = excluded.updated_at,
                snapshot_key = excluded.snapshot_key
            """,
            (performer, now, now, snapshot_key),
        )
        return int(cancelled)

    def _insert_event(
        self,
        connection: sqlite3.Connection,
        performer: str,
        ticket_id: int,
        generation: int,
        now: str,
        status: str,
    ) -> None:
        detected = datetime.fromisoformat(now)
        connection.execute(
            """
            INSERT OR IGNORE INTO metabase_assignment_events
            (performer, ticket_id, generation, status, detected_at, updated_at,
             next_attempt_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                performer,
                ticket_id,
                generation,
                status,
                now,
                now,
                now,
                (detected + self.pending_ttl).isoformat(),
            ),
        )

    @staticmethod
    def _cancel_active_events(
        connection: sqlite3.Connection,
        performer: str,
        ticket_id: int,
        now: str,
        reason: str,
    ) -> int:
        return int(
            connection.execute(
                """
                UPDATE metabase_assignment_events
                SET status = 'cancelled', error = ?, updated_at = ?
                WHERE performer = ? AND ticket_id = ? AND status IN ('pending', 'processing')
                """,
                (reason, now, performer, ticket_id),
            ).rowcount
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
                SET status = 'expired', error = 'Pending event exceeded its time-to-live', updated_at = ?
                WHERE performer = ? AND status IN ('pending', 'processing')
                  AND expires_at IS NOT NULL AND expires_at <= ?
                """,
                (now, performer, now),
            )
            connection.execute(
                """
                UPDATE metabase_assignment_events
                SET status = 'pending', updated_at = ?, next_attempt_at = ?
                WHERE performer = ? AND status = 'processing' AND updated_at < ?
                """,
                (now, now, performer, stale_before),
            )
            rows = connection.execute(
                """
                SELECT event_id, performer, ticket_id, generation, attempts, task_id,
                       detected_at, content_notice_sent
                FROM metabase_assignment_events
                WHERE performer = ? AND status = 'pending'
                  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                ORDER BY detected_at DESC, event_id DESC
                LIMIT ?
                """,
                (performer, now, limit),
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

    def _retry_delay(self, attempts: int) -> float:
        index = min(max(attempts - 1, 0), len(self.retry_delays_seconds) - 1)
        return self.retry_delays_seconds[index]

    def _reschedule(self, event_id: int, error: str) -> None:
        now_dt = datetime.now(timezone.utc)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT attempts FROM metabase_assignment_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            attempts = int(row["attempts"]) if row else 1
            next_attempt = now_dt + timedelta(seconds=self._retry_delay(attempts))
            connection.execute(
                """
                UPDATE metabase_assignment_events
                SET status = 'pending', error = ?, updated_at = ?, next_attempt_at = ?
                WHERE event_id = ? AND status = 'processing'
                """,
                (error[:1000], now_dt.isoformat(), next_attempt.isoformat(), event_id),
            )

    def mark_retry(self, event_id: int, error: str) -> None:
        self._reschedule(event_id, error)

    def mark_waiting_for_content(self, event_id: int) -> None:
        """Defer silently; Telegram receives only the final useful analysis."""
        self._reschedule(event_id, "Waiting for CallCenter comments to become available")

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
