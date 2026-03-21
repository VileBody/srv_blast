from __future__ import annotations

import json
from pathlib import Path

import pytest

from mlcore import gemini_orchestrator as go
from mlcore.models.footage_style import FootageStylePickPayload
from mlcore.models.stage1_asr import Stage1AsrPayload
from mlcore.models.stage1_scenario import Stage1ScenarioPayload
from mlcore.models.subtitles_tokens import BlocksTokensPayload
from mlcore.models.switch_timing import Stage2TimingAnalysisPayload, Stage2TimingCutsPayload


def _draft_blocks() -> dict:
    return {
        "block_1": {"phrases": ["a"]},
        "block_2": {"p1": {"phrases": ["b"]}, "p2": {"phrases": ["c"]}},
        "block_3": {"phrases": ["d"]},
        "block_4": {"p1": {"phrases": ["e"]}, "p2": {"phrases": ["f"]}},
        "block_5": {
            "slowly_in": {"phrases": ["g"]},
            "fast_reveal": {"phrases": ["h"]},
            "glitch_peak": {"phrases": ["i"]},
            "mine": {"phrases": ["j"]},
        },
        "block_6": {"phrases": ["k"]},
        "block_7": {"part1": {"phrases": ["l"]}, "part2": {"phrases": ["m"]}},
    }


def _subtitles_payload() -> BlocksTokensPayload:
    def tok(text: str, ts: float, te: float) -> dict:
        return {"text": text, "t_start": ts, "t_end": te, "trailing": ""}

    obj = {
        "clip": {"start": 0.0, "end": 14.0},
        "block_1": {"phrase": "a", "tokens": [tok("a", 0.0, 1.0)]},
        "block_2": {
            "p1": {"phrase": "b", "tokens": [tok("b", 1.0, 2.0)]},
            "p2": {"phrase": "c", "tokens": [tok("c", 2.0, 3.0)]},
        },
        "block_3": {"phrase": "d", "tokens": [tok("d", 3.0, 4.0)]},
        "block_4": {
            "p1": {"phrase": "e", "tokens": [tok("e", 4.0, 5.0)]},
            "p2": {"phrase": "f", "tokens": [tok("f", 5.0, 6.0)]},
        },
        "block_5": {
            "slowly_in": {"phrase": "g", "tokens": [tok("g", 6.0, 7.0)]},
            "fast_reveal": {"phrase": "h", "tokens": [tok("h", 7.0, 8.0)]},
            "glitch_peak": {"phrase": "i", "tokens": [tok("i", 8.0, 9.0)]},
            "mine": {"phrase": "j", "tokens": [tok("j", 9.0, 10.0)]},
        },
        "block_6": {"phrase": "k", "tokens": [tok("k", 10.0, 11.0)]},
        "block_7": {
            "part1": {"phrase": "l", "tokens": [tok("l", 11.0, 12.0)]},
            "part2": {"phrase": "m", "tokens": [tok("m", 12.0, 14.0)]},
        },
    }
    return BlocksTokensPayload.model_validate(obj)


def test_invalid_timing_payload_fails_without_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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
                        "duration_sec": 15.0,
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
    monkeypatch.setenv("STAGE2_TIMING_MODE", "prompts")
    monkeypatch.setenv("STAGE2_FAST_START_SECONDS", "6")
    monkeypatch.setenv("FOOTAGE_INVENTORY_JSON", str(inv_path))
    monkeypatch.setenv("OUT_DIR", str(out_dir))
    monkeypatch.setenv("AUDIO_FILE_PATH", str(audio_path))
    monkeypatch.setenv("AUDIO_DIR", str(audio_path.parent))
    monkeypatch.setenv("JOB_ID", "job_invalid_timing")

    monkeypatch.setattr(go, "_make_client", lambda **kwargs: object())
    monkeypatch.setattr(go, "pick_audio_files", lambda _audio_dir: [audio_path])
    monkeypatch.setattr(go, "detect_bpm_librosa", lambda **kwargs: 120.0)

    monkeypatch.setattr(
        go,
        "call_stage1_asr_once",
        lambda **kwargs: Stage1AsrPayload.model_validate(
            {
                "transcript_words": [
                    {"text": "a", "t_start": 0.0, "t_end": 0.5},
                    {"text": "b", "t_start": 0.5, "t_end": 1.0},
                ],
                "srt_items": [],
            }
        ),
    )
    monkeypatch.setattr(
        go,
        "call_stage1_scenario_once",
        lambda **kwargs: Stage1ScenarioPayload.model_validate(
            {"audio": {"clip_start_abs": 0.0, "clip_end_abs": 14.0}, "draft_blocks": _draft_blocks()}
        ),
    )
    monkeypatch.setattr(go, "call_subtitles_plan_once", lambda **kwargs: _subtitles_payload())
    monkeypatch.setattr(
        go,
        "call_footage_style_once",
        lambda **kwargs: FootageStylePickPayload.model_validate({"genre": "Rock", "tag": "dark_forest"}),
    )
    monkeypatch.setattr(
        go,
        "call_timing_analysis_once",
        lambda **kwargs: Stage2TimingAnalysisPayload.model_validate(
            {
                "selected_rule": "Dynamic Contrast",
                "reason": "test",
                "raw_timings": {
                    "kick_bass": [0.5],
                    "snare_clap": [1.0],
                    "vocal_phrases": [2.0],
                    "semantic_peaks": [3.0],
                },
            }
        ),
    )
    monkeypatch.setattr(
        go,
        "call_timing_cuts_once",
        lambda **kwargs: Stage2TimingCutsPayload.model_validate(
            {
                "applied_rule": "Dynamic Contrast",
                "final_cut_timings": [0.1],  # violates min segment >= 0.3
            }
        ),
    )

    monkeypatch.setattr(
        go,
        "render_all_steps",
        lambda **kwargs: {
            "audio_plan": tmp_path / "audio_plan.json",
            "full_edit_config": tmp_path / "full_edit_config.json",
            "footage_config": tmp_path / "footage_config.json",
        },
    )

    with pytest.raises(ValueError, match="min segment"):
        go.build_all_via_gemini_one_call()

