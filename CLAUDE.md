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
| **Звук** | `mlcore/hooks/f1_sound/`     | **готово end-to-end** — БЕЗ LLM: юзер сам загружает звук (бот→S3), он играет в окне `[0.5, drop−0.5]` до хука; визуал = combo как у F2 без шейпов (молния на дропе + рандомный F3-переход после). Бот подключён, зеркало + parity. Осталось: живой smoke (проверить, что нода тянет звук по remote_url). |
| **Объект** | `mlcore/hooks/f2_object/`  | **готово end-to-end** — packaged combo: 5 shape-переходов (rhomb/square/star1/star2/elipse) на pre-drop склейках + F3 `hook_light` на дропе + seeded-random F3 transition на post-drop склейках. Юзер выбирает только форму. Бот подключён, зеркало в `tg_bot_public` + parity-тест. Осталось: живой smoke. |
| **Эффект** | `mlcore/hooks/f3_effect/`  | **провязано (build+threading+бот)** — визуал-FX: хук/переход/грейд + звук/лого, инъекция в AE как f4. Осталось: S3-каталог ассетов + живой smoke. |
| **Движение** | `mlcore/hooks/f4_motion/` | **провязано (build+threading+бот)** — 5 приёмов (свайп/тап/зум/задержи палец/качай головой): engagement-bait оверлей в такт + вспышка на дропе, инъекция в AE как f3/f5. Анализ дропа/bpm в оркестраторе (`/hook/analyze`, librosa), reframe окна по дропу. Осталось: живой smoke 5 приёмов. |
| **Мысль** | `mlcore/hooks/f5_cognition/` | **готово** — TTS-вставка 2–3.5с поверх focal_start трека (Gemini), подключено в боте. Stage1 таргетит **строку после дропа** (определяется по ASR word-timings: первое слово с `t_start ≥ USER_DROP_T` → конец фразы), а не начало клипа. |

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
| 2026-06-07 | **F3 ассеты на S3 (звуки/лого, доставка как media[]).** `mlcore/hooks/f3_effect/asset_picker.py::resolve_assets(hook,trans,extra,seed)` — читает env `FX_ASSETS_S3_BUCKET`/`FX_ASSETS_S3_PREFIX`, по манифесту резолвит singleton-файлы (`hook_light` → `sounds/light_sound/myinstants.mp3`) и пикает из пулов (`camera_flash`/`glitch`) детерминированно по seed (`STAGE2_SELECTION_SEED`/`JOB_ID`). Возвращает `assets` (relpaths для overlay) + `_media` (S3-URL+relpath). `gemini_orchestrator` вызывает picker сразу после сборки `f3_block`. `app/project_builder._extract_f3_media` → `payload["f3_media"]` → `services/orchestrator/render_manifest.collect_media_urls_from_render_payload` добавляет в Windows-`media[]` рядом с футажом. Манифест переведён на S3-keys (`sounds/<pool>`, `logo/group_1245.png`). `.env.example`: `FX_ASSETS_S3_BUCKET`/`FX_ASSETS_S3_PREFIX=fx_assets/`. Тесты: `tests/test_f3_asset_picker.py` (mock boto3: определ. + кеш + dedup + no-env), `tests/test_f3_media_pipeline.py` (extract + collect_media_urls). Без env → F3 fx работает визуал-only (слоты пропускаются). Инструкция для Кирилла по заливке — отдельным сообщением в чате. |
| 2026-06-03 | **F3 «Эффект» провязан end-to-end (зеркало f4).** Build-side: `mlcore/hooks/f3_effect/overlay.py::build_overlay_jsx` (бандлер: manifest+дочерние .jsx → один инъект-блок, хук/переход/грейд+звук/лого, синхрон по дропу, cut-sounds до дропа с дедупом, slow-shutter extend) → `project_builder._build_f3_overlay_js` → токен `{{ f3_overlay_js }}` в `project_template.j2` (после f4, до save). Threading: `schemas.SendAudioS3Request.effect_hook/transition/extra/hook_extend` → `tasks` env `F3_HOOK/F3_TRANSITION/F3_EXTRA/F3_HOOK_EXTEND` (subprocess env) → `gemini_orchestrator` собирает `f3_block` (drop comp-relative = USER_DROP_T−clip_start) → `render_all_steps(f3_block=)` → `full_edit_config["f3"]`. Бот: категория «Эффект» (3 под-шага hook→transition→extra + extend для slow shutter), зеркало в tg_bot_public + parity-тест. Ассеты звук/лого = S3 media[] (каталог `assets.json` пока пуст → визуал работает без звука). Дев-харнесс: `run_job.jsx`+`manifest.json`. **Осталось: S3-каталог ассетов + живой smoke f3/f4/f5.** |
| 2026-06-04 | **F4 «Движение» провязан end-to-end (эталон для f3).** 5 device-оверлеев `mlcore/hooks/f4_motion/devices/{swipe,tap,pinch,holdfinger,head}.jsx` — порт engagement-bait шаблонов 4-го формата (морфинг руки/головы в такт + release-текст + вспышка на дропе). `overlay.py::build_overlay_jsx(device,bpm)` инжектит bpm и оборачивает в IIFE над MAIN_COMP → `project_builder._build_f4_overlay_js` → токен `{{ f4_overlay_js }}` (после flash-on-cuts, до save). Threading: `schemas.SendAudioS3Request.f4_device` → `tasks` env `F4_HOOK_DEVICE` → `gemini_orchestrator` собирает `f4_block{device,bpm}` (bpm из hook_aware-анализа) → `render_all_steps(f4_block=)` → `full_edit_config["f4"]`. **Выравнивание по дропу:** бот делает reframe окна `clip_start = drop − lead_eff` (`lead_eff = LEAD[device]·refBpm/bpm`, refBpm=128), чтобы конец «накрывающего» чёрного слоя лёг ровно на дроп. **Анализ дропа+bpm вынесен в оркестратор** (`POST /hook/analyze`, runtime-образ с librosa; `analyze_focus_clip` через `mlcore/audio_analysis.py`) — слим-боты (`requirements.infra.txt`) librosa не тянут. Бот: категория «Движение» (5 кнопок-приёмов), фоновая аналитика заливает клип на S3 и зовёт `/hook/analyze`. Зеркало в tg_bot_public + parity. Деплой через GHCR prebuilt (Кирилл — фикс медленной сборки на ноде: образы собираются в CI, нода тянет готовое). **Осталось:** живой smoke 5 приёмов; F5 в проде временно no-op (pydub/ffmpeg откатили при фиксе деплоя — возврат ffmpeg в runtime-образ за Кириллом). |
| 2026-06-05 | **Хуки env-проброс + F5 pydub**: `_LLM_ENV_KEYS` дополнен `F3_*`/`F4_*`/`F5_*`/`USER_DROP_T`/`BG_*` — без этого in-process оркестратор не видел хук-env, `f3_block`/`f4_block` оставались None, эффекты не применялись (диагноз по Loki: задача `701ceafd…` выбрала slow_shutter+minimax+analog_glitch, но в логах ни `f3.fx block`, ни `f3.fx FAILED`). pydub+ffmpeg возвращены в runtime-образ. |
| 2026-06-06 | **Дубль flash/shake устранён (вариант B):** `addFlashOnCuts()` больше не авто-вызывается в шаблоне; базовая тряска футажа `shake_for_layer=False` в `app/footage_comp.py`. F3 — единственный источник `flash_on_cuts`/`layer_shake`; ролики без F3 теперь не получают дефолтных вспышек/тряски (осознанный трейд-офф). |
| 2026-06-06 | **F2 «Объект» build-side provязан (Pass 1).** 5 shape-скриптов `mlcore/hooks/f2_object/shapes/{rhomb,square,star1,star2,elipse}.jsx` (пара shape+layer + minimax flash + snap wipe, T_FX_OFFSET=0.434). `overlay.py::build_overlay_jsx(shape, drop_time, seed)` собирает packaged-combo: pre-drop склейки получают выбранную форму (`startTime=cut−T_FX_OFFSET`), на дропе фигачит `rebuild_light.jsx` из F3, на post-drop склейках — seeded-random `__f2_rng` (mulberry32) выбор F3-перехода per cut → группировка по tid → один вызов на группу (layer_shake ignore cuts → вызываем глобально на comp). Загрузка F3-скриптов по пути из `f3_effect/` — без дубликатов. Threading: `schemas.f2_shape` (`Literal[rhomb|square|star1|star2|elipse]`, требует `user_drop_t`) → `tasks.env["F2_SHAPE"]` (+ опц. `F2_SEED`; иначе hash(JOB_ID)) → `gemini_orchestrator` собирает `f2_block{shape, drop_time, seed}` (drop comp-relative = `USER_DROP_T−clip_start`) → `render_all_steps(f2_block=)` → `full_edit_config["f2"]` → `project_builder._build_f2_overlay_js` → токен `{{ f2_overlay_js }}` в `project_template.j2` (после f3, до save). 14+6 тестов зелёные. |
| 2026-06-09 | **F1 «Звук» end-to-end + F5 пост-дроп фокус + аудит.** **F1:** новый `mlcore/hooks/f1_sound/` (БЕЗ LLM) — юзер грузит звук, бот заливает в S3 и шлёт `f1_sound_url`. `inject.py::inject_f1_audio` ставит audio-слой (remote_url) в окно `[0.5, drop−0.5]` (пады `F1_LEAD/TAIL_PAD=0.5`). Визуал = `f2_object.overlay.build_overlay_jsx(shape=None)` (combo без pre-drop шейпов: hook_light на дропе + seeded-random F3-переход после) через `f1_sound/overlay.py`. Threading: `schemas.f1_sound_url` (требует `user_drop_t`) → `tasks.env["F1_SOUND_URL"]` (+ в `_LLM_ENV_KEYS`) → `gemini_orchestrator` собирает `f1_block{sound_url, drop_time, seed}` (drop_rel>1.0) → `render_all_steps(f1_block=)` → `full_edit_config["f1"]` → `project_builder._apply_f1_audio_if_present` (аудио) + `_build_f1_overlay_js` → токен `{{ f1_overlay_js }}`. Бот: категория «Звук» → `STAGE_WAIT_F1_SOUND` (загрузка аудио, переиспользует `_extract_audio_spec`/`_download_telegram_audio_with_retry`/`s3.upload_file`) → `f1_sound_url`. `_HOOK_CATEGORY_NOT_READY` теперь пуст (все 5 категорий провязаны). Зеркало в `tg_bot_public` + parity-тест. **F5 фикс:** Stage1 промпт таргетит пост-дроп строку (`orchestrator_hook._post_drop_focus_line` по `transcript_words`+`USER_DROP_T`), убран «первая строка/слово» акцент; `F5Request.focus_line`. **Аудит:** все hook-env в `_LLM_ENV_KEYS`, drop-валидация согласована, routing аудио ок. Тесты: f1(12)+f5(8) новые, build-side 57/57 зелёные. Осталось: живой smoke F1 (нода тянет звук по remote_url из raw-audio bucket?). |
| 2026-06-06 | **F2 «Объект» UX в боте (Pass 2).** `tg_bot_botapi`: новая стадия `STAGE_WAIT_F2_SHAPE` + поле `ChatState.f2_shape`. Категория «Объект» убрана из `_HOOK_CATEGORY_NOT_READY`; `_handle_wait_hook_type` при `BTN_HOOK_CAT_OBJECT` проверяет `hook_drop_t`, сетит `hook_category="object"`, ведёт в `_ask_f2_shape` (5 кнопок: Ромб/Квадрат/Звезда-10/Звезда-5/Эллипс). `_handle_wait_f2_shape` валидирует выбор, лог-сообщение, дальше `_ask_versions`. Callsite `send_audio_s3(f2_shape=…)` гейтнут `st.hook_enabled and st.hook_category == "object"`. `orchestrator_client.send_audio_s3` принимает `f2_shape` kwarg → payload `"f2_shape"`. `_handle_wait_hook_choice` при «Нет» сбрасывает `f2_shape`. Зеркало в `tg_bot_public`: `STAGE_WAIT_F2_SHAPE` + `ChatState.f2_shape` + `F2_SHAPE_IDS`/`F2_SHAPE_LABELS_RU` + `OrchestratorClient.send_audio_s3.f2_shape` (UX там за `HOOK_FLOW_ENABLED`, но state/client мирьорятся для CI parity). Parity-тест `tests/test_tg_bot_public_f2_object_mirror.py` (5 проверок: stage в HOOK_STAGES, id-set, labels, ChatState поле, client kwarg, drift schema↔public id-set). Локальные тесты 39/39 зелёные (3 mirror-теста требуют `boto3` — та же среда-проблема, что F3-mirror; в CI зелёные). |
