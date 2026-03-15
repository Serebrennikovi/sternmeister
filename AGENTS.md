# AGENTS.md

Этот файл содержит обязательные operational-правила для AI-агентов при работе с репозиторием.

<!-- PROD_GUARDRAILS_START -->

## Production Guardrails (Global)

### CI/CD First

- Для production-деплоя и smoke по умолчанию использовать CI/CD workflow (`workflow_run`/pipeline) как источник истины.
- Если нужный workflow уже `in_progress`, дождаться завершения и не запускать параллельный ручной деплой.
- Ручной SSH deploy — только fallback: CI/CD сломан или пользователь явно потребовал manual deploy.

### Preflight Before Mutable Actions

Перед любым изменяющим действием на production (deploy/restart/migrate/rollback) обязательно проверить:

1. `hostname`
2. `pwd`
3. `git rev-parse --show-toplevel`
4. `git rev-parse HEAD`

И убедиться, что работа идёт в ожидаемом репозитории и каталоге.

### SHA Consistency

Всегда сверять тройку SHA:

- `local HEAD`
- `workflow/pipeline SHA`
- `server deployed HEAD`

Операция считается корректной только при полном совпадении.

### Smoke Artifacts

После прод-прогона обязательно зафиксировать:

- путь/ссылку на `result.json`
- пути/ссылки на ключевые скриншоты
- значения счётчиков ошибок (`runtime`, `console`, `request`, `server`)

### Target Safety

- Нельзя выполнять production-команды на «похожем» или чужом сервере.
- Если canonical production target (host/path) не определён в документации проекта, сначала получить его у пользователя и только после этого выполнять mutable действия.

<!-- PROD_GUARDRAILS_END -->

