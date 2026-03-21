from __future__ import annotations

from core.clip_window import (
    CLIP_WINDOW_MAX_LABEL,
    CLIP_WINDOW_MIN_LABEL,
    CLIP_WINDOW_RANGE_S_LABEL,
)


SYSTEM_PART = f"""
========================
STAGE 1B — SCENARIO ONLY
========================
You receive:
- transcript_words from STAGE 1A (single source of truth for words/times).
- Optional USER_TARGET_FRAGMENT directive in user prompt.

Return JSON for Stage1ScenarioPayload:
1) audio window on full-track timeline:
   - audio.clip_start_abs
   - audio.clip_end_abs
   - audio.moment_of_interest_sec (optional)
2) draft_blocks over 7 blocks:
   - phrases list per block part
   - block_5 split into slowly_in / fast_reveal / glitch_peak / mine
3) Optional fragment_analytics:
   - REQUIRED only when USER_TARGET_FRAGMENT is provided and not empty
   - MUST explain how requested fragment relates to selected {CLIP_WINDOW_RANGE_S_LABEL} working window

Hard constraints:
- audio window duration must be >= {CLIP_WINDOW_MIN_LABEL}s.
- clip_end_abs > clip_start_abs.
- default branch (no USER_TARGET_FRAGMENT):
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
- Branching rule:
  - If USER_TARGET_FRAGMENT is absent/empty: run default logic (existing behavior).
  - If USER_TARGET_FRAGMENT is present/non-empty:
    - keep audio window at least {CLIP_WINDOW_MIN_LABEL}s,
    - audio window MAY exceed {CLIP_WINDOW_MAX_LABEL}s when needed to keep USER_TARGET_FRAGMENT fully covered,
    - maximize overlap between working window and USER_TARGET_FRAGMENT,
    - if requested fragment is shorter than {CLIP_WINDOW_MIN_LABEL}s: expand context around it,
    - if requested fragment is longer than {CLIP_WINDOW_MAX_LABEL}s: keep the full fragment (do NOT narrow/select subfragment),
    - treat USER_TARGET_FRAGMENT as lexical source of truth for wording:
      - you MAY correct ASR recognition mistakes in draft_blocks wording to match USER_TARGET_FRAGMENT,
      - keep order/timeline grounded in transcript_words timings (no invented timeline),
    - fragment_analytics.target_fragment MUST copy USER_TARGET_FRAGMENT wording exactly,
    - fill fragment_analytics with relation_to_target + chosen_action + start/end markers.
- Return valid JSON only, no markdown/comments.
"""
