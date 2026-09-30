"""Превью основных шрифтов в стиле tape: одна композиция на шрифт, без рендера.

Боевой билдер tape (`app/template_4th_reference_builder`) с раскладкой от метрик
(`app/subtitle_font_layout.tape_layout`) + боевой шаблон. На шрифт — три фразы
(одна на две строки) с фокус-словами; у прямых шрифтов faux italic (вид tape).

    python scripts/subtitle_font_lab/build_tape_font_lab.py --out-aep ".../tape-font-lab.aep"
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build_jakson_font_lab as lab  # noqa: E402  (кладёт корень репо в sys.path)
from jinja2 import Environment, FileSystemLoader  # noqa: E402

from app import template_4th_reference_builder as tape  # noqa: E402
from app.project_builder import _tojson_filter  # noqa: E402
from app.project_config import AE_PROJECT  # noqa: E402
from app.render_plan import build_render_plan_v1  # noqa: E402
from app.subtitle_font_layout import load_catalog, tape_layout  # noqa: E402
from app.text_comp import build_text_layers  # noqa: E402
from core.subtitles_mode import SUBTITLES_MODE_TEMPLATE_4TH as MODE  # noqa: E402

_GROUP = {"sans_system": "01_CORE_SANS", "display": "02_DISPLAY", "editorial": "03_EDITORIAL", "script": "04_SCRIPT"}
FONTS = [("01_CORE_SANS", "Montserrat Bold Italic (прод)", "Montserrat-BoldItalic")] + [
    (_GROUP[row["category"]], row["label"], ps)
    for ps, row in load_catalog().items()
    if not row.get("hidden") and "base" in (row.get("roles") or []) and "tape" not in (row.get("excluded_styles") or [])
]

# (текст, фокус-слова, длительность) — 2–4 слова, 5 только короткие (промпт tape)
SEQUENCE = [
    ("я вернусь к тебе", {"вернусь"}, 1.8),
    ("всё пройдёт это щемящее чувство", {"чувство"}, 2.6),
    ("не отпускай меня", {"отпускай"}, 1.8),
]
LEAD_IN = 0.3
GAP = 0.25
TAIL = 0.6


def _segments() -> List[Dict[str, Any]]:
    segs, t = [], LEAD_IN
    for k, (text, focus, dur) in enumerate(SEQUENCE):
        words = text.split(" ")
        step = dur / len(words)
        tokens = [{"text": w, "t_start": round(t + i * step, 3), "t_end": round(t + (i + 0.9) * step, 3),
                   "focus": w in focus} for i, w in enumerate(words)]
        segs.append({"id": f"seg_{k + 1}", "text": text, "in_point": round(t, 3), "out_point": round(t + dur, 3),
                     "style_tag": "tape", "lines": [text], "tokens": tokens})
        t += dur + GAP
    return segs


def _font_jsx(env: Environment, label: str, ps: str, metrics: Path) -> str:
    tape.apply_tape_layout(tape_layout(ps, path=metrics))
    try:
        main_name, text_name, mine_name = f"Tape · {label}", f"Текст · tape {label}", f'Текст "Mine" · tape {label}'
        segs = _segments()
        dur = segs[-1]["out_point"] + TAIL
        cfg = {"subtitles_mode": MODE, "subtitle_flow_plan": {"mode": MODE, "clip": {"start": 0.0, "end": dur},
                                                              "segments": segs}}
        text_layers = build_text_layers(full_edit_config=cfg, text_comp_name=text_name, mine_comp_name=mine_name)
    finally:
        tape.apply_tape_layout(None)
    lab.COMP_DUR = dur
    comps = [lab._comp(AE_PROJECT["main_comp"], main_name, bg=lab.BG_GREY),
             lab._comp(AE_PROJECT["text_comp"], text_name), lab._comp(AE_PROJECT["mine_comp"], mine_name)]
    plan = build_render_plan_v1(
        main_comp_name=main_name, subtitles_mode=MODE, comps=comps,
        footage_layers=[lab._text_precomp(text_name, main_name)], text_layers=text_layers,
        full_edit_config={"subtitles_mode": MODE}, f3_media=[],
    )
    overlays = {f"{k}_overlay_js": "" for k in ("f4", "f3", "f2", "f1", "f6", "f5", "frame")}
    return env.get_template("project_template.j2").render(**plan.to_ae_payload(), **overlays, jsx_subtitles_js="")


def _finalize_jsx(out_aep: str, groups: Dict[str, List[str]]) -> str:
    return """
(function () {
  var groups = %s;
  function find(name) {
    for (var i = 1; i <= app.project.numItems; i++) {
      var it = app.project.item(i);
      if (it instanceof CompItem && it.name === name) return it;
    }
    return null;
  }
  var inner = app.project.items.addFolder("_text_precomps");
  for (var g in groups) {
    var folder = app.project.items.addFolder(g);
    for (var j = 0; j < groups[g].length; j++) {
      var label = groups[g][j];
      var main = find("Tape · " + label); if (main) main.parentFolder = folder;
      var t = find("Текст · tape " + label); if (t) t.parentFolder = inner;
      var m = find('Текст "Mine" · tape ' + label); if (m) m.parentFolder = inner;
    }
  }
  if (app.project.renderQueue.numItems !== 0) throw new Error("tape lab must not touch the render queue");
  app.project.save(new File(%s));
})();
""" % (json.dumps(groups, ensure_ascii=False), json.dumps(out_aep, ensure_ascii=False))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--metrics", type=Path, default=lab.REPO / "config" / "styles" / "subtitle_font_metrics.json")
    ap.add_argument("--out-aep", required=True)
    ap.add_argument("--out-dir", type=Path, default=Path(tempfile.gettempdir()) / "blast_font_lab")
    args = ap.parse_args()
    env = Environment(loader=FileSystemLoader(str(lab.REPO / "templates")), autoescape=False)
    env.filters["tojson"] = _tojson_filter
    parts = ["// tape font lab — scripts/subtitle_font_lab/build_tape_font_lab.py\n" + lab.CLEAN_GUARD_JS]
    groups: Dict[str, List[str]] = {}
    rows = []
    for group, label, ps in FONTS:
        parts.append(f"// ===== {label} ({ps}) =====\n{_font_jsx(env, label, ps, args.metrics)}\n")
        groups.setdefault(group, []).append(label)
        lay = tape_layout(ps, path=args.metrics)
        rows.append(f"{label}\t{lay.size}\t{lay.leading}\t{lay.case}\t{lay.faux_italic}")
    parts.append(_finalize_jsx(args.out_aep, groups))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    out = args.out_dir / "tape_font_lab.jsx"
    out.write_text("".join(parts), encoding="utf-8")
    print(out)
    print("font\tsize_pt\tleading\tcase\tfaux_italic")
    print("\n".join(rows))


if __name__ == "__main__":
    main()
