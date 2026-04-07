# Dispatch Incident Investigation (2026-04-07)

## 1) Что исследовано

Источник данных:

- Текущий код `main` на сервере `timeweb-blast` (`/home/blast/blast_final`, commit `ba8f010`).
- Логи контейнеров `worker-render` / `worker-build` (через Docker/Dozzle-источник).
- История job-состояний в Redis (`JOBSTORE_PREFIX=blast`).
- Проверка фактического наличия результата в S3 (`renders/<job_id>/output.mp4`).
- Git-история изменений dispatch/poll за последний месяц.

Ограничение:

- Docker-логи за весь месяц недоступны в полном объеме из-за ротации/пересоздания контейнеров.
- Для исторических кейсов использованы Redis JobStore + S3 (это дало необходимые подтверждения).


## 2) Текущее поведение (as-is)

Серверный runtime:

- `MODE=prod`
- `WINDOWS_RENDER_URL=http://85.239.48.31:8000`
- `WINDOWS_TIMEOUT_S=300`
- `WINDOWS_POLL_INTERVAL_S=2.0`
- `WINDOWS_POLL_TIMEOUT_S=3600`
- `CELERY_QUEUE_RENDER=render`

Фактически в проде сейчас используется sync-контракт:

- `dispatch_to_windows` вызывает Windows API.
- Для успешных задач в Redis в `result.windows._api` всегда `jobs`.
- По статистике JobStore: `sync_jobs = 541`, `async_render_api = 0`.

То есть текущая рабочая схема не “dispatch + poll до output_url”, а “длинный синхронный запрос /jobs с ожиданием результата”.


## 3) Что видно по инциденту

### 3.1 Критический факт

Найдены FAILED-задачи с ошибкой `windows_dispatch_transient`, у которых `output.mp4` в S3 **существует**:

- `1bbc7031d0104853942ec39607e4a1f5`  
  failed_at=`2026-04-06T21:35:59Z`, output_last_modified=`2026-04-06T21:55:51Z`
- `30c59b8615c24f208827d9c39f45a17e`  
  failed_at=`2026-04-04T13:04:28Z`, output_last_modified=`2026-04-04T13:05:21Z`
- `917dcee31cc247938f98c97883c46949`  
  failed_at=`2026-04-02T19:13:07Z`, output_last_modified=`2026-04-02T17:55:32Z` (output был раньше финального fail)

Это прямое подтверждение сценария “задача ушла в retry/fail, хотя результат уже был”.

### 3.2 Паттерн retry

В Redis есть успешные задачи с `celery_retry stage=dispatch`, например:

- `1d6a13bebc7a4d638361bc1524e59b4c` -> `SUCCEEDED`, `retries=4`, есть `output_url`.

В текущем коде dispatch retry: `base=5, cap=120`, то есть последовательность `5, 10, 20, 40, 80, 120...`.  
Описанный оператором паттерн `10/20/40/80` полностью укладывается в эту модель (обычно замечают уже со 2-го шага).

### 3.3 Дополнительные наблюдения

- На участке 2026-04-04 были регрессии из merge/горячих правок dispatch:
  - `NameError: WindowsNodePool is not defined`
  - `AttributeError: 'Settings' object has no attribute 'windows_render_urls'`
- Эти ошибки потом были исправлены hotfix-ами.


## 4) Почему это происходит (RCA)

### Главная причина

Сейчас dispatch работает как длинный sync `/jobs` вызов (ожидание до окончания рендера), при этом есть task retry на transient timeout.

Если рендер на Windows длится дольше сетевого/HTTP ожидания (`WINDOWS_TIMEOUT_S=300`) или рвется соединение:

1. Оркестратор получает timeout и считает dispatch неуспешным.
2. Celery делает retry с exponential backoff.
3. Повторный retry снова отправляет тот же render payload на ту же ноду.
4. При этом один из предыдущих запусков может уже завершить рендер и залить `output.mp4` в S3.
5. Job при этом может остаться в `FAILED` или долго “переигрываться”, хотя файл уже есть.

Итого: нет “at-most-once” гарантий на dispatch в sync-режиме.


## 5) Что менялось в dispatch за последний месяц

Ключевые редакции (по `services/orchestrator/tasks.py` и связанным частям):

- `2026-04-03` `dbd077e`  
  Добавлен Redis-backed pool нод (`WindowsNodePool`), выбор ноды через `reserve_best`, retry при `all_nodes_failed`.
- `2026-04-04` `8aac1f9`  
  Hotfix импорта `WindowsNodePool` (исправление `NameError`).
- `2026-04-04` `049d5e0`  
  Hotfix восстановления default URL-листа (`WINDOWS_RENDER_URL` + `WINDOWS_RENDER_URLS`) для dispatch/poll.
- `2026-04-02` `92520bd`  
  В poll добавлено закрепление endpoint из dispatch (`pinned_url`), чтобы poll переживал смену URL.
- `2026-04-04` `a0e2938`  
  Добавлены метрики `render_poll_timeout_outcomes`.

Смежное изменение:

- `2026-03-13` `c5a8426`  
  Ускорен backoff для Gemini overload (LLM-stage), не dispatch-stage.


## 6) Итоговое решение (рекомендация)

### Целевое (оптимальное)

Перейти на явный async dispatch-контракт:

1. `dispatch_to_windows` должен быстро получать `render_id` (без ожидания конца рендера).
2. Далее только `poll_windows_render` отслеживает completion.
3. Повторный dispatch одного и того же `job_id` должен быть идемпотентным на стороне render-node (dedupe по `job_id`/idempotency key).

Это устраняет повторные “перезапуски рендера” из-за сетевых таймаутов длинного sync-вызова.

### Обязательный стабилизатор до полного async cutover

Если временно оставаться на sync `/jobs`:

1. Перед каждым retry dispatch делать recovery-check:
   - `HEAD s3://.../renders/<job_id>/output.mp4`
   - если объект уже есть -> закрывать job как `SUCCEEDED` без повторного dispatch.
2. Ввести явный state `dispatch_unknown` (а не немедленный re-dispatch).
3. Добавить метрику/алерт `dispatch_timeout_but_output_exists`.

### Важное замечание по контракту

Сейчас `WindowsRenderClient` делает implicit fallback `/render -> /jobs` при `404`.  
Для детерминизма лучше задать явный режим API в конфиге и не полагаться на неявные fallback-переходы.


## 7) Короткий вывод

Проблема подтверждена данными прода: есть реальные job, где dispatch заканчивается timeout/fail, но `output.mp4` уже в S3.  
Корень — retry длинного sync dispatch без идемпотентности/без recovery по факту существующего output.

Рекомендуемый путь: явный async dispatch + poll с идемпотентным `job_id`, а до миграции — обязательный S3 recovery-check перед любым повторным dispatch.
