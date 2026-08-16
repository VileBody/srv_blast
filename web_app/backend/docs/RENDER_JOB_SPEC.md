# Blast — спека рендер-джоба (сайт → бэк → AE-пайплайн)

Версия черновика: `blast.render_job/1`. Автор врезки: Трек A.
Цель: связать конфигурацию, которую собирает визард сайта (zustand-стор
`blast-wizard-v4`), с реальным рендер-пайплайном (AE-оркестратор
`АЕ/Хуки/Эффекты/run_job.jsx` + `manifest.json`). Ничего во флоу визарда не
меняем — только формализуем контракт между слоями.

---

## 0. TL;DR

- Сайт коммитит конфиг **одним POST `/api/wizard/submit`** (payload = `stageData` из стора).
- Бэк из `stageData` собирает **`render_job.json`** — рецепт **батча** (Figma W36 «Батч видео №1») из **N вариаций**.
- Воркер на рендер-ноде разворачивает батч в N сборок. Каждая сборка проходит 3 слоя:
  1. **Assembly** (Python `script_jakson.py`/`build_layers.py`): футаж + субтитры + текст → собранная AE-комп.
  2. **Effects** (`run_job.jsx`): читает **per-variation `job.json`** (срез `effects`) + `manifest.json` → вешает хук/переход/грейд/звук/лого.
  3. **Render**: AE рендерит комп → mp4 → S3, статус в БД.
- **Ключевой факт:** текущий `run_job.jsx` уже ест минимальный `job.json`
  (`{dropTime, hook, transition, extra}` из manifest-id) и работает на **уже
  собранной** компе. Мы его НЕ трогаем — воркер отдаёт ему ровно срез `effects`.
- **Два разрыва, которые закрывает спека:** (1) стор хранит RU-лейблы
  (`Молния`, `Щелчок`, `Ксерокс`), а manifest — snake_case id (`shutter_effect`,
  `snap_wipe`, `xerox`) → нужна мап-таблица; (2) один хук визарда (`Негатив зум`)
  не имеет эффекта в манифесте → gap.

---

## 1. Архитектура: батч → вариации → 3 слоя

```
                  сайт (визард v4)
                        │  POST /api/wizard/submit  { stageData, videosToGenerate, idempotencyKey }
                        ▼
                  БЭК: build_render_job(stageData)
                        │  render_job.json  (батч = N вариаций)
                        ▼
                  ОЧЕРЕДЬ (PG + polling)          ◄── фронт polling'ует GET /api/jobs/{id}
                        ▼
   ┌───────────────── ВОРКЕР (Windows + AE) ─────────────────┐
   │  for variation in render_job.variations:                │
   │    1) ASSEMBLY  script_jakson.py → text_layers + футаж   │
   │    2) EFFECTS   run_job.jsx  ← job.json (срез effects)   │
   │    3) RENDER    aerender → mp4 → S3;  status → PG        │
   └─────────────────────────────────────────────────────────┘
                        ▼
             Детали проекта (W36): строки-генерации + скачивание
```

- **Батч** = один сабмит визарда. В UI это «Батч видео №1» (W36). Юзер может
  добавлять батчи (кнопка «+») — каждый новый сабмит = новый `render_job`.
- **Вариация** = один готовый ролик. В UI — строка «Видео №N» с чипами
  (фон / субтитр-стиль / хук).
- **Слой превью на сайте (Remotion)** — отдельный, НЕ часть рендер-джоба:
  во время визарда крутится быстрый Remotion-превью с **дефолт-таймингами
  субтитров** (равномерно по длине строки). LLM-тайминги нужны только в
  Assembly финального рендера. Спека это фиксирует разделением
  `subtitle.timingSource: "default" | "llm"`.

---

## 2. Карта коннекторов фронт → бэк

Все 31 вызовов `api.*` имеют эндпоинт в `backend/app/main.py` (недостающих нет).
Статус: `mock` = отдаёт мок-данные из `mock_store.py`; `stub` = заглушка без
логики; ⚠️ = точка врезки реального пайплайна.

| `api.*` (frontend/src/lib/api.ts) | Метод + эндпоинт | Что делает сейчас | Врезка в пайплайн |
|---|---|---|---|
| `me` | GET `/api/me` | mock user/subscription/tiktok | реальный auth (Трек D) |
| `login` / `register` / `tgVerify` | POST `/api/auth/*`, GET `/api/auth/tg-verify` | mock TG-верификация | реальный auth + антифрод (Трек D) |
| `projects` / `project` | GET `/api/projects[/{id}]` | mock проекты+джобы | БД проектов |
| `createProject` | POST `/api/projects` | создаёт mock-проект | БД |
| `createOrder` / `cancelSubscription` | POST `/api/payments/*` | mock TBank | реальный TBank + вебхук |
| `previousTrack` | GET `/api/wizard/previous-track` | mock прошлый трек | S3 треков юзера |
| `uploadTrack` | POST `/api/wizard/upload-track` | принимает файл (mock) | ⚠️ presigned S3 upload трека |
| `analyzeTrack` | POST `/api/wizard/analyze-track` | mock jobId | ⚠️ анализ BPM/дропов (движок) |
| `drops` | GET `/api/wizard/drops` | mock BPM+кандидаты дропа | ⚠️ реальные дропы из анализа |
| `rankVibes` / `vibes` | POST/GET `/api/wizard/(rank-vibes|vibes)` | mock вайбы | ⚠️ подбор групп футажа по тексту (как в боте: группа+пример) |
| `photos` | GET `/api/wizard/photos` | mock фото | ⚠️ фото-группы |
| `subtitleStyles` | GET `/api/wizard/subtitle-styles` | mock стили | каталог субтитр-стилей + Remotion-превью |
| `wizardSession` / `saveWizardSession` | GET/POST `/api/wizard/session` | mock автосейв | БД сессий визарда |
| **`submitWizard`** | **POST `/api/wizard/submit`** | **`store.create_job(project_id, stageData, N)`** | **⚠️⚠️ ГЛАВНАЯ ВРЕЗКА: здесь строится `render_job.json` и кладётся в очередь** |
| `subtitlePreview` / `compositePreview` | GET `/api/preview/*` | mock previewUrl | Remotion-превью (пример-затычки, п.4) |
| `job` / `activeJob` | GET `/api/jobs/[{id}|active]` | mock прогресс (`simulate_job`) | ⚠️ реальный статус из очереди/воркера |
| `rateJob` | POST `/api/jobs/{id}/rate` | mock рейтинг | БД (сигнал для движка эволюции) |
| `updateProfile` / `uploadAvatar` | PATCH `/api/profile`, POST `/api/profile/avatar` | mock | БД + S3 |
| `disconnectTiktok` / `postTiktok` | DELETE/POST `/api/tiktok/*` + GET `/api/tiktok/(auth|callback)` | mock OAuth/пост | ⚠️ Трек C: Login Kit + Content Posting (Direct Post) |

**Единственная точка коммита конфига** — `submitWizard`. Всё остальное — чтение
справочников/автосейв. Значит `render_job.json` строится ровно в обработчике
`POST /api/wizard/submit` (сейчас там `mock_store.create_job`, стр. 227 —
маппинг **устаревший**: ждёт `background.vibes`, `subtitles.style`, `hook.label`,
которых в сторе v4 уже нет; это надо переписать под §5).

---

## 3. Источник истины: стор визарда v4 (`stageData`)

`useWizardStore.stageData()` отдаёт (см. `frontend/src/stores/wizardStore.ts`):

```ts
{
  track,        // SavedTrack | null  { s3Key, filename, durationS, ... }
  lyrics,       // string — весь текст
  fragment,     // string | null — вкл. фрагмент-текст (fragmentEnabled)
  timing,       // { from, to } (manual) | { mode: 'ai' }  — окно трека
  background: { mode, footage[], photo[], photoEffects, photoStyle?, color?, strobe, glue? },
  hooks:      { dropTime?, kind?, configs: { [HookKind]: HookConfig } },
  subtitles:  { color, pool[] },
  allocation: { total, background{}, subtitles{}, hooks{}, strobeFont?, colorFont?, seeded },
  final:      { subtitleColor, accentColor, videosToGenerate, idempotencyKey }
}
```

Семантика (по коду панелей; помечено ⚠️ где вывод):
- `background.footage[]` / `photo[]` — **id/лейблы ГРУПП** (вайбов), не отдельные
  клипы. Точный подбор клипов остаётся в пайплайне (по договорённости —
  как в боте: группа + пример). `color` — hex фон, `strobe` — строб-режим,
  `glue` — склейка между клипами (id из `GLUE_TYPES`, §4).
- `hooks.kind` — семейство FX; `hooks.configs[kind]` — его настройка.
  Для `kind:'effects'` это `{effectHook, effectGlue, effectStyle}` — прямо
  ложится в `run_job` `{hook, transition, extra}`.
- `hooks.dropTime` — строка `mm:ss:ms`, момент дропа. ⚠️ `timing.from/to` —
  окно трека (какой кусок песни берём); не путать с дропом.
- `allocation` — распределение `total` роликов: независимо по срезам
  (`background` по unit-key, `subtitles` по имени стиля, `hooks` по `kind`).
  Вариации = index-zip срезов (см. §6).
- `final.subtitleColor`/`accentColor` — цвета; `idempotencyKey` — дедуп сабмита.

---

## 4. Мап-таблицы: RU-лейбл визарда → manifest id

`manifest.json` (`АЕ/Хуки/Эффекты/manifest.json`) оперирует id. Стор хранит
RU-лейблы. Резолвер (в бэке) должен переводить по таблицам ниже.

### 4.1 Хук-эффект (`hooks.configs.effects.effectHook` → manifest `group:hook`)
| Визард (`EFFECT_HOOKS`) | manifest id | Есть? |
|---|---|---|
| Молния | `hook_light` | ✅ |
| Затвор | `shutter_effect` | ✅ |
| Слоу-шаттер | `flash_slow_shutter` | ✅ |
| **Негатив зум** | — | ❌ **GAP: нет эффекта в манифесте** |

### 4.2 Склейка (`hooks.configs.effects.effectGlue` **и** `background.glue` → `group:transition`)
| Визард | id из | manifest id | Есть? |
|---|---|---|---|
| Щелчок | `EFFECT_GLUES` | `snap_wipe` | ✅ |
| Минимакс | `EFFECT_GLUES` | `minimax` | ✅ |
| Экстракт | `EFFECT_GLUES` | `extract_flash` | ✅ |
| Инверт | `EFFECT_GLUES` | `invert_flash` | ✅ |
| Вспышка | `EFFECT_GLUES` | `flash_on_cuts` | ✅ |
| snap-wipe | `GLUE_TYPES` (фон) | `snap_wipe` | ✅ |
| minimax | `GLUE_TYPES` | `minimax` | ✅ |
| extract | `GLUE_TYPES` | `extract_flash` | ✅ |
| invert | `GLUE_TYPES` | `invert_flash` | ✅ |

⚠️ Два источника склейки: `background.glue` (общая склейка футажа) и
`effects.effectGlue` (склейка внутри FX-хука). Правило приоритета — §5.4.
`layer_shake` в манифесте есть, но в визарде нет чипа → зарезервировано.

### 4.3 Стиль/грейд (`hooks.configs.effects.effectStyle` **и** `background.photoStyle` → `group:extra`)
| Визард (`EFFECT_STYLES`) | manifest id | Есть? |
|---|---|---|
| Ксерокс | `xerox` | ✅ |
| Глитч | `analog_glitch` | ✅ |
| Неон | `neon_extract` | ✅ |
| Старая камера | `old_camera` | ✅ |

manifest `extra` также содержит `pixel_grain`, `warm_map` (нет чипов в визарде — резерв).

### 4.4 Прочие семейства хука (пока НЕ идут в `run_job` — обрабатываются отдельными скриптами)
- `kind:'object'` (`Круг/Квадрат/Ромб/Звезда-5/Звезда-10`) → `АЕ/Хуки/Лого и шейпы/Шейпы/rebuild_shape_*.jsx`.
- `kind:'motion'` (`Свайп/Тап/Зум/Задержи/Голова`) → `АЕ/Хуки/Движение/*/rebuild_*.jsx`.
- `kind:'sound'` → пользовательский звук (manifest: `sounds.user_sound.handled=false` — отдельный формат).
- `kind:'thought'` (`Панчлайн/…`) → ИИ-голосовая вставка (движок, не AE-эффект).

Эти семейства **не** ложатся в текущий `run_job` `{hook,transition,extra}` —
для них воркер вызывает соответствующие скрипты напрямую. Спека резервирует под
них поле `variation.hook.family` + `resolvedScript` (§5.3), чтобы воркер знал,
какой скрипт звать. Расширение `run_job.jsx` под object/motion — отдельная задача.

---

## 5. Схема `render_job.json` (батч-уровень)

### 5.1 Полная схема

```jsonc
{
  "schema": "blast.render_job/1",
  "batchId": "job_ab12cd34",          // = job.id
  "projectId": "project_1",
  "userId": "user_1",
  "idempotencyKey": "…",              // final.idempotencyKey (дедуп сабмита)
  "createdAt": "2026-07-13T…Z",

  "track": {
    "s3Key": "…/tracks/user_1/…/source.mp3",
    "durationS": 204.0,
    "segment": { "from": 15.0, "to": 45.0 }   // timing.from/to → сек; null = весь трек
  },
  "lyrics": { "full": "…", "fragment": null },  // fragment из stageData.fragment

  "output": { "resolution": [1080, 1920], "fps": 23.976,
              "codec": "h264", "bitrateMbps": 12, "audio": "aac_320k",
              "s3Prefix": "videos/user_1/job_ab12cd34" },

  "variations": [ /* N штук, см. 5.2 */ ]
}
```

### 5.2 Одна вариация (элемент `variations[]`)

```jsonc
{
  "index": 1,
  "subtitle": {
    "style": "Impulse",           // из subtitles.pool (имя = allocation-ключ)
    "color": "#f6f5fd",           // final.subtitleColor (или subtitles.color)
    "timingSource": "llm"         // "default" (Remotion-превью) | "llm" (финал)
  },
  "background": {
    "mode": "footage",            // footage | photo | color
    "groups": ["neon", "cars"],   // background.footage[] / photo[] (id групп)
    "footageType": "standard",    // mode:footage — тип футажей (id из frontend/src/data/footage-types.json,
                                  // реестр пополняемый: standard|persons|movies|…), иначе null
    "uploads": [],                // свои исходники (Figma W39/W49): имена файлов из background.uploads
    "color": null,                // background.color (для mode:color)
    "strobe": false,              // background.strobe
    "photoStyle": null,           // background.photoStyle (mode:photo)
    "glueId": "snap_wipe"         // резолв background.glue → manifest id
  },
  "hook": {
    "family": "effects",          // hooks.kind (effects|object|motion|sound|thought)
    "dropTime": 4.2,              // hooks.dropTime "mm:ss:ms" → сек
    "resolved": {                 // РЕЗУЛЬТАТ резолва под слой Effects (см. 5.3/5.4)
      "hook": "shutter_effect",   // group:hook  | null
      "transition": "snap_wipe",  // group:transition | null
      "extra": "xerox"            // group:extra | null
    },
    "family_script": null         // для object/motion/thought — путь скрипта (иначе null)
  },
  "branding": { "enabled": true, "style": "stamp_flash" },  // из manifest.branding
  "sound":    { "userSound": null }                          // kind:'sound' → s3Key звука
}
```

### 5.3 Что уходит в `run_job.jsx` (per-variation `job.json`, БЕЗ изменений скрипта)

Воркер для вариации с `hook.family === "effects"` пишет `__job.json` = **срез**:

```json
{ "dropTime": 4.2, "hook": "shutter_effect", "transition": "snap_wipe", "extra": "xerox" }
```

Это ровно то, что читает текущий `run_job.jsx` (см. его хедер, стр. 12–14).
`extraStart`/`extraDuration` опциональны (по умолчанию грейд тянется до дропа).
Для `hook.family` ∈ {object, motion} воркер вместо `run_job` зовёт
`family_script` напрямую (§4.4). Для {sound, thought} — свои шаги (звук/ИИ-войс).

### 5.4 Правила резолва (бэк, `build_render_job`)

1. `subtitle.style` = ключ из `allocation.subtitles`, распределённый на вариацию.
2. `background.*` = unit из `allocation.background`, распределённый на вариацию:
   - unit-key декодит `mode` + конкретную группу/цвет (см. `SlicePanel` units).
   - `glueId` = `map_glue(background.glue)` (§4.2).
3. `hook` = kind из `allocation.hooks`, распределённый на вариацию (или «без хука»,
   если вариация в `units.filter(noHook)`).
   - `resolved.hook` = `map_hook(configs.effects.effectHook)` (§4.1).
   - `resolved.transition` = приоритет: `configs.effects.effectGlue` (§4.2), иначе
     `background.glueId` (общая склейка футажа).
   - `resolved.extra` = приоритет: `configs.effects.effectStyle` (§4.3), иначе
     `map_style(background.photoStyle)`.
   - Если хук неразрешим (напр. `Негатив зум` — §4.1 GAP): `resolved.hook = null`,
     лог-предупреждение, вариация рендерится без хука (fail-soft).
4. `branding` = из `manifest.branding` по эффекту хука (`branding: true|built_in`
   → штампуем; `hook_light` → `false`, лого не вешаем).
5. `dropTime` = `parse_mmssms(hooks.dropTime)`; если пусто — воркер берёт
   последнюю склейку (`run_job` уже так делает: `drop = cuts[last]`).

### 5.5 Алгоритм разворота вариаций (index-zip)

Зеркалит `distribute()` из `SlicePanel.tsx`:

```
def distribute(keys, total):  # раздать total по keys как можно ровнее, round-robin
    return {k: base + (1 if i < rem else 0) for i,k in enumerate(keys)}  # base,rem = divmod(total,len)

N   = allocation.total
bg  = expand(distribute-counts allocation.background) -> список длины N (по 1 на ролик)
sub = expand(allocation.subtitles) -> длины N
hk  = expand(allocation.hooks)     -> длины (N - кол-во noHook)
variations[i] = combine(bg[i], sub[i], hk[i or none])
```

Бэк ДОЛЖЕН держать `distribute()` идентичным фронтовому, иначе превью в «Пуле»
разойдётся с фактическим рендером.

---

## 6. Точки врезки в бэк/воркер (что делать)

1. **`POST /api/wizard/submit`** (`main.py:342`, сейчас `mock_store.create_job`):
   переписать под §5 — построить `render_job.json` из `stageData` v4 (актуальные
   поля!), сохранить в БД, положить в очередь, вернуть `job` фронту (контракт
   `GenerationJob` не меняется — фронт уже polling'ует `/api/jobs/{id}`).
2. **Резолвер лейблов** (§4) — модуль `effect_map.py` (RU-лейбл → manifest id),
   с логом GAP'ов (`Негатив зум` и т.п.).
3. **Очередь + воркер** — таблица `render_jobs` в PG (status: `queued|assembling|
   rendering|done|failed`, per-variation прогресс), воркер на AE-ноде забирает
   job, гоняет Assembly→Effects(`run_job.jsx`)→Render, заливает mp4 в S3
   (`output.s3Prefix/{index}.mp4`), пишет статус. `run_job.jsx` уже пишет
   `__status.json` (running|done|error) — воркер читает его как сигнал шага Effects.
4. **Статус-маппинг** — `render_jobs` → `GenerationJob`/`VideoVersion` (index,
   status, downloadUrl, thumbnailUrl) для `/api/jobs` и W36.
5. **Assembly-вход** — Python-сборщик принимает срез вариации (track.segment,
   lyrics, subtitle.style/timingSource, background.groups) и готовит комп с
   layer `Текст` (по нему `run_job` находит комп и склейки).

---

## 7. Открытые вопросы / допущения

- **A1 `timing.from/to`**: принято за окно трека (сегмент песни). Подтвердить,
  что это не дубль дропа. (В `render_job` → `track.segment`.)
- **A2 GAP `Негатив зум`**: нужен новый эффект в манифесте ИЛИ убрать чип из
  визарда. Пока fail-soft (рендер без хука).
- **A3 Тайминги субтитров**: онлайн-превью — `default` (Remotion, равномерно),
  финал — `llm`. LLM в интерактиве не вызываем (решение согласовано).
- **A4 object/motion/thought**: `run_job.jsx` их не покрывает; воркер зовёт
  скрипты `Шейпы/*`, `Движение/*` напрямую. Возможно, стоит расширить `run_job`
  до единого входа — отдельная задача, не блокер `render_job/1`.
- **A5 Формат контента** (лирикс/b-roll/липсинк, будущий Этап 0): в схему заложить
  `render_job.contentFormat` (сейчас неявно `"lyrics"`).
- **A6 Rust-движок**: заменит слой Effects/Render; `render_job.json` спроектирован
  движко-агностично (Effects-срез — единственное AE-специфичное место).
- **A7 Постинг/аналитика**: `variation` уже несёт полный конфиг ролика → это
  ключ для движка эволюции (конфиг × метрики TikTok). Схему под метрики добавим
  отдельным `blast.post_result/1`, когда подключим Display API (Трек C).

---

## 8. Единый реестр эффектов (расширяемость «добавил эффект → и чип, и резолв»)

Source of truth: **`frontend/src/data/effects-registry.json`** (`blast.effects_registry/1`).
Один эффект = одна запись `{ label, manifestId, icon, inner, altId?, branding? }`
в группе `hook | glue | style`. Из него деривится:
- **фронт** — `HookPanel.tsx`: `EFFECT_HOOKS/EFFECT_GLUES/EFFECT_STYLES` + иконки
  `CHIP_ICONS` (FX-часть) подмешиваются циклом из реестра;
- **бэк** — `effect_map.py`: `HOOK_MAP/GLUE_MAP/STYLE_MAP` + `HOOK_BRANDING`
  строятся из того же файла (с хардкод-фолбэком, если файл недоступен при отдельном
  деплое бэка).

Добавление нового FX = **одна запись** в реестре (правило единообразно). Пример:
`Негатив зум` (`negative_zoom`) добавлен так — чип уже был в визарде, резолв
появился автоматически. `BackgroundPanel.GLUE_TYPES` (склейка футажа, свои
иконки `glue-*.svg`) резолвится через `altId` реестра, но список пока свой —
кандидат на такую же дериву позже.

## 9. Статус реализации (мок-бэк)

| Компонент | Файл | Статус |
|---|---|---|
| Резолвер лейбл→manifest | `backend/app/effect_map.py` | ✅ читает реестр |
| Билдер `render_job` | `backend/app/render_job.py` | ✅ stageData v4 → render_job |
| Врезка в submit | `mock_store.create_job` | ✅ строит render_job + enqueue |
| Store-абстракция очереди | `backend/app/render_store.py` | ✅ `RenderStore` protocol + InMemory (мок) + Pg (референс, `DATABASE_URL`) |
| PG-схема | `backend/db/migrations/001_render_jobs.sql` | ✅ `render_jobs` + `render_variations`, claim = `FOR UPDATE SKIP LOCKED` |
| Воркер (claim→3 слоя→статус) | `backend/app/render_worker.py` | ✅ serial, стейт-машина слоёв (assembling→rendering→done), heartbeat; `python -m app.render_worker` на ноде |
| Слои рендера (assembly/effects/render) | `backend/app/render_layers.py` | ✅ реальные subprocess-вызовы за гейтом `BLAST_RENDER_MODE=real`; `effects_slice()` пишет `{dropTime,hook,transition,extra}` = контракт run_job.jsx (сверено) |
| Прогон на живой AE-ноде | — | ⛔ нужен AE/aerender/python на Windows-ноде (env BLAST_AE_ROOT/AFTERFX/AERENDER/SCRIPT_JAKSON) |
| Чтение статуса из PG в API | `main.py` get_job | ⛔ прод: `store.snapshot()` вместо JOBS |
| LLM-тайминги / Assembly | — | ⛔ прод (script_jakson в воркере) |

**Мост render_job → run_job.jsx (ядро Трека A):** `render_layers.effects_slice(variation)` →
`{dropTime, hook, transition, extra}` (null = «нет эффекта», run_job трактует сам) → пишется в
`__job.json`, путь в env `BLAST_JOB`, `afterfx -noui -r run_job.jsx <aep>`, ждём `__status.json`
(done|error). Слой Effects скрипт run_job.jsx НЕ меняли.

## 10. ⚠️ Для render-команды: manifest.json рассинхронизирован с папками

На момент врезки `АЕ/Хуки/Эффекты/manifest.json` **отстаёт от реальной структуры**:
- скрипты хуков лежат в `hook/<name>/…`, но manifest указывает `hook light/…`
  (без префикса `hook/`); экстра-стили в `stylize/<name>/…`, а manifest —
  `extra/…` / `analog glitch/…`. `run_job.jsx` резолвит путь от `Эффекты/` →
  текущие пути в манифесте не найдутся.
- **`negative_zoom` отсутствует** в манифесте, хотя скрипт есть
  (`hook/negative  zoom/rebuild_negative_zoom.jsx`) + `.aep` + пример.
- В `stylize/` есть эффекты вне манифеста/визарда: `blackwhite`, `crystal glow`,
  `night vision`, `wave` — кандидаты в реестр, когда подтвердите готовность.

Резолвер сайта уже знает `negative_zoom` (реестр), но для фактического рендера
нужен manifest-entry + актуализация путей. Это артефакт render-команды — не
трогал, только фиксирую.
```
