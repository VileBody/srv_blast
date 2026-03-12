from __future__ import annotations

from typing import Literal


SUBTITLES_MODE_LEGACY_BLOCKS = "legacy_blocks"
SUBTITLES_MODE_IMPULSE_2ND = "impulse_2nd"
SUBTITLES_MODE_SCENES_3RD = "scenes_3rd"

SUBTITLES_MODE_VALUES = (
    SUBTITLES_MODE_LEGACY_BLOCKS,
    SUBTITLES_MODE_IMPULSE_2ND,
    SUBTITLES_MODE_SCENES_3RD,
)

SubtitlesMode = Literal[
    "legacy_blocks",
    "impulse_2nd",
    "scenes_3rd",
]


def normalize_subtitles_mode(raw: str | None, *, default: str = SUBTITLES_MODE_LEGACY_BLOCKS) -> str:
    mode = str(raw or "").strip()
    if not mode:
        mode = str(default).strip()
    if mode not in SUBTITLES_MODE_VALUES:
        raise RuntimeError(f"Unknown subtitles_mode={mode!r}; allowed={SUBTITLES_MODE_VALUES}")
    return mode
