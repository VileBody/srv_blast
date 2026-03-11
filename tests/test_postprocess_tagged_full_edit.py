from __future__ import annotations

from mlcore.gemini_postprocess import _build_full_edit_from_tagged_subtitles
from mlcore.models.tagged_subtitles import TaggedSubtitlesPayload


def test_build_full_edit_from_tagged_subtitles_contains_exit_t_for_long_before_short() -> None:
    tagged = TaggedSubtitlesPayload.model_validate(
        {
            "clip_start_abs": 10.0,
            "clip_end_abs": 14.0,
            "subtitles": [
                {"text": "мы станем", "tag": "long", "in": 10.0, "out": 11.2},
                {"text": "чужими", "tag": "short", "in": 11.2, "out": 12.0},
                {"text": "дальше", "tag": "long", "in": 12.0, "out": 14.0},
            ],
        }
    )

    out = _build_full_edit_from_tagged_subtitles(
        tagged_abs=tagged,
        clip_start_abs=10.0,
        clip_end_abs=14.0,
        fps=23.976,
    )
    segs = list(out["subtitle_segments"])
    assert len(segs) == 3
    assert abs(float(segs[0]["in_point"]) - 0.0) < 1e-9
    assert abs(float(segs[0]["out_point"]) - 1.2) < 1e-9
    assert abs(float(segs[0]["exit_t"]) - 1.2) < 1e-9
    assert "exit_t" not in segs[1]

