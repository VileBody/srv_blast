"""render_v1/assembler_core.py (legacy shim)

This module used to host the AE payload compiler.
The implementation moved to: src/render/ae/compiler/build_payload.py

Keep this shim so older imports keep working during refactors.
"""

from src.render.ae.compiler import build_project_payload_from_composition

__all__ = [
    "build_project_payload_from_composition",
]
