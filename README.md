# ticket-assistant

Небольшой FastAPI-сервис для первичного разбора внутренних тикетов CallCenter по проблемам МПЛК. MVP принимает уже ограниченный JSON от CallCenter/внутреннего адаптера, отделяет служебные события, выполняет детерминированные проверки, ищет подтверждённые случаи и формирует черновик для сотрудника.

Сервис **не подключается к Oracle**, не меняет тикет, не отвечает абоненту и не переназначает исполнителя. LLM и Telegram по умолчанию работают в безопасном mock/выключенном режиме.

## Возможности

- `GET /health`;
- асинхронный контракт `start → status → result`;
- SQLite-хранилище версий и идемпотентность по содержимому обращения;
- базовая классификация МПЛК и Markdown-отчёт;
- источник для каждого детерминированного факта;
- обезличивание телефонов, ФИО, адресов и логинов перед внешним LLM;
- локальная база знаний Markdown/JSONL, использующая только записи с `confirmed: true`;
- загрузка вручную подтверждённых пар из Telegram JSON;
- опциональная отправка отчёта в закрытый Telegram-чат;
- mock LLM без сетевых вызовов и OpenAI-compatible HTTP-адаптер.

## Локальный запуск

Нужен Python 3.11+.

```bash
cd ticket-assistant
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Документация API будет доступна на `http://localhost:8000/docs`.

Запуск в Docker:

```bash
docker compose up --build
```

Файл `.env` необязателен в mock-режиме. Для собственной конфигурации скопируйте `.env.example` в `.env`; секреты не добавляйте в репозиторий.

## Пример обработки тикета

PowerShell:

```powershell
$started = Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/api/v1/tickets/start `
  -ContentType application/json `
  -InFile examples/ticket.json

$started
Invoke-RestMethod "http://localhost:8000/api/v1/tickets/status/$($started.task_id)"
Invoke-RestMethod "http://localhost:8000/api/v1/tickets/result/$($started.task_id)"
```

`curl`:

```bash
curl -sS -X POST http://localhost:8000/api/v1/tickets/start \
  -H 'Content-Type: application/json' \
  --data-binary @examples/ticket.json

curl -sS http://localhost:8000/api/v1/tickets/status/<task_id>
curl -sS http://localhost:8000/api/v1/tickets/result/<task_id>
```

Повторный `POST` с тем же значимым содержимым возвращает прежний `task_id` и `duplicate: true`. Изменение только технического поля `updated_at` не создаёт новую версию; изменение сообщения, диагностики или значимого поля создаёт новую задачу.

## Контракты и статусы

`POST /api/v1/tickets/start` возвращает HTTP 202:

```json
{"task_id":"uuid","status":"queued","duplicate":false}
```

Статусы задачи: `queued`, `processing`, `completed`, `error`. До завершения `GET /result/{task_id}` отвечает HTTP 409. Неизвестный идентификатор отвечает HTTP 404.

Полный входной пример находится в `examples/ticket.json`. Поля `subject_id` и `group_id` обязательны в контракте, но сервис не предполагает их реальные значения и не фильтрует по ним: отбор целевых обращений остаётся ответственностью CallCenter/адаптера.

## Privacy и внешний LLM

При `LLM_PROVIDER=mock` никакие данные не покидают процесс. Для разрешённого OpenAI-compatible endpoint задаются:

```dotenv
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://approved-internal-or-external-endpoint.example/v1
LLM_API_KEY=...
LLM_MODEL=...
```

Перед запросом удаляются `ticket_id`, `ticket_number`, `subscriber`, `id_abon`, `performer` и поля с именами/логинами/адресами; свободный текст проходит регулярное обезличивание. Это защитный MVP-фильтр, а не замена DLP и согласованной политики обработки персональных данных. При запрете внешней обработки следует использовать одобренную внутреннюю модель за совместимым endpoint.

## База знаний

Сервис читает `*.jsonl` и `*.md` из `KNOWLEDGE_PATH`. Для JSONL используются только строки с `"confirmed": true`:

```json
{"id":"case-1","title":"...","category":"payment","symptoms":"...","resolution":"...","source":"reviewed-ticket","confirmed":true}
```

Markdown-файлы считаются вручную курированными материалами. Поиск MVP лексический; его можно заменить векторным индексом без изменения API.

Подтверждённые реальные кейсы сохраняются в `data/knowledge/reviewed/manual-*.jsonl`.
Docker при сборке добавляет их в `runtime/manual-knowledge.jsonl` вместе с Telegram
и Confluence. Допускаются только `confirmed: true`, без запрета retrieval, с
`automatic_answer_allowed: false`. Поля `resolution` и `required_checks` должны
содержать подтверждённый вердикт и границы его применения. История расследований
в `manual-review*.jsonl` остаётся вне рабочего каталога; получение вердикта не
означает автоматического закрытия тикета.

### Импорт Telegram

Соседство сообщений и даже `reply_to` сами по себе не считаются подтверждением. Нужен отдельный, вручную проверенный manifest с точными `question_id`, `solution_id` и `confirmed: true`; пример — `examples/telegram-confirmations.json`.

```bash
python -m app.knowledge.telegram_loader \
  path/to/telegram/result.json \
  examples/telegram-confirmations.json \
  data/knowledge/telegram-cases.jsonl
```

Загрузчик сохраняет только явно подтверждённые пары и обезличивает оба сообщения.

Если правильные ответы в чате дают заранее известные сотрудники, кандидаты можно
собрать непосредственно по их стабильным Telegram `from_id`:

```bash
python -m app.knowledge.telegram_candidates \
  path/to/telegram/result.json \
  path/to/output \
  --answer-author-id user123 \
  --answer-author-id user456
```

Точные reply-треды сохраняются в `knowledge.jsonl` как кандидаты: у них
подтверждены доверенный автор и связь Reply, но не полнота и применимость
решения. Поэтому они получают `semantic_status: "unverified_candidate"` и
`automatic_answer_allowed: false`. Непривязанные, служебные и слишком короткие
ответы попадают в `ambiguous.jsonl`. Для чтения удобнее открыть `review.html` с
поиском и фильтрами; `review.csv` оставлен для массовой разметки. Контрольная
точка и SHA-256 исходного экспорта сохраняются в `progress.json`. Имена
участников, телефоны, номера тикетов, длинные внутренние идентификаторы, email и
Telegram-логины удаляются из выходных текстов.

После смысловой проверки сервису следует указывать не исходный каталог
кандидатов, а `semantic-validation/retrieval`. В нём находятся только записи со
статусом `approved`; длинные исходные треды заменены нормализованными полями
«проблема», «ответ/действие» и «условия применения». Частичные и отклонённые
записи в поиск не попадают. Даже для одобренных записей сохраняется
`automatic_answer_allowed: false`: старый ответ остаётся аналогом, а не готовым
автоматическим ответом абоненту.

### Импорт и проверка Confluence

Экспортёр работает только на чтение. URL и токен задаются в локальном `.env`;
токен нельзя переносить в `.env.example`, отчёты или Git. Повторный запуск
продолжает выгрузку с контрольной точки:

```bash
python -m app.knowledge.confluence_loader --output data/confluence
python -m app.knowledge.confluence_candidates \
  data/confluence/pages.jsonl data/confluence/candidate-review
```

Страницы с признаками логинов, паролей, PIN или токенов изолируются до
смыслового разбора. В runtime-базу переносятся только короткие проверенные
карточки; сырой текст Confluence туда не копируется. Документы старше 2024 года
не допускаются в ответы без подтверждения свежим источником или владельцем
процесса, а старые цены, тарифы и акции исключаются отдельно.

Покрытие документов пересобирается командой:

```bash
python -m app.knowledge.confluence_coverage \
  data/confluence/candidate-review/candidates.jsonl \
  data/confluence/semantic-validation \
  data/confluence/semantic-validation/document-decisions.jsonl \
  data/confluence/semantic-validation/coverage
```

Перед запуском сервиса объединённая база проверяется на персональные данные,
секреты, запрет автоответов и воспроизводимость поиска карточек:

```bash
python -m app.knowledge.runtime_audit data/knowledge/combined \
  --output data/confluence/semantic-validation/coverage/runtime-audit.json \
  --strict
```

Затем проверенные карточки собираются ровно в два файла, которые читает сервис:

```bash
python -m app.knowledge.build_runtime_bundle \
  data/knowledge/combined data/knowledge/runtime
```

- `data/knowledge/runtime/telegram-knowledge.jsonl` — знания из Telegram;
- `data/knowledge/runtime/confluence-knowledge.jsonl` — знания из Confluence.

В `.env` должно быть `KNOWLEDGE_PATH=data/knowledge/runtime`. Сервис читает оба
файла при каждом поиске похожих случаев; перезапускать сборку нужно после
добавления или повторной проверки карточек.

Для ручной публикации двух обезличенных баз в отдельное пространство Confluence:

```bash
python -m app.knowledge.publish_confluence_bundle \
  --space-key TICKETAI \
  --space-name "Ticket Assistant — база знаний ИИ"
```

Команда создаёт пространство при первом запуске, две дочерние страницы и
обновляет главную страницу ссылками на них. Повторный запуск обновляет страницы,
не создавая дубликаты. Это отдельная явно запускаемая операция записи;
экспортёр Confluence остаётся read-only.

Если создавать пространство нельзя, публикацию можно поместить отдельным
деревом под уже существующую страницу:

```bash
python -m app.knowledge.publish_confluence_bundle --parent-page-id 123456789
```

Родительская страница не изменяется: под ней создаётся страница-оглавление, а
две базы становятся её дочерними страницами.

## Telegram

По умолчанию отправка отключена. Для пилотного закрытого чата:

```dotenv
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

Ошибка доставки логируется, но не переводит успешно сформированный разбор в `error`. Telegram использует обычный текст без `parse_mode`, поэтому сгенерированный Markdown не может влиять на разметку или создавать скрытые ссылки.

## Metabase

Для доступа к сохранённому вопросу используются отдельный API-ключ с правами
только на чтение и его числовой `card_id`:

```dotenv
METABASE_BASE_URL=https://metabase.example.internal
METABASE_AUTH_MODE=api_key
METABASE_API_TOKEN=...
METABASE_ALLOW_INSECURE_HTTP=false
```

Для фонового получения назначений на одного исполнителя используются:

```dotenv
METABASE_PERFORMER_FILTER=NIKULINA_VS
METABASE_PERFORMER_LABEL=@Valeri_Nikulina
METABASE_DATABASE_ID=5
METABASE_POLLING_ENABLED=true
METABASE_POLL_INTERVAL_SECONDS=60
METABASE_BASELINE_ON_FIRST_RUN=true
METABASE_ACTIVE_STATUSES=0
METABASE_MAX_TICKET_AGE_HOURS=72
METABASE_DEPARTURE_CONFIRMATION_POLLS=3
METABASE_MAX_NEW_EVENTS_PER_POLL=10
METABASE_PENDING_TTL_HOURS=24
METABASE_RETRY_DELAYS_SECONDS=300,900,1800,3600
METABASE_DIAGNOSTICS_ENABLED=true
METABASE_DIAGNOSTICS_MAX_AGE_DAYS=90
METABASE_UTC_OFFSET_HOURS=7
```

При первом запуске сервис сохраняет текущий список назначений как базовую точку и ничего
по нему не отправляет. На следующих опросах новое появление тикета у исполнителя создаёт
событие. Если тикет снимут, а затем назначат повторно, это считается новым событием.
Состояние и очередь повторных попыток хранятся в SQLite. Пустой или резко уменьшившийся
снимок Metabase не изменяет состояние, исчезновение подтверждается несколькими опросами,
а подозрительная пачка новых назначений помещается в карантин. После долгого простоя или
изменения фильтра сервис создаёт безопасную базовую точку без исторической рассылки.

Для нового назначения сервис загружает заголовок и историю из
`METRICS.CALLCENTER_PROBLEMS`, `METRICS.SUBJECTS` и
`METRICS.VW_CALLCENTER_MESSAGES`, выполняет разбор с обеими runtime-базами знаний и
отправляет в Telegram одно логическое уведомление только после появления содержательной
истории. Предварительные сообщения о пустой истории не отправляются; повторные проверки
используют backoff и прекращаются по TTL. Телефоны, ФИО, адреса, логины и
длинные внутренние идентификаторы в истории маскируются перед отправкой. Если Telegram
временно недоступен, событие остаётся в ограниченной очереди повторных попыток.

При включённой read-only диагностике сервис запрашивает только разрешённые поля последней
сессии из `METRICS.SS_STAT_LK_SESSIONS`: платформу, версию ОС, версию приложения из
`DETAILS` и время сессии. Телефон и другие идентификаторы не передаются языковой модели.
Устаревшие диагностические записи отмечаются как недоступные и не выдаются за актуальные.

`LLM_PROVIDER=mock` означает детерминированный разбор и поиск подтверждённых похожих
случаев без обращения к языковой модели. Для полноценного ИИ-уточнения нужно настроить
одобренный OpenAI-compatible endpoint в параметрах `LLM_BASE_URL`, `LLM_API_KEY` и
`LLM_MODEL`.

Ключ передаётся заголовком `X-API-Key` и не выводится в логи. Удалённый HTTP по
умолчанию запрещён, потому что при нём токен и результаты тикетов передаются без
шифрования. Для изолированной внутренней сети это можно разрешить только явно
через `METABASE_ALLOW_INSECURE_HTTP=true` после принятия риска владельцем системы.

## Тесты

```bash
pytest
```

Проверяются API-сценарий, источники фактов, обезличивание, отделение служебных событий, идемпотентность и запрет неявных Telegram-пар.

## Архитектура

```text
CallCenter / internal adapter
        │ restricted JSON (HTTP)
        ▼
FastAPI → SQLite task/version store
        │
        ├─ message filter + deterministic analyzer
        ├─ confirmed local knowledge search
        ├─ privacy filter → mock/approved LLM
        └─ optional Telegram notification
```

SQLite подходит для одного экземпляра MVP. Перед горизонтальным масштабированием нужны общая БД и внешняя очередь; `BackgroundTasks` не гарантирует восстановление незавершённой работы после аварийного перезапуска.

## Незакрытые вопросы следующего этапа

Проект намеренно не выдумывает значения и адреса. До интеграции нужно определить:

1. реальный `ID_SUBJECT` темы «Проблемы МП Goodline Город/ЛК»;
2. допустимые `ID_GROUP` и/или список исполнителей;
3. точное правило нерешённых статусов и повторной постановки после изменения;
4. утверждённый состав JSON, источники описания/комментариев и связь с баготряской;
5. read-only источники версии приложения, биллинга и логов по `id_abon`;
6. разрешён ли внешний обезличенный LLM или нужна модель внутри контура;
7. утверждённые URL/модель LLM и политика хранения входных данных;
8. токен и идентификатор закрытого Telegram-чата либо решение использовать вкладку CallCenter;
9. можно ли переиспользовать интерфейс существующего Diag Service и вкладку «ИИ Диагностика»;
10. срок хранения задач, требования к аудиту, оценке сотрудником и очистке данных.

## Границы MVP

- Oracle/Jira/production API не вызываются.
- Сервис не утверждает отсутствие ошибки по отсутствующим данным; используется формулировка «в доступных данных не найдено».
- Вывод является внутренним черновиком и всегда помечен как требующий проверки сотрудником.
- Автоматических ответов абоненту и мутаций CallCenter нет.
