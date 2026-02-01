# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Tuple
from app.config import (
    STYLE_RULE_DEFAULT,
    STYLE_RULE_DUAL_OUTLINE,
    STYLE_RULE_MINE_INNER,
)

StyleRuleName = Literal["break_after_r", "dual_outline", "mine_inner"]


@dataclass(frozen=True)
class CharStyle:
    i: int
    font: str
    fontSize: float
    fauxItalic: Optional[bool] = None

    def as_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"i": self.i, "font": self.font, "fontSize": self.fontSize}
        if self.fauxItalic is not None:
            d["fauxItalic"] = bool(self.fauxItalic)
        return d


def _break_idx(phrase: str) -> int:
    """Index of \\r. If absent, returns -1."""
    return phrase.find("\r")


def char_styles_break_after_r(
    phrase: str,
    font_before: str = "Point-SemiBold",
    size_before: float = 100,
    font_after: str = "Point-ExtraBold",
    size_after: float = 200,
    include_break_char_in_before: bool = True,
) -> List[Dict[str, Any]]:
    """
    Default global rule:
      - before '\\r': SemiBold 100
      - after '\\r': ExtraBold 200
    Option: whether '\\r' itself belongs to "before" (default yes; matches common AE intuition).
    """
    bi = _break_idx(phrase)
    out: List[Dict[str, Any]] = []
    for i, _ch in enumerate(phrase):
        after = False
        if bi != -1:
            if include_break_char_in_before:
                after = i > bi
            else:
                after = i >= bi
        if after:
            out.append(CharStyle(i=i, font=font_after, fontSize=size_after).as_dict())
        else:
            out.append(CharStyle(i=i, font=font_before, fontSize=size_before).as_dict())
    return out


def apply_style_rule(
    phrase: str,
    rule: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Entry point.
    Allowed rules:
      - break_after_r (default)
      - dual_outline  (still uses break_after_r; rule exists for semantic routing)
      - mine_inner    (special for Mine inner text: no break, just SemiBold 100)
    """
    r = rule or STYLE_RULE_DEFAULT

    if r == STYLE_RULE_MINE_INNER or r == "mine_inner":
        # Mine inner is a single-word text; keep it simple and stable
        return char_styles_break_after_r(
            phrase=phrase,
            font_before="Point-SemiBold",
            size_before=100,
            font_after="Point-SemiBold",
            size_after=100,
            include_break_char_in_before=True,
        )

    if r == STYLE_RULE_DUAL_OUTLINE or r == "dual_outline":
        # Style is still break_after_r; "dual_outline" affects TEXT_BASE (fill/stroke),
        # not char-styles.
        return char_styles_break_after_r(phrase)

    # default
    return char_styles_break_after_r(phrase)
