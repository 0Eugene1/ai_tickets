from __future__ import annotations

import argparse
import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.integrations.confluence import ConfluenceConfig, ConfluenceError
from app.integrations.confluence_publisher import ConfluencePublisher


SPACE_KEY = "TICKETAI"
SPACE_NAME = "Ticket Assistant — база знаний ИИ"
TELEGRAM_TITLE = "Ticket Assistant — база знаний из Telegram"
CONFLUENCE_TITLE = "Ticket Assistant — база знаний из Confluence"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def render_knowledge_page(title: str, records: list[dict[str, Any]], source_name: str) -> str:
    generated = datetime.now(timezone.utc).date().isoformat()
    sections: list[str] = []
    for number, record in enumerate(records, start=1):
        checks = [str(value) for value in record.get("required_checks", []) if value]
        checks_html = (
            "<ul>" + "".join(f"<li>{html.escape(value)}</li>" for value in checks) + "</ul>"
            if checks
            else "<p>Не указаны.</p>"
        )
        sections.append(
            f"<h2>{number}. {html.escape(str(record.get('title', 'Проверенный случай')))}</h2>"
            f"<p><strong>ID:</strong> <code>{html.escape(str(record.get('id', '')))}</code><br/>"
            f"<strong>Категория:</strong> <code>{html.escape(str(record.get('category', 'other_mplk')))}</code></p>"
            f"<h3>Проблема</h3><p>{html.escape(str(record.get('symptoms', '')))}</p>"
            f"<h3>Решение или вывод</h3><p>{html.escape(str(record.get('resolution', '')))}</p>"
            f"<h3>Что проверить перед применением</h3>{checks_html}"
            "<hr/>"
        )
    return (
        f"<h1>{html.escape(title)}</h1>"
        f"<p><strong>Источник:</strong> {html.escape(source_name)}<br/>"
        f"<strong>Карточек:</strong> {len(records)}<br/>"
        f"<strong>Дата сборки:</strong> {generated}</p>"
        "<ac:structured-macro ac:name=\"warning\"><ac:rich-text-body>"
        "<p>Материал предназначен для поиска похожих случаев ИИ-помощником. "
        "Совпадение является подсказкой, а не доказанным решением конкретного тикета. "
        "Автоматический ответ абоненту запрещён; требуется проверка сотрудником.</p>"
        "</ac:rich-text-body></ac:structured-macro>"
        "<p>В публикацию включены только обезличенные и прошедшие смысловой отбор карточки. "
        "Сырые переписки, телефоны, адреса, логины, токены и изолированные документы отсутствуют.</p>"
        + "".join(sections)
    )


def render_homepage(telegram_url: str, confluence_url: str) -> str:
    return (
        "<h1>Ticket Assistant — база знаний ИИ</h1>"
        "<p>Единая точка входа в проверенную базу знаний для анализа обращений МПЛК и "
        "Goodline — город.</p>"
        "<h2>Данные для ИИ</h2><ol>"
        f"<li><a href=\"{html.escape(telegram_url, quote=True)}\">База знаний из Telegram</a> — "
        "подтверждённые ответы доверенных авторов.</li>"
        f"<li><a href=\"{html.escape(confluence_url, quote=True)}\">База знаний из Confluence</a> — "
        "проверенные инструкции, диагностика и известные ограничения.</li></ol>"
        "<h2>Инструкция для ИИ</h2><p>Открой обе страницы. Сопоставь описание нового тикета "
        "с полем «Проблема». Используй поле «Решение или вывод» только как гипотезу и обязательно "
        "включай перечисленные проверки. Не выдумывай отсутствующие факты, не отправляй ответ "
        "абоненту автоматически и явно указывай, что итог требует проверки сотрудником.</p>"
    )


def publish_bundle(runtime_dir: Path, space_key: str, space_name: str) -> dict[str, Any]:
    telegram_records = _read_jsonl(runtime_dir / "telegram-knowledge.jsonl")
    confluence_records = _read_jsonl(runtime_dir / "confluence-knowledge.jsonl")
    settings = get_settings()
    if not settings.confluence_base_url or not settings.confluence_api_token:
        raise ValueError("Confluence connection is not configured")

    config = ConfluenceConfig(
        base_url=settings.confluence_base_url,
        auth_mode=settings.confluence_auth_mode,
        email=settings.confluence_email,
        api_token=settings.confluence_api_token,
        timeout_seconds=settings.confluence_timeout_seconds,
    )
    with ConfluencePublisher(config) as publisher:
        space, space_created = publisher.ensure_space(
            space_key,
            space_name,
            "Обезличенная проверенная база знаний для ИИ-помощника по тикетам.",
        )
        homepage = space.get("homepage") if isinstance(space.get("homepage"), dict) else {}
        homepage_id = str(homepage.get("id", ""))
        if not homepage_id:
            refreshed = publisher.get_space(space_key) or {}
            homepage = (
                refreshed.get("homepage")
                if isinstance(refreshed.get("homepage"), dict)
                else {}
            )
            homepage_id = str(homepage.get("id", ""))
        if not homepage_id:
            raise ConfluenceError("Created space has no homepage")

        telegram_page, telegram_created = publisher.upsert_page(
            space_key=space_key,
            title=TELEGRAM_TITLE,
            storage_body=render_knowledge_page(
                TELEGRAM_TITLE, telegram_records, "проверенная выгрузка Telegram"
            ),
            ancestor_id=homepage_id,
        )
        confluence_page, confluence_created = publisher.upsert_page(
            space_key=space_key,
            title=CONFLUENCE_TITLE,
            storage_body=render_knowledge_page(
                CONFLUENCE_TITLE, confluence_records, "проверенные документы Confluence"
            ),
            ancestor_id=homepage_id,
        )
        telegram_url = publisher.web_url(telegram_page)
        confluence_url = publisher.web_url(confluence_page)
        homepage_page = publisher.update_page(
            homepage_id, render_homepage(telegram_url, confluence_url)
        )
        return {
            "space_key": space_key,
            "space_created": space_created,
            "homepage_url": publisher.web_url(homepage_page),
            "telegram": {
                "cards": len(telegram_records),
                "created": telegram_created,
                "url": telegram_url,
            },
            "confluence": {
                "cards": len(confluence_records),
                "created": confluence_created,
                "url": confluence_url,
            },
        }


def publish_under_parent(runtime_dir: Path, parent_page_id: str) -> dict[str, Any]:
    telegram_records = _read_jsonl(runtime_dir / "telegram-knowledge.jsonl")
    confluence_records = _read_jsonl(runtime_dir / "confluence-knowledge.jsonl")
    settings = get_settings()
    if not settings.confluence_base_url or not settings.confluence_api_token:
        raise ValueError("Confluence connection is not configured")

    config = ConfluenceConfig(
        base_url=settings.confluence_base_url,
        auth_mode=settings.confluence_auth_mode,
        email=settings.confluence_email,
        api_token=settings.confluence_api_token,
        timeout_seconds=settings.confluence_timeout_seconds,
    )
    with ConfluencePublisher(config) as publisher:
        parent = publisher.get_page(parent_page_id)
        space = parent.get("space") if isinstance(parent.get("space"), dict) else {}
        space_key = str(space.get("key", ""))
        if not space_key:
            raise ConfluenceError("Parent page has no space key")

        index_page, index_created = publisher.upsert_page(
            space_key=space_key,
            title=SPACE_NAME,
            storage_body=(
                "<h1>Ticket Assistant — база знаний ИИ</h1>"
                "<p>Публикация базы знаний выполняется. Обновите страницу позже.</p>"
            ),
            ancestor_id=parent_page_id,
        )
        index_page_id = str(index_page.get("id", ""))
        if not index_page_id:
            raise ConfluenceError("Created index page has no id")

        telegram_page, telegram_created = publisher.upsert_page(
            space_key=space_key,
            title=TELEGRAM_TITLE,
            storage_body=render_knowledge_page(
                TELEGRAM_TITLE, telegram_records, "проверенная выгрузка Telegram"
            ),
            ancestor_id=index_page_id,
        )
        confluence_page, confluence_created = publisher.upsert_page(
            space_key=space_key,
            title=CONFLUENCE_TITLE,
            storage_body=render_knowledge_page(
                CONFLUENCE_TITLE, confluence_records, "проверенные документы Confluence"
            ),
            ancestor_id=index_page_id,
        )
        telegram_url = publisher.web_url(telegram_page)
        confluence_url = publisher.web_url(confluence_page)
        index_page = publisher.update_page(
            index_page_id, render_homepage(telegram_url, confluence_url)
        )
        return {
            "space_key": space_key,
            "parent_page_id": parent_page_id,
            "index_created": index_created,
            "homepage_url": publisher.web_url(index_page),
            "telegram": {
                "cards": len(telegram_records),
                "created": telegram_created,
                "url": telegram_url,
            },
            "confluence": {
                "cards": len(confluence_records),
                "created": confluence_created,
                "url": confluence_url,
            },
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish the two knowledge pages to Confluence")
    parser.add_argument("--runtime-dir", type=Path, default=Path("data/knowledge/runtime"))
    parser.add_argument("--space-key", default=SPACE_KEY)
    parser.add_argument("--space-name", default=SPACE_NAME)
    parser.add_argument("--parent-page-id")
    args = parser.parse_args()
    result = (
        publish_under_parent(args.runtime_dir, args.parent_page_id)
        if args.parent_page_id
        else publish_bundle(args.runtime_dir, args.space_key, args.space_name)
    )
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
