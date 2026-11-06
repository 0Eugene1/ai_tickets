from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status

from app.models.task import StartTaskResponse, TaskResultResponse, TaskStatusResponse
from app.models.ticket import TicketInput
from app.services.task_service import TaskService


router = APIRouter()


def _service(request: Request) -> TaskService:
    return request.app.state.task_service


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/api/v1/tickets/start", response_model=StartTaskResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_ticket(
    ticket: TicketInput,
    background_tasks: BackgroundTasks,
    request: Request,
) -> StartTaskResponse:
    service = _service(request)
    record, created = service.start(ticket)
    if created:
        background_tasks.add_task(service.process, record.task_id)
    return StartTaskResponse(task_id=record.task_id, status=record.status, duplicate=not created)


@router.get("/api/v1/tickets/status/{task_id}", response_model=TaskStatusResponse)
def task_status(task_id: str, request: Request) -> TaskStatusResponse:
    record = _service(request).get(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskStatusResponse(
        task_id=record.task_id,
        status=record.status,
        created_at=datetime.fromisoformat(record.created_at),
        updated_at=datetime.fromisoformat(record.updated_at),
        error=record.error,
    )


@router.get("/api/v1/tickets/result/{task_id}", response_model=TaskResultResponse)
def task_result(task_id: str, request: Request) -> TaskResultResponse:
    service = _service(request)
    record = service.get(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if record.status != "completed":
        raise HTTPException(status_code=409, detail={"status": record.status, "error": record.error})
    result = service.result(task_id)
    if result is None:
        raise HTTPException(status_code=500, detail="Completed task has no result")
    return TaskResultResponse(task_id=task_id, result=result)
