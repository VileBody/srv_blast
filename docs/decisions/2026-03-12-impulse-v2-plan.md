# Design Plan: impulse v2 (no implementation in this change)

- Date: 2026-03-12
- Status: Proposed (design-only)
- Type: Architecture plan after rollback

## Goal

Re-introduce an experimental subtitle style path safely, without destabilizing the production pipeline and without changing public API contracts.

## Non-goals

- No runtime code changes in this plan document.
- No reintroduction of `text_preset` into `/send_audio_s3` at this stage.
- No hidden fallback logic.

## Constraints

- Keep repository policy: explicit failures over implicit fallback.
- Preserve runtime contract: `MODE` must be explicit (`dev` or `prod`).
- Preserve explicit Gemini model contract (`GEMINI_MODEL_STAGE1`, `GEMINI_MODEL_SUBTITLES`, `GEMINI_MODEL_FOOTAGE` required).

## Proposed v2 architecture

1. Isolated adapter boundary:
- Keep Stage2 canonical output as the single source for subtitle semantics.
- Add a separate text-style adapter module (builder-side) that transforms canonical text layers into a style-specific render representation.
- Adapter is invoked explicitly; unknown adapter id must fail fast.

2. Stable external interfaces:
- Keep `/send_audio_s3` schema unchanged (no style field in public request for now).
- Keep orchestrator queue payload shape unchanged for stable path.

3. Experiment gating:
- Enable v2 only through explicit internal runtime configuration in controlled environments.
- If gating is enabled but required adapter artifacts are missing or invalid, fail with clear runtime error.

4. Template isolation:
- Move style-specific AE logic into dedicated, bounded template fragments instead of mixing into the default text flow.
- Keep classic template path untouched and independently testable.

## Implementation phases (future)

Phase A: Adapter skeleton and contract tests
- Define adapter interface and strict validation.
- Add unit tests for adapter input/output invariants.

Phase B: Mapping parity
- Implement style mapping from canonical subtitle layers.
- Add deterministic tests for timing, cleanup, and animation metadata.

Phase C: Controlled integration
- Integrate adapter behind explicit gate in builder/orchestrator.
- Add end-to-end tests for gate off/on behavior.

Phase D: Readiness review
- Compare artifact quality and regressions against classic baseline.
- Decide go/no-go for broader rollout.

## Acceptance criteria for future implementation

- Classic path behavior unchanged when experiment gate is off.
- No public API changes required for stable operation.
- No fallback switching at runtime.
- Clear operator-visible errors for invalid style configuration.
- Test coverage includes unit + integration + template smoke checks.

## Risks and mitigations

- Risk: style logic leaks into core path again.
- Mitigation: enforce adapter boundary and separate tests.

- Risk: hidden contract drift between canonical subtitles and adapter.
- Mitigation: schema-validated adapter inputs with strict invariants.

- Risk: operational confusion in prod.
- Mitigation: explicit gate defaults to off; production path remains classic-only.

