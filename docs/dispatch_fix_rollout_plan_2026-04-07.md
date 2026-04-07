# Dispatch Fix Rollout Plan (2026-04-07)

## Цель

Убрать кейс “dispatch ретраится/падает, хотя `output.mp4` уже существует”, и перевести dispatch на предсказуемый идемпотентный контракт.

## Execution Update (2026-04-07)

Сделано в коде:

1. Orchestrator переведен на явный API mode:
   - добавлен `WINDOWS_RENDER_API_MODE=render|jobs`;
   - убран implicit fallback `/render -> /jobs` в `WindowsRenderClient`.
2. Для `WINDOWS_RENDER_API_MODE=render` используется strict async flow:
   - dispatch ожидает `render_id`;
   - дальше состояние ведет `poll_windows_render`.
3. Добавлен source-of-truth runtime:
   - `windows/render-node-runtime/`;
   - async endpoints `POST /render`, `GET /render/{render_id}`;
   - идемпотентность async dispatch по `job_id`.

Важно по rollout:

- default mode в orchestrator оставлен `jobs` для безопасного включения;
- после выката `windows/render-node-runtime` на donor/clone переключить orchestrator env на `WINDOWS_RENDER_API_MODE=render`.

## Фаза 0 — Быстрый стабилизатор (сразу)

Изменения в orchestrator:

1. Перед `self.retry(...)` в `dispatch_to_windows` делать recovery-check:
   - проверить наличие `s3://<output_bucket>/renders/<job_id>/output.mp4`;
   - если объект есть — завершать job как `SUCCEEDED` без повторного dispatch.
2. В `JobStore.result`/логах явно маркировать кейс:
   - `result.dispatch_recovery.marker=dispatch_timeout_but_output_exists`.
3. Добавить метрику:
   - `dispatch_recovery_outcomes{recovered_from_existing_output=true|false}`.

Ожидаемый эффект:

- немедленно прекращаются повторные re-dispatch при уже готовом результате.

## Фаза 1 — Контракт API (короткий горизонт)

1. Ввести явный режим Windows API (без implicit fallback):
   - `WINDOWS_RENDER_API_MODE=render|jobs`.
2. Убрать неявный `/render -> /jobs` fallback в клиенте.
3. Для production выбрать один режим детерминированно (рекомендуется `render` async).

Ожидаемый эффект:

- поведение становится прозрачным и воспроизводимым.

## Фаза 2 — Идемпотентный async dispatch (основной fix)

1. На Windows node обеспечить идемпотентность по `job_id`:
   - повторный submit того же `job_id` не создает второй рендер, а возвращает существующий `render_id`/статус.
2. В orchestrator:
   - `dispatch_to_windows` только получает/фиксирует `render_id`;
   - `poll_windows_render` ведет job до `SUCCEEDED`/`FAILED`.
3. Повторные сетевые ошибки dispatch не должны порождать повторный рендер.

Ожидаемый эффект:

- исчезает дублирование работы и race между timeout и фактическим completion.

## Фаза 3 — Source Of Truth для Windows-кода

Уже сделано:

- runtime-код с donor/clone выгружен в:
  - `windows/render-node-snapshots/donor`
  - `windows/render-node-snapshots/clone`

Дальше:

1. Выбрать базу (`donor`) + cherry-pick нужные operational scripts из `clone`.
2. Собрать каноничную директорию исходников:
   - `windows/render-node-runtime/`.
3. Добавить скрипт синхронизации/deploy на Windows из репо (вместо ручных правок на ноде).

## Тесты и приемка

Минимум:

1. Unit-test: recovery-check при timeout и существующем output.
2. Unit-test: recovery-check при timeout и отсутствии output.
3. E2E smoke: искусственный timeout dispatch + заранее созданный output -> `SUCCEEDED` без re-dispatch.
4. E2E smoke: обычный dispatch path без регрессий.

## Rollout

1. Включить Фазу 0 под feature-flag.
2. Наблюдать метрики/логи 24-48 часов.
3. После стабилизации включить Фазу 1 и начать Фазу 2.
4. Затем закрепить Фазу 3 (единый source of truth + деплой).

## Rollback

1. Фича-флаг Фазы 0 выключаем мгновенно.
2. Возврат к предыдущему client-mode через env (`WINDOWS_RENDER_API_MODE`).
3. В крайнем случае откат конкретного orchestrator релиза.

## Пункты Со Звездочкой (после стабилизации donor)

Выполнять только после подтверждения стабильного рендера на единственной рабочей ноде-donor (`85.239.48.31`) на текущем fix-релизе.

1. CI/CD для Windows node runtime:
   - CI: lint/smoke для `windows/render-node-runtime` + проверка контрактов API (`/render` или `/jobs` по выбранному mode).
   - CD: управляемый deploy runtime на ноды из репозитория (без ручных правок на сервере).
2. Добавить `render-only` txt на donor по аналогии с clone:
   - зафиксировать файл/путь в `windows/render-node-runtime` и в deploy-скрипте, чтобы состояние donor/clone было одинаковым.
3. Выпилить `curves` из всех шаблонов:
   - переносим change-set из прошлого branch в текущий только после успешной проверки пункта 1 (рабочий рендер на donor).

## Отдельно (позже): CI

Да, добавить CI нужно.

Рекомендуемый минимум:

1. `lint + unit` для orchestrator dispatch/poll.
2. smoke-тест recovery-case.
3. отдельная job на проверку `windows/render-node-runtime` (после завершения Фазы 3).
