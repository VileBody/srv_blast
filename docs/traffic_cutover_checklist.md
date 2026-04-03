# Blast Traffic Cutover Checklist

Этот файл фиксирует практический план перед заливом трафика на прод-контур.

Принципы:
- это не список "low vs high priority", а рабочий checklist для стабилизации системы;
- пункты сгруппированы по контурам, чтобы ими было удобно управлять в разработке;
- если задача уже начата, это отмечено прямо в тексте.

Уточнения по текущему контексту:
- orchestrator сидит за nginx и не рассматривается как публично торчащий наружу endpoint "для всех";
- риск "один пользователь зафлудит всё без rate limit" не считаем главным блокером в текущей конфигурации, потому что бот держит пользователя в `PROCESSING`, а кредиты не бесконечные;
- работа по Windows render node и scale-out рендера уже начата.

## Status snapshot (2026-04-02, incident note)

- Прод восстановлен через GitHub Actions rerun на стабильный `main@8b22515` (до релиза `claude/reverent-archimedes` с `asyncpg`).
- Симптом инцидента: `orchestrator-api` и `tg-bot` уходили в restart-loop с `ModuleNotFoundError: asyncpg`.
- Asyncpg-ветка для отдельного ревью подтянута локально: `origin/claude/reverent-archimedes`, локальная review-ветка `review/claude-reverent-archimedes`.
- По render node lifecycle уже сделано:
  - CLI pipeline: `infra/timeweb/render-node/render_node_pipeline.py` (`list/create/delete/probe`);
  - make-таргеты `render-node-list/create/delete/probe`;
  - admin UI `/admin/render-nodes` для ручного create/delete/probe.
- По render node lifecycle осталось:
  - реальный E2E render через orchestrator с фиксацией `job_id/output_url`;
  - переход от single `WINDOWS_RENDER_URL` к пулу нод (свободная нода забирает задачу из очереди).

## Status snapshot (2026-04-03, render-node diagnostics)

- Диагностика велась по двум нодам:
  - donor: `85.239.48.31` (`blast-worker-node-0`, `id=6849259`);
  - candidate: `72.56.246.24` (`blast-worker-node-20260402-211624`, `id=7205065`).
- Обе ноды отвечают по текущему API-контракту одинаково:
  - `GET / -> 404`;
  - `POST /render -> 404`;
  - `POST /jobs -> 500` (endpoint существует, но валится на пустом payload).
- SSH-доступ на обе ноды отсутствует (`port 22 timed out`), поэтому live-diagnostics внутри Windows через SSH недоступен.
- Попытка зайти на candidate-ноду по WinRM через штатный `make iac-start-uvicorn SERVER_ID=7205065` дала `credentials were rejected by the server`.
  - В `.env.iac` `WIN_ADMIN_PASSWORD` не задан;
  - Timeweb API отдаёт `root_pass` для обоих серверов, но этот пароль не проходит WinRM-аутентификацию на candidate-ноде.
- Практический вывод: зависание рендера на candidate-ноде подтверждено на уровне поведения job, но root-cause на стороне AE/локальных процессов пока не локализован из-за отсутствия рабочего remote-shell/WinRM доступа с валидными кредами.
- Операционный rollback прода выполнен через GitHub Actions (без hotfix на сервере):
  - создана ветка `release/stable-8b22515` от коммита `8b22515`;
  - запущен `Deploy Main` (`workflow_dispatch`) с `branch=release/stable-8b22515`;
  - run `23939671820` завершился `success`, на self-hosted runner зафиксирован `HEAD is now at 8b22515`.

## Status snapshot (2026-04-03, sequential rollout on release/stable-8b22515)

- Поэтапно влиты и прокачены через CI/CD:
  - `#81` (runtime emergency fixes),
  - `#82` (payment core),
  - `#85` (fix после регресса из `#82`),
  - `#83` (bot credit spend/refund + batch outcomes),
  - `#84` (artist flow).
- Текущий стабильный релиз после этой цепочки: `release/stable-8b22515@52f9034`.
- После каждого шага сделан runtime gate:
  - `orchestrator /health` зелёный,
  - реальная `with_gemini` smoke-job до `SUCCEEDED` с `output_url`.
- Зафиксирован edge-case:
  - synthetic no-speech smoke (`[NO_SPEECH]`) может падать на strict-валидации `stage2_subtitles` (`block_5.mine` overlap с `block_5.glitch_peak`);
  - реальные архивные тест-джобы после `#83/#84` проходят end-to-end.
  - узкий фикс для no-speech overlap в `BlocksTokensPayload` влит в `MR-1`.

## Status snapshot (2026-04-03, MR-1 no-speech closed)

- В `mlcore/models/subtitles_tokens.py` разрешён overlap только для no-speech маркеров между `block_5.mine` и `block_5.glitch_peak`.
- Добавлен точечный тест `tests/test_subtitles_tokens_no_speech_overlap.py`:
  - synthetic no-speech overlap проходит;
  - overlap обычного слова продолжает падать в strict-валидации.
- Добавлен runtime gate-скрипт `scripts/run_mr1_smoke_gate.py` + make target `smoke-mr1`:
  - серия `GET /health`;
  - synthetic no-speech job;
  - real archival `with_gemini` job;
  - явный fail gate при любом статусе != `SUCCEEDED`.
- PR: `#86` (`MR-1: no-speech overlap fix + runtime smoke gate`) в `release/stable-8b22515`.
- CI: run `23958016071` — `passed`.
- Deploy: `deploy-current-branch.yml` run `23958050065` — `passed`.
- Runtime verification на прод-сервере (прямой запуск изнутри хоста, без full smoke-suite):
  - synthetic/no-speech: `job_id=802bbf6fc443498585e11f806307dd59` -> `SUCCEEDED`;
  - real archival: `job_id=b8af16ca1c164764aa9b12a8ebfedb91` -> `SUCCEEDED`.
- Решение по циклу: full smoke-suite оставлен на финальный этап; между MR проверяем только непосредственно затронутый контур.

## Status snapshot (2026-04-03, MR-2 payments tail closed)

- В `services/tg_bot_public/credits_db.py` закрыт ledger edge-case для отрицательных admin adjustments:
  - баланс всегда clamp к `>= 0`;
  - в `transactions.amount` пишется фактически применённый delta (`applied_delta`), а не запрошенный.
- Для post-generation paid-flow унифицирован критерий paid user:
  - `has_paid()` учитывает `payment`, `admin_activate`, `manual_activation`.
- В `services/tg_bot_public/admin_panel.py` отвязан unlock state от Telegram notify в webhook path:
  - сначала commit credits/event + `reset_to_wait_audio`;
  - уведомление пользователю остаётся side-effect с логированием ошибок.
- Targeted verification для затронутого контура:
  - `PYTHONPATH=. pytest -q tests/test_payments_tail_fixes.py` -> `3 passed`.
- Full smoke-suite остаётся отложенным на финальный этап цикла.

## Status snapshot (2026-04-03, MR-3 llm-workers admission + admin control)

- Добавлен `llm-workers` слой в orchestrator:
  - типы `sdk/openrouter/hybrid` (`core/llm_worker_types.py`);
  - runtime-config и выбор воркера (`services/orchestrator/llm_workers.py`);
  - admission reservation сделан атомарно через Redis Lua (`INCR` только при `inflight < max_inflight`).
- Admission больше не зависит от full scan всех jobs:
  - inflight учитывается по материализованным Redis-счётчикам `...:llm_workers:inflight:*`.
- Инициализация в текущем цикле: `gemini-only` по умолчанию:
  - `sdk` включён;
  - `openrouter`/`hybrid` выключены по умолчанию, но управляются runtime из админки.
- Добавлен runtime control в админке public bot:
  - страница `/admin/llm-workers`;
  - чтение/обновление orchestrator `/llm-workers` (`GET`/`PUT`);
  - guardrail: нельзя сохранить конфиг, где выключены все admission-пути (`enabled + weight + max_inflight`).
- JobStore обновлён для устойчивости admission/idempotency:
  - idempotency claim переведён на `SET ... NX` (race-safe создание job по ключу);
  - retryable idempotent `FAILED` (admission/queue capacity path) пересоздаётся на повторе;
  - retention TTL добавлен для job state и idempotency keys (`JOBSTORE_JOB_TTL_SECONDS`, `JOBSTORE_IDEMPOTENCY_TTL_SECONDS`);
  - при переходе job из `QUEUED/RUNNING` в терминальный статус inflight-slot освобождается автоматически.
- Targeted verification для затронутого контура:
  - `PYTHONPATH=. pytest -q tests/test_llm_workers.py` -> `5 passed`;
  - `PYTHONPATH=. pytest -q tests/test_orchestrator_lyrics_schema.py` -> `4 passed`.
- Ограничение окружения локального прогона:
  - `tests/test_orchestrator_tasks_preflight_retry.py` не выполнялся из-за отсутствия `celery` в локальном env.

## 1. Admission, orchestrator, job lifecycle

- [x] Сделать атомарный admission / reservation для `llm_worker_type`, чтобы burst из 20-30 запросов не переполнял один и тот же backend.
- [x] Убрать snapshot-only выбор worker-а и перенести ограничение inflight в атомарную Redis-операцию.
- [x] Исправить idempotency race в `JobStore.new_job()`, чтобы параллельные одинаковые запросы не создавали несколько job-ов.
- [ ] Исправить lost-update в `JobStore.set_status()`, чтобы параллельные обновления статуса не перетирали друг друга.
- [x] Починить retry semantics после admission failure: повтор того же idempotent request не должен навсегда возвращать старую `FAILED` job.
- [x] Добавить retention policy для job state и idempotency keys в Redis.
- [x] Убрать full scan всей истории jobs из hot path admission.
- [ ] Сделать startup/health более честными: если критические runtime prerequisites не готовы, сервис не должен выглядеть "зелёным".

## 2. Payments, credits, money correctness

- [x] Сделать начисление кредитов по оплате атомарным и строго one-time per order/payment.
- [x] Закрыть duplicate webhook scenario: повторный `CONFIRMED` не должен давать двойные кредиты.
- [x] Связать manual activation и payment confirmation, чтобы одно и то же приобретение нельзя было начислить дважды двумя разными путями.
- [x] Переставить списание кредитов в public bot: не списывать кредиты до успешного enqueue либо добавить корректный reserve/refund flow.
- [x] Проверять результат `deduct_credit()` и не запускать generation, если фактическое списание не прошло.
- [x] Починить ledger consistency для отрицательных admin adjustments, чтобы `transactions` и реальный баланс не расходились.
- [x] Определить единое правило "paid user" для post-generation flow: manual activation тоже должна переводить пользователя в paid-ветку, если это бизнес-ожидание.
- [x] Отвязать unlock user state от Telegram notify в webhook path: сбой отправки сообщения не должен оставлять оплаченного пользователя в старом stage.

## 3. Telegram bot state, batch flow, user-visible correctness

- [x] Убрать ложный success flow после failed/partial batch: не отправлять "Готово" и не переводить пользователя в success-ветку, если batch собран не полностью.
- [x] Развести явные конечные состояния batch-а: `all_succeeded`, `partial_failed`, `enqueue_failed`, `master_failed`.
- [ ] Исправить referral race при активации второго ролика, чтобы состояние реферера не терялось при параллельных сообщениях.
- [ ] Заменить O(n) поиск друга по username на нормальный индекс `username -> chat_id`.
- [ ] Добавить recovery policy для `WAITING_REFERRAL` и застрявшего `PROCESSING`, чтобы пользователь не зависал навсегда в лимбо.
- [ ] Убрать silent reset state при битом JSON/validation error из Redis; такие случаи должны логироваться и быть диагностируемыми.
- [ ] Привести batch idempotency в ботах к детерминированному ключу на `(chat_id, batch_id, version)`, а не к случайному UUID на каждый retry.

## 4. Redis and state growth

- [ ] Убрать full scan `list_processing()` из polling loop обоих ботов.
- [ ] Убрать full scan `list_pending_reminders()` из reminder loop public bot.
- [ ] Убрать full scan `list_all_states()` из тяжёлых страниц админки либо перевести их на материализованные/индексируемые представления состояния.
- [ ] Ввести bounded retention для старых chat states там, где это допустимо продуктово.
- [ ] Отдельно описать и внедрить политику cleanup stale Redis state после abandoned flows.

## 5. Render path and Windows nodes

- [ ] Продолжить уже начатую работу по Windows render node scale-out и зафиксировать целевую операционную схему.
  Статус: уже в работе, не новый пункт.
- [ ] Зафиксировать endpoint render node per job, чтобы in-flight poll не ломался при switchover/rollback.
- [ ] Описать поведение системы при одном render worker и одном Windows node как текущий throughput ceiling.
- [ ] Решить, когда нужен полноценный artifact store вместо опоры на локальный shared volume между build/render сервисами.
- [ ] Ввести cleanup policy для job artifacts, не ограничиваясь только local job logs.

## 6. Admin panel and operator safety

- [x] Добавить guardrail в `LLM Workers` admin UI: нельзя сохранить конфиг, который effectively выключает admission на проде.
- [ ] Показать в admin UI явное предупреждение, если runtime config приводит к `no_enabled_types` или к нулевой суммарной полезной weight.
- [ ] Улучшить payment/admin audit trail, чтобы было видно: кто начислил, по какой причине, к какому order это относится.
- [ ] Сделать UTM summary полезнее для маркетинга: не терять `content` и `term` в основной аналитической сводке.
- [ ] Пересмотреть тяжёлые admin pages с точки зрения operational safety: страница не должна сама создавать заметную нагрузку на Redis/бот state.

## 7. Disk, tmp, and filesystem hygiene

- [ ] Добавить фоновую очистку `incoming/`, `prepared/` и stale `result/` файлов в обоих Telegram bots.
- [ ] Определить retention policy для `/app/work`, `/app/output` и job-local artifacts.
- [ ] Проверить, какие артефакты реально нужны для отладки, а какие можно удалять автоматически без потери полезной диагностики.

## 8. Timing / subtitles / render correctness

- [ ] Привести FPS math к единому source of truth во всём AE/text/render коде.
- [ ] Убедиться, что округление времени к кадру делается единообразно там, где это влияет на текстовые слои и keyframes.
- [ ] Сохранить строгий preflight для текста как дефолтное прод-поведение и не позволять "тихо" протащить битый тайминг.

## 9. Testability, release safety, observability

- [ ] Привести локальный test entrypoint к воспроизводимому виду без ручного `PYTHONPATH=.`.
- [ ] Сделать минимальный рабочий test setup для suites, которые сейчас валятся на отсутствующих `aiogram` / `celery`.
- [ ] Починить integration tests, которые сейчас упираются в жёсткую зависимость на style metadata mapping.
- [ ] Зафиксировать smoke checklist перед релизом: enqueue, payment webhook, public bot paid flow, render dispatch, render poll, result delivery.
- [ ] Добавить базовую операционную наблюдаемость по очередям, in-flight jobs, failed jobs, webhook outcomes и render poll timeouts.

## Definition of done для traffic cutover

Перед активным заливом трафика должны быть подтверждены следующие свойства системы:

- [ ] burst enqueue не переполняет один LLM backend из-за race condition;
- [x] duplicate payment/webhook/manual activation не приводят к двойным кредитам;
- [x] public bot не теряет кредиты без фактического запуска generation;
- [x] failed или partial batch не маскируется под success;
- [ ] Redis hot paths не зависят линейно от всей исторической массы jobs/chats;
- [ ] render path переживает штатный switchover Windows node без потери in-flight poll;
- [ ] tmp/artifact growth ограничен понятной retention policy;
- [ ] smoke tests и базовая диагностика воспроизводимы локально и на релизе.
