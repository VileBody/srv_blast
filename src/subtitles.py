from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Literal

from .ffmpeg_utils import FFmpegExecutor
from .genai_client import GeminiClient
from .elevenlabs_client import ElevenLabsClient, STTWord

log = logging.getLogger(__name__)

SubtitlesProvider = Literal["gemini", "elevenlabs"]


def _format_ts(seconds: float) -> str:
    """
    Переводим float секунд в формат SRT: hh:mm:ss,ms
    (оставляем для дебага, хотя для анимации мы работаем с float-секундами).
    """
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    total_sec = total_ms // 1000
    s = total_sec % 60
    total_min = total_sec // 60
    m = total_min % 60
    h = total_min // 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


class SubtitleService:
    """
    Универсальный сервис сабов:

      provider="gemini":
        - Gemini отдаёт SRT
        - парсим SRT, строим анимированные cues (слово за словом)
        - ffmpeg рисует через drawtext по центру

      provider="elevenlabs":
        - ElevenLabs STT даёт слова с таймкодами
        - строим сегменты и cues так же
        - ffmpeg рисует через drawtext
    """

    def __init__(
        self,
        provider: SubtitlesProvider,
        ffmpeg: FFmpegExecutor,
        work_dir: Path,
        gemini: GeminiClient | None = None,
        eleven: ElevenLabsClient | None = None,
    ):
        self.provider = provider
        self.ffmpeg = ffmpeg
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.gemini = gemini
        self.eleven = eleven

    # --------- публичный API --------- #

    def add_subtitles(self, video: Path, output_with_subs: Path | None = None) -> Path:
        provider = self.provider.lower()
        if provider == "elevenlabs":
            return self._add_subtitles_eleven(video, output_with_subs)
        # по умолчанию — gemini
        return self._add_subtitles_gemini(video, output_with_subs)

    # --------- GEMINI: SRT → cues → drawtext --------- #

    def _add_subtitles_gemini(
        self, video: Path, output_with_subs: Path | None = None
    ) -> Path:
        if self.gemini is None:
            raise RuntimeError("Gemini subtitle provider selected, but GeminiClient is None")

        log.info("Generating animated subtitles for %s via Gemini SRT + drawtext", video)
        srt_text = self.gemini.generate_srt_for_video(video)
        # на всякий случай сохраняем SRT в файл (полезно для дебага)
        srt_path = self.work_dir / (video.stem + ".srt")
        srt_path.write_text(srt_text, encoding="utf-8")
        log.info("SRT saved (Gemini): %s", srt_path)

        # парсим SRT → сегменты
        segments = self._parse_srt_to_segments(srt_text)
        # конвертим сегменты в cues для анимации
        cues = self._segments_to_cues(segments)

        if output_with_subs is None:
            output_with_subs = video.with_name(video.stem + "_subs.mp4")

        self.ffmpeg.burn_word_animation(video, cues, output_with_subs)
        return output_with_subs

    # --------- ELEVENLABS: слова → cues → drawtext --------- #

    def _add_subtitles_eleven(
        self, video: Path, output_with_subs: Path | None = None
    ) -> Path:
        if self.eleven is None:
            raise RuntimeError("ElevenLabs subtitle provider selected, but ElevenLabsClient is None")

        log.info("Generating animated subtitles for %s via ElevenLabs STT + drawtext", video)

        audio_path = self._extract_full_audio(video)
        words = self.eleven.transcribe_with_word_timestamps(audio_path)

        cues = self._build_cues_from_words(words)

        if output_with_subs is None:
            output_with_subs = video.with_name(video.stem + "_subs.mp4")

        self.ffmpeg.burn_word_animation(video, cues, output_with_subs)
        return output_with_subs

    # --------- вспомогательное: аудио для ElevenLabs --------- #

    def _extract_full_audio(self, video: Path) -> Path:
        """
        Достаём аудиодорожку из видео на всю длительность.
        Используем probe_video для длительности и cut_audio_segment с -vn.
        """
        _, _, duration = self.ffmpeg.probe_video(video)
        audio_path = self.work_dir / (video.stem + "_stt.wav")
        self.ffmpeg.cut_audio_segment(video, 0.0, duration, audio_path)
        return audio_path

    # --------- парсер SRT → сегменты --------- #

    def _parse_srt_to_segments(
        self, srt_text: str
    ) -> List[tuple[float, float, str]]:
        """
        Простой парсер SRT: возвращает список (start_sec, end_sec, text).
        """
        segments: List[tuple[float, float, str]] = []
        lines = srt_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if not line:
                i += 1
                continue

            # строка с индексом (можем проигнорировать)
            if line.isdigit():
                i += 1
                if i >= len(lines):
                    break
                timing = lines[i].strip()
            else:
                # иногда SRT без индекса — пробуем считать текущую строку таймингом
                timing = line

            if "-->" not in timing:
                i += 1
                continue

            try:
                start_str, end_str = [x.strip() for x in timing.split("-->")]
                start_sec = self._srt_timestamp_to_seconds(start_str)
                end_sec = self._srt_timestamp_to_seconds(end_str)
            except Exception:
                i += 1
                continue

            i += 1
            text_lines: List[str] = []
            # собираем текст до пустой строки
            while i < len(lines) and lines[i].strip():
                text_lines.append(lines[i].strip())
                i += 1

            text = " ".join(text_lines).strip()
            if text:
                segments.append((start_sec, end_sec, text))

        log.info("Parsed %d SRT segments from Gemini", len(segments))
        return segments

    def _srt_timestamp_to_seconds(self, ts: str) -> float:
        # формат: hh:mm:ss,ms
        hhmmss, ms = ts.split(",")
        h_str, m_str, s_str = hhmmss.split(":")
        h = int(h_str)
        m = int(m_str)
        s = int(s_str)
        millis = int(ms)
        return h * 3600 + m * 60 + s + millis / 1000.0

    # --------- сегменты → cues для drawtext (Gemini) --------- #

    def _segments_to_cues(
        self,
        segments: List[tuple[float, float, str]],
        max_words_per_seg: int = 9999,  # при желании можно ограничить
    ) -> List[tuple[float, float, str]]:
        """
        Для каждого сегмента (start,end,text) строим ступеньки:
        [t0,t1): слово1
        [t1,t2): слово1 слово2
        ...
        """
        cues: List[tuple[float, float, str]] = []

        for seg_start, seg_end, text in segments:
            if seg_end <= seg_start:
                seg_end = seg_start + 0.5
            words = text.split()
            if not words:
                continue
            words = words[:max_words_per_seg]
            n = len(words)
            total_duration = seg_end - seg_start
            step = total_duration / max(n, 1)

            for i in range(n):
                start = seg_start + i * step
                end = seg_start + (i + 1) * step if i < n - 1 else seg_end
                text_step = " ".join(words[: i + 1]).strip()
                cues.append((start, end, text_step))

        log.info("Built %d cues from %d SRT segments (Gemini)", len(cues), len(segments))
        return cues

    # --------- слова ElevenLabs → cues для drawtext --------- #

    def _build_cues_from_words(
        self,
        words: List[STTWord],
        max_gap: float = 0.6,
        max_len: float = 3.5,
        max_chars: int = 60,
    ) -> List[tuple[float, float, str]]:
        """
        Анимация в духе SRT-сегментов, но основанная на словах:

        1) группируем слова в фразы по gap/duration/длине,
        2) для каждой фразы делаем ступеньки.
        """
        if not words:
            raise RuntimeError("No words from ElevenLabs STT")

        segments: list[tuple[float, float, List[str]]] = []

        current_words: List[str] = []
        seg_start = words[0].start
        last_end = words[0].end
        current_words.append(words[0].text)

        for w in words[1:]:
            gap = w.start - last_end
            seg_duration = last_end - seg_start
            text_len = len(" ".join(current_words))

            need_new = (
                gap > max_gap
                or seg_duration > max_len
                or text_len > max_chars
            )

            if need_new:
                segments.append((seg_start, last_end, current_words.copy()))
                current_words = [w.text]
                seg_start = w.start
            else:
                current_words.append(w.text)

            last_end = w.end

        if current_words:
            segments.append((seg_start, last_end, current_words.copy()))

        cues: List[tuple[float, float, str]] = []

        for seg_start, seg_end, seg_words in segments:
            if not seg_words:
                continue
            if seg_end <= seg_start:
                seg_end = seg_start + 0.5

            n = len(seg_words)
            total_duration = seg_end - seg_start
            step = total_duration / max(n, 1)

            for i in range(n):
                start = seg_start + i * step
                end = seg_start + (i + 1) * step if i < n - 1 else seg_end
                text_step = " ".join(seg_words[: i + 1]).strip()
                cues.append((start, end, text_step))

        log.info(
            "Built %d cues from %d word segments (ElevenLabs)",
            len(cues),
            len(segments),
        )
        return cues
