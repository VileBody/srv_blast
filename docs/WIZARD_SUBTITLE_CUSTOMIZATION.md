# Wizard subtitle customization: implementation map

## Current milestone

The wizard now stores the text controls alongside the selected subtitle styles and exposes them at the end of the subtitle stage. The work-zone example is a live CSS composition driven by the same saved values; it does not pre-render a grid of combinations and it does not claim to be an AE render. The subtitle fill color is already sent on the production path; render-job construction now correctly prefers `subtitles.color` over the legacy final-stage default (which previously masked non-white selections). Font, size, vertical scale, position, shadow, outline width, and outline color are preview/state only until the render contract below is implemented and validated on the Windows node.

## State contract

`stageData.subtitles` keeps the style pool and color and adds `text`:

```json
{
  "font": "Point-SemiBold",
  "size": "medium",
  "height": "normal",
  "shadow": "soft",
  "outline": "thin",
  "outlineColor": "#000000",
  "position": "center"
}
```

These are named presets rather than renderer-native floats so the UI stays stable if the AE mappings need calibration. Keep old persisted drafts valid by merging text settings with defaults during migration and session restore.

## AE / production insertion points

1. `web_app/frontend/src/stores/wizardStore.ts`: persisted subtitle settings and `stageData()` are the source of truth.
2. `web_app/backend/app/render_job.py`: add the resolved text settings to each variation's `subtitle` object (beside `style`, `color`, and `timingSource`). Resolve them once per variation, not from browser state later.
3. `web_app/backend/app/production_backend.py`: `_request_payload()` is the web-to-orchestrator boundary; map the resolved object into explicit request fields.
4. `services/orchestrator/schemas.py`: `SendAudioS3Request` is a strict Pydantic contract, so new fields must be declared here. Do not rely on arbitrary JSON surviving validation.
5. `services/orchestrator/tasks.py`: copy the validated settings into a single deterministic `SUBTITLE_TEXT_STYLE_JSON` build-env value, following the existing explicit color override pattern (`SUBTITLES_FORCE_FILL_HEX`).
6. Renderer mode dispatch is selected in `services/orchestrator/tasks.py` / `mlcore/gemini_orchestrator.py`; the actual AE adapters are mode-specific. The reusable control points found in this repo are `3rd_template/script_jakson.py` (`TextLayerFactory.text_layer`, `text_base_dict`, and `base_transforms`), `5th_template/trendy_subtitles.jsx` (`CONFIG.font`, `fontSize`, `verticalScale`, `yOffsetRatio`, stroke and shadow), and `5th_template/brat_subtitles.jsx` (BRAT-specific config and generated layers). The second- and fourth-template modes also need an explicit adapter or must reject unsupported text controls; they should not silently ignore them.

Recommended adapter order: position/alignment and vertical scale first (shared layer transforms / paragraph justification), then font and size, then stroke and shadow. Preserve style-specific motion, focus-word styling, and fill overrides. Apply settings as a post-configuration layer so each subtitle style keeps its distinctive default unless the user explicitly changes a shared control. Keep width scale fixed at 100%.

The initial font choices include names found in checked-in renderer configs (`Point-SemiBold`, `Point-ExtraBold`, `Montserrat-Bold`) plus browser preview candidates (`Arial`, `Impact`, `Georgia`, `Trebuchet MS`, `Courier New`). The latter are visual examples only; they are not verified as installed on the render node or supported by AE. Before wiring the setting, verify exact PostScript names on the node and map only confirmed fonts.

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
