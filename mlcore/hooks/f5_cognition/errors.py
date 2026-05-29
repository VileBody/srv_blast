# mlcore/hooks/f5_cognition/errors.py
"""
Кастомные исключения F5. См. §2.3 ТЗ.

Все ошибки наследуются от F5Error, чтобы вызывающий код мог ловить одним
except'ом и понимать, что упало внутри F5, а не в общем пайплайне.
"""
from __future__ import annotations


class F5Error(Exception):
    """Базовое исключение F5."""
    code: str = "F5_ERROR"


class F5LyricsEmpty(F5Error):
    """Пустая или < 5 слов лирика."""
    code = "F5_LYRICS_EMPTY"


class F5FocalOutOfBounds(F5Error):
    """focal_start_ms + N > длина трека."""
    code = "F5_FOCAL_OUT_OF_BOUNDS"


class F5TtsTooLong(F5Error):
    """TTS вышел > 4.0с — для внутренних retry. Наружу не пробрасываем (cut)."""
    code = "F5_TTS_TOO_LONG"


class F5TtsTooShort(F5Error):
    """TTS вышел < 1.5с — для внутренних retry."""
    code = "F5_TTS_TOO_SHORT"


class F5GeminiTimeout(F5Error):
    """Вызов Gemini > timeout."""
    code = "F5_GEMINI_TIMEOUT"


class F5TtsRetryExhausted(F5Error):
    """retry Stage 2 не помог даже с reverb extension."""
    code = "F5_TTS_RETRY_EXHAUSTED"


class F5Stage1ParseError(F5Error):
    """Stage 1 вернул не валидный JSON / поля отсутствуют."""
    code = "F5_STAGE1_PARSE_ERROR"
