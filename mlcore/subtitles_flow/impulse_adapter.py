from __future__ import annotations

from typing import Any, Dict, List

from mlcore.models.subtitles_flow import (
    Impulse2ndRawPayload,
    SubtitleFlowPlan,
)


def build_impulse_raw_context(stage1_json: Dict[str, object]) -> Dict[str, object]:
    audio = stage1_json.get("audio") if isinstance(stage1_json, dict) else None
    cs = float((audio or {}).get("clip_start_abs") or 0.0)
    ce = float((audio or {}).get("clip_end_abs") or 0.0)

    transcript_words = stage1_json.get("transcript_words") if isinstance(stage1_json, dict) else None
    words_abs: List[Dict[str, object]] = []
    if isinstance(transcript_words, list):
        for w in transcript_words:
            if not isinstance(w, dict):
                continue
            try:
                ts = float(w.get("t_start") or 0.0)
                te = float(w.get("t_end") or 0.0)
            except Exception:
                continue
            if ts < cs - 1e-6 or te > ce + 1e-6:
                continue
            txt = str(w.get("text") or "").strip()
            if not txt:
                continue
            words_abs.append({"word": txt, "start": ts, "end": te})

    if words_abs:
        anchor_in_abs = float(words_abs[0]["start"])
    else:
        anchor_in_abs = float(cs)

    words_norm: List[Dict[str, object]] = []
    for w in words_abs:
        words_norm.append(
            {
                "word": str(w["word"]),
                "start": float(w["start"]) - anchor_in_abs,
                "end": float(w["end"]) - anchor_in_abs,
            }
        )

    return {
        "anchor_in_abs": anchor_in_abs,
        "word_timings": words_norm,
        "normalization_rule": "normalized_time = absolute_time - anchor_in_abs",
    }


def flow_to_impulse_raw_payload(flow_plan: SubtitleFlowPlan) -> Impulse2ndRawPayload:
    segs = sorted(flow_plan.segments, key=lambda s: (float(s.in_point), str(s.segment_id)))
    if not segs:
        raise ValueError("subtitle flow plan is empty")
    anchor_in_abs = float(segs[0].in_point)

    segments: List[Dict[str, Any]] = []
    all_word_timings: List[Dict[str, Any]] = []
    for seg in segs:
        segment_word_timings: List[Dict[str, Any]] = []
        for tok in seg.tokens:
            wt = {
                "word": str(tok.text),
                "start": float(tok.t_start) - anchor_in_abs,
                "end": float(tok.t_end) - anchor_in_abs,
            }
            segment_word_timings.append(wt)
            all_word_timings.append(wt)

        style = str(seg.style_tag or "").strip().lower()
        if style not in {"long", "short"}:
            style = "long"

        segments.append(
            {
                "text": str(seg.text),
                "in": float(seg.in_point) - anchor_in_abs,
                "out": float(seg.out_point) - anchor_in_abs,
                "type": style,
                "word_timings": segment_word_timings,
            }
        )

    all_word_timings.sort(key=lambda x: (float(x["start"]), float(x["end"]), str(x["word"])))
    return Impulse2ndRawPayload.model_validate(
        {
            "anchor_in_abs": anchor_in_abs,
            "word_timings": all_word_timings,
            "segments": segments,
        }
    )

