from __future__ import annotations

import math
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mlcore.gemini_postprocess import render_all_steps
from mlcore.models.full_plan import FullPlanPayload


def _tok(text: str, t_start: float, t_end: float, trailing: str) -> dict:
    return {"text": text, "t_start": t_start, "t_end": t_end, "trailing": trailing}


def _assert_close(a: float, b: float, *, abs_tol: float = 1e-6) -> None:
    assert math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=float(abs_tol)), (a, b)


@contextmanager
def _temp_environ(**kwargs: str) -> None:
    old = {k: os.environ.get(k) for k in kwargs}
    try:
        for k, v in kwargs.items():
            os.environ[str(k)] = str(v)
        yield
    finally:
        for k, prev in old.items():
            if prev is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = prev


def test_stage3_uses_stage2_subtitles_clip_window() -> None:
    repo_root = Path(__file__).resolve().parent.parent

    with tempfile.TemporaryDirectory() as td, _temp_environ(MODE="dev"):
        tmp_path = Path(td)

        dummy_audio = tmp_path / "audio.mp3"
        dummy_audio.write_bytes(b"fake")

        with _temp_environ(AUDIO_FILE_PATH=str(dummy_audio), AUDIO_DIR=str(dummy_audio.parent)):
            subs_clip_start = 100.0
            subs_clip_end = 115.0

            subtitles = {
                "clip": {"start": subs_clip_start, "end": subs_clip_end},
                "block_1": {
                    "phrase": "b1",
                    "tokens": [
                        _tok("a", 100.0, 100.5, " "),
                        _tok("b", 100.5, 101.0, ""),
                    ],
                },
                "block_2": {
                    "p1": {
                        "phrase": "b2p1",
                        "tokens": [
                            _tok("c", 101.0, 101.5, "\r"),
                            _tok("d", 101.5, 102.0, ""),
                        ],
                    },
                    "p2": {
                        "phrase": "b2p2",
                        "tokens": [
                            _tok("e", 102.0, 102.5, " "),
                            _tok("f", 102.5, 103.0, ""),
                        ],
                    },
                },
                "block_3": {"phrase": "b3", "tokens": [_tok("g", 103.0, 104.0, "")]},
                "block_4": {
                    "p1": {"phrase": "b4p1", "tokens": [_tok("h", 104.0, 105.0, "")]},
                    "p2": {"phrase": "b4p2", "tokens": [_tok("i", 105.0, 106.0, "")]},
                },
                "block_5": {
                    "slowly_in": {"phrase": "s", "tokens": [_tok("j", 106.0, 107.0, "")]},
                    "fast_reveal": {"phrase": "f", "tokens": [_tok("k", 107.0, 108.0, "")]},
                    "glitch_peak": {"phrase": "g", "tokens": [_tok("l", 108.0, 109.0, "")]},
                    "mine": {"phrase": "m", "tokens": [_tok("m", 109.0, 109.5, "")]},
                },
                "block_6": {"phrase": "b6", "tokens": [_tok("n", 109.5, 111.0, "")]},
                "block_7": {
                    "part1": {"phrase": "p1", "tokens": [_tok("o", 111.0, 112.0, "")]},
                    # last token ends at 114.0 => clip-zero last end is 14.0 => comp_dur == 15.0
                    "part2": {"phrase": "p2", "tokens": [_tok("p", 112.0, 114.0, "")]},
                },
            }

            footage = {
                "clips": [
                    {
                        "file_name": "clip1.mp4",
                        "fit_mode": "cover",
                        "in_point": subs_clip_start,
                        "out_point": subs_clip_end,
                        "start_time": subs_clip_start,
                    }
                ],
                "allow_gaps": False,
            }

            # Intentionally mismatch Stage1 audio window vs Stage2 clip window:
            # Stage3 must follow subtitles.clip.*.
            plan = FullPlanPayload.model_validate(
                {
                    "audio": {"clip_start_abs": 0.0, "clip_end_abs": 15.0, "moment_of_interest_sec": 0.0},
                    "subtitles": subtitles,
                    "footage": footage,
                }
            )

            inv = {
                "assets": [
                    {"file_name": "clip1.mp4", "file_path": "/tmp/clip1.mp4", "src_w": 720, "src_h": 1280},
                ],
                "adjustment_preset": {
                    "id": "ADJ_LAYER_16",
                    "name": "Adjustment Layer 16",
                    "dump_file": "data/0_4.504505__Adjustment Layer 16__adjustment.json",
                    "time_warp_mode": "pin_edges_v1",
                },
            }
            inv_path = tmp_path / "inv.json"
            inv_path.write_text(json.dumps(inv, ensure_ascii=False), encoding="utf-8")

            out_dir = tmp_path / "out"
            data_dir = tmp_path / "data"
            render_all_steps(
                repo_root=repo_root,
                plan=plan,
                footage_inventory_json=inv_path,
                out_dir=out_dir,
                data_dir=data_dir,
            )

            audio_plan = json.loads((out_dir / "audio_plan.json").read_text(encoding="utf-8"))
            _assert_close(audio_plan["audio"]["clip_start_abs"], subs_clip_start)
            _assert_close(audio_plan["audio"]["clip_end_abs"], subs_clip_end)
            _assert_close(audio_plan["audio"]["layer_start_time"], -subs_clip_start)


if __name__ == "__main__":
    test_stage3_uses_stage2_subtitles_clip_window()
    print("OK")
