# Footage pipeline — карта и порядок работы с базой

Как устроен подбор футажа от S3 до рендера, и **как безопасно растить базу**. Цепочка держится на двух осях, которые легко перепутать.

---

## Две оси

### Ось 1 — КАКИЕ клипы существуют (пул выборки)
```
S3 (pinterest_collection/<prefix>/...)
  → static_assets_index_1to1.json   (scripts/build_static_assets_index.py)   ← ВХОД ПУЛА
  → footage_inventory.json          (footage_config.py)
  → picker_assets                   (mlcore/footage_picker.load_picker_assets_from_inventory)
```
**Только то, что есть в `static_assets_index_1to1.json`, может попасть в выборку.** Клип, залитый в S3, но не попавший в индекс, пикеру невидим.

### Ось 2 — ЧЕМ клип отегирован (матчинг)
```
Groq Vision (pin/scan.py | кнопка «Разметить»)
  → footage_tags (Postgres, ключ clip_id)
  → footage_tags_snapshot.json   (scripts/export_footage_tags_snapshot.py)
  → навешивается на клипы инвентаря по clip_id (map_inventory_assets_with_style_metadata)
```
Теги **только навешиваются** на клипы из Оси 1 по `clip_id`. Лишние теги без клипа в инвентаре игнорируются — добавить клип в пул через теги нельзя.

---

## Источники правды

| Что | Файл / хранилище | Кто читает |
|-----|------------------|-----------|
| Пул (какие клипы) | `data/static_assets_index_1to1.json` | footage_config → inventory |
| Теги | Postgres `footage_tags` → `data/footage_tags_snapshot.json` | пикер; admin UI |
| Таксономия LLM (темы/группы) | `footage_v2.py` | Stage2B |
| Бан-теги | `3rd_footage_selection_prompt/prompt.md` | пикер |
| Алиасы тегов (free-form → таксономия) | `data/tag_aliases.json` | пикер |
| Профили артистов | `footage_v2.py` + `config/styles/artist_presets.json` | Stage2B / resolver |

Подбор per job: юзер выбрал `artist_id` → Stage2B (`footage_v2.py`) выбирает тему+группу+фильтры → `footage_picker` скорит клипы инвентаря по пересечению `meta_theme_tags ∩ priority_theme_tags`, применяет баны/исключения, детерминированно (seeded) раскидывает по интервалам.

---

## Как добавить футаж (ЧЕКЛИСТ)

> Порядок важен. Пропуск шага = клип не попадёт в пул ИЛИ попадёт без тегов (невидим пикеру).

1. **Залить в S3** — локально скачал/отсеял → Импорт в admin UI (или прямой upload в `<prefix>/<genre>/<tag>/`).
2. **Пересобрать индекс пула** (Ось 1):
   ```bash
   S3_BUCKET_ASSET_STORAGE=... S3_ASSET_PREFIX=pinterest_collection/<prefix> \
   python scripts/build_static_assets_index.py
   ```
   → обновит `data/static_assets_index_1to1.json` (ffprobe тянет src_w/h/duration по presigned URL; цвет из прошлого индекса сохраняется).
3. **Пересобрать инвентарь** (на нодах оркестратора): `python footage_config.py` (либо это делает старт контейнера).
4. **Разметить новые клипы** (Ось 2): кнопка «🏷 Разметить» в admin UI (тегает только untagged) → пишет в `footage_tags`. **Снапшот экспортируется автоматически** в конце разметки на ноде воркера (ручной шаг не нужен).
5. **(Только мульти-нода)** Раздать обновлённый `data/footage_tags_snapshot.json` на остальные ноды — авто-экспорт обновляет только ноду воркера. Ручной экспорт при необходимости:
   ```bash
   python scripts/export_footage_tags_snapshot.py data/footage_tags_snapshot.json
   ```
6. **Передеплой / пересоздание контейнеров** оркестратора+воркеров, если файлы не на общем volume.

Проверка: клип виден в admin UI с тегами → он есть и в `footage_tags`, и (после шага 2-3) в инвентаре → участвует в выборке.

---

## Известные дыры (бэклог стабильности)

- ✅ #1 Генератор индекса — `scripts/build_static_assets_index.py` (этот коммит).
- ✅ #2 Blacklist тегов: пишется в Postgres (admin) → авто-экспорт в `data/tag_overrides.json` в конце разметки (тот же хук, что у снапшота тегов) → пикер видит. На мульти-ноде раздача файла — на деплое. (exclude/assign оставлены файловыми — не используются; удаление работает через S3-trash + пересборку индекса.)
- ⏳ #3 Словарь теггера (open-vocab) vs таксономия пикера: видимость тегов ~66% после алиасов. Растить алиасы из частотного отчёта перед большими заливками.
- ⏳ #4 Таксономия размазана по 3 файлам (footage_v2 / 3rd_prompt / artist_presets) → дрейф. Свести к одному источнику + CI-гейты.
- ✅ #5 Снапшот авто-экспортируется в конце разметки (на ноде воркера). Мульти-нодовая раздача — на деплое.

---

## Быстрая диагностика

- **Клип без тегов в UI, но должен быть** → проверь, есть ли его `clip_id` в `footage_tags`; если нет — прогони разметку (шаг 4) + ре-экспорт (шаг 5).
- **Клип не выбирается никогда** → есть ли он в `static_assets_index_1to1.json` (шаг 2)? Совпадает ли его тег с таксономией темы (см. `tag_aliases.json`)?
- **«70% мимо» в выборке** → смотри `stage2_footage_rotation_diag.json`: `primary_pool_count` (маленький = дефицит темы), `repeat_ratio`/`exclude_relaxed` (высокие = пул не тянет).
