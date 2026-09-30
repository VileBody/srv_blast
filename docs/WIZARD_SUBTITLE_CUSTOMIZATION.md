# Wizard subtitle customization: implementation map

## Current milestone (2026-09-30)

Контракт провязан от визарда до сборки. Настройки у каждого стиля свои: вкладки над блоком «Настройки текста». Ограничения одного стиля (скрипт основным у Jakson, фиксированный шрифт Brat) не блокируют другие. Числа живут только на рендере, фронт и бэк сайта сверяются с тем же движком (`app/subtitle_font_layout.py`). Невозможная комбинация отклоняется на отправке, а не падает на ноде.

```
wizardStore.subtitles.textByStyle[<стиль>]         (фронт; вкладка = subtitles.textTab)
  → render_job variation.subtitle.text             (web_app/backend/app/render_job.py)
  → production_backend._request_payload:           subtitle_text.resolve(...) — приведение к стилю + прогон движка
       subtitle_text_style (snake_case) + accent_color_hex
  → SendAudioS3Request.subtitle_text_style         (строгая схема, extra=forbid)
  → env SUBTITLE_TEXT_STYLE_JSON / SUBTITLES_FOCUS_HEX (services/orchestrator/tasks.py)
  → app/subtitle_text_style.py:                    jakson/impulse/tape — applied_text_style() вокруг build_text_layers;
                                                   trendy/brat — jsx_style_config() → $.global.__BLAST_STYLE
```

- Всё по умолчанию (шрифт «стандартный для стиля») → `subtitle_text_style` не отправляется, раскладка прод.
- **Акцентный цвет** (второй и последний цвет кадра) берётся из настроек текста стиля. Он красит фокус-слово или пару, ударное слово TYPE_4 и фигуру F2. Скрытый `final.accentColor` (фиолетовый по умолчанию, в UI не менялся) больше не применяется: без выбора остаются прод-цвета стиля.
- **Brat:** шрифт, пара и высота не применяются, шрифт зафиксирован. UI это показывает. Курсив фокус-слова (`focusStyle`) есть только у brat.
- **Jakson:** рукописный шрифт основным не принимается, пока не сделан строчный режим. Акцентом можно.
- Стандартные шрифты impulse / tape / trendy (Point Light, Montserrat Bold Italic, Montserrat Bold) лежат в каталоге с `"hidden": true`: в списке их нет, но пары для «стандартного для стиля» считаются от них (`defaultAccents`).
- Тур подсказок шага «Текст»: подгонка слов → выбор стилей → настройки текста (`subtitles-text`, показывается только при выбранном стиле; прежний шаг `subtitles-color` убран).
- Каталог для фронта — `GET /api/wizard/subtitle-fonts`: роли, категории, засечки, `excludedStyles`, допустимые акценты. Движок и JSON каталога вшиты в образ сайта (`Dockerfile.production`, деплой сайта срабатывает и на их изменение).
- **Превью (CSS)** — те же пресеты (`lib/subtitleText.ts`). Шрифт берётся через `local()`, то есть только если он установлен у человека; иначе подставляется близкий по жанру. Пиксельной точности с AE не обещаем.

**До включения на проде:** все шрифты каталога должны стоять на рендер-ноде. trendy/brat проверяют шрифт в AE (`strictFont`) и падают явно. Python-стили (jakson/impulse/tape) при отсутствии шрифта получат подмену от самого AE. Проверка: `scripts/subtitle_font_lab/list_ae_fonts.jsx` на ноде.

## State contract

`stageData.subtitles.textByStyle` — ключ = имя стиля из пула (`"Jakson"`):

```json
{
  "Jakson": {
    "font": "CormorantSC-Medium", "accentFont": "PrincessDiana",
    "size": "large", "height": "tall", "position": "center", "shadow": "soft",
    "accentColor": "#FF5FA8", "focusStyle": null
  },
  "Brat": { "font": null, "accentFont": null, "size": "medium", "height": "normal",
            "position": "left", "shadow": "strong", "accentColor": null, "focusStyle": "italic" }
}
```

Стор v9: прежний общий `text` (v6–v8, только CSS-превью) не переносится, стили стартуют со стандартных настроек.

**Смотр в AE 2026-09-29 (jakson):** обводки нет вовсе (нигде не выглядит хорошо, убрана из стора v7 и UI); `size` — large (= авто-максимум, прод) / medium −10% / small −20%, крупнее авто нельзя; `height` — вертикальное растяжение букв compact 80% / normal / tall 130%, **только для шрифтов с засечками** (каталог `"serif": true`; на гротесках вроде Point выглядит плохо — фронт прячет контрол, рендер отклоняет), ширина букв всегда 100% (растяжения по ширине нет и не планируется); `position` — center / left / right, `down` только для 16:9 (рендер отклоняет его для вертикали, тумблера в 9:16 нет), «выше» нет; цвета — не больше двух в кадре: основной + один акцентный, который красит и акцентное слово пары (TYPE_2), и ударное слово (TYPE_4). Числа пресетов живут только в `app/subtitle_font_layout.py` (`SIZE_/HEIGHT_/POSITION_/SHADOW_PRESETS`); шрифты, роли и правила пар — `config/styles/subtitle_font_catalog.json`, замеры — `subtitle_font_metrics.json`, ручные поправки — `subtitle_font_tuning.json`. Лабы: `scripts/subtitle_font_lab/`.

**Смотр в AE 2026-09-30 (trendy, brat).** Оба стиля — самостоятельные JSX (`5th_template/*.jsx`); значения движка (`trendy_layout(...)` / `brat_layout(...)` → `.jsx_config()`) вливаются в их `CONFIG` через `$.global.__BLAST_STYLE` (`build_jsx_subtitles_overlay(style_config=...)`). Неизвестный ключ — ошибка скрипта; глобал пишется всегда (`null` без настроек), чтобы стиль прошлой джобы не протёк в переиспользуемой сессии AE. Без настроек оба скрипта ведут себя как прод.
- **trendy:** шрифты каталога + пары. Фокус-слово набирается акцентным шрифтом, растянутым ×4 или в естественных пропорциях. Работают размер, высота (только с засечками, умножает прод-растяжение ×4), позиция (left/right — край букв на поле), тень (S_DropShadow: none/soft = прод/strong) и цвет фокус-слов. Скрипты идут строчными без трекинга. Из гротесков в trendy только Point, Inter и Helvetica Neue: под растяжением ×4 остальные неотличимы (каталог `excluded_styles: ["trendy"]`).
- **brat:** шрифт зафиксирован (Arial Narrow), пар нет, высоты нет. Работают размер (ширина бокса full-justify), позиция (бокс к полю; `down` только 16:9), тень, цвет фокус-слов и курсив фокус-слова (Arial Narrow Italic / Bold Italic / наклон). Тень — ADBE Drop Shadow под каждым словом внутри его прекомпа, после Minimax и Gaussian Blur. До PR #203 тень стояла первым эффектом, и они её раздували и мылили. Плашка под строкой выключена: выглядела грязным пятном. Прекомп слова один на весь ролик, чётной высоты. Замер в лабе: базовые линии слов строки совпадают и до правки, и после.

These are named presets rather than renderer-native floats so the UI stays stable if the AE mappings need calibration. Keep old persisted drafts valid by merging text settings with defaults during migration and session restore.

## Preview decision

- Now: CSS-only caption in the work-zone frame, updating directly from React state. The frame contains no PNG/photo or decorative overlay; the text stays centered on both axes in the default center state, while left/right only change horizontal placement and alignment. Best fit for deterministic controls (font fallback, size, vertical scale, left/center/right, stroke, shadow, fill) and works without adding a renderer dependency.
- Later: Remotion Player for motion examples and effects that CSS cannot represent. Its official Player API accepts a React composition and updates it when input props change ([Remotion Player](https://www.remotion.dev/docs/player)); that avoids an asset for every cross-product. Build one representative composition and pass the current settings as props. It still will not reproduce proprietary AE plug-ins by itself, so plug-in parity needs a deliberate web approximation or an AE-rendered example.
- Avoid: pre-rendering every combination. It grows multiplicatively with each control and produces stale assets as presets change.

For calibration, render a small agreed set on the Windows node (default, tall, large, left/right, outline, shadow) and tune the CSS ratios against the same words, 9:16 frame, and AE comp settings. Do not claim pixel parity before that comparison.

## Deferred plugin-effects follow-up

Choose up to five subtitle effects to expose through plugins after the core text settings are calibrated. Validate them with the user present at the Windows render node; browser/CSS preview alone cannot verify AE plug-in availability or output. Keep this as a later milestone, not part of the current text-controls pass.

## QA checklist for completing the AE hookup

- Old drafts load with default text settings; new draft values survive refresh and server session restore.
- Horizontal position changes paragraph justification and safe-area inset together.
- `height` maps only to AE vertical scale; horizontal scale stays 100%.
- `size` respects the renderer's line/word fitting and min-size rules.
- Color remains an explicit fill override and continues to work with focus/accent words.
- Each supported subtitle mode consumes all declared settings; unsupported modes fail visibly instead of silently dropping them.
- CSS preview and AE frames are compared at portrait output size, including long words and two-line captions.
