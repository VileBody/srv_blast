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
{ "dropTime": 4.2, "hook": "hook_light", "transition": "snap_wipe", "extra": "warm_map" }
```
Параметры в дочерние скрипты прокидываются через `$.global.__BLAST` (merge в CONFIG на старте каждого скрипта).

## Синхронизация
Всё относительно **drop_time** (мастер-якорь). Звук с импульсом внутри файла: `audio.inPoint = drop_time - impact_at`
(Light Sound: импульс на 0.5с → старт за 0.5с до дропа). Лого stamp_flash вспыхивает ровно на дропе.

## ⚠️ TODO интеграции (asset roots)
- В `manifest.json` пути `Звуки/*` и `branding.logo_default` (`Хуки/Лого и шейпы/...`) — **относительные** и сейчас
  резолвятся `run_job.jsx` через `BASE_ROOT = <скрипт>/../..`. На рендер-ноде это укажет на `mlcore/hooks` — **ассетов там нет.**
  Нужно прокинуть реальный корень ассетов (звуки/лого) на ноде: либо положить ассеты под этот корень,
  либо завести в `run_job.jsx` переменную `BLAST_ASSET_ROOT` (env) и резолвить пути от неё.
- Плагины: часть эффектов требует **Sapphire** (`S_*`) и **VISINF Grain** — должны стоять на ноде.
- `.aep`-зависимости только у двух extra: `pixel_grain` и `warm_map` (Colorama/Grain-пресет читаются copyToComp из .aep — лежат рядом в `extra/`).
- `user_sound` — отдельный формат, в этом пайплайне НЕ трогается.
