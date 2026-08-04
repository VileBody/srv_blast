"""Redaction (masked profanity) support for the local CTC aligner.

Users routinely mask letters with asterisks (``К*р``, ``х*й``, ``бл**ь``,
``с*г*рету``). The audio still contains the fully pronounced word, so the
hidden letters are real acoustic evidence that simply has no known grapheme.

The alignment pipeline therefore splits every reference word into two
representations:

* the **display token** — the exact spelling the user sent, which is what ends
  up in ``Stage1AsrPayload`` and in the subtitles;
* the **alignment token** — the CTC target, where every run of redaction
  markers becomes a single wildcard unit (:data:`ALIGNMENT_WILDCARD`).

The wildcard is a real CTC target token backed by a garbage/"star" emission
column (see ``core.wildcard_emission_column``), not a deletion: it owns the
frames of the hidden phonemes so the display word's timing covers the whole
spoken word.

Nothing here guesses the hidden letters. A word that consists only of markers
carries no acoustic anchor at all and is rejected explicitly.
"""

from __future__ import annotations

import re

from .contracts import (
    AlignmentFailure,
    ERROR_FULLY_REDACTED_WORD,
    ERROR_UNSUPPORTED_TEXT,
)


# Canonical marker used inside alignment text, CTC unit splitting and
# diagnostics. Chosen to equal U+002A so diagnostics stay readable.
ALIGNMENT_WILDCARD = "*"

# Explicit allow-list of accepted redaction markers. Only the asterisk family
# is accepted: these are the characters keyboards and messengers actually
# produce when a user masks a letter. Every other unsupported character keeps
# failing with ALIGNMENT_UNSUPPORTED_TEXT.
REDACTION_MARKERS: frozenset[str] = frozenset(
    {
        "*",  # ASTERISK
        "⁎",  # LOW ASTERISK
        "∗",  # ASTERISK OPERATOR
        "✱",  # HEAVY ASTERISK
        "﹡",  # SMALL ASTERISK (NFKC-folds to U+002A)
        "＊",  # FULLWIDTH ASTERISK (NFKC-folds to U+002A)
    }
)

_MARKER_CLASS = "".join(sorted(re.escape(marker) for marker in REDACTION_MARKERS))

# Edge punctuation is stripped from reference words, but redaction markers are
# part of the word: ``«*уй!»`` must keep its leading mask and lose the quotes.
EDGE_PUNCTUATION_RE = re.compile(
    rf"^[^\w{_MARKER_CLASS}]+|[^\w{_MARKER_CLASS}]+$",
    flags=re.UNICODE,
)

UNIT_LITERAL = "literal"
UNIT_WILDCARD = "wildcard"


def is_redaction_marker(character: str) -> bool:
    return str(character) in REDACTION_MARKERS


def has_redaction(word: str) -> bool:
    return any(character in REDACTION_MARKERS for character in str(word))


def strip_edge_punctuation(raw: str) -> str:
    """Drop surrounding punctuation while keeping redaction markers."""
    return EDGE_PUNCTUATION_RE.sub("", str(raw)).strip()


def count_wildcards(alignment_text: str) -> int:
    """Number of wildcard units (runs of markers collapse into one unit)."""
    return sum(
        1
        for index, character in enumerate(str(alignment_text))
        if character == ALIGNMENT_WILDCARD
        and (index == 0 or str(alignment_text)[index - 1] != ALIGNMENT_WILDCARD)
    )


def alignment_units(alignment_text: str, *, display_text: str = "") -> list[tuple[str, str]]:
    """Split alignment text into ordered literal/wildcard CTC units.

    Consecutive markers collapse into a single wildcard unit: ``бл**ь`` hides
    one contiguous unknown audio region, and two identical adjacent CTC targets
    would additionally require a blank frame between them for no reason.
    """
    text = str(alignment_text)
    reported = str(display_text or alignment_text)
    units: list[tuple[str, str]] = []
    literal: list[str] = []
    for character in text:
        if character == ALIGNMENT_WILDCARD:
            if literal:
                units.append((UNIT_LITERAL, "".join(literal)))
                literal = []
            if not units or units[-1][0] != UNIT_WILDCARD:
                units.append((UNIT_WILDCARD, ALIGNMENT_WILDCARD))
            continue
        if character in REDACTION_MARKERS:
            raise AlignmentFailure(
                ERROR_UNSUPPORTED_TEXT,
                f"alignment text {reported!r} contains a non-canonical redaction "
                f"marker {character!r}",
            )
        literal.append(character)
    if literal:
        units.append((UNIT_LITERAL, "".join(literal)))

    if not units:
        raise AlignmentFailure(
            ERROR_UNSUPPORTED_TEXT,
            f"reference word {reported!r} has no alignable pronunciation",
        )
    if all(kind == UNIT_WILDCARD for kind, _ in units):
        raise AlignmentFailure(
            ERROR_FULLY_REDACTED_WORD,
            f"reference word {reported!r} is fully masked and has no alignable "
            "letters; keep at least one visible letter in the word",
        )
    return units
