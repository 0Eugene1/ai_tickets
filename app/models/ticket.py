from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Subscriber(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id_abon: int


class TicketMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int | str
    created_at: datetime
    author_role: str
    scope: str
    text: str = Field(min_length=1)


class DiagnosticCheck(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    status: Literal["found", "not_found", "ok", "error", "unknown", "unavailable"]
    value: Any | None = None
    source: str
    checked_at: datetime | None = None


class Diagnostics(BaseModel):
    model_config = ConfigDict(extra="allow")

    platform: str | None = None
    app_version: str | None = None
    os_version: str | None = None
    bugshaker_found: bool | None = None
    checks: list[DiagnosticCheck] = Field(default_factory=list)


class TicketInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: int
    ticket_number: str
    subject_id: int
    subject: str
    group_id: int
    performer: str
    status: int
    created_at: datetime
    updated_at: datetime
    subscriber: Subscriber
    messages: list[TicketMessage] = Field(default_factory=list)
    diagnostics: Diagnostics = Field(default_factory=Diagnostics)


class Evidence(BaseModel):
    fact: str
    source: str


class SimilarCase(BaseModel):
    case_id: str
    title: str
    resolution: str
    source: str
    score: float = Field(ge=0, le=1)
    required_checks: list[str] = Field(default_factory=list)
    match_status: Literal["candidate", "relevant", "partial", "rejected"] = "candidate"
    match_reason: str | None = None
    automatic_answer_allowed: bool = False


class TicketAnalysis(BaseModel):
    ticket_id: int
    status: Literal["completed"] = "completed"
    analysis_mode: Literal["rules", "llm", "rules_fallback"] = "rules"
    analysis_model: str | None = None
    category: str
    category_label: str = ""
    confidence: Literal["low", "medium", "high"]
    summary: str
    confirmed_facts: list[str] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)
    required_checks: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    operator_reply: str = ""
    evidence: list[Evidence] = Field(default_factory=list)
    similar_cases: list[SimilarCase] = Field(default_factory=list)
    report_markdown: str = ""
