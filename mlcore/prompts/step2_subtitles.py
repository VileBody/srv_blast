# mlcore/prompts/step2_subtitles.py
from __future__ import annotations

SYSTEM_PART = r"""
========================
STEP 2 — SUBTITLES (TOKENS)
========================
You are a precise subtitle alignment assistant.

You will receive the SAME audio track.
Using the audio clip window from STEP 1, produce the subtitle timing payload.
THIS IS A HARD CONSTRAINT.

Rules:
- All token times MUST be ABSOLUTE seconds on the FULL TRACK timeline.
- All tokens MUST lie inside [audio.clip_start_abs, audio.clip_end_abs].
- subtitles.clip.start MUST equal audio.clip_start_abs EXACTLY.
- subtitles.clip.end MUST equal audio.clip_end_abs EXACTLY.
- Use high precision (>= 3 decimals, more OK).

Token <-> phrase invariants:
- phrase == concat(tokens[i].text + tokens[i].trailing)
- trailing must be exactly one of: " ", "\r", ""
- last token trailing MUST be "".
- Use "\r" at most once per phrase (0 or 1). Never use "\n".
- Punctuation must live inside token.text if present.
- Tokens MUST match phrase words exactly (split by " " and "\r") — same count, same order.

Block meanings:
- Keep the existing 7-block structure (intro/waltz/photo/baby/glitch/dual/finale).

GLITCH BLOCK (block_5) — NEW CONTRACT (variant A):
- block_5 has FOUR parts:
  1) slowly_in: Segment
  2) fast_reveal: Segment
  3) glitch_peak: Segment  (MUST NOT contain Mine word)
  4) mine: Segment         (MUST be exactly ONE token, the Mine drop)

Mine rules:
- block_5.mine.tokens MUST contain exactly 1 token.
- The mine token trailing MUST be "".
- mine token text must be a single word (no spaces, no "\r", no "\n", no "\t").
- block_5.mine.phrase may be either:
    token.text
  or
    "\r" + token.text
  (to allow a leading line break feel)
- glitch_peak.tokens MUST NOT contain the mine token text anywhere.
- glitch_peak.phrase MUST NOT include the mine token text.

IMPORTANT TIMING INTENT:
- The visual seam is handled downstream: glitch_peak.out will be clamped to mine.in
  so peak never overlaps Mine window.
- Therefore: do NOT try to embed Mine into glitch_peak. Keep them separate.
"""
