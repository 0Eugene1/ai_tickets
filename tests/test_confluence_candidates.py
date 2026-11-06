from app.knowledge.confluence_candidates import score_document, sensitive_markers


def _record(title: str, text: str, space: str = "SSG") -> dict:
    return {"title": title, "text": text, "space_key": space, "labels": []}


def test_support_instructions_are_high_relevance() -> None:
    score, reasons, exclusion = score_document(
        _record(
            "Инструкция: проблема авторизации в мобильном приложении",
            "Если вход не работает, проверить аккаунт и выполнить диагностику. " * 15,
        )
    )
    assert exclusion is None
    assert score >= 12
    assert any(reason.startswith("title_support:") for reason in reasons)


def test_project_administration_is_penalized() -> None:
    score, reasons, exclusion = score_document(
        _record("Еженедельный отчет", "План проекта и обсуждение команды. " * 20, "FQAA")
    )
    assert exclusion is None
    assert score < 6
    assert any(reason.startswith("project_admin:") for reason in reasons)


def test_empty_and_placeholder_pages_are_excluded() -> None:
    assert score_document(_record("Home", ""))[2] == "empty"
    assert score_document(_record("Оплата", "дочерняя страница"))[2] == "placeholder_or_redirect"


def test_detects_plaintext_credential_material() -> None:
    assert "login_password_table" in sensitive_markers("Тестовые данные\nЛогин Пароль Услуги")
    assert "password_assignment" in sensitive_markers("password: do-not-store")
    assert sensitive_markers("Пользователь забыл пароль и хочет восстановить доступ") == []


def test_detects_inline_privileged_credential() -> None:
    assert "privileged_inline_credential" in sensitive_markers("стенд: root abc12345xyz")


def test_detects_test_account_pin() -> None:
    assert "test_account_pin" in sensitive_markers(
        "Тестовые данные. Тестовый аккаунт: номер скрыт, код 5475"
    )
