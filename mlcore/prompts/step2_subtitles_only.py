from __future__ import annotations

PROMPT_VERSION = "v1"

SYSTEM_PART = r"""
========================
STAGE 2A — SUBTITLES (TOKENS ALIGNMENT)
========================
You receive:
- stage1 result:
  - audio clip window
  - draft_blocks (scenario phrases per segment)
  - transcript_words (word-level ASR with ABSOLUTE times)
  - optional lyrics_text (plain lyrics string; use only as disambiguation context)
  - optional target_fragment (user-provided wording source for lexical corrections)

Task:
- Produce ONLY subtitles payload matching BlocksTokensPayload schema.

Hard constraints:
- Token timing source is stage1.transcript_words.
  - For every output token: copy t_start + t_end EXACTLY from stage1.transcript_words.
  - Do NOT invent timings.
  - Keep token order aligned to transcript timeline.
- token.text rules:
  - Default branch (target_fragment empty): copy token.text from stage1.transcript_words.
  - target_fragment branch (target_fragment non-empty):
    - you MAY correct recognition mistakes in token.text using target_fragment/lyrics_text as lexical source of truth,
    - keep timing/order from transcript_words,
    - do NOT invent extra words outside the selected clip context.
- All token times are ABSOLUTE seconds on full-track timeline.
- clip.start MUST equal stage1.audio.clip_start_abs EXACTLY.
- clip.end MUST equal stage1.audio.clip_end_abs EXACTLY.
- Keep 7-block structure and block_5 split:
  - slowly_in / fast_reveal / glitch_peak / mine
- phrase fields:
  - copy phrase text from stage1.draft_blocks for the corresponding segment (join phrase lists with single spaces).
  - phrase is for readability; actual layout (\r) and trailing will be applied deterministically downstream.
- trailing:
  - you may output only " " or "" (do NOT use "\r" or "\n")
  - last token in each segment MUST have trailing ""
- No re-use / no overlap:
  - tokens across different segments MUST NOT overlap in time and MUST NOT be re-used.
  - segments must follow the transcript order (timeline order).
- block_5.mine:
  - MUST be exactly ONE token.
  - MUST NOT overlap in time with any block_5.glitch_peak token.
- Keep segments concise (best effort):
  - target <= 6 words,
  - hard cap <= 8 words.
"""
