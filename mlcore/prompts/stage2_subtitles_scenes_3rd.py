from __future__ import annotations

SYSTEM_PART = r"""
========================
STAGE 2A — SUBTITLES (SCENES 3RD FLOW)
========================
You receive:
- stage1 result:
  - audio clip window
  - transcript_words (word-level timings on full-track timeline)
  - optional lyrics_text and target_fragment

Task:
- Produce ONLY JSON matching Scenes3rdPayload:
  - clip: {start, end}
  - scenes: [{id, type, words, start, end, lines, focus_word, focus_style, word_timings[]}]

Hard constraints:
- clip.start MUST equal stage1.audio.clip_start_abs EXACTLY.
- clip.end MUST equal stage1.audio.clip_end_abs EXACTLY.
- Scene times are ABSOLUTE full-track seconds.
- Scenes must be timeline-ordered and non-overlapping.
- type is strictly one of: TYPE_1..TYPE_6.
- end must be > start for every scene.
- words must be non-empty.
- If word_timings are present, every item must keep valid start/end and stay inside clip.

Scene structure:
- lines should represent intended line groups for rendering.
- TYPE_4 must stay compact (1–2 key words, single line).
- Use focus_word/focus_style only when semantically justified.

Content constraints:
- Keep lexical source aligned with transcript_words.
- If target_fragment is provided, lexical corrections are allowed but timeline order must remain stable.
- Ignore backing vocals/adlibs that are not part of the primary subtitle flow.

Output policy:
- Return strictly valid JSON for the schema.
- No markdown, no comments, no extra keys.
"""
