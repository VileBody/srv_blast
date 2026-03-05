from __future__ import annotations

import json
import os
from pathlib import Path

from mlcore import gemini_orchestrator as go
from mlcore.models.footage_style import FootageStylePickPayload
from mlcore.models.stage1_asr import Stage1AsrPayload
from mlcore.models.stage1_scenario import Stage1ScenarioPayload
from mlcore.models.subtitles_tokens import BlocksTokensPayload


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


def test_with_gemini_stage2_style_and_deterministic_picker(monkeypatch, tmp_path: Path) -> None:
    audio_path = tmp_path / "audio.mp3"
    audio_path.write_bytes(b"fake")

    inv_path = tmp_path / "inventory.json"
    inv = {
        "assets": [
            {
                "file_name": "f1.mp4",
                "file_path": "s3://bucket/pinterest_collection/Rock/dark_forest/f1.mp4",
                "src_w": 720,
                "src_h": 1280,
                "duration_sec": 4.0,
                "genre": "Rock",
                "tag": "dark_forest",
            },
            {
                "file_name": "f2.mp4",
                "file_path": "s3://bucket/pinterest_collection/Rock/dark_forest/f2.mp4",
                "src_w": 720,
                "src_h": 1280,
                "duration_sec": 3.0,
                "genre": "Rock",
                "tag": "dark_forest",
            },
            {
                "file_name": "f3.mp4",
                "file_path": "s3://bucket/pinterest_collection/Rock/rain_aesthetic/f3.mp4",
                "src_w": 720,
                "src_h": 1280,
                "duration_sec": 5.0,
                "genre": "Rock",
                "tag": "rain_aesthetic",
            },
        ]
    }
    inv_path.write_text(json.dumps(inv, ensure_ascii=False), encoding="utf-8")

    out_dir = tmp_path / "out"
    logs_dir = out_dir / "logs"

    monkeypatch.setenv("MODE", "dev")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GEMINI_MODEL_STAGE1", "m1")
    monkeypatch.setenv("GEMINI_MODEL_SUBTITLES", "m2")
    monkeypatch.setenv("GEMINI_MODEL_FOOTAGE", "m3")
    monkeypatch.setenv("FOOTAGE_INVENTORY_JSON", str(inv_path))
    monkeypatch.setenv("OUT_DIR", str(out_dir))
    monkeypatch.setenv("AUDIO_FILE_PATH", str(audio_path))
    monkeypatch.setenv("AUDIO_DIR", str(audio_path.parent))
    monkeypatch.setenv("JOB_ID", "job_123")

    monkeypatch.setattr(go, "_make_client", lambda **kwargs: object())
    monkeypatch.setattr(go, "pick_audio_files", lambda _audio_dir: [audio_path])

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

    assert "plan" in captured
    plan = captured["plan"]
    clips = sorted(plan.footage.clips, key=lambda c: float(c.in_point))
    assert clips
    assert abs(float(clips[0].in_point) - 0.0) <= 1e-6
    assert abs(float(clips[-1].out_point) - 14.0) <= 1e-6
    for i in range(len(clips) - 1):
        assert abs(float(clips[i].out_point) - float(clips[i + 1].in_point)) <= 1e-6

    style_latest = logs_dir / "stage2_style.json"
    assert style_latest.exists()
    style_obj = json.loads(style_latest.read_text(encoding="utf-8"))
    assert style_obj == {"genre": "Rock", "tag": "dark_forest"}

    assert os.environ.get("JOB_ID") == "job_123"


def test_hedged_mode_wires_openrouter_for_all_stage_calls(monkeypatch, tmp_path: Path) -> None:
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
    monkeypatch.setenv("GEMINI_API_KEY", "gk")
    monkeypatch.setenv("OPENROUTER_API_KEY", "ork")
    monkeypatch.setenv("LLM_PROVIDER_MODE", "hedged")
    monkeypatch.setenv("LLM_HEDGE_DELAY_S", "60")
    monkeypatch.setenv("GEMINI_MODEL_STAGE1", "gemini-2.5-pro")
    monkeypatch.setenv("GEMINI_MODEL_SUBTITLES", "gemini-3-pro-preview")
    monkeypatch.setenv("GEMINI_MODEL_FOOTAGE", "gemini-3-flash-preview")
    monkeypatch.setenv("FOOTAGE_INVENTORY_JSON", str(inv_path))
    monkeypatch.setenv("OUT_DIR", str(out_dir))
    monkeypatch.setenv("AUDIO_FILE_PATH", str(audio_path))
    monkeypatch.setenv("AUDIO_DIR", str(audio_path.parent))
    monkeypatch.setenv("JOB_ID", "job_hedged")

    gemini_clients: list[object] = []
    openrouter_clients: list[object] = []

    def _mk_gemini(**kwargs):
        c = object()
        gemini_clients.append(c)
        return c

    def _mk_openrouter(**kwargs):
        c = object()
        openrouter_clients.append(c)
        return c

    monkeypatch.setattr(go, "_make_client", _mk_gemini)
    monkeypatch.setattr(go, "_make_openrouter_client", _mk_openrouter)
    monkeypatch.setattr(go, "pick_audio_files", lambda _audio_dir: [audio_path])

    seen: dict[str, dict] = {}

    def _remember(name: str, kwargs: dict) -> None:
        seen[name] = kwargs
        assert kwargs["provider_mode"] == "hedged"
        assert float(kwargs["hedge_delay_s"]) == 60.0
        assert kwargs["openrouter_client"] is not None
        assert kwargs["client"] is not None

    def _asr(**kwargs):
        _remember("asr", kwargs)
        return Stage1AsrPayload.model_validate(
            {
                "transcript_words": [
                    {"text": "a", "t_start": 0.0, "t_end": 0.5},
                    {"text": "b", "t_start": 0.5, "t_end": 1.0},
                ],
                "srt_items": [],
            }
        )

    def _scenario(**kwargs):
        _remember("scenario", kwargs)
        return Stage1ScenarioPayload.model_validate(
            {"audio": {"clip_start_abs": 0.0, "clip_end_abs": 14.0}, "draft_blocks": _draft_blocks()}
        )

    def _subs(**kwargs):
        _remember("subtitles", kwargs)
        return _subtitles_payload()

    def _style(**kwargs):
        _remember("style", kwargs)
        return FootageStylePickPayload.model_validate({"genre": "Rock", "tag": "dark_forest"})

    monkeypatch.setattr(go, "call_stage1_asr_once", _asr)
    monkeypatch.setattr(go, "call_stage1_scenario_once", _scenario)
    monkeypatch.setattr(go, "call_subtitles_plan_once", _subs)
    monkeypatch.setattr(go, "call_footage_style_once", _style)

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
    assert set(seen.keys()) == {"asr", "scenario", "subtitles", "style"}
    assert len(gemini_clients) == 4
    assert len(openrouter_clients) == 4


def test_resume_state_skips_stage1_llm_calls(monkeypatch, tmp_path: Path) -> None:
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
    resume_state_path = tmp_path / "llm_resume_state.json"

    stage1_asr = Stage1AsrPayload.model_validate(
        {
            "transcript_words": [
                {"text": "a", "t_start": 0.0, "t_end": 0.5},
                {"text": "b", "t_start": 0.5, "t_end": 1.0},
            ],
            "srt_items": [],
        }
    )
    stage1_plan = {
        "audio": {"clip_start_abs": 0.0, "clip_end_abs": 14.0},
        "transcript_words": stage1_asr.model_dump(mode="json")["transcript_words"],
        "draft_blocks": _draft_blocks(),
    }
    resume_state_path.write_text(
        json.dumps(
            {
                "stage1_asr": stage1_asr.model_dump(mode="json"),
                "stage1_plan": stage1_plan,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("MODE", "dev")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GEMINI_MODEL_STAGE1", "m1")
    monkeypatch.setenv("GEMINI_MODEL_SUBTITLES", "m2")
    monkeypatch.setenv("GEMINI_MODEL_FOOTAGE", "m3")
    monkeypatch.setenv("FOOTAGE_INVENTORY_JSON", str(inv_path))
    monkeypatch.setenv("OUT_DIR", str(out_dir))
    monkeypatch.setenv("AUDIO_FILE_PATH", str(audio_path))
    monkeypatch.setenv("AUDIO_DIR", str(audio_path.parent))
    monkeypatch.setenv("JOB_ID", "job_resume")

    monkeypatch.setattr(go, "_make_client", lambda **kwargs: object())
    monkeypatch.setattr(go, "pick_audio_files", lambda _audio_dir: [audio_path])

    def _should_not_call(**kwargs):
        raise AssertionError("stage1 call must be skipped from resume state")

    monkeypatch.setattr(go, "call_stage1_asr_once", _should_not_call)
    monkeypatch.setattr(go, "call_stage1_scenario_once", _should_not_call)

    calls = {"subtitles": 0, "style": 0}

    def _subs(**kwargs):
        calls["subtitles"] += 1
        return _subtitles_payload()

    def _style(**kwargs):
        calls["style"] += 1
        return FootageStylePickPayload.model_validate({"genre": "Rock", "tag": "dark_forest"})

    monkeypatch.setattr(go, "call_subtitles_plan_once", _subs)
    monkeypatch.setattr(go, "call_footage_style_once", _style)
    monkeypatch.setattr(
        go,
        "render_all_steps",
        lambda **kwargs: {
            "audio_plan": tmp_path / "audio_plan.json",
            "full_edit_config": tmp_path / "full_edit_config.json",
            "footage_config": tmp_path / "footage_config.json",
        },
    )

    out = go.build_all_via_gemini_one_call(resume_state_path=resume_state_path)
    assert set(out.keys()) == {"audio_plan", "full_edit_config", "footage_config"}
    assert calls == {"subtitles": 1, "style": 1}

    state_after = json.loads(resume_state_path.read_text(encoding="utf-8"))
    assert "stage2_subtitles" in state_after
    assert "stage2_style" in state_after
