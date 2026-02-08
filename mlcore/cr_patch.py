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

_LINE2_WEIGHT: float = 2.0
_MAX_TOP_LINE_CHARS: int = 18
_NON_FORCE_BREAK_TRIGGER: int = 16


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


def _weighted_len(words: List[str], *, line2_weight: float = _LINE2_WEIGHT) -> float:
    # visual proxy: line2 is 2x size, so weight chars accordingly
    return float(sum(len(w) for w in words)) * float(line2_weight)


def _choose_break(
    words: List[str],
    *,
    line2_weight: float = _LINE2_WEIGHT,
    force: bool = False,
    prefer_last_word: bool = True,
    max_top_chars: int = _MAX_TOP_LINE_CHARS,
) -> int:
    """
    Returns split index k where:
      line1 = words[:k]
      line2 = words[k:]

    Score is soft:
      - prefer keeping top visually wider than bottom*weight,
      - avoid very long top line,
      - keep weighted max compact,
      - optionally prefer last-word accent.
    If force=False, break is applied only when it improves readability enough.
    """
    n = len(words)
    if n <= 1:
        return 0

    single = _weighted_len(words, line2_weight=1.0)

    best_k = 0
    best_score: Optional[Tuple[int, int, float, float, int]] = None

    for k in range(1, n):
        l1 = _weighted_len(words[:k], line2_weight=1.0)
        l2 = _weighted_len(words[k:], line2_weight=line2_weight)
        ratio_ok = 1 if l1 > l2 else 0
        top_over = max(0.0, l1 - float(max_top_chars))
        weighted_max = max(l1, l2)
        balance = abs(l1 - l2)
        # Lower is better:
        # 1) prefer ratio_ok
        # 2) avoid top overflow
        # 3) reduce weighted max / imbalance
        # 4) slight preference for last-word accent
        last_penalty = 0 if (prefer_last_word and k == n - 1) else 1
        score = (0 if ratio_ok else 1, 0 if top_over == 0 else 1, top_over, weighted_max + 0.25 * balance, last_penalty)
        if best_score is None or score < best_score:
            best_score = score
            best_k = k

    if best_k == 0:
        return 0

    # Non-forced mode: only split if single line is long enough and split gives meaningful gain.
    if not force:
        l1_b = _weighted_len(words[:best_k], line2_weight=1.0)
        l2_b = _weighted_len(words[best_k:], line2_weight=line2_weight)
        best_weighted = max(l1_b, l2_b)
        if int(single) < _NON_FORCE_BREAK_TRIGGER:
            return 0
        # Require at least 10% reduction of weighted max or top overflow fix.
        top_over_single = max(0.0, single - float(max_top_chars))
        top_over_best = max(0.0, l1_b - float(max_top_chars))
        if (best_weighted > 0.90 * single) and not (top_over_best < top_over_single):
            return 0

    return best_k


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
        break_idx = _choose_break(
            words,
            line2_weight=_LINE2_WEIGHT,
            force=True,
            prefer_last_word=True,
            max_top_chars=_MAX_TOP_LINE_CHARS,
        )
        if break_idx > 0 and break_idx < len(words):
            words[break_idx] = _capitalize_word(words[break_idx])
    else:
        break_idx = _choose_break(
            words,
            line2_weight=_LINE2_WEIGHT,
            force=False,
            prefer_last_word=False,
            max_top_chars=_MAX_TOP_LINE_CHARS,
        )
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
