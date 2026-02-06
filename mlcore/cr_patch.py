# mlcore/cr_patch.py
from __future__ import annotations

import re
import unicodedata
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


def _strip_punct_and_spaces(s: str) -> str:
    out: List[str] = []
    for ch in str(s or ""):
        if ch in {" ", "\t", "\n", "\r"}:
            continue
        cat = unicodedata.category(ch)
        if cat.startswith("P") or cat.startswith("S"):
            continue
        out.append(ch)
    return "".join(out)


def _normalize_word_text(s: str) -> str:
    return _strip_punct_and_spaces(s)


def _capitalize_word(s: str) -> str:
    if not s:
        return s
    return s[0].upper() + s[1:]


def _sanitize_trailing(tokens: List[Dict[str, Any]]) -> None:
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


def _filtered_tokens(tokens: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for t in tokens:
        if not isinstance(t, dict):
            continue
        txt = _normalize_word_text(t.get("text", ""))
        if not txt:
            continue
        out.append(
            {
                "text": txt,
                "t_start": float(t.get("t_start", 0.0)),
                "t_end": float(t.get("t_end", 0.0)),
                "trailing": str(t.get("trailing", " ")),
            }
        )
    if not out:
        # fallback: keep one minimal token to avoid exploding schema validators upstream
        out = [{"text": "X", "t_start": 0.0, "t_end": 0.001, "trailing": ""}]
    _sanitize_trailing(out)
    return out


def _weighted_len(words: List[str], *, line2_weight: float = 2.0) -> float:
    # visual proxy: line2 is 2x size, so weight chars accordingly
    return float(sum(len(w) for w in words)) * float(line2_weight)


def _choose_break(words: List[str], *, line2_weight: float = 2.0) -> int:
    """
    Returns split index k where:
      line1 = words[:k]
      line2 = words[k:]

    Valid split must satisfy "top line visually wider than bottom line":
      len(line1) > len(line2) * line2_weight

    Preference:
      1) if last-word accent (k=n-1) is valid, use it
      2) otherwise use any valid k with minimal positive width diff
      3) if no valid k -> no break (k=0)
    """
    n = len(words)
    if n <= 1:
        return 0

    if n == 2:
        l1 = _weighted_len(words[:1], line2_weight=1.0)
        l2 = _weighted_len(words[1:], line2_weight=line2_weight)
        return 1 if l1 > l2 else 0

    # Prefer "last word accent" when valid
    k_last = n - 1
    l1_last = _weighted_len(words[:k_last], line2_weight=1.0)
    l2_last = _weighted_len(words[k_last:], line2_weight=line2_weight)
    if l1_last > l2_last:
        return k_last

    best_k: Optional[int] = None
    best_diff: Optional[float] = None

    for k in range(1, n):
        l1 = _weighted_len(words[:k], line2_weight=1.0)
        l2 = _weighted_len(words[k:], line2_weight=line2_weight)
        diff = l1 - l2
        if diff > 0 and (best_diff is None or diff < best_diff):
            best_k, best_diff = k, diff

    if best_k is not None:
        return best_k

    return 0


def _set_phrase_and_trailing(tokens: List[Dict[str, Any]], *, break_idx: int = 0) -> str:
    for i, t in enumerate(tokens):
        t["trailing"] = " " if i < len(tokens) - 1 else ""
    if break_idx > 0 and break_idx <= len(tokens) - 1:
        tokens[break_idx - 1]["trailing"] = CR
    _sanitize_trailing(tokens)
    return "".join(str(t["text"]) + str(t["trailing"]) for t in tokens)


def normalize_segment_inplace(seg: Dict[str, Any], *, force_two_line: bool, mine_mode: bool = False) -> None:
    tokens = seg.get("tokens")
    if not isinstance(tokens, list):
        return
    tok = _filtered_tokens(tokens)

    if mine_mode:
        t0 = tok[0]
        t0["trailing"] = ""
        seg["tokens"] = [t0]
        seg["phrase"] = str(t0["text"])
        return

    words = [str(t["text"]) for t in tok]
    if not words:
        return

    # capitalization at start of phrase / line
    words[0] = _capitalize_word(words[0])

    break_idx = 0
    if force_two_line:
        break_idx = _choose_break(words, line2_weight=2.0)
        if break_idx > 0 and break_idx < len(words):
            words[break_idx] = _capitalize_word(words[break_idx])

    for i, w in enumerate(words):
        tok[i]["text"] = w

    seg["phrase"] = _set_phrase_and_trailing(tok, break_idx=break_idx)
    seg["tokens"] = tok

def _get_path(d: Dict[str, Any], path: Tuple[str, ...]) -> Optional[Dict[str, Any]]:
    cur: Any = d
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur if isinstance(cur, dict) else None

def patch_payload_dict_inplace(d: Dict[str, Any]) -> Dict[str, Any]:
    # Regular segments
    for path in _PHRASE_TARGETS:
        seg = _get_path(d, path)
        if seg is not None:
            normalize_segment_inplace(seg, force_two_line=True, mine_mode=False)

    # Mine (single-word, no auto two-line)
    b5 = d.get("block_5")
    if isinstance(b5, dict) and isinstance(b5.get("mine"), dict):
        normalize_segment_inplace(b5["mine"], force_two_line=False, mine_mode=True)

    # Any other known segment paths not listed above -> one-line deterministic normalize
    for path in (
        ("block_4", "p1"),
        ("block_5", "slowly_in"),
        ("block_5", "fast_reveal"),
    ):
        seg = _get_path(d, path)
        if seg is not None:
            normalize_segment_inplace(seg, force_two_line=False, mine_mode=False)

    return d
