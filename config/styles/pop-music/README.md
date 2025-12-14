# Pop Music style pack

Этот пакет — **набор “пресетов/стилей” для сборки AE-проекта**, которые используются:
1) LLM’кой (через prompt) при генерации `composition.json`,
2) Python-ассемблером (сборка `PROJECT_DATA`),
3) JSX-движком в After Effects (применение свойств/ключей).

Пак лежит в `config/styles/pop-music/`.
Выбор пака: env `AE_STYLE_PACK=pop-music` (по умолчанию тоже pop-music).

---

## Файлы пака

### 1) project_settings_template.json
**Назначение:** базовая геометрия проекта (defaults).

Используется Python-ассемблером как “fallback” для компов, если LLM не указала `width/height/fps/pixelAspect`.

Ключевые поля:
- `defaults.width`
- `defaults.height`
- `defaults.pixelAspect`
- `defaults.fps`

Важно: `duration` чаще задаётся динамически из `global_start_sec/global_end_sec`, либо из `projectSettings.defaults.duration`.

---

### 2) text_styles.json
**Назначение:** стили для `TextDocument` (Source Text) в AE.

LLM указывает:
- `styleId: "main_subtitle"` или `"highlight_subtitle"`
- `content: "текст"`

Ассемблер превращает это в:
- `textDocument: { font, fontSize, fillColor, ... , text }`

---

### 3) footage_presets.json
**Назначение:** пресеты для футажа (`ref`-слои): transform/флаги.

LLM указывает:
- `presetId: "vertical_fit" | "bg_transform" | "shake_adj" ...`

Ассемблер мерджит пресет + слой (у слоя приоритет).

---

### 4) text_motion_library.json
**Назначение:** “движение текста” и “шаблоны ключей” (dedup keyframe attributes).

Секции:

#### keyTemplates
Это переиспользуемые шаблоны для ключей, чтобы не дублировать
`inInterpolationType/outInterpolationType/inTemporalEase/outTemporalEase/...` на каждом ключе.

В overrides / keys используем:
```json
{"time": 0.0, "value": 0, "templateRef": "tpl_fade_out"}
```

#### textAnimPresets
Готовые деревья `Text > ADBE Text Animators`.

LLM указывает на слое:
- `animId: "anim_reveal_opacity"` или `"anim_static"`
- `overrides: {...}` по `exposedParams`.

Ассемблер:
- берёт preset.propertyTree
- применяет overrides через `matchNamePath` в нужное место дерева
- результат кладёт в `textAnimTree`, который применяет JSX.

#### transformPresets
Готовые деревья `ADBE Transform Group` (full-fidelity).

LLM указывает:
- `transformId: "tf_subtitle_base"`
- `overrides: { "scale": ..., "opacity": ... }`

Ассемблер кладёт результат в `transformTree`.

---

## Как это завязано с нейронками (LLM)

LLM генерит `composition.json` и использует **ID-шники**, а не “сырой AE-дамп”:
- `styleId` (TextDocument)
- `presetId` (footage)
- `animId` / `transformId` (text motion)
- `overrides` (точки управления, которые разрешены `exposedParams`)
- `templateRef` (шаблон атрибутов ключа из `keyTemplates`)

Тем самым LLM не “изобретает” AE-иерархию заново — она выбирает из ограниченной библиотеки.

---

## matchNamePath и индексация

`matchNamePath` поддерживает индексацию вида:
- `ADBE Text Animator[2]`
- `ADBE Text Selector[3]`

Это важно, когда в одном preset’е будет несколько одинаковых групп (например: reveal + glow + bounce),
и нужно адресовать overrides именно во **второй** Animator.

Индексация обрабатывается в Python (на этапе сборки дерева), а JSX просто применяет готовый tree.

---

## Мини-пример слоя текста (composition.json)

```json
{
  "type": "text",
  "styleId": "main_subtitle",
  "content": "Привет, AE",
  "animId": "anim_reveal_opacity",
  "transformId": "tf_subtitle_base",
  "overrides": {
    "selector_start": {
      "keys": [
        { "time": 0.0, "value": 0, "templateRef": "tpl_linear_hold" },
        { "time": 0.6, "value": 100, "templateRef": "tpl_ease_explosive" }
      ]
    },
    "opacity": 100,
    "scale": [100, 100, 100]
  }
}
```
