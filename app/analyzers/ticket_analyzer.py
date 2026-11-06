from __future__ import annotations

from app.analyzers.classifier import Classification, classify_text, label_for_category
from app.analyzers.message_filter import split_messages
from app.knowledge.store import KnowledgeStore
from app.models.ticket import Evidence, TicketAnalysis, TicketInput


class TicketAnalyzer:
    def __init__(self, knowledge: KnowledgeStore) -> None:
        self.knowledge = knowledge

    @staticmethod
    def _classify(text: str) -> Classification:
        return classify_text(text)

    def analyze(self, ticket: TicketInput) -> TicketAnalysis:
        meaningful, _service = split_messages(ticket.messages)
        message_text = "\n".join(message.text for message in meaningful)
        subscriber_text = "\n".join(
            message.text
            for message in meaningful
            if message.author_role.casefold() == "subscriber"
        )
        retrieval_text = "\n".join(value for value in (ticket.subject, message_text) if value)
        # Chat operators often ask generic questions about a contract, address,
        # or phone number. Those words must not override the symptom stated by
        # the subscriber when choosing the ticket category.
        classification = self._classify(subscriber_text or message_text or ticket.subject)
        lowered_text = message_text.casefold()
        screenshot_only = "скриншот" in lowered_text and not any(
            marker in lowered_text
            for marker in ("текст ошибки", "ожидал", "ожидала", "фактически", "после нажат", "не открыва")
        )

        facts: list[str] = []
        evidence: list[Evidence] = []
        diagnostics = ticket.diagnostics
        for value, label, source in (
            (diagnostics.platform, "Платформа", "diagnostics.platform"),
            (diagnostics.app_version, "Версия приложения", "diagnostics.app_version"),
            (diagnostics.os_version, "Версия ОС", "diagnostics.os_version"),
        ):
            if value:
                fact = f"{label}: {value}."
                facts.append(fact)
                evidence.append(Evidence(fact=fact, source=source))

        if diagnostics.bugshaker_found is True:
            fact = "Баготряска приложена к обращению."
            facts.append(fact)
            evidence.append(Evidence(fact=fact, source="diagnostics.bugshaker_found"))
        elif diagnostics.bugshaker_found is False:
            fact = "Баготряска в доступных данных не найдена."
            facts.append(fact)
            evidence.append(Evidence(fact=fact, source="diagnostics.bugshaker_found"))

        for check in diagnostics.checks:
            if check.status in {"found", "ok", "error", "not_found", "unavailable"}:
                value = f": {check.value}" if check.value not in (None, "") else ""
                fact = f"Проверка «{check.name}» — {check.status}{value}."
                facts.append(fact)
                evidence.append(Evidence(fact=fact, source=check.source))

        missing: list[str] = []
        if not meaningful:
            missing.append("содержательное описание актуальной проблемы")
        if screenshot_only:
            missing.append("содержимое скриншота или текст ошибки")
            missing.append("экран, действия пользователя и фактический результат")

        category_checks = {
            "authentication": ["Сверить время и результат последних попыток авторизации в read-only диагностике."],
            "payment": ["Сверить состояние конкретной операции оплаты и услугу, на которую она зачислена."],
            "intercom_calls": ["Проверить привязку устройства и состояние доставки домофонного вызова."],
            "door_access": ["Проверить права абонента и актуальную привязку двери к договору."],
            "camera_archive": ["Проверить доступность камеры и права на архив для выбранного договора."],
            "notifications": ["Проверить состояние push-разрешений и регистрацию устройства в доступной диагностике."],
            "service_display": ["Сверить подключённые услуги и договор, выбранный в приложении."],
            "contract_selection": ["Проверить связи мастер-аккаунта, договоров и адресов."],
        }
        required = list(category_checks.get(classification.category, []))
        app_categories = {
            "authentication",
            "app_stability",
            "intercom_calls",
            "door_access",
            "camera_archive",
            "notifications",
            "device",
        }
        if classification.category in app_categories and not any(
            (diagnostics.platform, diagnostics.app_version, diagnostics.os_version)
        ):
            required.append(
                "Проверить профиль устройства и версии в read-only диагностике; не запрашивать эти данные у оператора, если они доступны системе."
            )
        if classification.category == "parental_control":
            required.extend(
                [
                    "проверить договор, выбранный в приложении",
                    "проверить ответ API списка подключённых услуг",
                ]
            )
        if screenshot_only:
            required.extend(
                [
                    "открыть приложенный скриншот и зафиксировать текст ошибки",
                    "уточнить экран приложения, действия пользователя и ожидаемый результат",
                ]
            )

        similar = self.knowledge.search(
            retrieval_text,
            category=classification.category,
            strict_category=False,
        )
        summary = self._summary(classification, message_text, ticket.subject)
        # Lexical retrieval alone must never increase confidence.
        confidence = "medium" if message_text and facts and not screenshot_only else "low"
        reply = self._operator_reply(classification, missing, screenshot_only=screenshot_only)

        result = TicketAnalysis(
            ticket_id=ticket.ticket_id,
            category=classification.category,
            category_label=classification.label,
            confidence=confidence,
            summary=summary,
            confirmed_facts=facts,
            hypotheses=[classification.hypothesis],
            required_checks=required,
            missing_information=missing,
            operator_reply=reply,
            evidence=evidence,
            similar_cases=similar,
        )
        result.report_markdown = self.render_markdown(ticket.ticket_number, result)
        return result

    @staticmethod
    def _summary(classification: Classification, text: str, subject: str) -> str:
        lowered = text.lower()
        if "скриншот" in lowered and any(word in lowered for word in ("баг", "ошиб")):
            return "Ошибка в приложении показана только на скриншоте; текстового описания недостаточно"
        if classification.category == "parental_control" and any(word in lowered for word in ("не отображ", "подключ")):
            return "Подключённая услуга родительского контроля не отображается в МПЛК"
        if not text:
            return f"{subject}. В доступных данных нет содержательного описания проблемы"
        return classification.label

    @staticmethod
    def _operator_reply(
        classification: Classification,
        missing: list[str],
        *,
        screenshot_only: bool = False,
    ) -> str:
        if screenshot_only:
            base = "По тексту тикета симптом определить нельзя: проблема описана только на скриншоте."
        elif classification.category == "parental_control":
            base = "Повторно подключать услугу до проверки её состояния не требуется."
        elif not missing:
            base = "Менеджеру следует проверить факты и выполнить указанные выше целевые проверки."
        else:
            base = "Доступных данных пока недостаточно для обоснованного вывода."
        if missing:
            return f"{base} Не хватает: {', '.join(missing)}."
        return base

    @staticmethod
    def render_markdown(ticket_number: str, result: TicketAnalysis) -> str:
        def section(title: str, items: list[str]) -> str:
            body = "\n".join(f"- {item}" for item in items) or "- в доступных данных не найдено"
            return f"### {title}\n{body}"

        confidence = {"low": "низкая", "medium": "средняя", "high": "высокая"}[result.confidence]
        analysis_mode = {
            "rules": "локальные правила и база знаний",
            "llm": f"языковая модель {result.analysis_model or ''}".strip(),
            "rules_fallback": "локальные правила и база знаний (Bothub временно недоступен)",
        }[result.analysis_mode]
        similar = [
            f"{case.title}\n  Статус совпадения: {case.match_status}\n  Возможное решение из истории: {case.resolution}\n  Источник: {case.source}"
            + (f"\n  Обоснование совпадения: {case.match_reason}" if case.match_reason else "")
            + (f"\n  Уточнить: {'; '.join(case.required_checks)}" if case.required_checks else "")
            for case in result.similar_cases
        ]
        sections = [
                f"## 🤖 ИИ-разбор тикета {ticket_number}\nТребуется проверка сотрудником",
                f"**Режим анализа:** {analysis_mode}\n\n"
                f"**Категория:** {result.category_label or label_for_category(result.category)}"
                f"\n\n**Уверенность:** {confidence}",
                section("Подтверждённые факты", result.confirmed_facts),
                section("Предварительный вывод", result.hypotheses),
                section("Нужно проверить", result.required_checks),
        ]
        if similar:
            sections.append(section("Кандидаты из истории — совпадение причины ещё не подтверждено", similar))
        sections.extend(
            [
                f"### Рекомендация менеджеру\n{result.operator_reply}",
                section("Не хватает данных", result.missing_information),
            ]
        )
        return "\n\n".join(sections)
