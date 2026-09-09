from __future__ import annotations

from typing import Any


ALIGNMENT_ALGORITHM_VERSION = "local-ctc-viterbi-v19-bounded-boundary-overflow-demucs-4.1.0"

ERROR_UNSUPPORTED_TEXT = "ALIGNMENT_UNSUPPORTED_TEXT"
ERROR_FULLY_REDACTED_WORD = "ALIGNMENT_FULLY_REDACTED_WORD"
ERROR_PRONUNCIATION_UNAVAILABLE = "ALIGNMENT_PRONUNCIATION_UNAVAILABLE"
ERROR_WINDOW_MISMATCH = "ALIGNMENT_WINDOW_MISMATCH"
# The reference text physically cannot be pronounced inside the requested
# window: the CTC target needs more emission frames than the window has.
# Distinct from ERROR_WINDOW_MISMATCH so the caller can tell "your text is too
# long" from "we could not prove where it starts and ends".
ERROR_TEXT_TOO_LONG_FOR_WINDOW = "ALIGNMENT_TEXT_TOO_LONG_FOR_WINDOW"
ERROR_MODEL_UNAVAILABLE = "ALIGNMENT_MODEL_UNAVAILABLE"
ERROR_SEPARATOR_UNAVAILABLE = "ALIGNMENT_SEPARATOR_UNAVAILABLE"
ERROR_SOURCE_SEPARATION_FAILED = "ALIGNMENT_SOURCE_SEPARATION_FAILED"
ERROR_TIMEOUT = "ALIGNMENT_TIMEOUT"
ERROR_INTERNAL = "ALIGNMENT_INTERNAL_ERROR"


class AlignmentFailure(RuntimeError):
    """Explicit, coded alignment failure. Defined here so that every module of
    the package (including leaf helpers imported by ``core``) can raise it
    without an import cycle."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ):
        self.code = str(code)
        self.message = str(message)
        self.details = dict(details or {})
        super().__init__(f"{self.code}: {self.message}")
