# Styles config layout

This folder contains style libraries used by the AE/FFmpeg pipeline.

## Layout

```
config/styles/
  effects/
    effects_library.json
  footage/
    footage_presets.json
  project/
    project_settings_template.json
  text/
    text_styles.json
    text_fx_combos.json
```

## Notes
- JSON contents and schema stay unchanged; only file paths were reorganized.
- Code should load styles recursively from `config/styles/` (recommended).
