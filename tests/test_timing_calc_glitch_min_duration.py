from __future__ import annotations

from mlcore.models.subtitles_tokens import BlocksTokensPayload
from mlcore.timing_calc import compute_timings


def _tok(text: str, t_start: float, t_end: float, trailing: str = " ") -> dict:
    return {"text": text, "t_start": t_start, "t_end": t_end, "trailing": trailing}


def _seg(text: str, t_start: float, t_end: float) -> dict:
    return {"phrase": text, "tokens": [_tok(text, t_start, t_end, "")]}


def test_glitch_peak_timing_is_never_zero_when_mine_starts_at_peak_start() -> None:
    payload = BlocksTokensPayload.model_validate(
        {
            "clip": {"start": 0.0, "end": 14.0},
            "block_1": {"phrase": "b1", "tokens": [_tok("b1", 0.1, 0.5)]},
            "block_2": {
                "p1": _seg("b2p1", 0.6, 1.0),
                "p2": _seg("b2p2", 1.1, 1.5),
            },
            "block_3": {"phrase": "b3", "tokens": [_tok("b3", 1.6, 2.0)]},
            "block_4": {
                "p1": _seg("b4p1", 2.1, 2.5),
                "p2": _seg("b4p2", 2.6, 3.0),
            },
            "block_5": {
                "slowly_in": _seg("slow", 3.1, 3.6),
                "fast_reveal": _seg("fast", 3.7, 4.2),
                # glitch_peak start marker = token end = 8.5
                "glitch_peak": _seg("peak", 8.0, 8.5),
                # mine starts exactly at 8.5 -> previous code produced zero window for glitch_peak
                "mine": {
                    "phrase": "MINE",
                    "tokens": [_tok("MINE", 8.5, 9.0, "")],
                },
            },
            "block_6": {"phrase": "b6", "tokens": [_tok("b6", 9.1, 10.0)]},
            "block_7": {
                "part1": _seg("b7p1", 10.1, 11.0),
                "part2": _seg("b7p2", 11.1, 13.0),
            },
        }
    )

    timings, _ = compute_timings(payload)
    b5 = timings["block_5"]
    g = b5.glitch_peak  # type: ignore[attr-defined]

    assert float(g.out_p) > float(g.in_p)
