from __future__ import annotations

from typing import Literal


SUBTITLES_MODE_LEGACY_BLOCKS = "legacy_blocks"
SUBTITLES_MODE_IMPULSE_2ND = "impulse_2nd"
SUBTITLES_MODE_SCENES_3RD = "scenes_3rd"
SUBTITLES_MODE_SCENES_3RD_SINGLE_STEP = "scenes_3rd_single_step"
SUBTITLES_MODE_TEMPLATE_4TH = "template_4th"
# 5th-template JSX subtitle generators (word-timings driven, self-contained AE
# scripts injected over the main comp; see 5th_template/). "trendy" = one word
# centered; "brat" = box-text blocks + BPM blinker.
SUBTITLES_MODE_TRENDY_5TH = "trendy_5th"
SUBTITLES_MODE_BRAT_5TH = "brat_5th"

# Modes whose subtitles are produced by an injected JSX generator from raw
# word-timings (NOT the Python text_layers pipeline). In these modes the builder
# skips the normal subtitle text_layers and inlines the chosen 5th_template JSX.
SUBTITLES_MODE_JSX_5TH = frozenset({
    SUBTITLES_MODE_TRENDY_5TH,
    SUBTITLES_MODE_BRAT_5TH,
})

SUBTITLES_MODE_VALUES = (
    SUBTITLES_MODE_LEGACY_BLOCKS,
    SUBTITLES_MODE_IMPULSE_2ND,
    SUBTITLES_MODE_SCENES_3RD,
    SUBTITLES_MODE_SCENES_3RD_SINGLE_STEP,
    SUBTITLES_MODE_TEMPLATE_4TH,
    SUBTITLES_MODE_TRENDY_5TH,
    SUBTITLES_MODE_BRAT_5TH,
)

SubtitlesMode = Literal[
    "legacy_blocks",
    "impulse_2nd",
    "scenes_3rd",
    "scenes_3rd_single_step",
    "template_4th",
    "trendy_5th",
    "brat_5th",
]


def normalize_subtitles_mode(raw: str | None, *, default: str = SUBTITLES_MODE_LEGACY_BLOCKS) -> str:
    mode = str(raw or "").strip()
    if not mode:
        mode = str(default).strip()
    if mode not in SUBTITLES_MODE_VALUES:
        raise RuntimeError(f"Unknown subtitles_mode={mode!r}; allowed={SUBTITLES_MODE_VALUES}")
    return mode
