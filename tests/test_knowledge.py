import json
from pathlib import Path

from app.knowledge.store import KnowledgeStore


def test_search_prefers_requested_category(tmp_path: Path) -> None:
    records = [
        {"id": "wrong", "title": "Приложение и услуга", "category": "payment", "symptoms": "Услуга не отображается", "resolution": "Оплата", "source": "test", "confirmed": True},
        {"id": "right", "title": "Родительский контроль", "category": "parental_control", "symptoms": "Услуга не отображается", "resolution": "Проверить договор", "source": "test", "confirmed": True},
    ]
    (tmp_path / "cases.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
        encoding="utf-8",
    )

    matches = KnowledgeStore(tmp_path).search(
        "Родительский контроль: услуга не отображается",
        category="parental_control",
    )

    assert [match.case_id for match in matches] == ["right"]


def test_search_does_not_match_on_generic_ticket_words(tmp_path: Path) -> None:
    record = {"id": "camera", "title": "Камера", "category": "camera_archive", "symptoms": "Абонент сообщает проблему в приложении", "resolution": "Проверить камеру", "source": "test", "confirmed": True}
    (tmp_path / "cases.jsonl").write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

    matches = KnowledgeStore(tmp_path).search(
        "Проблема абонента в приложении с оплатой",
        category="camera_archive",
    )

    assert matches == []


def test_search_finds_parental_control_status_mismatch(tmp_path: Path) -> None:
    records = [
        {
            "id": "rk-status-mismatch",
            "title": "Родительский контроль активен, но МПЛК предлагает подключить услугу",
            "category": "parental_control",
            "symptoms": "РК входит в тариф и активен в учётной системе, но МПЛК создаёт заявку на подключение",
            "resolution": "Проверить расхождение статуса между учётной системой и МПЛК; не подключать повторно",
            "source": "confluence:test",
            "confirmed": True,
        },
        {
            "id": "unrelated",
            "title": "Подключение домофона",
            "category": "door_access",
            "symptoms": "Нет договора",
            "resolution": "Оформить заявку",
            "source": "test",
            "confirmed": True,
        },
    ]
    (tmp_path / "cases.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
        encoding="utf-8",
    )

    matches = KnowledgeStore(tmp_path).search(
        "Услуга РК входит в тариф, но МПЛК предлагает подключить и создаёт заявку",
        category="parental_control",
    )

    assert matches
    assert matches[0].case_id == "rk-status-mismatch"


def test_search_treats_latin_and_cyrillic_sms_as_same_token(tmp_path: Path) -> None:
    record = {
        "id": "sms-timeout",
        "title": "Кнопка SMS-кода при авторизации ещё не активна",
        "category": "authentication",
        "symptoms": "Попытка подтверждения истекла",
        "resolution": "Запросить SMS заново",
        "source": "test",
        "confirmed": True,
    }
    (tmp_path / "cases.jsonl").write_text(
        json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    matches = KnowledgeStore(tmp_path).search(
        "Не приходит код авторизации по СМС", category="authentication"
    )

    assert matches[0].case_id == "sms-timeout"


def test_search_matches_common_domain_inflections_and_prefers_exact_title(tmp_path: Path) -> None:
    records = [
        {
            "id": "permissions",
            "title": "Разрешения для звонков домофона",
            "category": "intercom_calls",
            "symptoms": "Домофонный звонок не приходит",
            "resolution": "Обновить разрешения",
            "source": "test",
            "confirmed": True,
        },
        {
            "id": "one-number",
            "title": "Домофонный звонок не приходит только на один номер",
            "category": "intercom_calls",
            "symptoms": "На остальных номерах звонок работает",
            "resolution": "Проверить привязку номера",
            "source": "test",
            "confirmed": True,
        },
    ]
    (tmp_path / "cases.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
        encoding="utf-8",
    )

    matches = KnowledgeStore(tmp_path).search(
        "На один номер домофон не звонит, на другие звонит",
        category="intercom_calls",
    )

    assert matches[0].case_id == "one-number"


def test_soft_category_search_keeps_strong_cross_category_case(tmp_path: Path) -> None:
    records = [
        {
            "id": "camera-generic",
            "title": "Камера не отображается",
            "category": "camera_archive",
            "symptoms": "Нет камеры",
            "resolution": "Проверить камеру",
            "source": "test",
            "confirmed": True,
        },
        {
            "id": "account-exact",
            "title": "После выбора адреса пропали двери, камеры и интернет",
            "category": "contract_selection",
            "symptoms": "Одновременно отсутствуют несколько сервисов",
            "resolution": "Проверить профиль и связи мастер-аккаунта",
            "source": "test",
            "confirmed": True,
        },
    ]
    (tmp_path / "cases.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
        encoding="utf-8",
    )

    strict = KnowledgeStore(tmp_path).search(
        "После выбора адреса пропали двери, камеры и интернет",
        category="camera_archive",
    )
    soft = KnowledgeStore(tmp_path).search(
        "После выбора адреса пропали двери, камеры и интернет",
        category="camera_archive",
        strict_category=False,
    )

    assert "account-exact" not in [case.case_id for case in strict]
    assert soft[0].case_id == "account-exact"


def test_soft_search_prefers_domain_case_over_generic_callback(tmp_path: Path) -> None:
    records = [
        {
            "id": "generic-callback",
            "title": "После подключения создаётся заявка на звонок",
            "category": "service_display",
            "symptoms": "Кнопка подключения создаёт обратный звонок",
            "resolution": "Дождаться оператора",
            "source": "test",
            "confirmed": True,
        },
        {
            "id": "rk-status",
            "title": "Родительский контроль активен, но предлагается подключить",
            "category": "parental_control",
            "symptoms": "Услуга есть в биллинге, приложение создаёт заявку",
            "resolution": "Не подключать повторно, проверить расхождение статусов",
            "source": "test",
            "confirmed": True,
        },
    ]
    (tmp_path / "cases.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
        encoding="utf-8",
    )

    matches = KnowledgeStore(tmp_path).search(
        "Родительский контроль активен, но приложение создаёт заявку на звонок",
        category="parental_control",
        strict_category=False,
    )

    assert matches[0].case_id == "rk-status"
