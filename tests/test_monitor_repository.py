from pathlib import Path

from app.services.monitor_repository import MonitorRepository


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
    repository = MonitorRepository(tmp_path / "state.db")
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
    repository = MonitorRepository(tmp_path / "state.db")
    repository.sync_snapshot("NIKULINA_VS", set(), baseline_on_first_run=True)
    repository.sync_snapshot("NIKULINA_VS", {7}, baseline_on_first_run=True)
    event = repository.claim_pending("NIKULINA_VS")[0]

    repository.mark_analyzed(event.event_id, "completed-task")
    repository.mark_retry(event.event_id, "Telegram timeout")
    retried = repository.claim_pending("NIKULINA_VS")[0]

    assert retried.task_id == "completed-task"
    assert retried.attempts == 2


def test_waiting_notice_is_remembered_while_event_stays_pending(tmp_path: Path) -> None:
    repository = MonitorRepository(tmp_path / "state.db")
    repository.sync_snapshot("NIKULINA_VS", set(), baseline_on_first_run=True)
    repository.sync_snapshot("NIKULINA_VS", {9}, baseline_on_first_run=True)
    event = repository.claim_pending("NIKULINA_VS")[0]

    repository.mark_waiting_for_content(event.event_id)
    retried = repository.claim_pending("NIKULINA_VS")[0]

    assert retried.content_notice_sent is True
    assert retried.task_id is None
