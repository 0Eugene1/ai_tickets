from app.analyzers.classifier import classify_text


def test_dr_web_is_not_mistaken_for_door_key_due_to_podklyuchit() -> None:
    assert classify_text("Не могу подключить антивирус Dr.Web").category == "other_mplk"


def test_downloaded_record_is_camera_archive() -> None:
    assert classify_text("На iPhone запись скачалась, но в Фото её нет").category == "camera_archive"


def test_inflected_intercom_call_is_recognized() -> None:
    assert classify_text("На один номер домофон не звонит").category == "intercom_calls"


def test_guest_access_code_is_door_access() -> None:
    assert classify_text("Как сделать временный код гостю").category == "door_access"


def test_otklyuchil_does_not_match_door_key() -> None:
    assert classify_text("Отключил подписку, но она ещё работает").category == "other_mplk"


def test_receipt_is_payment() -> None:
    assert classify_text("Как отправить чек домофона на почту").category == "payment"


def test_callback_request_is_not_intercom_call() -> None:
    assert (
        classify_text("После подключения создалась заявка на звонок оператора").category
        == "service_display"
    )


def test_parental_control_remains_primary_when_callback_is_created() -> None:
    assert (
        classify_text(
            "Родительский контроль подключён, но приложение создаёт заявку на звонок"
        ).category
        == "parental_control"
    )


def test_generic_app_bug_is_not_classified_from_routing_template() -> None:
    assert (
        classify_text("Баг в приложении. На скриншоте в красном блоке").category
        == "app_stability"
    )
