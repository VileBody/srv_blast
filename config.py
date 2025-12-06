from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    gemini_api_key: str
    pins_dir: Path
    descriptions_dir: Path
    work_dir: Path
    output_dir: Path
    gemini_model_planning: str
    gemini_model_subtitles: str
    target_width: int
    target_height: int

    # ElevenLabs (опционально, если используем его для сабов)
    eleven_api_key: str
    eleven_stt_model: str

    # Как делаем субтитры: "gemini" или "elevenlabs"
    subtitles_provider: str

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            gemini_api_key=os.environ["GEMINI_API_KEY"],
            pins_dir=Path(os.getenv("PINS_DIR", "./pins")),
            descriptions_dir=Path(os.getenv("DESCRIPTIONS_DIR", "./descriptions")),
            work_dir=Path(os.getenv("WORK_DIR", "./work")),
            output_dir=Path(os.getenv("OUTPUT_DIR", "./output")),
            gemini_model_planning=os.getenv(
                "GEMINI_MODEL_PLANNING", "gemini-3-pro-preview"
            ),
            gemini_model_subtitles=os.getenv(
                "GEMINI_MODEL_SUBS", "gemini-flash-latest"
            ),
            target_width=int(os.getenv("TARGET_WIDTH", "1080")),
            target_height=int(os.getenv("TARGET_HEIGHT", "1920")),
            # ElevenLabs: не заставляем указывать ключ, если используем gemini
            eleven_api_key=os.getenv("ELEVENLABS_API_KEY", ""),
            eleven_stt_model=os.getenv("ELEVEN_STT_MODEL", "scribe_v1"),
            subtitles_provider=os.getenv("SUBTITLES_PROVIDER", "gemini").lower(),
        )
