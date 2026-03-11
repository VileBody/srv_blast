from __future__ import annotations

from mlcore.prompts.assemble import _timing_semantic_context_from_subtitles


def test_timing_semantic_context_supports_tagged_subtitles() -> None:
    ctx = _timing_semantic_context_from_subtitles(
        {
            "subtitles": [
                {"text": "второй", "tag": "short", "in": 2.0, "out": 2.4},
                {"text": "первый", "tag": "long", "in": 1.0, "out": 1.8},
            ]
        }
    )
    segs = list(ctx["segments"])
    assert len(segs) == 2
    assert segs[0]["where"] == "subtitle[1]"
    assert segs[0]["phrase"] == "первый"
    assert segs[0]["tag"] == "long"
    assert float(segs[0]["start_abs"]) == 1.0
    assert segs[1]["where"] == "subtitle[0]"
    assert segs[1]["tag"] == "short"

