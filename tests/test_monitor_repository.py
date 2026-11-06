from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from app.services.monitor_repository import MonitorRepository


def test_migrates_legacy_monitor_schema_without_replaying_pending_events(tmp_path: Path) -> None:
    database = tmp_path / "legacy.db"
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE metabase_monitor_runs (
                performer TEXT PRIMARY KEY, initialized_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE metabase_assignment_state (
                performer TEXT NOT NULL, ticket_id INTEGER NOT NULL, generation INTEGER NOT NULL,
                is_current INTEGER NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
                PRIMARY KEY (performer, ticket_id)
            );
            CREATE TABLE metabase_assignment_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT, performer TEXT NOT NULL,
                ticket_id INTEGER NOT NULL, generation INTEGER NOT NULL, status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, task_id TEXT, error TEXT,
                content_notice_sent INTEGER NOT NULL DEFAULT 0,
                detected_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE (performer, ticket_id, generation)
            );
            """
        )
        connection.execute(
            "INSERT INTO metabase_monitor_runs VALUES (?, ?, ?)",
            ("NIKULINA_VS", now, now),
        )
        connection.execute(
            "INSERT INTO metabase_assignment_state VALUES (?, ?, 1, 1, ?, ?)",
            ("NIKULINA_VS", 1, now, now),
        )
        connection.execute(
            """
            INSERT INTO metabase_assignment_events
            (performer, ticket_id, generation, status, detected_at, updated_at)
            VALUES (?, ?, 1, 'pending', ?, ?)
            """,
            ("NIKULINA_VS", 1, now, now),
        )

    repository = MonitorRepository(database)
    result = repository.sync_snapshot(
        "NIKULINA_VS", {1}, baseline_on_first_run=True, snapshot_key="active-only"
    )

    assert result.baseline_reason == "snapshot-filter-changed"
    assert result.cancelled_count == 1
    assert repository.claim_pending("NIKULINA_VS") == []


def test_first_snapshot_is_baseline_and_does_not_enqueue_old_tickets(tmp_path: Path) -> None:
    repository = MonitorRepository(tmp_path / "state.db")

    first = repository.sync_snapshot(
        "NIKULINA_VS", {1, 2}, baseline_on_first_run=True
    )
    repeated = repository.sync_snapshot(
        "NIKULINA_VS", {1, 2}, baseline_on_first_run=True
    )

    assert first.baseline_created is True
    assert first.detected_count == 0
    assert repeated.baseline_created is False
    assert repeated.detected_count == 0
    assert repository.claim_pending("NIKULINA_VS") == []


def test_new_and_repeated_assignments_get_distinct_generations(tmp_path: Path) -> None:
    repository = MonitorRepository(
        tmp_path / "state.db", departure_confirmation_polls=1
    )
    repository.sync_snapshot("NIKULINA_VS", {1}, baseline_on_first_run=True)

    entered = repository.sync_snapshot("NIKULINA_VS", {1, 2}, baseline_on_first_run=True)
    first_event = repository.claim_pending("NIKULINA_VS")
    repository.mark_delivered(first_event[0].event_id, "task-1")
    repository.sync_snapshot("NIKULINA_VS", {1}, baseline_on_first_run=True)
    reentered = repository.sync_snapshot("NIKULINA_VS", {1, 2}, baseline_on_first_run=True)
    second_event = repository.claim_pending("NIKULINA_VS")

    assert entered.detected_count == 1
    assert first_event[0].ticket_id == 2
    assert first_event[0].generation == 1
    assert first_event[0].content_notice_sent is False
    assert reentered.detected_count == 1
    assert second_event[0].ticket_id == 2
    assert second_event[0].generation == 2


def test_analyzed_task_is_reused_after_delivery_retry(tmp_path: Path) -> None:
    repository = MonitorRepository(tmp_path / "state.db", retry_delays_seconds=(0,))
    repository.sync_snapshot("NIKULINA_VS", set(), baseline_on_first_run=True)
    repository.sync_snapshot("NIKULINA_VS", {7}, baseline_on_first_run=True)
    event = repository.claim_pending("NIKULINA_VS")[0]

    repository.mark_analyzed(event.event_id, "completed-task")
    repository.mark_retry(event.event_id, "Telegram timeout")
    retried = repository.claim_pending("NIKULINA_VS")[0]

    assert retried.task_id == "completed-task"
    assert retried.attempts == 2


def test_waiting_for_content_is_silent_and_rescheduled(tmp_path: Path) -> None:
    repository = MonitorRepository(tmp_path / "state.db", retry_delays_seconds=(0,))
    repository.sync_snapshot("NIKULINA_VS", set(), baseline_on_first_run=True)
    repository.sync_snapshot("NIKULINA_VS", {9}, baseline_on_first_run=True)
    event = repository.claim_pending("NIKULINA_VS")[0]

    repository.mark_waiting_for_content(event.event_id)
    retried = repository.claim_pending("NIKULINA_VS")[0]

    assert retried.content_notice_sent is False
    assert retried.task_id is None


def test_single_empty_snapshot_is_ignored_without_reentry_flood(tmp_path: Path) -> None:
    repository = MonitorRepository(
        tmp_path / "state.db",
        snapshot_drop_guard_min_size=3,
        snapshot_drop_guard_fraction=0.5,
    )
    repository.sync_snapshot("NIKULINA_VS", {1, 2, 3}, baseline_on_first_run=True)

    empty = repository.sync_snapshot("NIKULINA_VS", set(), baseline_on_first_run=True)
    recovered = repository.sync_snapshot(
        "NIKULINA_VS", {1, 2, 3}, baseline_on_first_run=True
    )

    assert empty.snapshot_ignored is True
    assert recovered.detected_count == 0
    assert repository.claim_pending("NIKULINA_VS") == []


def test_large_new_batch_is_quarantined(tmp_path: Path) -> None:
    repository = MonitorRepository(tmp_path / "state.db", max_new_events_per_poll=2)
    repository.sync_snapshot("NIKULINA_VS", set(), baseline_on_first_run=True)

    result = repository.sync_snapshot(
        "NIKULINA_VS", {1, 2, 3}, baseline_on_first_run=True
    )

    assert result.detected_count == 0
    assert result.quarantined_count == 3
    assert repository.claim_pending("NIKULINA_VS") == []


def test_snapshot_key_change_reconciles_without_historical_events(tmp_path: Path) -> None:
    repository = MonitorRepository(tmp_path / "state.db", retry_delays_seconds=(0,))
    repository.sync_snapshot(
        "NIKULINA_VS", {1}, baseline_on_first_run=False, snapshot_key="all-statuses"
    )
    assert repository.claim_pending("NIKULINA_VS")

    result = repository.sync_snapshot(
        "NIKULINA_VS", {2}, baseline_on_first_run=True, snapshot_key="active-only"
    )

    assert result.baseline_created is True
    assert result.baseline_reason == "snapshot-filter-changed"
    assert repository.claim_pending("NIKULINA_VS") == []
