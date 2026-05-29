# blast — AI Lyric Video Service

## Суть продукта
Пользователь отправляет аудио в Telegram-бот → AI-пайплайн генерирует субтитры и подбирает футаж → After Effects на Windows рендерит видео → бот отдаёт готовое видео (1080×1920, Reels/TikTok/Shorts).

---

## Архитектура (высокий уровень)

```
Telegram Bot (tg_bot_public / tg_bot_botapi)
    ↓
Orchestrator (FastAPI + Celery) — services/orchestrator/
    ↓ Stage 1: ASR + сценарий
    ↓ Stage 2: субтитры + футаж (Gemini / OpenRouter)
    ↓ Stage 3: сборка JSX / AE-проекта
ML Core (Celery workers) — services/ml_core/ + mlcore/
    ↓
Windows Render Node — windows/render-node-runtime/
    AfterEffects → mp4 → S3 → бот → пользователь
```

---

## Сервисы (docker-compose.yml)

| Контейнер | Описание |
|-----------|----------|
| `orchestrator-api` | FastAPI, port 18000, управление job'ами |
| `worker-build` | Celery, очередь `build` (сборка JSX) |
| `worker-render` | Celery, очередь `render` (отправка в AE) |
| `worker-render-poll` | Celery, polling рендера |
| `tg-bot` | Внутренний Telegram-бот (botapi) |
| `tg-bot-public` | Публичный бот `@blast808bot`, port admin 18081 |
| `asset-ui` | React UI для управления видеоасетами, port 18173 |
| `finance-bot` | Финанс-бот (Groq), port 18082 |
| `minio` | S3-совместимое хранилище (profile: storage) |

---

## Ключевые директории

```
mlcore/                  — AI-пайплайн
  gemini_orchestrator.py — главный оркестратор стадий 1-3
  gemini_client.py       — клиент Gemini
  openrouter_client.py   — клиент OpenRouter (fallback)
  llm_router.py          — выбор провайдера (gemini/openrouter/hedged)
  footage_picker.py      — подбор клипов по стилю/интервалам
  prompts/               — все промпты по стадиям
  models/                — Pydantic-модели для каждой стадии

services/orchestrator/   — FastAPI + Celery job management
  app.py                 — роуты, health, runtime config
  job_store.py           — хранилище job'ов
  llm_workers.py         — диспетчер LLM-воркеров
  tasks.py               — Celery-таски

services/tg_bot_public/  — публичный бот
  app.py, state_store.py, credits_db.py, tbank_client.py

services/tg_bot_botapi/  — внутренний бот
  season/                — flow для сезонных кампаний

windows/render-node-runtime/
  main.py, ae_sdk.py     — HTTP-сервер + запуск AE
  run_afterfx_job.ps1    — PowerShell-раннер AfterEffects

app/                     — legacy pipeline (blocks-based, всё ещё активен)
  orchestrator.py        — ProjectOrchestrator (автоскейл текста)
  blocks/                — macro_block_01..07 (INTRO, WALTZ, PHOTO, BABY, GLITCH, DUAL, FINALE)

config/styles/           — пресеты: artist_presets.json, effects_library.json, text_styles.json
```

---

## AI-пайплайн (стадии)

| Стадия | Что делает | Модель |
|--------|-----------|--------|
| Stage 1a | ASR — распознавание слов + тайминги | `GEMINI_MODEL_STAGE1` |
| Stage 1b | Сценарий — структура трека (блоки/секции) | `GEMINI_MODEL_STAGE1` |
| Stage 2 субтитры | Разметка сцен (TYPE_1..6 или impulse long/short) | `GEMINI_MODEL_SUBTITLES` |
| Stage 2 футаж | Подбор стиля + интервалы переключения клипов | `GEMINI_MODEL_FOOTAGE` |
| Stage 3 | Сборка JSX/AE-проекта из всех артефактов | — (Python) |

**Модели** задаются в `.env`:
- `GEMINI_MODEL_STAGE1`, `GEMINI_MODEL_SUBTITLES`, `GEMINI_MODEL_FOOTAGE` — обязательные
- `GEMINI_MODEL_FALLBACK` — опциональный (для 503/429)

**LLM провайдеры**: `PROVIDER_MODE_GEMINI` / `PROVIDER_MODE_OPENROUTER` / `PROVIDER_MODE_HEDGED`

---

## Хуки (раздел «Выбор хука» в боте)

В боте перед рендером — выбор хука из 5 кнопок:

| Хук | Модуль | Что делает |
|-----|--------|-----------|
| Звук     | `mlcore/hooks/f1_sound/`     | TBD |
| Объект   | `mlcore/hooks/f2_object/`    | TBD |
| Эффект   | `mlcore/hooks/f3_effect/`    | TBD |
| Движение | `mlcore/hooks/f4_motion/`    | TBD |
| **Мысль** | `mlcore/hooks/f5_cognition/` | **готово** — TTS-вставка 2–3.5с поверх focal_start трека (Gemini), подключено в боте |

**F5 Cognition (Мысль) — статус 2026-05-29:** модуль рабочий end-to-end. Stage1 (текст) + Stage2 (TTS) подключены через `google.genai.Client` напрямую (хелпер `_gemini.py`). TTS-модель = `gemini-2.5-flash-preview-tts` (3.1-preview отдаёт 500 INTERNAL; переключение = смена env `GEMINI_MODEL_F5_TTS`). Точка вызова: `mlcore/hooks/f5_cognition/orchestrator_hook.py::build_f5_block_if_requested()` врезана в `gemini_orchestrator.py` между merge и `render_all_steps`; блок кладётся в `full_edit_config["f5"]`, который `app/project_builder.build_full_project` читает → `inject.py::apply_f5()` добавляет audio-слой (z=5, между трек-аудио=2 и видео=100+) + TTS text-слой (клон стиля), удаляя перекрытые трек-субтитры. Управление env: `F5_HOOK_DEVICE` (вкл/выбор), `F5_HOOK_INJECT_FOCAL_MS` (дефолт 0), `F5_HOOK_SEED`, `F5_HOOK_S3_UPLOAD`/`F5_HOOK_S3_BUCKET`/`F5_HOOK_S3_PREFIX`. Нет device → zero impact. Mixer.py — только preview (`pipeline.generate_preview()`). Ducking пока no-op. **Бот подключён (2026-05-29):** в `tg_bot_botapi` на стадии `STAGE_WAIT_HOOK_TYPE` теперь 5 кнопок-категорий (Звук/Объект/Эффект/Движение/Мысль); 4 — заглушки «скоро», «Мысль» → новая стадия `STAGE_WAIT_HOOK_DEVICE` с 5 приёмами (Панчлайн/Пропущенное слово/Эхо/Вопрос к треку/Инверсия) → F5Device. Выбор едет `send_audio_s3(hook_device=…)` → schema `SendAudioS3Request.hook_device` → `tasks.py env["F5_HOOK_DEVICE"]` → orchestrator_hook. `user_drop_t` пробрасывается в `F5Request.drop_at_sec` (orchestrator_hook конвертит abs→relative от clip_start). Зеркало в `tg_bot_public` (state-поля + стадия + HOOK_STAGES + orchestrator_client) для CI parity; UI там за `HOOK_FLOW_ENABLED`. **Осталось:** ручная прослушка голоса; (некритично) reverb/ducking, 5 демо-роликов. Источник ТЗ: `outputs/tz_f5_gemini_tts.md` (v1.2 + v1.3). Детали: память `project_hooks_f5.md`.

---

## Шаблоны субтитров

### impulse (`2nd_template/`)
- Слои: `long` (≤15–18 символов) и `short` (акцент: императив/рефрен)
- Max 1 short на 2 строчки; short только если ≥0.4с и пауза после ≥0.4с
- Тайминг: `in`=start слова, `out`=start следующего слоя

### jakson / 3rd (`3rd_template/`)
- TYPE_1: нейтральный, 2 строки | TYPE_2: фокус-слово курсивом
- TYPE_3: нарастание к финальному слову | TYPE_4: 1–2 слова, красный
- TYPE_5: outline+fill, >3с | TYPE_6: 2 смысловые группы
- Python: `script_jakson.py` (scenes.json → AE text_layers)

### 4th template (`4th_template/`)
- `flash_on_cuts.jsx` — вспышки на переключениях клипов
- `tape.jsx` + `prompt.md`

---

## Инфраструктура

**Серверы (Timeweb Cloud):**
- Linux-сервер: Docker + Celery + Postgres + Redis + MinIO
- Windows render node: `blast-render-node-dist` → `72.56.246.24` (**приоритетный**)
- Windows render node: `blast-worker-node-0` → `85.239.48.31`
- Доступ к Windows: WinRM, user=`Administrator`, пароль из Timeweb API (`TWC_TOKEN` в `.env.iac`)

**CI/CD:** `.github/workflows/` — deploy-current-branch, deploy-split-main, ci, logs-watchdog

**Observability:** Prometheus + Loki + Grafana + Alertmanager (`infra/runners/observability/`)

**Хранилище:** S3 (`S3_ASSET_PREFIX: pinterest_collection/pins2_1to1_20260323`) + MinIO как зеркало

**БД:** PostgreSQL (credits, users), Redis (state, Celery), SQLite (finance_bot)

**Платежи:** TBank (`tbank_client.py`)

---

## Режимы работы (`.env`)

- `MODE=dev` — локальный запуск, медиа из `footage/`, AE не запускается
- `MODE=prod` — очередь+оркестратор, dispatch на Windows-ноду

---

## Правила кода (AGENTS.md)

- **No Fallback Policy**: никаких implicit fallback — явный fail с ошибкой
- Исключение: Gemini fallback только при `503`/`429`, с логированием
- Поведение детерминировано и видно оператору

---

## Важные файлы для быстрого старта

| Задача | Файл |
|--------|------|
| Понять правила репо | `AGENTS.md`, `WORKING_WITH_THIS_PROJECT.md` |
| AI-пайплайн целиком | `mlcore/gemini_orchestrator.py` |
| Промпты | `mlcore/prompts/` |
| Job management | `services/orchestrator/job_store.py`, `tasks.py` |
| Публичный бот | `services/tg_bot_public/app.py` |
| Windows AE runner | `windows/render-node-runtime/main.py` |
| Конфиги стилей | `config/styles/` |
| Видеобаза | `pin/meta/video_database.json` (в репо `blast`) |

---

## Сессионный журнал

| Дата | Что сделано |
|------|-------------|
| 2026-05-22 | Первичный анализ всего проекта, создание CLAUDE.md |
| 2026-05-29 | Создан скелет `mlcore/hooks/f5_cognition/` (Мысль = TTS-хук через Gemini 3.1). Добавлен раздел «Хуки» с картой 5 категорий. |
| 2026-05-29 | F5 завершён end-to-end: подключены Gemini Stage1+Stage2 (TTS=`gemini-2.5-flash-preview-tts`, 3.1-preview даёт 500), врезана точка вызова `orchestrator_hook.py` между merge и `render_all_steps` → блок в `full_edit_config["f5"]`. |
| 2026-05-29 | F5 подключён в боте: `STAGE_WAIT_HOOK_TYPE`=5 категорий хука, «Мысль»→новая `STAGE_WAIT_HOOK_DEVICE` (5 приёмов→F5Device). Проброс `hook_device` через send_audio_s3 → `SendAudioS3Request.hook_device` → `F5_HOOK_DEVICE`; `user_drop_t`→`F5Request.drop_at_sec`. Зеркало в tg_bot_public (CI parity). |
