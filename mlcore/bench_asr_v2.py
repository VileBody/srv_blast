from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, List, Sequence, Tuple

from mlcore.models.stage1_forced_alignment import (
    ForcedAlignedWord,
    Stage1ForcedAlignmentPayload,
)

_RE_LEFT_TRIM = re.compile(r"^[^\w]+", flags=re.UNICODE)
_RE_RIGHT_TRIM = re.compile(r"[^\w]+$", flags=re.UNICODE)


def normalize_word(token: str) -> str:
    t = str(token or "").lower().replace("ё", "е")
    t = _RE_LEFT_TRIM.sub("", t)
    t = _RE_RIGHT_TRIM.sub("", t)
    return t


def normalized_words_from_text(text: str) -> List[str]:
    out: List[str] = []
    for raw in str(text or "").split():
        w = normalize_word(raw)
        if w:
            out.append(w)
    return out


@dataclass(frozen=True)
class BestSubsequenceAlignment:
    start_idx: int
    end_idx: int
    substitutions: int
    deletions: int
    insertions: int

    @property
    def distance(self) -> int:
        return int(self.substitutions + self.deletions + self.insertions)


def _sdi_from_ops(ops: Sequence[str]) -> Tuple[int, int, int]:
    s = 0
    d = 0
    i = 0
    for op in ops:
        if op == "S":
            s += 1
        elif op == "D":
            d += 1
        elif op == "I":
            i += 1
    return s, d, i


def _global_alignment_ops(reference: Sequence[str], hypothesis: Sequence[str]) -> List[str]:
    n = len(reference)
    m = len(hypothesis)

    dp: List[List[int]] = [[0] * (m + 1) for _ in range(n + 1)]
    back: List[List[str]] = [[""] * (m + 1) for _ in range(n + 1)]

    for x in range(1, n + 1):
        dp[x][0] = x
        back[x][0] = "D"
    for y in range(1, m + 1):
        dp[0][y] = y
        back[0][y] = "I"

    for x in range(1, n + 1):
        rw = reference[x - 1]
        for y in range(1, m + 1):
            hw = hypothesis[y - 1]
            sub_cost = 0 if rw == hw else 1
            c_sub = dp[x - 1][y - 1] + sub_cost
            c_del = dp[x - 1][y] + 1
            c_ins = dp[x][y - 1] + 1
            best = min(c_sub, c_del, c_ins)
            dp[x][y] = best
            if best == c_sub:
                back[x][y] = "M" if sub_cost == 0 else "S"
            elif best == c_del:
                back[x][y] = "D"
            else:
                back[x][y] = "I"

    ops_rev: List[str] = []
    x = n
    y = m
    while x > 0 or y > 0:
        op = back[x][y]
        if not op:
            break
        ops_rev.append(op)
        if op in {"M", "S"}:
            x -= 1
            y -= 1
        elif op == "D":
            x -= 1
        elif op == "I":
            y -= 1
        else:
            raise RuntimeError(f"unexpected alignment op: {op!r}")

    ops_rev.reverse()
    return ops_rev


def compute_global_sdi(reference: Sequence[str], hypothesis: Sequence[str]) -> Tuple[int, int, int]:
    ops = _global_alignment_ops(reference, hypothesis)
    return _sdi_from_ops(ops)


def best_subsequence_alignment(reference: Sequence[str], hypothesis: Sequence[str]) -> BestSubsequenceAlignment:
    n = len(reference)
    m = len(hypothesis)
    if n <= 0:
        raise ValueError("reference words are empty")
    if m <= 0:
        return BestSubsequenceAlignment(
            start_idx=0,
            end_idx=-1,
            substitutions=0,
            deletions=n,
            insertions=0,
        )

    # Semiglobal edit distance: free gaps at beginning/end of hypothesis.
    dp: List[List[int]] = [[0] * (m + 1) for _ in range(n + 1)]
    back: List[List[str]] = [[""] * (m + 1) for _ in range(n + 1)]

    for y in range(1, m + 1):
        dp[0][y] = 0
        back[0][y] = "F"  # free-prefix move on hypothesis axis
    for x in range(1, n + 1):
        dp[x][0] = x
        back[x][0] = "D"

    for x in range(1, n + 1):
        rw = reference[x - 1]
        for y in range(1, m + 1):
            hw = hypothesis[y - 1]
            sub_cost = 0 if rw == hw else 1
            c_sub = dp[x - 1][y - 1] + sub_cost
            c_del = dp[x - 1][y] + 1
            c_ins = dp[x][y - 1] + 1
            best = min(c_sub, c_del, c_ins)
            dp[x][y] = best
            if best == c_sub:
                back[x][y] = "M" if sub_cost == 0 else "S"
            elif best == c_del:
                back[x][y] = "D"
            else:
                back[x][y] = "I"

    y_end = 0
    best_score = dp[n][0]
    for y in range(1, m + 1):
        score = dp[n][y]
        if score < best_score or (score == best_score and y < y_end):
            best_score = score
            y_end = y

    ops_rev: List[str] = []
    x = n
    y = y_end
    while x > 0:
        op = back[x][y]
        if not op:
            raise RuntimeError("alignment backtrace failed (empty op)")
        ops_rev.append(op)
        if op in {"M", "S"}:
            x -= 1
            y -= 1
        elif op == "D":
            x -= 1
        elif op == "I":
            y -= 1
        else:
            raise RuntimeError(f"unexpected alignment op: {op!r}")

    y_start = y
    y_last = y_end - 1
    ops_rev.reverse()
    s, d, i = _sdi_from_ops(ops_rev)
    if y_last < y_start:
        y_start = 0
        y_last = -1
    return BestSubsequenceAlignment(
        start_idx=y_start,
        end_idx=y_last,
        substitutions=s,
        deletions=d,
        insertions=i,
    )


def validate_forced_alignment_strict(
    payload: Stage1ForcedAlignmentPayload | dict[str, Any],
    reference_words: Sequence[str],
) -> tuple[List[ForcedAlignedWord], List[str]]:
    if isinstance(payload, Stage1ForcedAlignmentPayload):
        model = payload
    else:
        model = Stage1ForcedAlignmentPayload.model_validate(payload)

    ref = [w for w in (normalize_word(x) for x in reference_words) if w]
    if not ref:
        raise ValueError("reference_words are empty after normalization")

    got = list(model.aligned_words)
    warnings: List[str] = []
    if len(got) != len(ref):
        warnings.append(f"aligned_words count mismatch: got={len(got)} expected={len(ref)}")

    prev_start = -1.0
    prev_end = -1.0
    for idx, word in enumerate(got):
        ref_word = ref[idx] if idx < len(ref) else ""
        norm = normalize_word(word.text)
        if not norm:
            raise ValueError(f"aligned_words[{idx}] normalized text is empty")
        if ref_word and norm != ref_word:
            warnings.append(f"aligned_words[{idx}] mismatch: got={norm!r} expected={ref_word!r}")
        ts = float(word.t_start_sec)
        te = float(word.t_end_sec)
        if te <= ts:
            raise ValueError(f"aligned_words[{idx}] invalid timing: {ts}..{te}")
        if idx > 0 and ts < prev_start:
            raise ValueError(
                f"aligned_words[{idx}] non-monotonic t_start: {ts} < {prev_start}"
            )
        if idx > 0 and te < prev_end:
            raise ValueError(
                f"aligned_words[{idx}] non-monotonic t_end: {te} < {prev_end}"
            )
        prev_start = ts
        prev_end = te

    return got, warnings


def build_error_metrics(
    *,
    substitutions: int,
    deletions: int,
    insertions: int,
    reference_words_count: int,
) -> dict[str, float | int]:
    n = int(reference_words_count)
    if n <= 0:
        raise ValueError("reference_words_count must be > 0")
    s = int(substitutions)
    d = int(deletions)
    i = int(insertions)
    err = s + d + i
    return {
        "reference_words_count": n,
        "substitutions": s,
        "deletions": d,
        "insertions": i,
        "error_pct": (float(err) / float(n)) * 100.0,
        "wrong_word_pct": (float(s) / float(n)) * 100.0,
        "miss_pct": (float(d) / float(n)) * 100.0,
        "extra_pct": (float(i) / float(n)) * 100.0,
    }


def build_normalized_word_stream(words: Iterable[dict[str, Any]]) -> List[dict[str, Any]]:
    out: List[dict[str, Any]] = []
    for item in words:
        txt = str(item.get("text") or "")
        norm = normalize_word(txt)
        if not norm:
            continue
        out.append(
            {
                "text": txt,
                "norm": norm,
                "t_start": float(item.get("t_start", 0.0)),
                "t_end": float(item.get("t_end", 0.0)),
            }
        )
    return out


def format_srt_time(seconds: float) -> str:
    total_ms = int(round(max(0.0, float(seconds)) * 1000.0))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    sec = total_s % 60
    total_m = total_s // 60
    minute = total_m % 60
    hour = total_m // 60
    return f"{hour:02d}:{minute:02d}:{sec:02d},{ms:03d}"


def words_to_srt(words: Sequence[dict[str, Any]]) -> str:
    lines: List[str] = []
    for idx, w in enumerate(words, start=1):
        txt = str(w.get("text") or "").replace("\r", " ").replace("\n", " ").strip()
        if not txt:
            txt = "_"
        ts = float(w.get("t_start", 0.0))
        te = float(w.get("t_end", ts + 0.001))
        if te <= ts:
            te = ts + 0.001
        lines.append(str(idx))
        lines.append(f"{format_srt_time(ts)} --> {format_srt_time(te)}")
        lines.append(txt)
        lines.append("")
    return "\n".join(lines)
