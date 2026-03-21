# Decision: Rollback failed impulse attempt

- Date: 2026-03-12
- Status: Accepted
- Type: Stabilization / rollback

## Context

Between 2026-03-11 and 2026-03-12, the branch introduced an experimental subtitle path inspired by local `2nd_template/` assets:

- `8b7870a` (`classic|impulse` preset switch + API/UI surface)
- `84ea9d1` (impulse-specific text time shift behavior)
- `6a1e4d9` (tagged subtitles stage + impulse runtime/template flow)

The `2nd_template/` folder itself remains untracked and was never part of the repository contract.

The experiment increased runtime coupling across orchestrator, postprocess, AE template, and bot UX. This made the stable path harder to reason about and introduced a second subtitle contract (`tagged_subtitles`) that is not required for current production goals.

## Decision

We rollback the experiment using non-destructive history:

- `6c80002` Revert `6a1e4d9`
- `f3f469e` Revert `84ea9d1`
- `c7f64a6` Revert `8b7870a`

Rollback scope:

- Remove runtime `impulse`/`tagged_subtitles` flow.
- Remove `text_preset` from external `/send_audio_s3` payload surface and tg-bot state machine.
- Restore single stable subtitle path (`classic`) in runtime/template behavior.

Intentionally preserved:

- Stage1 strict retry logic and fragment workflow.
- Stage2 timing and footage selection improvements (`8f59055`, `7757cc0` and related fixes).

## Why this approach

- Keeps history auditable and explicit (no rewrite/reset).
- Makes failure visible to operators and future maintainers.
- Restores deterministic, single-path behavior aligned with repository "no fallback" policy.

## Consequences

Positive:

- Reduced operational complexity and lower regression surface.
- API/queue contracts return to one canonical subtitle path.
- Easier debugging and on-call behavior.

Tradeoff:

- The impulse experiment is not available in runtime until a redesigned v2 is implemented.

## Follow-up

Design work continues in:

- `docs/decisions/2026-03-12-impulse-v2-plan.md`

