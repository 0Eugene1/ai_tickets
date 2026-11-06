from app.knowledge.publish_confluence_bundle import render_homepage, render_knowledge_page


def test_rendered_knowledge_page_contains_only_normalized_fields() -> None:
    body = render_knowledge_page(
        "База",
        [
            {
                "id": "case-1",
                "title": "Камера не открывается",
                "category": "camera_archive",
                "symptoms": "Нет видео",
                "resolution": "Проверить статус камеры",
                "required_checks": ["Проверить договор"],
                "source_url": "https://should-not-be-published.invalid/raw",
                "message_ids": [123],
            }
        ],
        "test",
    )

    assert "Камера не открывается" in body
    assert "Проверить договор" in body
    assert "should-not-be-published" not in body
    assert "message_ids" not in body


def test_homepage_links_both_knowledge_pages() -> None:
    body = render_homepage("https://example/telegram", "https://example/confluence")

    assert "https://example/telegram" in body
    assert "https://example/confluence" in body
