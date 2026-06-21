#!/usr/bin/env python3
"""Report free-form clip tags that the picker can't see, ranked by frequency,
with conservative alias suggestions. Run before big ingests to grow
data/tag_aliases.json (and to spot tags worth ADDING to the taxonomy).

A tag is "visible" to the picker if it's in the footage_v2 taxonomy OR already
has an alias. Everything else is wasted descriptive work. Suggestions use token
overlap + fuzzy match (NO naive substring — that produced garbage like
"tree"->"street fashion").

Tag source: Postgres footage_tags if CREDITS_DB_URL is set, else the legacy
video_database JSONs. Output: console summary + JSON report.

Usage:
  python scripts/footage_tag_alias_report.py [out_report.json] [--min-freq N]
"""
from __future__ import annotations

import json
import os
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_OUT = "data/tag_alias_report.json"
_VIDEO_DBS = [
    _ROOT / "2nd_footage_selection_prompt" / "video_database (2).json",
    _ROOT / "2nd_footage_selection_prompt" / "video_database2.json",
]


def norm(v: Any) -> str:
    return " ".join(str(v or "").strip().lower().split())


# --------------------------------------------------------------------------- #
# Pure helpers (unit tested)
# --------------------------------------------------------------------------- #
def extract_taxonomy(footage_v2_src: str) -> set:
    """All tag strings from the THEMES LOGIC section of footage_v2.py."""
    start = footage_v2_src.find("THEMES LOGIC")
    body = footage_v2_src[start:] if start >= 0 else footage_v2_src
    out: set = set()
    for m in re.findall(r'"([^"]+)"', body):
        t = norm(m)
        if t and not t.startswith("_") and t not in {"color", "exclude", "tags_groups"}:
            out.add(t)
    return out


def suggest_match(tag: str, taxonomy: set) -> Tuple[Optional[str], float, str]:
    """Best taxonomy candidate via token-overlap then fuzzy. Conservative:
    returns (None, 0, "") when nothing shares a token and fuzzy is weak."""
    tag_tokens = set(tag.split())
    best: Tuple[Optional[str], float, str] = (None, 0.0, "")
    for cand in taxonomy:
        cand_tokens = set(cand.split())
        shared = tag_tokens & cand_tokens
        if shared:
            denom = max(len(tag_tokens), len(cand_tokens)) or 1
            score = 0.5 + 0.25 * len(shared) + 0.25 * (len(shared) / denom)
            reason = "token"
        else:
            ratio = SequenceMatcher(None, tag, cand).ratio()
            if ratio < 0.85:
                continue
            score, reason = ratio, "fuzzy"
        if score > best[1]:
            best = (cand, round(float(score), 2), reason)
    return best


def classify(
    tag_freq: Dict[str, int],
    taxonomy: set,
    aliases: Dict[str, str],
    *,
    min_freq: int = 5,
) -> Dict[str, Any]:
    """Split tags into visible vs unmatched; rank unmatched + suggest aliases."""
    alias_keys = {norm(k) for k in aliases}
    total_instances = sum(tag_freq.values()) or 1
    visible_instances = sum(c for t, c in tag_freq.items() if t in taxonomy or t in alias_keys)

    high: List[Dict[str, Any]] = []
    mid: List[Dict[str, Any]] = []
    add_taxonomy: List[Dict[str, Any]] = []
    for tag, freq in sorted(tag_freq.items(), key=lambda x: -x[1]):
        if tag in taxonomy or tag in alias_keys:
            continue
        if freq < min_freq:
            continue
        cand, score, reason = suggest_match(tag, taxonomy)
        row = {"tag": tag, "freq": freq, "suggest": cand, "score": score, "reason": reason}
        if cand and score >= 0.8:
            high.append(row)
        elif cand and score >= 0.6:
            mid.append(row)
        else:
            add_taxonomy.append({"tag": tag, "freq": freq})

    return {
        "totals": {
            "distinct_tags": len(tag_freq),
            "tag_instances": total_instances,
            "visibility_pct": round(100.0 * visible_instances / total_instances, 1),
            "unmatched_distinct": len(high) + len(mid) + len(add_taxonomy),
        },
        "high_confidence_aliases": high,
        "mid_confidence_review": mid,
        "add_to_taxonomy_candidates": add_taxonomy,
    }


# --------------------------------------------------------------------------- #
# I/O
# --------------------------------------------------------------------------- #
def _tag_freq_from_records(records: List[Dict[str, Any]]) -> Dict[str, int]:
    freq: Dict[str, int] = {}
    for r in records:
        for t in r.get("theme_tags") or []:
            nt = norm(t)
            if nt:
                freq[nt] = freq.get(nt, 0) + 1
    return freq


def _load_tag_freq() -> Tuple[Dict[str, int], str]:
    db_url = (os.environ.get("CREDITS_DB_URL") or "").strip()
    if db_url:
        try:
            import asyncio

            import asyncpg  # type: ignore

            from mlcore.footage_tags_db import fetch_all_records

            async def _go():
                conn = await asyncpg.connect(dsn=db_url)
                try:
                    return await fetch_all_records(conn)
                finally:
                    await conn.close()

            recs = asyncio.run(_go())
            return _tag_freq_from_records(recs), "postgres footage_tags"
        except Exception as e:
            print(f"[warn] Postgres read failed ({e}); using JSON DBs")
    records: List[Dict[str, Any]] = []
    for p in _VIDEO_DBS:
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                records.extend(data if isinstance(data, list) else [])
            except Exception:
                pass
    return _tag_freq_from_records(records), "video_database JSONs"


def main() -> int:
    args = [a for a in sys.argv[1:]]
    min_freq = 5
    if "--min-freq" in args:
        i = args.index("--min-freq")
        min_freq = int(args[i + 1])
        del args[i:i + 2]
    out_path = Path(args[0] if args else _DEFAULT_OUT)

    taxonomy = extract_taxonomy((_ROOT / "footage_v2.py").read_text(encoding="utf-8"))
    aliases = {}
    alias_file = _ROOT / "data" / "tag_aliases.json"
    if alias_file.exists():
        aliases = json.loads(alias_file.read_text(encoding="utf-8")).get("aliases", {})
    tag_freq, source = _load_tag_freq()

    report = classify(tag_freq, taxonomy, aliases, min_freq=min_freq)
    report["source"] = source
    report["taxonomy_size"] = len(taxonomy)
    report["alias_count"] = len(aliases)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    t = report["totals"]
    print(f"source={source} taxonomy={len(taxonomy)} aliases={len(aliases)}")
    print(f"visibility: {t['visibility_pct']}% of tag-instances | unmatched distinct (freq>={min_freq}): {t['unmatched_distinct']}")
    print(f"\nHIGH-confidence alias suggestions (add to tag_aliases.json):")
    for r in report["high_confidence_aliases"][:30]:
        print(f"  {r['freq']:4d}  {r['tag']:24s} -> {r['suggest']:24s} [{r['score']} {r['reason']}]")
    print(f"\nNO good match (consider ADDING to taxonomy):")
    for r in report["add_to_taxonomy_candidates"][:20]:
        print(f"  {r['freq']:4d}  {r['tag']}")
    print(f"\nfull report -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
