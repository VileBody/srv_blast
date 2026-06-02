# f3_effect — пайплайн визуальных эффектов (категория «Эффект»)

Headless-сборка эффектов для AE-ролика **за один проход** на рендер-ноде.
Юзер выбирает 3 вещи → оркестратор сам применяет всё + звук + лого, синхронно с **дропом**.

## Поток (3 шага)
1. **hook** — эффект-хук в начале ролика (+ звук + лого-штамп)
2. **transition** — удар/переход на склейках (точки склейки детектятся автоматически по inPoint клипов)
3. **extra** — стилизация/грейд футажа до дропа

## Раскладка
```
f3_effect/
  manifest.json        # реестр: эффекты, apply_modes, branding, sounds. Единый источник правды.
  run_job.jsx          # ОРКЕСТРАТОР. Читает manifest + job.json, детектит склейки, применяет hook+trans+extra+лого+звук.
  hooks/               # rebuild_light, rebuild_shutter, rebuild_flash_slow_shutter
  transitions/         # snap_wipe, minimax, invert_flash, extract_flash, flash_on_cuts, layer_shake
  extra/               # xerox, analog_glitch, neon_extract, old_camera, pixel_grain(+.aep), warm_map(+.aep)
  branding/            # brand_logo.jsx — лого-штамп Бласта (stamp_flash на дропе)
  sound/               # sound.jsx (импорт+синхрон по impact_on_drop), test_sounds.jsx (ручной аудит)
```

## Запуск
```
afterfx.exe -r run_job.jsx          # проект уже открыт; job.json берётся из env BLAST_JOB или __job.json рядом
```
`job.json`:
```json
{ "dropTime": 4.2, "hook": "flash_slow_shutter", "transition": "snap_wipe",
  "extra": "warm_map", "hookExtend": "to_end" }
```
- `dropTime` — **COMP-relative** секунды в `Comp 1` (= `user_drop_t` минус начало рендер-сегмента; конвертация как в `f5_cognition` abs→relative). Кто пишет job.json (в `services/orchestrator/tasks.py`), тот и делает конвертацию.
- `hookExtend` (опц., только для `extendable`-хуков, см. slow shutter): `"to_end"` | `"after_drop:N"`.
- Параметры в дочерние скрипты — через `$.global.__BLAST` (merge в CONFIG на старте каждого скрипта).

## Целевой комп
`findComp` ищет комп, содержащий слой `"Текст"` → это корневой **`Comp 1`** (1080×1960), куда `app/footage_comp.build_footage_layers` кладёт футаж и где лежит прекомп-слой `"Текст"`. Туда же садятся эффекты, `detectCuts` берёт `inPoint` футаж-слоёв этого компа.

## Синхронизация звука (политика)
- **Дроп:** звук даёт ТОЛЬКО хук (молния у hook light / вспышка камеры у shutter, slow shutter). **Сабдроп не применяется.** Звук с импульсом: `audio.inPoint = drop - impact_at` (Light Sound impact 0.5с).
- **Склейки:** звук перехода/грейда (его `sound.pool`) вешается на склейки **строго до дропа**, после дропа — тишина. Один звук на склейку, переход+грейд дедупятся (`attachCutSounds` в run_job).
- Лого `stamp_flash` вспыхивает ровно на дропе.

## ⚠️ TODO интеграции
- **Asset root:** пути `Звуки/*` и лого в `manifest.json` относительные; `run_job.jsx` резолвит их от env **`BLAST_ASSET_ROOT`** (без env — падает на `BASE_ROOT = mlcore/hooks`, где ассетов нет). На рендер-ноде **обязательно задать `BLAST_ASSET_ROOT`** на папку с `Звуки/` и `Хуки/Лого и шейпы/`.
- **Плагины на ноде:** Sapphire (`S_*`) + VISINF Grain.
- **`.aep`-зависимости** только у `pixel_grain` и `warm_map` (Colorama/Grain-пресет через copyToComp — `.aep` лежат рядом в `extra/`).
- **dropTime-конвертация** в `tasks.py` (см. выше) — обязательна, иначе хук уедет.
- `user_sound` — отдельный формат, в этом пайплайне НЕ трогается.
