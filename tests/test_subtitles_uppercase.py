from __future__ import annotations

from mlcore.gemini_postprocess import sanitize_subtitles_dict_inplace


def _tok(text: str) -> dict:
    return {"text": text, "t_start": 0.0, "t_end": 1.0, "trailing": ""}


def test_subtitles_are_uppercased_in_postprocess() -> None:
    d = {
        "clip": {"start": 0.0, "end": 14.0},
        "block_1": {"phrase": "Abв", "tokens": [_tok("heLLo")]},
        "block_2": {
            "p1": {"phrase": "cdЕ", "tokens": [_tok("worLd")]},
            "p2": {"phrase": "fgЖ", "tokens": [_tok("mixEd")]},
        },
        "block_3": {"phrase": "hи", "tokens": [_tok("text")]},
        "block_4": {
            "p1": {"phrase": "jkЛ", "tokens": [_tok("sub")]},
            "p2": {"phrase": "mnМ", "tokens": [_tok("title")]},
        },
        "block_5": {
            "slowly_in": {"phrase": "opН", "tokens": [_tok("alpha")]},
            "fast_reveal": {"phrase": "qrО", "tokens": [_tok("beta")]},
            "glitch_peak": {"phrase": "stП", "tokens": [_tok("gamma")]},
            "mine": {"phrase": "uvР", "tokens": [_tok("mine")]},
        },
        "block_6": {"phrase": "wxС", "tokens": [_tok("delta")]},
        "block_7": {
            "part1": {"phrase": "yzТ", "tokens": [_tok("omega")]},
            "part2": {"phrase": "abУ", "tokens": [_tok("final")]},
        },
    }

    sanitize_subtitles_dict_inplace(d)

    assert d["block_1"]["phrase"] == "HELLO"
    assert d["block_1"]["tokens"][0]["text"] == "HELLO"
    assert d["block_5"]["mine"]["tokens"][0]["text"] == "MINE"
    assert d["block_7"]["part2"]["phrase"] == "FINAL"
