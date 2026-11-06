# Импорт документов из Confluence

Импортёр работает только на чтение. Он не редактирует Jira/Confluence и не добавляет
выгруженные страницы в рабочую базу знаний автоматически.

## Настройка

Секреты хранятся только в локальном `.env`, который исключён из Git.

Для Confluence Cloud:

```dotenv
CONFLUENCE_BASE_URL=https://company.atlassian.net/wiki
CONFLUENCE_AUTH_MODE=basic
CONFLUENCE_EMAIL=service-account@example.com
CONFLUENCE_API_TOKEN=<local-secret>
CONFLUENCE_SPACE_KEYS=MPLK,SLK
```

Для Confluence Data Center с персональным токеном:

```dotenv
CONFLUENCE_BASE_URL=https://confluence.company.example/confluence
CONFLUENCE_AUTH_MODE=bearer
CONFLUENCE_EMAIL=
CONFLUENCE_API_TOKEN=<local-secret>
CONFLUENCE_SPACE_KEYS=MPLK,SLK
```

Сначала можно получить перечень доступных разделов:

```powershell
python -m app.knowledge.confluence_loader --list-spaces
```

Результат появится в `data/confluence/spaces.json`. После выбора разделов укажите
их ключи в `CONFLUENCE_SPACE_KEYS` и запустите выгрузку:

```powershell
python -m app.knowledge.confluence_loader
```

Страницы сохраняются в `data/confluence/pages.jsonl`, а состояние — в
`data/confluence/progress.json`. После сбоя следующий запуск продолжит незавершённую
выгрузку. Повторно полученные версии страниц не дублируются.

Каждый документ помечается как `unverified_document`, `confirmed: false` и
`automatic_answer_allowed: false`. Поэтому текст не участвует в ответах ассистента,
пока не пройдёт отдельное извлечение правил и смысловую проверку.
