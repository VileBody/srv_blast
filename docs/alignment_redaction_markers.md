# Masked (redacted) words in local alignment

Users mask letters with asterisks — `К*р`, `х*й`, `бл**ь`, `с*г*рету`. The
audio still contains the fully pronounced word, so the hidden letters are real
acoustic evidence with no known grapheme. Before this contract the aligner
rejected them with
`ALIGNMENT_UNSUPPORTED_TEXT: reference word 'К*р' contains unsupported
character '*'`.

## Two representations per word

| Representation | Owner | Example |
|---|---|---|
| display token | `Stage1AsrPayload`, subtitles | `К*р` |
| alignment token | CTC target | `к` + wildcard + `р` |

The display token is never rewritten: what the user typed is what the subtitles
show. The alignment token exists only inside the aligner.

## Accepted markers

Only the asterisk family is accepted, and only these code points:

| Code point | Character | Name |
|---|---|---|
| U+002A | `*` | ASTERISK |
| U+204E | `⁎` | LOW ASTERISK |
| U+2217 | `∗` | ASTERISK OPERATOR |
| U+2731 | `✱` | HEAVY ASTERISK |
| U+FE61 | `﹡` | SMALL ASTERISK (NFKC-folds to U+002A) |
| U+FF0A | `＊` | FULLWIDTH ASTERISK (NFKC-folds to U+002A) |

Every other unsupported character keeps failing with
`ALIGNMENT_UNSUPPORTED_TEXT`. The marker list is an allow-list, not a filter for
arbitrary junk.

Markers are accepted anywhere in the word — at the start (`*уй`), at the end
(`бля*`), in the middle (`К*р`), and several times per word (`с*г*рету`).
Surrounding punctuation is still stripped (`«*уй!»` → `*уй`), which markers used
to fall victim to.

## Wildcard as a CTC target

A run of markers collapses into exactly **one** wildcard unit: `бл**ь` hides one
contiguous unknown audio region, and two identical adjacent CTC targets would
additionally require a blank frame between them for nothing.

The wildcard is a real CTC target token, not a deletion. It is backed by an
extra emission column appended to the model output:

```
wildcard(frame) = log Σ_{c ≠ blank} P(c | frame) + log(WILDCARD_NON_BLANK_WEIGHT)
```

* It scores "some non-blank grapheme is emitted here", which is exactly what is
  known about a masked letter.
* In silence and inter-word gaps the blank state is far cheaper, so the wildcard
  cannot park itself outside the spoken word.
* `WILDCARD_NON_BLANK_WEIGHT = 0.5` (`mlcore/alignment/core.py`) is the discount
  that keeps the **visible** letters intact: a known grapheme owning more than
  half of the non-blank mass in a frame always outbids the wildcard. Without it
  the wildcard wins every speech frame and squeezes the visible part of the word
  down to a single frame each (covered by
  `test_wildcard_does_not_compress_the_visible_part_of_the_word`).

Simply deleting the marker (`К*р` → `кр`) was rejected: CTC blank *can* absorb
the hidden audio between `к` and `р`, but the word's timing would then only span
the visible letters. A leading or trailing mask (`*уй`, `бля*`) would leave the
hidden phonemes outside the word entirely.

## Word timing

For ordinary tokens the reported span is trimmed to the posterior-supported
region around the token's peak. A wildcard has no single peak — it may cover
several hidden phonemes — so its **whole** Viterbi occupancy is its span.

A word's timing is the union of its token spans, wildcards included, so
`t_start … t_end` covers the masked audio too. Wildcards are excluded from the
word confidence average: they carry no grapheme evidence.

## Explicitly rejected

A word consisting only of markers (`***`) has no acoustic anchor at all: nothing
in it can be matched against the audio, and its timing would be pure guesswork.
It fails with a dedicated code:

```
ALIGNMENT_FULLY_REDACTED_WORD: reference word '***' is fully masked and has no
alignable letters; keep at least one visible letter in the word
```

HTTP status is 422 (client text problem). Previously such a token was silently
dropped by edge-punctuation stripping; the failure is now explicit and
observable.

Hidden letters are never reconstructed. If a specific masked spelling must map
to known graphemes, add it deliberately to
`config/alignment_pronunciations.json` — that is an operator decision, not a
runtime fallback.

## Diagnostics

`/align` returns a `redaction` block:

```json
"redaction": {
  "markers": ["*", "⁎", "∗", "✱", "﹡", "＊"],
  "wildcard_non_blank_weight": 0.5,
  "redacted_word_count": 1,
  "wildcard_token_count": 2,
  "words": [
    {
      "word_index": 3,
      "display_text": "с*г*рету",
      "alignment_text": "с*г*рету",
      "wildcard_count": 2
    }
  ]
}
```

Masked words also appear in the `pronunciation.words` diagnostics with strategy
`redacted_literal_cyrillic` / `redacted_espeak_en` /
`redacted_mixed_cyrillic_espeak`.

## Version

`ALIGNMENT_ALGORITHM_VERSION=local-ctc-viterbi-v11-dynamic-window-redaction-espeak-demucs-4.1.0`.
The worker verifies the alignment service identity field by field, so
`.env` / `docker-compose.yml` and the service image must be rolled out together.
