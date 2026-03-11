from __future__ import annotations

SYSTEM_PART = r"""
===========================================
STAGE 2A (IMPULSE MODE) — TAGGED SUBTITLES
===========================================
You are generating a simplified subtitle flow WITHOUT block structure.

Return ONLY valid JSON matching TaggedSubtitlesPayload:
{
  "clip_start_abs": number,
  "clip_end_abs": number,
  "subtitles": [
    {"text": "...", "tag": "long|short", "in": number, "out": number}
  ]
}

Input:
- fixed clip window [clip_start_abs..clip_end_abs]
- transcript_words with absolute timings inside clip window
- optional lyrics_text / target_fragment for lexical disambiguation only

Hard rules:
1) No blocks. Output only subtitles[] timeline.
2) Times are ABSOLUTE track seconds.
3) subtitles must be sorted by "in".
4) out > in for every item.
5) Keep timing deterministic:
   - "in" should align to the first word of the subtitle phrase.
   - "out" should normally align to the next subtitle "in".
   - for the last subtitle, "out" may extend slightly after the final word (<= +0.5s), but must stay <= clip_end_abs.
6) Use only two tags:
   - "short": accent word / short refrain hit.
   - "long": regular phrase line.
7) Avoid strobing:
   - do not produce subtitle durations below 0.3s.
8) Keep lexical content grounded in transcript_words order.
9) Normalize text:
   - lowercase only
   - remove punctuation , . ? ! ; : « » " ( ) / — –
   - keep apostrophe/defis only inside words
10) Preserve semantic flow and readability; avoid single-service noise tokens.
"""

