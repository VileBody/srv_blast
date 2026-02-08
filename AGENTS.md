# Repository Runtime Rules

## No Fallback Policy
- Do not add implicit fallbacks for models, URLs, or pipeline branches.
- Prefer explicit failure with clear error messages over auto-recovery.
- Keep behavior deterministic and visible to the operator.

## Runtime Mode Contract
- `MODE` must be explicitly set in `.env` to one of:
- `dev`
- `prod`

### `MODE=dev`
- Local development flow.
- Use local media paths from `footage/` to generate configs/JSX.
- Do not dispatch to Windows render node from local runner.

### `MODE=prod`
- Queue/orchestrator flow.
- Use remote media URLs/locators for dispatch.
- Windows render dispatch is enabled.

## Gemini Model Contract (explicit only)
- `GEMINI_MODEL_STAGE1` is required.
- `GEMINI_MODEL_SUBTITLES` is required.
- `GEMINI_MODEL_FOOTAGE` is required.
- No runtime model fallback switching.
