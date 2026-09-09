# Хендофф Кириллу — довести рендер-пайплайн до рабочего на ноде

> Локальная доп-дока (НЕ коммитить). База (сайт → job.json → очередь → воркер)
> собрана и проверена в моке. Ниже — что осталось на ноде с доступом к AE/S3/PG.
> Полный контекст: `backend/docs/RENDER_JOB_SPEC.md`. Все швы уже есть, всё за гейтами.

## Что уже готово (не трогать без нужды)
- `app/render_job.py` — стор визарда v4 → `render_job.json` (батч = N вариаций). Проверено.
- `app/effect_map.py` + `frontend/src/data/effects-registry.json` — единый реестр FX
  (RU-лейбл → manifestId). Добавить эффект = 1 запись в реестре + entry в manifest.
- `app/render_store.py` — `RenderStore`: InMemory (мок) / Pg (референс). `get_store()` по `DATABASE_URL`.
- `app/render_worker.py` — цикл claim→3 слоя→статус+heartbeat. `python -m app.render_worker`.
- `app/render_layers.py` — реальные слои за гейтом `BLAST_RENDER_MODE=real`.
- `db/migrations/001_render_jobs.sql` — таблицы `render_jobs` + `render_variations`.
- `АЕ/Хуки/Эффекты/manifest.json` — v3, пути актуализированы, `negative_zoom` добавлен.

## Осталось на ноде (чек-лист)

### 1. Postgres
- [ ] Применить `backend/db/migrations/001_render_jobs.sql`.
- [ ] `pip install "psycopg[binary]>=3.2"` (в requirements закомментировано).
- [ ] Выставить `DATABASE_URL=postgres://…` → `get_store()` сам возьмёт `PgRenderStore`.

### 2. Чтение статуса из БД в API (сейчас мок читает in-memory JOBS)
- [ ] `app/main.py` `get_job`/`active_job` → отдавать из `store.snapshot(job_id)`
      (для PG). Сейчас `mock_store.get_job` читает JOBS. Нужно собрать полный
      `GenerationJob` из `render_jobs.render_job` + `render_variations`
      (`snapshot()` пока отдаёт урезанный набор — дополнить полями фронта:
      name/videos.source/subtitleStyle/hook — их можно взять из `render_job` JSONB).

### 3. AE-нода (Windows)
- [ ] AE + плагины из манифеста: **Sapphire** (hook_light, layer_shake, old_camera),
      **VISINF Grain** (xerox, pixel_grain). Шрифты **Point** (субтитры).
- [ ] Выставить env: `BLAST_RENDER_MODE=real`, `BLAST_AE_ROOT=…/АЕ/Хуки/Эффекты`,
      `BLAST_AFTERFX=…/afterfx.exe`, `BLAST_AERENDER=…/aerender.exe`,
      `BLAST_SCRIPT_JAKSON=…/script_jakson.py`, `BLAST_WORK_DIR=…`.
- [ ] Запустить воркер: `python -m app.render_worker`.

### 4. Слои в `render_layers.py` — довести до реального (сейчас команды-скелеты)
- [ ] **Assembly** `run_assembly`: я формирую `scenes.json` из вариации
      (`lyrics/subtitle/background/track`) и зову `python script_jakson.py scenes.json --out project.aep`.
      **Сверь сигнатуру script_jakson** — возможно, вход/флаги другие; поправь под реальный скрипт.
      Тайминги субтитров: `subtitle.timingSource` = `"llm"` для финала (в моке дефолт).
- [ ] **Effects** `run_effects`: ГОТОВО по контракту — пишу срез `{dropTime,hook,transition,extra}`
      в `__job.json`, ставлю `BLAST_JOB`, зову `afterfx -noui -r run_job.jsx <aep>`, жду
      `__status.json`. Проверь только, что `run_job.jsx` читает `__status.json` из `BLAST_AE_ROOT`
      (я жду его там) и что comp находится по слою `Текст`.
- [ ] **Render** `run_render`: `aerender -project <aep> -comp "Рабочая" -output <mp4>` —
      **уточни имя комп** (у меня placeholder `"Рабочая"`).
- [ ] **S3** `_upload_s3`: заменить TODO на boto3 upload (ключ = `output.s3Prefix/{index}.mp4`).

### 5. Устойчивость (по желанию)
- [ ] Watchdog-реклейм зависших: `render_jobs` где `status='processing' AND heartbeat_at < now()-'2 min'` → `'queued'`.
- [ ] Retry по `attempts`, лимит и `status='failed'`.

## Мост, который уже работает (ядро)
`effects_slice(variation)` → `{dropTime, hook, transition, extra}` — ровно контракт
`run_job.jsx` (сверено). `null` = «нет эффекта» (run_job трактует сам). Семейства
object/motion (шейпы/жесты) run_job не покрывает — для них воркер зовёт
`variation.hook.family_script` напрямую (пути в `effect_map.OBJECT_SCRIPT/MOTION_SCRIPT`).

## Открытый GAP по манифесту (я не трогал — твоя зона)
- `__job.json` (твой тест) ещё ссылается на `warm_map` — он теперь `deprecated` в `unuse/`.
- `stylize/`: `blackwhite / crystal glow / night vision / wave` — без `.jsx`, в манифест не добавлял.
  Как будут готовы — по одной записи в манифест + в `effects-registry.json` (тогда и чип на сайте появится).
