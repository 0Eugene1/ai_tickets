from app.privacy.redactor import Redactor


def test_redacts_common_pii() -> None:
    text = "ФИО: Иванов Иван Иванович, телефон +7 (923) 123-45-67, login: ivanov, @staff_user"
    redacted = Redactor().redact_text(text)

    assert "Иванов" not in redacted
    assert "923" not in redacted
    assert "ivanov" not in redacted.lower()
    assert "staff_user" not in redacted


def test_external_payload_removes_internal_identifiers() -> None:
    payload = {
        "ticket_id": 123,
        "ticket_number": "00-000-123",
        "performer": "secret-login",
        "subscriber": {"id_abon": 999},
        "messages": [{"text": "Звонить 8 923 123 45 67"}],
    }
    safe = Redactor().for_external_llm(payload)

    assert "ticket_id" not in safe
    assert "ticket_number" not in safe
    assert "performer" not in safe
    assert "subscriber" not in safe
    assert "923" not in safe["messages"][0]["text"]


def test_redacts_ip_address_postal_address_and_two_part_name() -> None:
    text = "Игорь Леонидович\nФетисов Илья\nЛенина пр., 115 - 38, подъезд 2\nIP 10.160.97.3"
    redacted = Redactor().redact_text(text)

    assert "Леонидович" not in redacted
    assert "Фетисов" not in redacted
    assert "Ленина" not in redacted
    assert "10.160.97.3" not in redacted
    assert "[ФИО]" in redacted
    assert "[АДРЕС]" in redacted
    assert "[IP-АДРЕС]" in redacted


def test_redacts_surname_left_before_existing_name_marker() -> None:
    assert Redactor().redact_text("Мищенко [ФИО]") == "[ФИО]"


def test_redacts_client_name_and_preserves_problem_after_inline_address() -> None:
    text = (
        "client_name: Иванова Мария ни на андроиде, ни на айфоне не работает экран\n"
        "По адресу проспект Ленинградский, 43-161 у абонента не отображаются камеры"
    )
    redacted = Redactor().redact_text(text)

    assert "Иванова" not in redacted
    assert "Ленинградский" not in redacted
    assert "не работает экран" in redacted
    assert "не отображаются камеры" in redacted


def test_redacts_short_structured_ids_and_address_variants() -> None:
    text = (
        "id_client: 92114, id_house=73\n"
        "Ворошилова, 7 - 52\n"
        "Солнечный бульвар, 20 - 36\n"
        "Юбилейная 3, 3 подъезд\n"
        "[АДРЕС], ленина 45а - 57\n"
        "[АДРЕС] бр., 5 - 9602\n"
        "Проверили с адреса Бакинского"
    )
    redacted = Redactor().redact_text(text)

    assert "92114" not in redacted
    assert "Ворошилова" not in redacted
    assert "Солнечный" not in redacted
    assert "Юбилейная" not in redacted
    assert "ленина" not in redacted
    assert "9602" not in redacted
    assert "Бакинского" not in redacted
    assert redacted.count("[АДРЕС]") == 6


def test_redacts_city_street_house_and_name_from_chat() -> None:
    text = "Г. Кемерово улица Якимова д.7 Баяндина Елизавета Евгеньевна"

    redacted = Redactor().redact_text(text)

    assert "Кемерово" not in redacted
    assert "Якимова" not in redacted
    assert "Баяндина" not in redacted
    assert "[АДРЕС]" in redacted
    assert "[ФИО]" in redacted
