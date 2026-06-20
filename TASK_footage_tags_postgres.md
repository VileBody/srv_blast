# Задача: миграция тегов футажа в Postgres + переключение пикера на снапшот

**Контекст:** теги клипов раньше жили в JSON-файлах (`2nd_footage_selection_prompt/video_database*.json`) — копии устаревали, конкурентная запись их ломала, один клип в нескольких жанровых папках = дубли. Перевели на единый источник в Postgres (таблица `footage_tags`, ключ `clip_id`). Пикер при этом **не меняется** — он читает JSON-снапшот, экспортируемый из Postgres. БД не в горячем пути рендера.

Код уже в `main` (коммит `716f793`). Нужно прогнать миграцию на сервере и переключить пикер. Я (Никита) сделать не могу — нет доступа к проду.

---

## Что нужно сделать

### 1. Миграция баз → Postgres

На сервере с доступом к Postgres (те же креды, что у credits-бота — `CREDITS_DB_URL` или `POSTGRES_*`):

```bash
CREDITS_DB_URL="postgres://..." \
python scripts/migrate_footage_tags_to_pg.py \
  "2nd_footage_selection_prompt/video_database (2).json" \
  "2nd_footage_selection_prompt/video_database2.json" \
  "/путь/к/pin/meta/video_database.json"
```

⚠️ **Порядок аргументов важен:** свежайшую/полную базу передавать ПОСЛЕДНЕЙ — при ничьей по полноте она перебивает. Самая полная — `pin/meta/video_database.json` (2192 строки), идёт третьей. Если этого файла нет на сервере — нужно его туда закинуть (он лежит локально у Никиты в репозитории `blast/pin/meta/`).

**Ожидаемый вывод:**
```
  loaded   788 keyed records from video_database (2).json
  loaded   924 keyed records from video_database2.json
  loaded  2191 keyed records from video_database.json
merged -> 2101 unique clip_ids
upserted 2101 rows | table 0 -> 2101 rows
```

Скрипт **идемпотентный** — повторный запуск даёт `table 2101 -> 2101` (upsert по clip_id, не дубли).

### 2. Проверка содержимого

```sql
-- всего строк: должно быть 2101
SELECT count(*) FROM footage_tags;

-- строк без тегов: должно быть 0
SELECT count(*) FROM footage_tags WHERE array_length(theme_tags,1) IS NULL;

-- sanity по распределению
SELECT mood, color_tone, count(*) FROM footage_tags GROUP BY 1,2 ORDER BY 3 DESC LIMIT 10;
```

**Критерий приёмки:** `count = 2101`, строк без тегов `= 0`.

### 3. Экспорт снапшота + переключение пикера

```bash
CREDITS_DB_URL="postgres://..." \
python scripts/export_footage_tags_snapshot.py data/footage_tags_snapshot.json
# ожидаем: wrote 2101 rows (2101 tagged) -> data/footage_tags_snapshot.json
```

Затем выставить env для оркестратора/воркеров и передеплоить:
```
FOOTAGE_STYLE_METADATA_DB_PATHS_JSON=["data/footage_tags_snapshot.json"]
```

После этого пикер читает теги из снапшота (сгенерированного из Postgres) вместо старых JSON-копий.

---

## Что в итоге в базе

Таблица `footage_tags`, PK = `clip_id`:

| поле | смысл |
|------|-------|
| `clip_id` | 8+ значный id из имени файла (ключ дедупа — один физический клип = одна строка) |
| `file_name`, `s3_key`, `video_key` | идентификаторы |
| `mood` | major / minor / пусто |
| `color_tone` | dark / light / warm / cold / neutral / пусто |
| `people_type` | none / girls / guys / couple / crowd / driver |
| `theme_tags[]` | нормализованные lowercase, дедуплицированные |
| `tagger` | `migration` (позже `groq` для авторазметки) |
| `updated_at` | |

**Про числа:** в базе 2101 клип, в S3 сейчас ~1013. Лишние записи (теги клипов, которых нет в S3) безвредны — пикер матчит только против инвентаря, который строится из реального S3. Это запас тегов на случай возврата клипа.

---

## Сопутствующее (не блокирует)

- В `mlcore/footage_picker.py` добавлен слой синонимов (`data/tag_aliases.json`): свободные теги теггера (`rainy`, `mountains`) → каноничные теги таксономии (`rain`, `mountain`). Видимость тегов пикеру 59.6% → 65.7%, спасено 15 «слепых» клипов. Работает уже сейчас, отдельных действий не требует.

---

## Server-side авторазметка (готова, код в main)

Авторазметка неотегированных S3-клипов: качает клип → ffmpeg 3 кадра (25/50/75%) → Groq Vision (голосование) → upsert в `footage_tags`. Только Groq (Gemini убран), ключи из env.

### Env для воркера (worker-build)

```
GROQ_API_KEYS=key1,key2,key3,...        # round-robin; либо одиночный GROQ_API_KEY
GROQ_VISION_MODEL=meta-llama/llama-4-scout-17b-16e-instruct   # дефолт, можно не ставить
S3_BUCKET_ASSET_STORAGE=...             # уже есть
CREDITS_DB_URL=...                      # уже есть
# ffmpeg/ffprobe уже в runtime-образе
```

### Запуск

**Через API (admin):**
```
POST /asset-ui/api/assets/tag-untagged          # запустить (опц. ?limit=N)
GET  /asset-ui/api/assets/tag-untagged/status    # прогресс: state/done/total/written
```
`POST` ставит Celery-таск `orchestrator.tag_untagged_footage` (очередь build). Single-flight: повторный POST при running → 409.

**Фронт-кнопка:** готова — в `asset_ui` добавлена кнопка «🏷 Разметить без тегов» в тулбаре (`asset_ui/src/components/TagUntaggedButton.tsx`), с поллингом прогресса `GET .../tag-untagged/status` и подхватом уже идущего прогона при перезагрузке. Билдится в Docker-образе (`npm run build`), отдельных действий не требует.

**Через Celery напрямую (без UI):**
```python
from services.orchestrator.celery_app import celery_app
celery_app.send_task("orchestrator.tag_untagged_footage", args=[0])  # 0 = без лимита
```

### Что делает батч
1. Берёт tagged clip_ids из `footage_tags`.
2. Листает S3 (`S3_ASSET_PREFIX` первый уровень / `ASSET_UI_SOURCE_PREFIX`).
3. Дифф → неотегированные (дедуп по clip_id, жанровые папки игнорируются).
4. Каждый: download → 3 кадра → Groq → upsert. Прогресс в Redis `footage_tagging:progress`.
5. После разметки — заново прогнать `export_footage_tags_snapshot.py` (Шаг 3 выше), чтобы пикер увидел новые теги.

Рекомендация: первый прогон с `?limit=20` — проверить качество тегов, потом без лимита.

## Контрольные тесты (локально, без БД)

```bash
python -m pytest tests/test_footage_tag_aliases.py tests/test_footage_tags_db.py -q
# 13 passed
```
