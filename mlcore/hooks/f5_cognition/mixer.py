# mlcore/hooks/f5_cognition/mixer.py
"""
Mixer — финальная склейка TTS-вставки и фокусного отрывка трека.

Алгоритм:
  1. Извлекаем фокусный отрывок (длина ≥ tts_duration + запас).
  2. Нормализуем TTS до -3 dB peak.
  3. Если TTS < 1.5с — расширяем reverb tail extension.
  4. Если TTS > 4.0с — обрезаем до 4.0с с fade-out 80мс.
  5. Применяем статичный duck -3..-6 dB на трек в окне TTS.
  6. Overlay TTS поверх ducked track.
  7. Финальный лимитер -1 dBFS.
  8. Длина выхода = max(3000, tts_duration_ms + 100).

Sidechain ducking откинут (см. чат) — используется статичный gain.
"""
from __future__ import annotations

import io
import logging
from pathlib import Path

from pydub import AudioSegment
from pydub.effects import compress_dynamic_range

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Константы микса (соответствуют §6.3 ТЗ + правки v1.3)
# ─────────────────────────────────────────────────────────────────────────────

TTS_MAX_OUTPUT_MS = 4000
TTS_CUT_FADE_MS = 80
TTS_OFFSET_MS = 100              # задержка TTS от старта фокуса
DUCK_DB = 4                      # статичный duck (-3..-6 dB середина)
TTS_NORMALIZE_HEADROOM_DB = 3.0
MASTER_PEAK_DBFS = -1.0
OUTPUT_SAMPLE_RATE = 48_000
OUTPUT_CHANNELS = 2
FOCAL_SLACK_MS = 500             # запас на хвост для fade'ов

# Длина выхода
MIN_OUTPUT_MS = 3000


# ─────────────────────────────────────────────────────────────────────────────
# Reverb tail extension (для слишком коротких TTS)
# ─────────────────────────────────────────────────────────────────────────────

def extend_with_reverb(tts: AudioSegment, target_ms: int) -> AudioSegment:
    """
    Растягивает TTS до target_ms через простой reverb-tail.

    Реализация-скелет: берём последние 300мс, делаем затухающий хвост поверх.
    Реальный reverb лучше через ffmpeg (`aecho` / `afftfilt`) или pedalboard —
    подключим когда будет понятно какая зависимость доступна на проде.

    TODO: заменить на pedalboard.Reverb или ffmpeg aecho.
    """
    cur_ms = len(tts)
    if cur_ms >= target_ms:
        return tts

    tail_src_ms = min(300, cur_ms)
    tail = tts[-tail_src_ms:]

    need_ms = target_ms - cur_ms
    # Простой echo: 2-3 копии хвоста с затуханием.
    extended = tts
    gain = -6
    pos_ms = cur_ms
    while pos_ms < target_ms:
        decayed = tail.apply_gain(gain)
        extended = extended.append(decayed, crossfade=min(50, tail_src_ms // 2))
        pos_ms += tail_src_ms
        gain -= 4
        if gain < -24:
            break

    logger.info("f5.mixer reverb-extended %d → %d ms", cur_ms, len(extended))
    return extended[:target_ms]


# ─────────────────────────────────────────────────────────────────────────────
# Cut + fade для длинных TTS
# ─────────────────────────────────────────────────────────────────────────────

def cut_with_fade(tts: AudioSegment, max_ms: int = TTS_MAX_OUTPUT_MS) -> AudioSegment:
    if len(tts) <= max_ms:
        return tts
    cut = tts[:max_ms].fade_out(TTS_CUT_FADE_MS)
    logger.info("f5.mixer cut %d → %d ms (fade %d)", len(tts), max_ms, TTS_CUT_FADE_MS)
    return cut


# ─────────────────────────────────────────────────────────────────────────────
# Главная точка входа
# ─────────────────────────────────────────────────────────────────────────────

def mix_overlay(
    *,
    track_path: str,
    focal_start_ms: int,
    tts_audio: bytes,
    output_path: str,
    duck_db: float = DUCK_DB,
    tts_offset_ms: int = TTS_OFFSET_MS,
) -> tuple[str, int, bool]:
    """
    Возвращает (output_path, output_duration_ms, extended_via_reverb).
    """
    extended = False

    tts = AudioSegment.from_file(io.BytesIO(tts_audio))
    tts = tts.normalize(headroom=TTS_NORMALIZE_HEADROOM_DB)

    # 1. Возможная reverb-extension если TTS короткий (1.5с — нижняя граница
    #    приёма из Stage 2; если Stage 2 вернул что-то длиннее, не трогаем).
    if len(tts) < MIN_OUTPUT_MS - tts_offset_ms - 100:
        target = MIN_OUTPUT_MS - tts_offset_ms - 100
        tts = extend_with_reverb(tts, target)
        extended = True

    # 2. Cut если > 4с.
    tts = cut_with_fade(tts, TTS_MAX_OUTPUT_MS)

    tts_ms = len(tts)
    output_ms = max(MIN_OUTPUT_MS, tts_ms + tts_offset_ms + 100)

    # 3. Берём фокусный отрывок нужной длины + запас.
    track = AudioSegment.from_file(track_path)
    focal_end_ms = focal_start_ms + output_ms + FOCAL_SLACK_MS
    focal = track[focal_start_ms:focal_end_ms]

    if len(focal) < output_ms:
        # focal_start_ms слишком близко к концу — это валидируется выше в pipeline,
        # тут аварийный padding тишиной чтобы не сегфолтнуть.
        focal = focal + AudioSegment.silent(duration=output_ms - len(focal))

    # 4. Статичный duck: трек на duck_db тише в окне TTS.
    ducked = _apply_static_duck(
        focal, duck_start_ms=tts_offset_ms, duck_end_ms=tts_offset_ms + tts_ms,
        duck_db=duck_db,
    )

    # 5. Overlay TTS поверх ducked.
    mixed = ducked.overlay(tts, position=tts_offset_ms)

    # 6. Лимитер.
    mixed = compress_dynamic_range(mixed, threshold=MASTER_PEAK_DBFS, ratio=20.0)

    # 7. Финальная длина и формат.
    mixed = mixed[:output_ms]
    mixed = mixed.set_frame_rate(OUTPUT_SAMPLE_RATE).set_channels(OUTPUT_CHANNELS)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    mixed.export(output_path, format="wav")

    logger.info(
        "f5.mixer done out=%s duration_ms=%d tts_ms=%d duck_db=%.1f extended=%s",
        output_path, len(mixed), tts_ms, duck_db, extended,
    )
    return output_path, len(mixed), extended


def _apply_static_duck(
    track: AudioSegment, *, duck_start_ms: int, duck_end_ms: int, duck_db: float,
    fade_ms: int = 40,
) -> AudioSegment:
    """
    Кусок трека в окне [duck_start_ms, duck_end_ms] делает -duck_db,
    с короткими fade-in/out на границах чтобы не щёлкало.
    """
    before = track[:duck_start_ms]
    middle = track[duck_start_ms:duck_end_ms].apply_gain(-abs(duck_db))
    after = track[duck_end_ms:]

    # Хвостовые fade'ы чтобы не было click'ов на стыках.
    if fade_ms > 0 and len(middle) > fade_ms * 2:
        middle = middle.fade_in(fade_ms).fade_out(fade_ms)

    return before + middle + after
