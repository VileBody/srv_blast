from __future__ import annotations

SYSTEM_PART = r"""
========================
STAGE 2A — SUBTITLES ONLY
========================
You receive:
- stage1 result (audio window + transcript words + draft blocks)
- the same audio track

Task:
- Produce ONLY subtitles payload matching BlocksTokensPayload schema.

Hard constraints:
- Token times MUST be ABSOLUTE full-track seconds.
- All tokens MUST be inside [clip.start, clip.end].
- clip.start == stage1.audio.clip_start_abs.
- clip.end == stage1.audio.clip_end_abs.
- Return plain words with timings only:
  no punctuation in token.text, no explicit "\r" layout decisions.
- trailing will be normalized downstream (space for non-last, empty for last is preferred).
- Keep 7-block structure and block_5 split with required mine semantics.
"""
