import asyncio

from app.integrations.llm import OpenAICompatibleLLMProvider, ResilientLLMProvider
from app.models.ticket import TicketAnalysis


class FailingProvider:
    model = "test-model"

    async def refine(self, safe_context, draft):
        raise RuntimeError("temporary failure")


def test_external_failure_keeps_local_analysis_available() -> None:
    draft = TicketAnalysis(
        ticket_id=1,
        category="other_mplk",
        confidence="low",
        summary="Недостаточно данных",
    )

    result = asyncio.run(
        ResilientLLMProvider(FailingProvider()).refine({}, draft)  # type: ignore[arg-type]
    )

    assert result.analysis_mode == "rules_fallback"
    assert result.analysis_model == "test-model"
    assert result.summary == draft.summary


def test_partial_llm_case_verdict_keeps_verified_source_metadata() -> None:
    draft = {
        "confidence": "low",
        "similar_cases": [
            {
                "case_id": "kb-1",
                "title": "Похожий случай",
                "resolution": "Проверить состояние услуги",
                "source": "confluence:42",
                "score": 0.7,
                "required_checks": [],
                "match_status": "candidate",
                "match_reason": None,
            }
        ],
    }
    refined = {
        "confidence": "средняя",
        "confirmed_facts": ["Придуманный моделью факт"],
        "evidence": [{"fact": "Придуманный моделью факт"}],
        "similar_cases": [
            {"case_id": "kb-1", "match_status": "relevant", "match_reason": "Совпали симптомы"}
        ],
    }

    merged = OpenAICompatibleLLMProvider._merge_with_draft(draft, refined)

    assert merged["confidence"] == "medium"
    assert merged["similar_cases"][0]["match_status"] == "relevant"
    assert merged["similar_cases"][0]["source"] == "confluence:42"
    assert merged["similar_cases"][0]["score"] == 0.7
    assert merged["confirmed_facts"] == []
    assert merged["evidence"] == []
