from __future__ import annotations

SYSTEM_PART = r"""
========================
STAGE 2A — SUBTITLES (IMPULSE 2ND FLOW)
========================
You receive:
- stage1 result:
  - audio clip window
  - transcript_words (word-level timings on full-track timeline)
  - optional lyrics_text and target_fragment

Task:
- Produce ONLY JSON matching Impulse2ndPayload:
  - clip: {start, end}
  - segments: [{text, in, out, type, word_timings[]}]

Hard constraints:
- clip.start MUST equal stage1.audio.clip_start_abs EXACTLY.
- clip.end MUST equal stage1.audio.clip_end_abs EXACTLY.
- Segment times are ABSOLUTE full-track seconds.
- Segment order MUST follow timeline.
- type is strictly one of: "long" | "short".
- out must be > in for every segment.
- No critical overlaps between adjacent segments.
- If word_timings are present:
  - each item has {word, start, end}
  - start/end must stay inside clip
  - end must be > start
  - order must follow timeline

Content constraints:
- Keep text lexical source from transcript_words.
- If target_fragment is provided, lexical corrections are allowed but timings stay timeline-consistent.
- Ignore backing vocals/adlibs that do not belong to the primary subtitle line.
- Keep segments concise and readable.

Output policy:
- Return strictly valid JSON for the schema.
- No markdown, no comments, no extra keys.
"""
