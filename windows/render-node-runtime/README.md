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

## Manual-Equivalent Donor Mode (операционный профиль)

Цель: воспроизвести стабильную ручную схему `RDP -> Administrator -> run_server.ps1`,
без hidden fallback и без двойных процессов.

### 1) Подъем API как при ручном запуске

1. На ноде держим source-of-truth env в `C:\ae_dev\repo\.env`.
2. Перед стартом убеждаемся, что на `:8000` нет старого listener.
3. Запускаем `C:\ae_dev\repo\run_server.ps1` от `Administrator`.
4. Проверяем:
   - `GET /health` -> `200`,
   - на `:8000` ровно один `python -m uvicorn main:app`.

Практика:
- не запускать второй `uvicorn` поверх первого (иначе bind error `10048` и неясный owner порта);
- не подменять server-side `.env` локальными файлами с dev-машины.

### 2) Работа с уже открытым GUI AE (render-only)

Если AE уже открыт под `Administrator` в render-only контексте, это допустимый и
рекомендуемый режим для сокращения cold-start времени.

Операционные правила:
1. AE держим прогретым, API не перезапускаем без необходимости.
2. По каждой job ориентируемся на файловые этапы:
   - `render.jsx` появился,
   - `ae_status.txt` появился (`OK`/`ERROR`),
   - появился `work\project.aep`,
   - появился/стабилизировался `work\output.mp4`.
3. Если в GUI «тишина», triage делаем по job-артефактам и API/poll статусу, а не по окну.
4. При stuck сначала гасим зависший `aerender`, полный restart AE/uvicorn — второй шаг recovery.

Замечание:
- «ping JSX» smoke подтверждает только базовый запуск AE, но не гарантирует прохождение full render pipeline.

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
