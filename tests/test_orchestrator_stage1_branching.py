from __future__ import annotations

import json
from pathlib import Path

from mlcore import gemini_orchestrator as go
from mlcore.models.footage_style import FootageStylePickPayload
from mlcore.models.stage1_asr import Stage1AsrPayload
from mlcore.models.subtitles_flow import Impulse2ndRawPayload
from mlcore.models.switch_timing import Stage2TimingAnalysisPayload, Stage2TimingCutsPayload


def _timing_analysis_payload() -> Stage2TimingAnalysisPayload:
    return Stage2TimingAnalysisPayload.model_validate(
        {
            "selected_rule": "Dynamic Contrast",
            "reason": "test",
            "raw_timings": {
                "kick_bass": [2.5, 6.0, 10.0, 14.0],
                "snare_clap": [3.0, 7.0, 11.0, 15.0],
                "vocal_phrases": [4.0, 9.0, 13.0],
                "semantic_peaks": [5.5, 12.0],
            },
        }
    )


def _timing_cuts_payload() -> Stage2TimingCutsPayload:
    return Stage2TimingCutsPayload.model_validate(
        {
            "applied_rule": "Dynamic Contrast",
            "final_cut_timings": [4.0, 8.0, 12.0],
        }
    )


def test_non_legacy_uses_stage1a_selected_fragment_and_skips_stage1b(
    monkeypatch,
    tmp_path: Path,
) -> None:
    audio_path = tmp_path / "audio.mp3"
    audio_path.write_bytes(b"fake")

    inv_path = tmp_path / "inventory.json"
    inv_path.write_text(
        json.dumps(
            {
                "assets": [
                    {
                        "file_name": "f1.mp4",
                        "file_path": "s3://bucket/pinterest_collection/Rock/dark_forest/f1.mp4",
                        "src_w": 720,
                        "src_h": 1280,
                        "duration_sec": 30.0,
                        "genre": "Rock",
                        "tag": "dark_forest",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    out_dir = tmp_path / "out"
    monkeypatch.setenv("MODE", "dev")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GEMINI_MODEL_STAGE1", "m1")
    monkeypatch.setenv("GEMINI_MODEL_SUBTITLES", "m2")
    monkeypatch.setenv("GEMINI_MODEL_FOOTAGE", "m3")
    monkeypatch.setenv("FOOTAGE_INVENTORY_JSON", str(inv_path))
    monkeypatch.setenv("OUT_DIR", str(out_dir))
    monkeypatch.setenv("AUDIO_FILE_PATH", str(audio_path))
    monkeypatch.setenv("AUDIO_DIR", str(audio_path.parent))
    monkeypatch.setenv("JOB_ID", "job_stage1a_branching")
    monkeypatch.setenv("SUBTITLES_MODE", "impulse_2nd")
    monkeypatch.setenv("STAGE2_TIMING_MODE", "prompts")
    monkeypatch.setenv("STAGE2_FAST_START_SECONDS", "6")

    monkeypatch.setattr(go, "_make_client", lambda **kwargs: object())
    monkeypatch.setattr(go, "pick_audio_files", lambda _audio_dir: [audio_path])
    monkeypatch.setattr(go, "detect_bpm_librosa", lambda **kwargs: 120.0)

    stage1_asr = Stage1AsrPayload.model_validate(
        {
            "transcript_words": [
                {"text": "intro", "t_start": 0.1, "t_end": 0.6},
                {"text": "hello", "t_start": 2.0, "t_end": 2.4},
                {"text": "world", "t_start": 2.7, "t_end": 3.2},
                {"text": "outro", "t_start": 18.0, "t_end": 18.5},
            ],
            "srt_items": [],
            "selected_fragment": {
                "audio": {"clip_start_abs": 2.0, "clip_end_abs": 16.0},
                "transcript_words": [
                    {"text": "hello", "t_start": 2.0, "t_end": 2.4},
                    {"text": "world", "t_start": 2.7, "t_end": 3.2},
                ],
                "srt_items": [{"start": 2.0, "end": 3.2, "text": "hello world"}],
            },
        }
    )

    monkeypatch.setattr(go, "call_stage1_asr_once", lambda **kwargs: stage1_asr)

    def _scenario_should_not_call(**kwargs):
        raise AssertionError("stage1b_scenario must be skipped for impulse_2nd")

    monkeypatch.setattr(go, "call_stage1_scenario_once", _scenario_should_not_call)

    monkeypatch.setattr(
        go,
        "call_subtitles_plan_model_once",
        lambda **kwargs: Impulse2ndRawPayload.model_validate(
            {
                "anchor_in_abs": 2.0,
                "word_timings": [
                    {"word": "hello", "start": 0.0, "end": 0.4},
                    {"word": "world", "start": 0.7, "end": 1.2},
                ],
                "segments": [
                    {
                        "text": "hello world",
                        "in": 0.0,
                        "out": 1.2,
                        "type": "long",
                        "word_timings": [
                            {"word": "hello", "start": 0.0, "end": 0.4},
                            {"word": "world", "start": 0.7, "end": 1.2},
                        ],
                    }
                ],
            }
        ),
    )
    monkeypatch.setattr(
        go,
        "call_footage_style_once",
        lambda **kwargs: FootageStylePickPayload.model_validate({"genre": "Rock", "tag": "dark_forest"}),
    )
    monkeypatch.setattr(go, "call_timing_analysis_once", lambda **kwargs: _timing_analysis_payload())
    monkeypatch.setattr(go, "call_timing_cuts_once", lambda **kwargs: _timing_cuts_payload())

    captured: dict = {}

    def _fake_render_all_steps(**kwargs):
        captured["plan"] = kwargs["plan"]
        return {
            "audio_plan": tmp_path / "audio_plan.json",
            "full_edit_config": tmp_path / "full_edit_config.json",
            "footage_config": tmp_path / "footage_config.json",
        }

    monkeypatch.setattr(go, "render_all_steps", _fake_render_all_steps)

    out = go.build_all_via_gemini_one_call()
    assert set(out.keys()) == {"audio_plan", "full_edit_config", "footage_config"}

    plan = captured["plan"]
    assert abs(float(plan.audio.clip_start_abs) - 2.0) < 1e-6
    assert abs(float(plan.audio.clip_end_abs) - 16.0) < 1e-6
    assert [w.text for w in plan.transcript_words] == ["hello", "world"]
    assert str(plan.subtitles.mode) == "impulse_2nd"


def test_non_legacy_retries_stage1a_when_selected_fragment_missing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    audio_path = tmp_path / "audio.mp3"
    audio_path.write_bytes(b"fake")

    inv_path = tmp_path / "inventory.json"
    inv_path.write_text(
        json.dumps(
            {
                "assets": [
                    {
                        "file_name": "f1.mp4",
                        "file_path": "s3://bucket/pinterest_collection/Rock/dark_forest/f1.mp4",
                        "src_w": 720,
                        "src_h": 1280,
                        "duration_sec": 30.0,
                        "genre": "Rock",
                        "tag": "dark_forest",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    out_dir = tmp_path / "out"
    monkeypatch.setenv("MODE", "dev")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GEMINI_MODEL_STAGE1", "m1")
    monkeypatch.setenv("GEMINI_MODEL_SUBTITLES", "m2")
    monkeypatch.setenv("GEMINI_MODEL_FOOTAGE", "m3")
    monkeypatch.setenv("FOOTAGE_INVENTORY_JSON", str(inv_path))
    monkeypatch.setenv("OUT_DIR", str(out_dir))
    monkeypatch.setenv("AUDIO_FILE_PATH", str(audio_path))
    monkeypatch.setenv("AUDIO_DIR", str(audio_path.parent))
    monkeypatch.setenv("JOB_ID", "job_stage1a_retry_missing_selected_fragment")
    monkeypatch.setenv("SUBTITLES_MODE", "impulse_2nd")
    monkeypatch.setenv("STAGE2_TIMING_MODE", "prompts")
    monkeypatch.setenv("STAGE2_FAST_START_SECONDS", "6")

    monkeypatch.setattr(go, "_make_client", lambda **kwargs: object())
    monkeypatch.setattr(go, "pick_audio_files", lambda _audio_dir: [audio_path])
    monkeypatch.setattr(go, "detect_bpm_librosa", lambda **kwargs: 120.0)

    stage1_asr_missing = Stage1AsrPayload.model_validate(
        {
            "transcript_words": [
                {"text": "hello", "t_start": 2.0, "t_end": 2.4},
                {"text": "world", "t_start": 2.7, "t_end": 3.2},
            ],
            "srt_items": [],
            "selected_fragment": None,
        }
    )
    stage1_asr_ok = Stage1AsrPayload.model_validate(
        {
            "transcript_words": [
                {"text": "hello", "t_start": 2.0, "t_end": 2.4},
                {"text": "world", "t_start": 2.7, "t_end": 3.2},
            ],
            "srt_items": [],
            "selected_fragment": {
                "audio": {"clip_start_abs": 2.0, "clip_end_abs": 16.0},
                "transcript_words": [
                    {"text": "hello", "t_start": 2.0, "t_end": 2.4},
                    {"text": "world", "t_start": 2.7, "t_end": 3.2},
                ],
                "srt_items": [{"start": 2.0, "end": 3.2, "text": "hello world"}],
            },
        }
    )

    calls = {"stage1_asr": 0}

    def _call_stage1_asr_with_one_retry(**kwargs):
        calls["stage1_asr"] += 1
        if calls["stage1_asr"] == 1:
            return stage1_asr_missing
        return stage1_asr_ok

    monkeypatch.setattr(go, "call_stage1_asr_once", _call_stage1_asr_with_one_retry)
    monkeypatch.setattr(
        go,
        "call_stage1_scenario_once",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("stage1b_scenario must be skipped for impulse_2nd")),
    )
    monkeypatch.setattr(
        go,
        "call_subtitles_plan_model_once",
        lambda **kwargs: Impulse2ndRawPayload.model_validate(
            {
                "anchor_in_abs": 2.0,
                "word_timings": [
                    {"word": "hello", "start": 0.0, "end": 0.4},
                    {"word": "world", "start": 0.7, "end": 1.2},
                ],
                "segments": [
                    {
                        "text": "hello world",
                        "in": 0.0,
                        "out": 1.2,
                        "type": "long",
                        "word_timings": [
                            {"word": "hello", "start": 0.0, "end": 0.4},
                            {"word": "world", "start": 0.7, "end": 1.2},
                        ],
                    }
                ],
            }
        ),
    )
    monkeypatch.setattr(
        go,
        "call_footage_style_once",
        lambda **kwargs: FootageStylePickPayload.model_validate({"genre": "Rock", "tag": "dark_forest"}),
    )
    monkeypatch.setattr(go, "call_timing_analysis_once", lambda **kwargs: _timing_analysis_payload())
    monkeypatch.setattr(go, "call_timing_cuts_once", lambda **kwargs: _timing_cuts_payload())
    monkeypatch.setattr(
        go,
        "render_all_steps",
        lambda **kwargs: {
            "audio_plan": tmp_path / "audio_plan.json",
            "full_edit_config": tmp_path / "full_edit_config.json",
            "footage_config": tmp_path / "footage_config.json",
        },
    )

    out = go.build_all_via_gemini_one_call()
    assert set(out.keys()) == {"audio_plan", "full_edit_config", "footage_config"}
    assert calls["stage1_asr"] == 2


def test_impulse_effective_clip_is_extended_for_timing_stage(monkeypatch, tmp_path: Path) -> None:
    audio_path = tmp_path / "audio.mp3"
    audio_path.write_bytes(b"fake")

    inv_path = tmp_path / "inventory.json"
    inv_path.write_text(
        json.dumps(
            {
                "assets": [
                    {
                        "file_name": "f1.mp4",
                        "file_path": "s3://bucket/pinterest_collection/Rock/dark_forest/f1.mp4",
                        "src_w": 720,
                        "src_h": 1280,
                        "duration_sec": 30.0,
                        "genre": "Rock",
                        "tag": "dark_forest",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    out_dir = tmp_path / "out"
    monkeypatch.setenv("MODE", "dev")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GEMINI_MODEL_STAGE1", "m1")
    monkeypatch.setenv("GEMINI_MODEL_SUBTITLES", "m2")
    monkeypatch.setenv("GEMINI_MODEL_FOOTAGE", "m3")
    monkeypatch.setenv("FOOTAGE_INVENTORY_JSON", str(inv_path))
    monkeypatch.setenv("OUT_DIR", str(out_dir))
    monkeypatch.setenv("AUDIO_FILE_PATH", str(audio_path))
    monkeypatch.setenv("AUDIO_DIR", str(audio_path.parent))
    monkeypatch.setenv("JOB_ID", "job_stage1a_effective_clip")
    monkeypatch.setenv("SUBTITLES_MODE", "impulse_2nd")
    monkeypatch.setenv("STAGE2_TIMING_MODE", "prompts")
    monkeypatch.setenv("STAGE2_FAST_START_SECONDS", "6")

    monkeypatch.setattr(go, "_make_client", lambda **kwargs: object())
    monkeypatch.setattr(go, "pick_audio_files", lambda _audio_dir: [audio_path])

    stage1_asr = Stage1AsrPayload.model_validate(
        {
            "transcript_words": [
                {"text": "hello", "t_start": 2.0, "t_end": 2.4},
                {"text": "world", "t_start": 15.7, "t_end": 16.0},
            ],
            "srt_items": [],
            "selected_fragment": {
                "audio": {"clip_start_abs": 2.0, "clip_end_abs": 16.0},
                "transcript_words": [
                    {"text": "hello", "t_start": 2.0, "t_end": 2.4},
                    {"text": "world", "t_start": 15.7, "t_end": 16.0},
                ],
                "srt_items": [{"start": 2.0, "end": 16.0, "text": "hello world"}],
            },
        }
    )
    monkeypatch.setattr(go, "call_stage1_asr_once", lambda **kwargs: stage1_asr)
    monkeypatch.setattr(
        go,
        "call_stage1_scenario_once",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("stage1b_scenario must be skipped for impulse_2nd")),
    )
    monkeypatch.setattr(
        go,
        "call_subtitles_plan_model_once",
        lambda **kwargs: Impulse2ndRawPayload.model_validate(
            {
                "anchor_in_abs": 2.0,
                "word_timings": [
                    {"word": "hello", "start": 0.0, "end": 0.4},
                    {"word": "world", "start": 13.7, "end": 14.0},
                ],
                "segments": [
                    {
                        "text": "hello world",
                        "in": 0.0,
                        "out": 14.5,  # abs=16.5, +0.5 tail pad beyond selected clip end=16.0
                        "type": "long",
                    }
                ],
            }
        ),
    )
    monkeypatch.setattr(
        go,
        "call_footage_style_once",
        lambda **kwargs: FootageStylePickPayload.model_validate({"genre": "Rock", "tag": "dark_forest"}),
    )

    captured: dict = {}

    def _timing_analysis_capture(**kwargs):
        prompt = str(kwargs.get("user_prompt") or "")
        marker = "AUDIO_CLIP_JSON:\n"
        assert marker in prompt
        clip_json_raw = prompt.split(marker, 1)[1].split("\n\nSEMANTIC_SUBTITLES_CONTEXT_JSON:\n", 1)[0]
        captured["timing_clip"] = json.loads(clip_json_raw)
        return _timing_analysis_payload()

    monkeypatch.setattr(go, "call_timing_analysis_once", _timing_analysis_capture)
    monkeypatch.setattr(go, "call_timing_cuts_once", lambda **kwargs: _timing_cuts_payload())

    def _fake_render_all_steps(**kwargs):
        captured["plan"] = kwargs["plan"]
        return {
            "audio_plan": tmp_path / "audio_plan.json",
            "full_edit_config": tmp_path / "full_edit_config.json",
            "footage_config": tmp_path / "footage_config.json",
        }

    monkeypatch.setattr(go, "render_all_steps", _fake_render_all_steps)

    out = go.build_all_via_gemini_one_call()
    assert set(out.keys()) == {"audio_plan", "full_edit_config", "footage_config"}

    timing_clip = captured["timing_clip"]
    assert abs(float(timing_clip["clip_start_abs"]) - 2.0) < 1e-6
    assert abs(float(timing_clip["clip_end_abs"]) - 16.5) < 1e-6

    plan = captured["plan"]
    assert abs(float(plan.subtitles.clip.end) - 16.5) < 1e-6
