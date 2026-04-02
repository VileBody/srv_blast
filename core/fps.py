"""Single source of truth for the AE composition frame rate (NTSC 23.976)."""
from __future__ import annotations

from typing import Final

# After Effects comp.frameRate (NTSC Film).
# Do NOT round to 23.976 — use the exact AE value everywhere.
COMP_FPS: Final[float] = 23.9759979248047
COMP_DT: Final[float] = 1.0 / COMP_FPS
