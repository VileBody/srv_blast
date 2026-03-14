from __future__ import annotations

from mlcore import gemini_orchestrator as go


def test_reference_words_ignore_structural_tags() -> None:
    words = go._reference_words_from_user_text(
        "[bridge] вернусь [pause], обратно [hook] только лишь с тобой"
    )
    assert words == ["вернусь", "обратно", "только", "лишь", "с", "тобой"]


def test_strip_structural_tags_from_text() -> None:
    cleaned, dropped = go._strip_structural_tags_from_text(
        "мы [pause] станем [bridge], чужими"
    )
    assert dropped == 2
    assert cleaned == "мы станем чужими"
