# Stage 2 subtitles: deterministic contract

Stage 2 subtitle layout is generated only from `Stage1PlanPayload` word timings.
It does not call Gemini, OpenRouter, or any other model. Identical Stage 1 JSON and
`subtitles_mode` must produce byte-equivalent model dumps.

## Common input rules

- Words are ordered by `(t_start, t_end)` and clipped to the Stage 1 audio window.
- Output timing always comes from ASR; no word timing is synthesized.
- Empty transcripts fail explicitly.
- Punctuation is removed only for modes whose former prompt required it. Internal
  apostrophes, hyphens, `#`, and `*` are preserved.
- Bracket-only stage directions are excluded from 2nd/3rd/4th layouts.

## `legacy_blocks`

- Keeps the fixed 13-segment topology, including the four Block 5 segments.
- Every real ASR token is used exactly once and in timeline order.
- Segment sizes follow Stage 1 `draft_blocks` phrase word counts, bounded to 1..8.
- `block_5.mine` is always exactly one word and never shares a timed token with
  `glitch_peak`.
- Fewer than 13 or more than 97 spoken words fail explicitly. A single canonical
  no-speech marker is the only permitted duplicated-token synthetic case.

## `impulse_2nd`

- Long groups are at most 4 words and 18 characters; a pause of at least 0.4s is
  always a boundary.
- Emphasis threshold is `max(0.4s, mean duration, 1.25 * median duration)`.
- A timed content word may become `short` only when it meets the threshold and
  has at least 0.4s of display hold.
- A final emphasis word is split from a preceding `long` only when that `long`
  retains at least 0.6s.
- Non-refrain shorts have a one-segment cooldown. Three or more immediately
  repeated copies of one word are all `short` refrains.
- Layer `out` is the next layer's `in`; the final layer ends 0.5s after its last
  word, preserving the template contract.

## `scenes_3rd` and `scenes_3rd_single_step`

- Both modes use the same deterministic layout; single-step no longer attaches
  audio or invokes a model.
- Scenes contain at most 5 words / 27 characters. A pause of at least 0.4s is a
  hard boundary. Two-line splits minimize overflow above 13 characters per line.
- `TYPE_4`: a 1-2 word hook repeated at least three times, or the first isolated
  1-2 word phrase lasting at least 0.44s.
- `TYPE_3`: 3-4 words with a final gap of at least 0.25s and a 3-8 character last
  word, fitting a single line.
- `TYPE_5`: 4-5 words, duration over 3s, with every internal gap below 0.2s.
- `TYPE_2`: 4-5 words over 1.5..3.5s with one content word crossing the common
  duration-emphasis threshold. That word receives italic focus.
- `TYPE_6`: 3-5 words over 1.5..4s, all gaps below 0.25s, and a valid two-line
  split.
- Everything else is `TYPE_1`. Four identical types in a row are prevented by a
  deterministic `TYPE_1` reset.

## `template_4th`

- Groups contain at most 4 words / 25 characters and respect 0.4s hard pauses.
- Every content word longer than the global mean word duration is focused.
- If a pair of consecutive subtitles has no such word, its longest timed content
  word is focused. Thus every two subtitles contain at least one red word.
- Subtitle `in`/`out` equal the first word start / last word end without padding.

## `trendy_5th` and `brat_5th`

These modes do not have a Stage 2 layout stage. They keep the existing JSX
passthrough that consumes raw Stage 1 word timings directly.
