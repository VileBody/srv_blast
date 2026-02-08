from __future__ import annotations

SYSTEM_PART = r"""
========================
STAGE 1B — SCENARIO ONLY
========================
You receive:
- transcript_words from STAGE 1A (single source of truth for words/times).

Return JSON for Stage1ScenarioPayload:
1) audio window on full-track timeline:
   - audio.clip_start_abs
   - audio.clip_end_abs
   - audio.moment_of_interest_sec (optional)
2) draft_blocks over 7 blocks:
   - phrases list per block part
   - block_5 split into slowly_in / fast_reveal / glitch_peak / mine

Hard constraints:
- audio window duration must be 13..18 sec.
- clip_end_abs > clip_start_abs.
- draft phrase words MUST be copied from transcript_words (no paraphrase, no invented words).
- keep phrase order consistent with transcript order.
- target arc inside the selected window:
  - blocks 1..5 = development,
  - block 6 = fixation/landing,
  - block 7 = exit.
- keep block load balanced: blocks should be roughly comparable by text amount
  (target around <= 6 words per segment; hard cap <= 8 words).
- block_5.mine MUST be exactly ONE word (single token) copied from transcript_words.
- block_5.glitch_peak should be the "big" phrase of block_5 (crescendos):
  - target 4..8 words,
  - should be longer than mine and usually longer than slowly_in,
  - do NOT put the mine word inside glitch_peak.
- avoid dangling fragments:
  - do not split phrases into unnatural leftovers,
  - if splitting a sentence is needed, split at natural clause boundaries (comma/pause/conjunction),
  - no orphan function words at segment edges.
- Return valid JSON only, no markdown/comments.
"""
