from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI

from app.analyzers.ticket_analyzer import TicketAnalyzer
from app.api.routes import router
from app.config import Settings, get_settings
from app.integrations.llm import build_llm_provider
from app.integrations.metabase_tickets import MetabaseTicketSource
from app.integrations.telegram import TelegramNotifier
from app.knowledge.store import KnowledgeStore
from app.privacy.redactor import Redactor
from app.services.metabase_monitor import MetabaseMonitor
from app.services.monitor_repository import MonitorRepository
from app.services.repository import TaskRepository
from app.services.task_service import TaskService


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    # Request URLs for Telegram contain the bot token. Never let dependency
    # request logging write those URLs to local logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    repository = TaskRepository(settings.database_path)
    redactor = Redactor()
    task_service = TaskService(
        repository=repository,
        analyzer=TicketAnalyzer(KnowledgeStore(settings.knowledge_path)),
        redactor=redactor,
        llm=build_llm_provider(settings),
        telegram=TelegramNotifier(settings),
    )
    monitor: MetabaseMonitor | None = None
    if settings.metabase_polling_enabled:
        monitor = MetabaseMonitor(
            source=MetabaseTicketSource(settings),
            repository=MonitorRepository(settings.database_path),
            task_service=task_service,
            redactor=redactor,
            poll_interval_seconds=settings.metabase_poll_interval_seconds,
            empty_ticket_grace_seconds=settings.metabase_empty_ticket_grace_seconds,
            baseline_on_first_run=settings.metabase_baseline_on_first_run,
        )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        monitor_task: asyncio.Task[None] | None = None
        if monitor is not None:
            monitor_task = asyncio.create_task(monitor.run_forever())
        try:
            yield
        finally:
            if monitor is not None and monitor_task is not None:
                monitor.stop()
                await monitor_task

    application = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    application.state.task_service = task_service
    application.state.metabase_monitor = monitor
    application.include_router(router)
    return application


app = create_app()
