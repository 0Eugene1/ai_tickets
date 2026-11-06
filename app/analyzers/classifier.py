from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Classification:
    category: str
    label: str
    hypothesis: str


_RULES: tuple[tuple[tuple[str, ...], Classification], ...] = (
    (("dr.web", "dr web", "антивирус"), Classification("other_mplk", "МПЛК → Антивирус", "Нужно отличить новое подключение антивируса от обслуживания существующей подписки.")),
    (("подписк", "абонентская плата"), Classification("other_mplk", "МПЛК → Подписки", "Нужно проверить состояние подписки, дату изменения и связанные услуги.")),
    (("родительск", " рк "), Classification("parental_control", "МПЛК → Родительский контроль", "Возможно расхождение состояния или прав доступа к родительскому контролю.")),
    (("заявка на звон", "обратн звон", "перезвон"), Classification("service_display", "МПЛК → Подключение услуги", "Нужно проверить, ожидается ли операторское подключение услуги и совпадает ли её статус с биллингом.")),
    (("сбп", "оплат", "возврат", "банк", "платеж", "чек"), Classification("payment", "МПЛК → Оплата", "Возможна ошибка клиентского сценария оплаты либо интеграции с платёжным сервисом.")),
    (("вход", "войти", "авторизац", "код смс", "смс-код", "sms-код", "парол"), Classification("authentication", "МПЛК → Авторизация", "Возможна ошибка авторизации или доставки кода подтверждения.")),
    (("звон", "вызов", " sip ", "трубк"), Classification("intercom_calls", "МПЛК → Звонки домофона", "Возможна проблема доставки или обработки вызова домофона.")),
    (("камер", "архив", "видео", "фрагмент", "скача", "запись"), Classification("camera_archive", "МПЛК → Камеры и архив", "Возможна проблема получения видеопотока, архива или прав доступа к камере.")),
    (("уведомлен", "пуш", "push"), Classification("notifications", "МПЛК → Уведомления", "Возможна проблема регистрации устройства или доставки push-уведомлений.")),
    (("шаринг", "расшар", "гостев", "доступ сосед"), Classification("access_sharing", "МПЛК → Общий доступ", "Возможна проблема выдачи или отображения общего доступа.")),
    (("двер", " ключ", "калитк", "код доступа", "гост"), Classification("door_access", "МПЛК → Двери и ключи", "Возможна проблема прав доступа или команды открытия двери.")),
    (("регистрац", "не наш абонент", "основн номер"), Classification("registration", "МПЛК → Регистрация", "Возможно, номер или договор не определён как основной при регистрации.")),
    (("договор", "лицев", "аккаунт"), Classification("contract_selection", "МПЛК → Договор", "Возможно, выбран другой договор или данные договоров синхронизированы не полностью.")),
    (("чат", "переписк"), Classification("support_chat", "МПЛК → Чат поддержки", "Возможна проблема сохранения или отображения переписки.")),
    (("не отображ", "пропал", "нет услуг"), Classification("service_display", "МПЛК → Отображение услуг", "Возможно расхождение данных услуги между системами и приложением.")),
    (("вылет", "завис", "серый экран", "не открыва", "баг в приложении", "ошибка в приложении"), Classification("app_stability", "МПЛК → Работа приложения", "Возможна ошибка клиентского приложения или несовместимость версии/устройства.")),
    (("пульт", "устройств", "приставк"), Classification("device", "МПЛК → Устройство", "Возможна проблема регистрации или привязки устройства.")),
    (("умный дом",), Classification("smart_home", "МПЛК → Умный дом", "Возможна проблема состояния или отображения устройства умного дома.")),
)


_OTHER = Classification(
    "other_mplk",
    "МПЛК → Другое",
    "Причину нельзя надёжно определить по доступным данным; требуется проверка сотрудником.",
)


def classify_text(text: str) -> Classification:
    lowered = f" {re.sub(r'\s+', ' ', text.casefold())} "
    best: tuple[int, int, Classification] | None = None
    for priority, (keywords, classification) in enumerate(_RULES):
        score = sum(keyword in lowered for keyword in keywords)
        candidate = (score, -priority, classification)
        if score and (best is None or candidate[:2] > best[:2]):
            best = candidate
    return best[2] if best else _OTHER


def label_for_category(category: str) -> str:
    for _keywords, classification in _RULES:
        if classification.category == category:
            return classification.label
    return _OTHER.label
