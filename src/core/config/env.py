from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv

# Подхватываем .env один раз на старте процесса
load_dotenv()

# --- Прокси зашит в код (временно) ---
# Серверный .env недоступен для правки без SSH, поэтому актуальный прокси живёт
# здесь и меняется через git push (тот же приём, что и STAGE2_TIMING_MODE в
# gemini_orchestrator.py). Форсим значение ПОВЕРХ того, что пришло из .env через
# docker env_file (там осталось протухшее).
#
# ВАЖНО: перекрываем не только OUTBOUND_PROXY (его читают Gemini/OpenRouter и
# передают в httpx ЯВНО), но и стандартные HTTP(S)_PROXY. Серверный .env выставляет
# HTTPS_PROXY напрямую → его наследует boto/S3 (trust_env) и OpenRouter (httpx.post
# без явного proxy). Именно поэтому build падал на S3 со СТАРЫМ прокси, хотя
# OUTBOUND_PROXY уже был перекрыт. Раньше S3 ходил через тот же прокси и работал,
# пока прокси был жив — поэтому просто перенаправляем все переменные на новый,
# а не меняем маршрутизацию S3 под огнём.
#
# Когда вернётся доступ к серверному .env — убрать этот блок и хранить прокси там.
_PROXY_URL = "http://FazPoo:U6WHvC@45.153.20.238:10506"
for _pk in ("OUTBOUND_PROXY", "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ[_pk] = _PROXY_URL


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

    # Опциональный исходящий прокси для внешних HTTP(S)-запросов
    # Берём либо валидную строку из .env, либо None
    outbound_proxy: str | None

    @classmethod
    def from_env(cls) -> "Config":
        # аккуратно приводим прокси к None, если пусто или закомментировано
        raw_proxy = os.getenv("OUTBOUND_PROXY", "").strip()
        if not raw_proxy or raw_proxy.startswith("#"):
            proxy = None
        else:
            proxy = raw_proxy

        return cls(
            gemini_api_key=os.environ["GEMINI_API_KEY"],
            pins_dir=Path(os.getenv("PINS_DIR", "./pins")),
            descriptions_dir=Path(os.getenv("DESCRIPTIONS_DIR", "./descriptions")),
            work_dir=Path(os.getenv("WORK_DIR", "./work")),
            output_dir=Path(os.getenv("OUTPUT_DIR", "./output")),
            gemini_model_planning=os.getenv(
                "GEMINI_MODEL_PLANNING", "gemini-2.5-pro"
            ),
            gemini_model_subtitles=os.getenv(
                "GEMINI_MODEL_SUBS", "gemini-flash-latest"
            ),
            target_width=int(os.getenv("TARGET_WIDTH", "1080")),
            target_height=int(os.getenv("TARGET_HEIGHT", "1080")),
            eleven_api_key=os.getenv("ELEVENLABS_API_KEY", ""),
            eleven_stt_model=os.getenv("ELEVEN_STT_MODEL", "scribe_v1"),
            subtitles_provider=os.getenv("SUBTITLES_PROVIDER", "gemini").lower(),
            outbound_proxy=proxy,
        )
