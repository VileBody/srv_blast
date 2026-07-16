from __future__ import annotations

from mlcore.gemini_orchestrator import _build_solid_background_footage_payload


def test_solid_background_payload_preserves_only_valid_scene_cuts() -> None:
    payload = _build_solid_background_footage_payload(
        clip_start_abs=10.0,
        clip_end_abs=20.0,
        switch_points_abs=[9.0, 12.5, 12.5, 17.0, 21.0],
        placeholder_file_name="placeholder.mp4",
    )

    assert [(clip.in_point, clip.out_point) for clip in payload.clips] == [
        (10.0, 12.5),
        (12.5, 17.0),
        (17.0, 20.0),
    ]
    assert {clip.file_name for clip in payload.clips} == {"placeholder.mp4"}
    assert [clip.start_time for clip in payload.clips] == [10.0, 12.5, 17.0]