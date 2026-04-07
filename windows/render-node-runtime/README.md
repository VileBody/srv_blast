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

## Git sparse-checkout на ноде (рекомендуется)

Чтобы обновлять только runtime-папку из репозитория, на каждой ноде:

```powershell
powershell -ExecutionPolicy Bypass -File C:\ae_dev\repo\sync_runtime_from_git.ps1 `
  -RepoUrl https://github.com/VileBody/srv_blast.git `
  -Branch main `
  -CheckoutDir C:\ae_dev\srv_blast `
  -RuntimeSubdir windows/render-node-runtime `
  -RuntimeLinkDir C:\ae_dev\repo `
  -ReplaceRuntimeLinkDir
```

Для приватного репозитория можно передать PAT:

```powershell
-GitAuthToken <token>
```

После первого bootstrap дальнейшие обновления:

```powershell
powershell -ExecutionPolicy Bypass -File C:\ae_dev\repo\sync_runtime_from_git.ps1 `
  -RepoUrl https://github.com/VileBody/srv_blast.git `
  -Branch main `
  -CheckoutDir C:\ae_dev\srv_blast `
  -RuntimeSubdir windows/render-node-runtime `
  -RuntimeLinkDir C:\ae_dev\repo
```

## Связка с orchestrator

На orchestrator side переключение задается явно:

- `WINDOWS_RENDER_API_MODE=render` (рекомендуется для prod async flow)
- `WINDOWS_RENDER_API_MODE=jobs` (legacy sync flow)

Не используется implicit fallback между контрактами.
