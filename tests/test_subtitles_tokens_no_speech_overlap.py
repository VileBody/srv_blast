from __future__ import annotations

import pytest

from mlcore.models.subtitles_tokens import BlocksTokensPayload


def _tok(text: str, t0: float, t1: float, trailing: str = " ") -> dict:
    return {"text": text, "t_start": t0, "t_end": t1, "trailing": trailing}


def _seg(text: str, t0: float, t1: float) -> dict:
    return {"phrase": text, "tokens": [_tok(text, t0, t1)]}


def _base_payload() -> dict:
    return {
        "clip": {"start": 0.0, "end": 13.0},
        "block_1": _seg("A", 0.0, 1.0),
        "block_2": {"p1": _seg("B", 1.0, 2.0), "p2": _seg("C", 2.0, 3.0)},
        "block_3": _seg("D", 3.0, 4.0),
        "block_4": {"p1": _seg("E", 4.0, 5.0), "p2": _seg("F", 5.0, 6.0)},
        "block_5": {
            "slowly_in": _seg("G", 6.0, 7.0),
            "fast_reveal": _seg("H", 7.0, 8.0),
            "glitch_peak": _seg("I", 8.0, 9.0),
            "mine": {
                "phrase": "\rMINE",
                "tokens": [_tok("MINE", 9.0, 9.3, trailing="")],
            },
        },
        "block_6": _seg("J", 9.3, 10.2),
        "block_7": {"part1": _seg("K", 10.2, 11.0), "part2": _seg("L", 11.0, 11.9)},
    }


def test_allows_no_speech_overlap_for_block5_mine_and_glitch_peak() -> None:
    payload = _base_payload()
    payload["block_5"]["glitch_peak"] = {
        "phrase": "[NO_SPEECH]",
        "tokens": [_tok("[NO_SPEECH]", 0.0, 7.5)],
    }
    payload["block_5"]["mine"] = {
        "phrase": "\r[NO_SPEECH]",
        "tokens": [_tok("[NO_SPEECH]", 0.0, 7.5, trailing="")],
    }

    out = BlocksTokensPayload.model_validate(payload)
    assert out.block_5.mine.tokens[0].text == "[NO_SPEECH]"


def test_keeps_overlap_error_for_non_no_speech_tokens() -> None:
    payload = _base_payload()
    payload["block_5"]["glitch_peak"] = _seg("MINE", 8.0, 9.0)
    payload["block_5"]["mine"] = {
        "phrase": "\rMINE",
        "tokens": [_tok("MINE", 8.0, 9.0, trailing="")],
    }

    with pytest.raises(ValueError, match="mine token must NOT overlap"):
        BlocksTokensPayload.model_validate(payload)
