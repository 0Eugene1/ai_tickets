from pathlib import Path

from app.analyzers.ticket_analyzer import TicketAnalyzer
from app.knowledge.store import KnowledgeStore
from app.models.ticket import TicketInput


def test_lexical_candidate_does_not_raise_confidence(tmp_path: Path, ticket_payload: dict) -> None:
    (tmp_path / "case.jsonl").write_text(
        '{"id":"case","title":"Родительский контроль","category":"parental_control","symptoms":"Родительский контроль не отображается","resolution":"Старое решение","source":"test","confirmed":true}\n',
        encoding="utf-8",
    )
    ticket_payload["diagnostics"] = {
        "platform": None,
        "app_version": None,
        "os_version": None,
        "bugshaker_found": None,
        "checks": [],
    }

    result = TicketAnalyzer(KnowledgeStore(tmp_path)).analyze(TicketInput.model_validate(ticket_payload))

    assert result.similar_cases
    assert result.similar_cases[0].match_status == "candidate"
    assert result.confidence == "low"
    assert "совпадение причины ещё не подтверждено" in result.report_markdown
