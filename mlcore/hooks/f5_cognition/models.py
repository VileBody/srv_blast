# mlcore/hooks/f5_cognition/models.py
"""
Pydantic-модели для F5 Cognition.

Контракт I/O модуля + внутренний контракт Stage1 → Stage2.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# Устройства (5 шт.) — см. §3 ТЗ
# ─────────────────────────────────────────────────────────────────────────────

class F5Device(str, Enum):
    PUNCHLINE = "punchline"
    MISSING_WORD = "missing_word"
    LYRIC_ECHO = "lyric_echo"
    QUESTION_TO_TRACK = "question_to_track"
    INVERSE_LYRIC = "inverse_lyric"


# ─────────────────────────────────────────────────────────────────────────────
# Voice spec (вывод Stage 1 → вход Stage 2)
# ─────────────────────────────────────────────────────────────────────────────

VoiceEmotion = Literal[
    "hype", "whisper", "robotic", "melancholic",
    "hostile", "playful", "urgent", "detached",
]

VoicePacing = Literal[
    "slow", "normal", "fast", "staccato", "rising", "falling",
]


class VoiceSpec(BaseModel):
    """Выход Stage 1, вход Stage 2."""
    tts_text: str = Field(..., min_length=1, description="Текст для синтеза (3–8 слов)")
    voice_persona: str = Field(..., description="5–10 слов: пол, возраст, тембр, акцент")
    voice_emotion: VoiceEmotion
    voice_pacing: VoicePacing
    expected_duration_ms: int = Field(..., ge=1500, le=4000)
    rationale: str = Field(..., description="Для логов и ручного review")


# ─────────────────────────────────────────────────────────────────────────────
# I/O модуля (см. §2 ТЗ)
# ─────────────────────────────────────────────────────────────────────────────

class LyricsTiming(BaseModel):
    start: float
    end: float
    text: str


class TrackMeta(BaseModel):
    bpm: Optional[float] = None
    key: Optional[str] = None
    genre: Optional[str] = None
    artist: Optional[str] = None


class F5Request(BaseModel):
    """Вход модуля F5."""
    track_path: str
    lyrics: str = Field(..., description="Полный текст или первые 30с")
    lyrics_timings: Optional[list[LyricsTiming]] = None
    track_meta: TrackMeta = Field(default_factory=TrackMeta)
    focal_start_ms: int = Field(..., ge=0)

    # В v1.3 device обязателен — пользователь выбирает кнопку в боте.
    device: F5Device

    drop_at_sec: Optional[float] = None
    seed: Optional[int] = None


class F5Response(BaseModel):
    """Выход модуля F5."""
    audio_path: str = Field(..., description=".wav, 3.0–4.0с, 48kHz, stereo")
    audio_duration_ms: int
    tts_text: str
    voice_persona: str
    voice_emotion: VoiceEmotion
    voice_pacing: VoicePacing
    tts_duration_ms: int = Field(..., description="Фактическая длина TTS до mixer'a")
    chosen_device: F5Device
    rationale: str
    extended_via_reverb: bool = False

    def to_config_block(
        self, *, focal_start_ms: int, audio_url: str | None = None,
    ) -> dict:
        """
        Сериализует F5Response в блок для full_edit_config["f5"].

        project_builder.build_full_project читает этот блок и зовёт apply_f5().
        Добавляет focal_start_ms (его нет в самом Response) и audio_url
        (S3-ссылка на загруженный .wav).
        """
        block = self.model_dump(mode="json")
        block["focal_start_ms"] = int(focal_start_ms)
        if audio_url:
            block["audio_url"] = audio_url
        return block

    @classmethod
    def from_config_block(cls, block: dict) -> "F5Response":
        """
        Обратная операция: достаёт F5Response из config-блока,
        отбрасывая служебные поля (focal_start_ms, audio_url).
        """
        known = set(cls.model_fields.keys())
        data = {k: v for k, v in block.items() if k in known}
        return cls(**data)
