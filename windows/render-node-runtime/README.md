# Windows Render Node Runtime

`windows/render-node-runtime` — каноничный runtime-код Windows render-node.

## Контракт API

- `POST /render` — async dispatch, быстрый accept:
  - ответ: `{"status":"accepted|running","render_id":"...","job_id":"..."}`
- `GET /render/{render_id}` — статус async render:
  - `status=accepted|running|succeeded|failed`
  - при `succeeded` приходит `output_url`/`output_path`
- `POST /jobs` — explicit sync-режим (legacy/compat), оставлен как отдельный контракт.

Важно:

- Идемпотентность в async-режиме по `job_id`.
- Повторный `POST /render` с тем же `job_id` и тем же payload возвращает тот же `render_id`.
- Повторный `POST /render` с тем же `job_id`, но другим payload возвращает `409`.

## Запуск

```powershell
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

или через `run_server.ps1`.

## Связка с orchestrator

На orchestrator side переключение задается явно:

- `WINDOWS_RENDER_API_MODE=render` (рекомендуется для prod async flow)
- `WINDOWS_RENDER_API_MODE=jobs` (legacy sync flow)

Не используется implicit fallback между контрактами.
