from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from mlcore.models.stage1_plan import Stage1DraftBlocks, Stage1PlanPayload


def _norm_word(s: str) -> str:
    t = str(s or "").lower().replace("ё", "е")
    t = re.sub(r"[^\w\-]+", "", t, flags=re.UNICODE)
    return t.strip("_")


def _norm_words_from_phrase(phrase: str) -> List[str]:
    out: List[str] = []
    for raw in str(phrase or "").replace("\r", " ").split():
        w = _norm_word(raw)
        if w:
            out.append(w)
    return out


def ordered_stage1_segments(draft: Stage1DraftBlocks) -> List[Tuple[str, str]]:
    def join_phrases(v: Any) -> str:
        phrases = list(getattr(v, "phrases", []) or [])
        return " ".join(str(x or "").strip() for x in phrases if str(x or "").strip()).strip()

    return [
        ("block_1", join_phrases(draft.block_1)),
        ("block_2.p1", join_phrases(draft.block_2.p1)),
        ("block_2.p2", join_phrases(draft.block_2.p2)),
        ("block_3", join_phrases(draft.block_3)),
        ("block_4.p1", join_phrases(draft.block_4.p1)),
        ("block_4.p2", join_phrases(draft.block_4.p2)),
        ("block_5.slowly_in", join_phrases(draft.block_5.slowly_in)),
        ("block_5.fast_reveal", join_phrases(draft.block_5.fast_reveal)),
        ("block_5.glitch_peak", join_phrases(draft.block_5.glitch_peak)),
        ("block_5.mine", join_phrases(draft.block_5.mine)),
        ("block_6", join_phrases(draft.block_6)),
        ("block_7.part1", join_phrases(draft.block_7.part1)),
        ("block_7.part2", join_phrases(draft.block_7.part2)),
    ]


def _find_contiguous_span(
    transcript_norm: List[str],
    needle_norm: List[str],
    *,
    start_cursor: int,
    end_limit: int | None = None,
) -> Tuple[int, int]:
    n = len(needle_norm)
    if n == 0:
        raise ValueError("empty phrase")
    last_i = len(transcript_norm) - n
    if end_limit is not None:
        last_i = min(last_i, int(end_limit) - n + 1)
    for i in range(max(0, start_cursor), last_i + 1):
        ok = True
        for j in range(n):
            if transcript_norm[i + j] != needle_norm[j]:
                ok = False
                break
        if ok:
            return i, i + n - 1
    raise ValueError("phrase not found as contiguous transcript span")


def align_stage1_draft_to_transcript(stage1: Stage1PlanPayload) -> List[Dict[str, Any]]:
    t_words = list(stage1.transcript_words)
    transcript_norm = [_norm_word(w.text) for w in t_words]
    # Start search from the first transcript token that falls inside the selected clip window.
    cs = float(stage1.audio.clip_start_abs)
    ce = float(stage1.audio.clip_end_abs)
    cursor = 0
    end_limit = len(t_words) - 1
    for i, w in enumerate(t_words):
        if float(w.t_start) >= cs - 1e-6:
            cursor = i
            break
    for i in range(len(t_words) - 1, -1, -1):
        if float(t_words[i].t_end) <= ce + 1e-6:
            end_limit = i
            break
    rows: List[Dict[str, Any]] = []

    for where, phrase in ordered_stage1_segments(stage1.draft_blocks):
        phrase_norm = _norm_words_from_phrase(phrase)
        if not phrase_norm:
            raise ValueError(f"{where}: empty phrase after normalization")
        try:
            st, en = _find_contiguous_span(
                transcript_norm,
                phrase_norm,
                start_cursor=cursor,
                end_limit=end_limit,
            )
        except Exception:
            # Try to locate the phrase anywhere inside the clip (ignoring cursor), so we can produce
            # an actionable error message (usually "phrase exists earlier but cursor already passed").\n
            extra = ""
            try:
                st2, en2 = _find_contiguous_span(
                    transcript_norm,
                    phrase_norm,
                    start_cursor=0,
                    end_limit=end_limit,
                )
                extra = (
                    f" (exists earlier in clip at idx={st2}..{en2} "
                    f"t={float(t_words[st2].t_start):.3f}..{float(t_words[en2].t_end):.3f})"
                )
            except Exception:
                extra = " (not found as contiguous span inside clip)"

            raise ValueError(
                f"{where}: phrase not found as contiguous transcript span within clip "
                f"(cursor={cursor}, end_limit={end_limit}) phrase={phrase!r}{extra}"
            )
        cursor = en + 1

        line1 = phrase.split("\r", 1)[0]
        line1_chars = len(" ".join(line1.split()))
        word_count = len(phrase_norm)
        risk: List[str] = []
        if word_count > 8 or line1_chars > 24:
            risk.append("long_phrase")
        if where.startswith("block_7") and (en >= len(t_words) - 1 or (en - st + 1) <= 1):
            risk.append("tail_coverage_risk")

        rows.append(
            {
                "where": where,
                "phrase": phrase,
                "start_idx": st,
                "end_idx": en,
                "start_t": float(t_words[st].t_start),
                "end_t": float(t_words[en].t_end),
                "word_count": word_count,
                "line1_chars": line1_chars,
                "risk_flags": risk,
            }
        )
    return rows


def build_stage1_report(stage1: Stage1PlanPayload, rows: List[Dict[str, Any]]) -> str:
    dur = float(stage1.audio.clip_end_abs) - float(stage1.audio.clip_start_abs)
    lines = [
        f"clip_start_abs: {float(stage1.audio.clip_start_abs):.3f}",
        f"clip_end_abs:   {float(stage1.audio.clip_end_abs):.3f}",
        f"clip_duration:  {dur:.3f}",
        f"transcript_words_count: {len(stage1.transcript_words)}",
        "",
        "segments:",
    ]
    for r in rows:
        risk = ",".join(r["risk_flags"]) if r["risk_flags"] else "-"
        lines.append(
            f"- {r['where']}: idx={r['start_idx']}..{r['end_idx']} "
            f"t={r['start_t']:.3f}..{r['end_t']:.3f} "
            f"words={r['word_count']} line1_chars={r['line1_chars']} risk={risk}"
        )
        lines.append(f"  phrase: {r['phrase']}")
    return "\n".join(lines) + "\n"
