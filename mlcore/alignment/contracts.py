ALIGNMENT_ALGORITHM_VERSION = "local-ctc-viterbi-v9-dynamic-window-redaction-espeak-demucs-4.1.0"

ERROR_UNSUPPORTED_TEXT = "ALIGNMENT_UNSUPPORTED_TEXT"
ERROR_FULLY_REDACTED_WORD = "ALIGNMENT_FULLY_REDACTED_WORD"
ERROR_PRONUNCIATION_UNAVAILABLE = "ALIGNMENT_PRONUNCIATION_UNAVAILABLE"
ERROR_WINDOW_MISMATCH = "ALIGNMENT_WINDOW_MISMATCH"
ERROR_MODEL_UNAVAILABLE = "ALIGNMENT_MODEL_UNAVAILABLE"
ERROR_SEPARATOR_UNAVAILABLE = "ALIGNMENT_SEPARATOR_UNAVAILABLE"
ERROR_SOURCE_SEPARATION_FAILED = "ALIGNMENT_SOURCE_SEPARATION_FAILED"
ERROR_TIMEOUT = "ALIGNMENT_TIMEOUT"
ERROR_INTERNAL = "ALIGNMENT_INTERNAL_ERROR"


class AlignmentFailure(RuntimeError):
    """Explicit, coded alignment failure. Defined here so that every module of
    the package (including leaf helpers imported by ``core``) can raise it
    without an import cycle."""

    def __init__(self, code: str, message: str):
        self.code = str(code)
        self.message = str(message)
        super().__init__(f"{self.code}: {self.message}")
