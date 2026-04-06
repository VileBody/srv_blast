from __future__ import annotations

import logging

from mlcore import gemini_orchestrator as go
from mlcore.models.stage1_forced_alignment import Stage1ForcedAlignmentPayload
from core.clip_window import CLIP_WINDOW_MIN_SECONDS


def _tc(seconds: float) -> str:
    """Convert seconds to mm:ss.mmm timecode."""
    mins = int(seconds) // 60
    secs = seconds - mins * 60
    return f"{mins}:{secs:06.3f}"


def _make_forced_payload(
    *,
    clip_start: float,
    clip_end: float,
    word_times: list[tuple[str, float, float]],
) -> Stage1ForcedAlignmentPayload:
    aligned_words = [
        {"text": w, "t_start": _tc(s), "t_end": _tc(e)} for w, s, e in word_times
    ]
    frag_words = [
        {"text": w, "t_start": _tc(s), "t_end": _tc(e)}
        for w, s, e in word_times
        if s >= clip_start - 0.01 and e <= clip_end + 0.01
    ]
    return Stage1ForcedAlignmentPayload.model_validate(
        {
            "aligned_words": aligned_words,
            "pause_spans": [],
            "selected_fragment": {
                "audio": {
                    "clip_start_abs": _tc(clip_start),
                    "clip_end_abs": _tc(clip_end),
                },
                "transcript_words": frag_words or aligned_words[:1],
            },
        }
    )


def test_short_clip_is_expanded_to_minimum() -> None:
    """An 11-second clip from the LLM should be expanded to >= 13 seconds."""
    word_times = [
        ("hello", 1.0, 2.0),
        ("world", 5.0, 6.0),
        ("foo", 14.0, 15.0),
        ("bar", 18.0, 19.0),
        ("baz", 22.0, 23.5),
        ("end", 28.0, 29.0),
    ]
    # 11 second clip: 13.1 .. 24.1
    payload = _make_forced_payload(
        clip_start=13.1,
        clip_end=24.1,
        word_times=word_times,
    )
    logger = logging.getLogger("test.short_clip")
    result = go._stage1_asr_from_forced_alignment(payload, logger=logger)
    sf = result.selected_fragment
    assert sf is not None
    dur = float(sf.audio.clip_end_abs) - float(sf.audio.clip_start_abs)
    assert dur >= CLIP_WINDOW_MIN_SECONDS - 1e-6, f"expanded dur={dur} < {CLIP_WINDOW_MIN_SECONDS}"


def test_normal_clip_is_not_modified() -> None:
    """A clip already >= 13 seconds should not be expanded."""
    word_times = [
        ("hello", 1.0, 2.0),
        ("foo", 5.0, 6.0),
        ("bar", 18.0, 19.0),
        ("end", 28.0, 29.0),
    ]
    payload = _make_forced_payload(
        clip_start=5.0,
        clip_end=19.0,
        word_times=word_times,
    )
    logger = logging.getLogger("test.normal_clip")
    result = go._stage1_asr_from_forced_alignment(payload, logger=logger)
    sf = result.selected_fragment
    assert sf is not None
    assert abs(float(sf.audio.clip_start_abs) - 5.0) < 1e-6
    assert abs(float(sf.audio.clip_end_abs) - 19.0) < 1e-6


def test_short_clip_expansion_bounded_by_track() -> None:
    """Expansion should not go below 0 or past the last word."""
    word_times = [
        ("a", 0.5, 1.5),
        ("b", 2.0, 3.0),
        ("c", 4.0, 5.0),
        ("d", 6.0, 7.0),
        ("e", 8.0, 9.0),
        ("f", 10.0, 11.0),
    ]
    # 8 second clip near start of track
    payload = _make_forced_payload(
        clip_start=0.5,
        clip_end=8.5,
        word_times=word_times,
    )
    logger = logging.getLogger("test.bounded")
    result = go._stage1_asr_from_forced_alignment(payload, logger=logger)
    sf = result.selected_fragment
    assert sf is not None
    assert float(sf.audio.clip_start_abs) >= 0.0
    dur = float(sf.audio.clip_end_abs) - float(sf.audio.clip_start_abs)
    assert dur >= CLIP_WINDOW_MIN_SECONDS - 1e-6
