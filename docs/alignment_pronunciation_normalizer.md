# Alignment pronunciation normalizer

The local alignment service uses a Russian grapheme CTC model. Before CTC
target construction, every reference word is converted into an alignable
Russian pronunciation while its original spelling remains unchanged in
`Stage1AsrPayload`.

## Runtime contract

- `ALIGNMENT_PRONUNCIATION_MODE=espeak_en_to_ru` is required.
- eSpeak NG runs locally and never makes network requests.
- The Docker image pins both Debian bookworm and the eSpeak package version.
- `ALIGNMENT_ESPEAK_EXPECTED_VERSION` must match the loaded engine.
- Missing binaries, version mismatches, invalid override files, timeouts, and
  unknown IPA symbols fail explicitly.

Normalization is deterministic:

1. Whole-word overrides from `config/alignment_pronunciations.json`.
2. Cyrillic runs remain unchanged.
3. Latin and numeric runs are phonemized by the pinned `en-us` eSpeak voice.
4. IPA is converted through the checked IPA-to-Russian map.
5. The resulting Russian graphemes are validated against the CTC vocabulary.

This is the defined normalization pipeline, not a model or service fallback.

## Overrides

Use overrides for brands, artist names, slang, and intentionally non-English
spellings:

```json
{
  "schema_version": 1,
  "english_to_russian": {
    "alyx": "аликс",
    "iphone": "айфон"
  }
}
```

Keys are matched with Unicode normalization and case folding. Values must be
single Cyrillic words because one display word must remain one aligned word.
Changing the file requires rebuilding the alignment image.

## Diagnostics

Successful alignment responses include:

- pronunciation mode and actual eSpeak version;
- count of converted words;
- original display text;
- internal CTC pronunciation;
- normalization strategy;
- eSpeak IPA for generated pronunciations.

`literal_cyrillic` words are omitted from the per-word diagnostic list to keep
responses compact.

