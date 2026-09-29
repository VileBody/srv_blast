"""Превью шрифтовых пар jakson: основной (капс) + акцентное слово (строчными) внутри строки.

Одна композиция «Пара · <акцент>» на акцентный шрифт; внутри по очереди идут
все основные шрифты (маркер с именем на старте каждого), у каждого две сцены
TYPE_2 — акцент в конце 2-й строки и в конце 1-й (росчерки вверх и вниз).
Слои — боевой билдер с раскладкой пары (`app.subtitle_font_layout`), JSX — боевой
шаблон. Без рендера.

    python scripts/subtitle_font_lab/build_jakson_pair_lab.py --out-aep ".../jakson-pair-lab.aep"
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

from app import scenes_3rd_reference_builder as jakson  # noqa: E402
from app.project_builder import _tojson_filter  # noqa: E402
from app.project_config import AE_PROJECT  # noqa: E402
from app.render_plan import build_render_plan_v1  # noqa: E402
from app.subtitle_font_layout import is_pairable, jakson_layout  # noqa: E402
from app.text_comp import build_text_layers  # noqa: E402
from core.subtitles_mode import SUBTITLES_MODE_SCENES_3RD_SINGLE_STEP as MODE  # noqa: E402

ACCENT_FONTS = [
    ("Katherine Plus", "Katherine-Plus"),
    ("Simphony", "SimphonyRegular"),
    ("Princess Diana", "PrincessDiana"),
    ("Anastasia", "AnastasiaScript"),
    ("Florisel", "Floriselscript-Thin"),
    ("Hello January", "Hello_January_script-Script"),
    ("Disruptors", "DisruptorsScript-Regular"),
    ("Beryozki", "Beryozki"),
    # Drive Hearts: в app.fonts есть, но AE подставляет Times при наборе (2026-09-29) — переустановить
    ("Aurora", "AuroraScript"),                 # кириллица без прописных Ъ Ы Ь Ё — акцент строчный, ок
    ("Script Thin Pen", "ScriptThinPen"),       # то же
]

# (строки, фокус-слово): акцент в конце 2-й строки, затем в конце 1-й
SCENES = [
    ([["ты", "моя"], ["первая", "любовь"]], "любовь"),
    ([["это", "наша", "судьба"], ["навсегда"]], "судьба"),
]
WORD_STEP = 0.45
SCENE_GAP = 0.35
LEAD_IN = 0.40
SLOT = 6.4          # секунд на один основной шрифт
# Самодостаточные (pairable=false: AKONY, Kudry) в пары не берём — на фронте блок акцентов скрыт.
PAIR_BASES = [f for f in lab.MAIN_FONTS if is_pairable(f[2])]


def _segments(t0: float, tag: str) -> List[Dict[str, Any]]:
    segs = []
    t = t0 + LEAD_IN
    for k, (lines, focus) in enumerate(SCENES):
        words = [w for line in lines for w in line]
        tokens = []
        start = t
        for w in words:
            tokens.append({"text": w, "t_start": round(t, 3), "t_end": round(t + WORD_STEP * 0.9, 3)})
            t += WORD_STEP
        end = round(tokens[-1]["t_end"] + 0.6, 3)
        segs.append({
            "id": f"seg_{tag}{k + 1}", "text": " ".join(words), "in_point": round(start, 3), "out_point": end,
            "style_tag": "TYPE_2", "lines": [" ".join(line) for line in lines], "tokens": tokens,
            "focus_word": focus, "focus_style": "italic",
        })
        t = end + SCENE_GAP
    if t > t0 + SLOT:
        raise RuntimeError(f"pair scenes do not fit SLOT={SLOT}s (need {t - t0:.2f}s)")
    return segs


def _accent_jsx(env: Environment, label: str, accent_ps: str, metrics: Path) -> tuple[str, List[Dict[str, Any]]]:
    main_name, text_name, mine_name = f"Пара · {label}", f"Текст · пара {label}", f'Текст "Mine" · пара {label}'
    text_layers: List[Dict[str, Any]] = []
    markers = []
    for i, (_group, base_label, base_ps, _focus) in enumerate(PAIR_BASES):
        t0 = i * SLOT
        layout = jakson_layout(base_ps, accent_font=accent_ps, path=metrics)
        jakson.apply_font_layout(layout)
        try:
            cfg = {"subtitles_mode": MODE, "subtitle_flow_plan": {
                "mode": MODE, "clip": {"start": 0.0, "end": SLOT * len(PAIR_BASES)},
                "segments": _segments(t0, f"{i}_")}}
            text_layers += build_text_layers(full_edit_config=cfg, text_comp_name=text_name, mine_comp_name=mine_name)
        finally:
            jakson.apply_font_layout(None)
        a = layout.accent
        markers.append({"t": t0, "label": f"{base_label} + {label}",
                        "accent_pt": a.size, "shift": a.baseline_shift})

    dur = SLOT * len(PAIR_BASES)
    lab.COMP_DUR = dur   # _comp/_text_precomp берут длительность отсюда
    comps = [lab._comp(AE_PROJECT["main_comp"], main_name, bg=lab.BG_GREY),
             lab._comp(AE_PROJECT["text_comp"], text_name), lab._comp(AE_PROJECT["mine_comp"], mine_name)]
    plan = build_render_plan_v1(
        main_comp_name=main_name, subtitles_mode=MODE, comps=comps,
        footage_layers=[lab._text_precomp(text_name, main_name)], text_layers=text_layers,
        full_edit_config={"subtitles_mode": MODE}, f3_media=[],
    )
    overlays = {f"{k}_overlay_js": "" for k in ("f4", "f3", "f2", "f1", "f6", "f5", "frame")}
    jsx = env.get_template("project_template.j2").render(**plan.to_ae_payload(), **overlays, jsx_subtitles_js="")
    return jsx, markers


def _finalize_jsx(out_aep: str, comps: Dict[str, List[Dict[str, Any]]]) -> str:
    return """
(function () {
  var comps = %s;
  function find(name) {
    for (var i = 1; i <= app.project.numItems; i++) {
      var it = app.project.item(i);
      if (it instanceof CompItem && it.name === name) return it;
    }
    return null;
  }
  var pairs = app.project.items.addFolder("PAIRS");
  var inner = app.project.items.addFolder("_text_precomps");
  for (var label in comps) {
    var main = find("Пара · " + label);
    if (!main) continue;
    main.parentFolder = pairs;
    var ms = comps[label];
    for (var j = 0; j < ms.length; j++) {
      var mv = new MarkerValue(ms[j].label);
      mv.comment = ms[j].label + "  |  accent " + ms[j].accent_pt + " pt, shift " + ms[j].shift + " px";
      main.markerProperty.setValueAtTime(ms[j].t, mv);
    }
    var t = find("Текст · пара " + label); if (t) t.parentFolder = inner;
    var m = find('Текст "Mine" · пара ' + label); if (m) m.parentFolder = inner;
  }
  if (app.project.renderQueue.numItems !== 0) throw new Error("pair lab must not touch the render queue");
  app.project.save(new File(%s));
})();
""" % (json.dumps(comps, ensure_ascii=False), json.dumps(out_aep, ensure_ascii=False))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--metrics", type=Path, default=lab.REPO / "config" / "styles" / "subtitle_font_metrics.json")
    ap.add_argument("--out-aep", required=True)
    ap.add_argument("--out-dir", type=Path, default=Path(tempfile.gettempdir()) / "blast_font_lab")
    args = ap.parse_args()

    env = Environment(loader=FileSystemLoader(str(lab.REPO / "templates")), autoescape=False)
    env.filters["tojson"] = _tojson_filter

    parts = ["// jakson pair lab — scripts/subtitle_font_lab/build_jakson_pair_lab.py\n" + lab.CLEAN_GUARD_JS]
    all_markers: Dict[str, List[Dict[str, Any]]] = {}
    for label, ps in ACCENT_FONTS:
        jsx, markers = _accent_jsx(env, label, ps, args.metrics)
        parts.append(f"// ===== accent {label} ({ps}) =====\n{jsx}\n")
        all_markers[label] = markers
    parts.append(_finalize_jsx(args.out_aep, all_markers))

    args.out_dir.mkdir(parents=True, exist_ok=True)
    out = args.out_dir / "jakson_pair_lab.jsx"
    out.write_text("".join(parts), encoding="utf-8")
    print(out)
    for label, ms in all_markers.items():
        sizes = [m["accent_pt"] for m in ms]
        print(f"{label}: accent {min(sizes)}–{max(sizes)} pt")


if __name__ == "__main__":
    main()
