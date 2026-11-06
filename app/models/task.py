from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.models.ticket import TicketAnalysis


TaskStatus = Literal["queued", "processing", "completed", "error"]


class StartTaskResponse(BaseModel):
    task_id: str
    status: TaskStatus
    duplicate: bool


class TaskStatusResponse(BaseModel):
    task_id: str
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    error: str | None = None


class TaskResultResponse(BaseModel):
    task_id: str
    result: TicketAnalysis
