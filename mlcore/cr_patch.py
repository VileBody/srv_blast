# mlcore/cr_patch.py
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

CR = "\r"
_WS = re.compile(r"\s+")
_WORDS = re.compile(r"\S+")

# Строго твои обязательные 2-строчные места
_PHRASE_TARGETS: Tuple[Tuple[str, ...], ...] = (
    ("block_1",),
    ("block_2", "p1"),
    ("block_2", "p2"),
    ("block_3",),
    ("block_4", "p2"),
    ("block_5", "glitch_peak"),
    ("block_6",),
    ("block_7", "part1"),
    ("block_7", "part2"),
)

def _clean_phrase(s: str) -> str:
    s = s.replace(CR, " ")
    s = _WS.sub(" ", s).strip()
    return s

def _words(s: str) -> List[str]:
    return _WORDS.findall(_clean_phrase(s))

def _char_len_no_spaces(s: str) -> int:
    return len(s.replace(" ", ""))

def _choose_break(words: List[str]) -> int:
    """
    k: line1=words[:k], line2=words[k:]
    Condition:
      A) len1 > len2 (char_len w/o spaces)
      OR
      B) line2 has exactly 1 word
    Prefer A with minimal positive diff, else B.
    """
    n = len(words)
    if n <= 1:
        return 0

    best_k: Optional[int] = None
    best_diff: Optional[int] = None

    for k in range(1, n - 1):
        l1 = " ".join(words[:k])
        l2 = " ".join(words[k:])
        diff = _char_len_no_spaces(l1) - _char_len_no_spaces(l2)
        if diff > 0 and (best_diff is None or diff < best_diff):
            best_k, best_diff = k, diff

    if best_k is not None:
        return best_k

    return n - 1  # B

def _sanitize_trailing(tokens: List[Dict[str, Any]]) -> None:
    # твои правила: trailing ∈ {" ", "\r", ""}, и последний токен trailing=""
    if not tokens:
        return
    for i, t in enumerate(tokens):
        tr = t.get("trailing", " ")
        if tr not in (" ", "\r", ""):
            tr = " "
        if tr == "" and i != len(tokens) - 1:
            tr = " "
        t["trailing"] = tr
    tokens[-1]["trailing"] = ""

def _remove_cr_from_tokens(tokens: List[Dict[str, Any]]) -> None:
    for t in tokens:
        if isinstance(t.get("text"), str) and CR in t["text"]:
            t["text"] = t["text"].replace(CR, "")
        if isinstance(t.get("trailing"), str) and CR in t["trailing"]:
            t["trailing"] = t["trailing"].replace(CR, "")

def _apply_two_line_phrase_and_trailing(seg: Dict[str, Any]) -> None:
    phrase = seg.get("phrase")
    tokens = seg.get("tokens")
    if not isinstance(phrase, str) or not isinstance(tokens, list) or not tokens:
        return
    if not all(isinstance(x, dict) for x in tokens):
        return

    _remove_cr_from_tokens(tokens)
    _sanitize_trailing(tokens)

    words = _words(phrase)
    if not words:
        seg["phrase"] = _clean_phrase(phrase)
        return

    k = _choose_break(words)

    if k == 0:
        # single-word edge: делаем "\rWORD" и ставим CR в trailing первого токена
        seg["phrase"] = CR + words[0]
        tokens[0]["trailing"] = CR
        _sanitize_trailing(tokens)
        return

    seg["phrase"] = " ".join(words[:k]) + CR + " ".join(words[k:])

    # 1 токен == 1 слово (в твоей модели так задумано)
    if 0 < k <= len(tokens):
        tokens[k - 1]["trailing"] = CR

    _sanitize_trailing(tokens)

def _get_path(d: Dict[str, Any], path: Tuple[str, ...]) -> Optional[Dict[str, Any]]:
    cur: Any = d
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur if isinstance(cur, dict) else None

def patch_payload_dict_inplace(d: Dict[str, Any]) -> Dict[str, Any]:
    for path in _PHRASE_TARGETS:
        seg = _get_path(d, path)
        if seg is not None:
            _apply_two_line_phrase_and_trailing(seg)
    return d
