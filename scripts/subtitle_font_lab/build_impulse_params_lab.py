"""Лаб параметров impulse: ударное слово (основной / акцент пары), размер, высота,
позиция, тень, цвет. Композиция на (пара × группа), варианты подряд с маркерами;
в каждом варианте long → short → long. Боевой рендерер + шаблон, без рендера.

    python scripts/subtitle_font_lab/build_impulse_params_lab.py --out-aep ".../impulse-params-lab.aep"
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build_jakson_font_lab as lab  # noqa: E402  (кладёт корень репо в sys.path)
from jinja2 import Environment, FileSystemLoader  # noqa: E402

from app import text_flow_renderer as impulse  # noqa: E402
from app.project_builder import _tojson_filter  # noqa: E402
from app.project_config import AE_PROJECT  # noqa: E402
from app.render_plan import build_render_plan_v1  # noqa: E402
from app.subtitle_font_layout import JaksonTextParams, allows_height_stretch, impulse_layout  # noqa: E402
from app.text_comp import build_text_layers  # noqa: E402
from core.subtitles_mode import SUBTITLES_MODE_IMPULSE_2ND as MODE  # noqa: E402

PAIRS = [
    ("Point + Katherine", "Point-SemiBold", "Katherine-Plus"),
    ("Bebas + Simphony", "BebasNeueBold", "SimphonyRegular"),
    ("Cormorant + Princess Diana", "CormorantSC-Medium", "PrincessDiana"),
]
BASE = JaksonTextParams()
# (подпись, параметры, short акцентом?)
GROUPS = {
    "01 ударное слово": [("short основным шрифтом", BASE, False), ("short акцентом пары", BASE, True)],
    "02 размер": [(f"{n}", replace(BASE, size=n), True) for n in ("large", "medium", "small")],
    "03 высота": [(f"{n}", replace(BASE, height=n), True) for n in ("compact", "normal", "tall")],
    "04 позиция": [(f"{n}", replace(BASE, position=n), True) for n in ("center", "left", "right")],
    "05 тень": [(f"{n}", replace(BASE, shadow=n), True) for n in ("none", "soft", "strong")],
    "06 цвет": [("по умолчанию", BASE, True), ("акцент розовый", replace(BASE, accent_color="#FF5FA8"), True),
                ("акцент жёлтый", replace(BASE, accent_color="#FFD23F"), True)],
}
SEQUENCE = [("long", "я не могу без тебя", 2.0), ("short", "дыши", 0.9), ("long", "всё пройдёт скоро", 2.0)]
LEAD_IN = 0.3
SLOT = 5.8


def _segments(t0: float, tag: str) -> List[Dict[str, Any]]:
    segs, t = [], t0 + LEAD_IN
    for k, (kind, text, dur) in enumerate(SEQUENCE):
        words = text.split(" ")
        step = dur / len(words)
        tokens = [{"text": w, "t_start": round(t + i * step, 3), "t_end": round(t + (i + 0.9) * step, 3)}
                  for i, w in enumerate(words)]
        segs.append({"id": f"seg_{tag}{k + 1}", "text": text, "in_point": round(t, 3), "out_point": round(t + dur, 3),
                     "style_tag": kind, "lines": [text], "tokens": tokens})
        t += dur
    if t > t0 + SLOT:
        raise RuntimeError(f"sequence does not fit SLOT={SLOT}s")
    return segs


def _comp_jsx(env: Environment, pair: tuple, group: str, variants: list, metrics: Path):
    pair_label, base_ps, accent_ps = pair
    tag = f"{pair_label} · {group}"
    main_name, text_name, mine_name = f"Impulse · {tag}", f"Текст · impulse {tag}", f'Текст "Mine" · impulse {tag}'
    text_layers: List[Dict[str, Any]] = []
    markers = []
    for i, (vlabel, params, use_accent) in enumerate(variants):
        lay = impulse_layout(base_ps, params=params, accent_font=accent_ps if use_accent else None, path=metrics)
        impulse.apply_impulse_layout(lay)
        try:
            cfg = {"subtitles_mode": MODE, "subtitle_flow_plan": {
                "mode": MODE, "clip": {"start": 0.0, "end": SLOT * len(variants)},
                "segments": _segments(i * SLOT, f"{i}_")}}
            text_layers += build_text_layers(full_edit_config=cfg, text_comp_name=text_name, mine_comp_name=mine_name)
        finally:
            impulse.apply_impulse_layout(None)
        markers.append({"t": i * SLOT, "label": f"{group}: {vlabel}"})
    lab.COMP_DUR = SLOT * len(variants)
    comps = [lab._comp(AE_PROJECT["main_comp"], main_name, bg=lab.BG_GREY),
             lab._comp(AE_PROJECT["text_comp"], text_name), lab._comp(AE_PROJECT["mine_comp"], mine_name)]
    plan = build_render_plan_v1(
        main_comp_name=main_name, subtitles_mode=MODE, comps=comps,
        footage_layers=[lab._text_precomp(text_name, main_name)], text_layers=text_layers,
        full_edit_config={"subtitles_mode": MODE}, f3_media=[],
    )
    overlays = {f"{k}_overlay_js": "" for k in ("f4", "f3", "f2", "f1", "f6", "f5", "frame")}
    jsx = env.get_template("project_template.j2").render(**plan.to_ae_payload(), **overlays, jsx_subtitles_js="")
    return tag, jsx, markers


def _finalize_jsx(out_aep: str, comps: Dict[str, Dict[str, Any]]) -> str:
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
  var folders = {};
  var inner = app.project.items.addFolder("_text_precomps");
  for (var tag in comps) {
    var c = comps[tag];
    if (!folders[c.pair]) folders[c.pair] = app.project.items.addFolder(c.pair);
    var main = find("Impulse · " + tag);
    if (!main) continue;
    main.parentFolder = folders[c.pair];
    for (var j = 0; j < c.markers.length; j++)
      main.markerProperty.setValueAtTime(c.markers[j].t, new MarkerValue(c.markers[j].label));
    var t = find("Текст · impulse " + tag); if (t) t.parentFolder = inner;
    var m = find('Текст "Mine" · impulse ' + tag); if (m) m.parentFolder = inner;
  }
  if (app.project.renderQueue.numItems !== 0) throw new Error("impulse params lab must not touch the render queue");
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
    parts = ["// impulse params lab — scripts/subtitle_font_lab/build_impulse_params_lab.py\n" + lab.CLEAN_GUARD_JS]
    comps: Dict[str, Dict[str, Any]] = {}
    for pair in PAIRS:
        for group, variants in GROUPS.items():
            if group == "03 высота" and not allows_height_stretch(pair[1]):
                continue   # растяжение — только шрифтам с засечками
            tag, jsx, markers = _comp_jsx(env, pair, group, variants, args.metrics)
            parts.append(f"// ===== {tag} =====\n{jsx}\n")
            comps[tag] = {"pair": pair[0], "markers": markers}
    parts.append(_finalize_jsx(args.out_aep, comps))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    out = args.out_dir / "impulse_params_lab.jsx"
    out.write_text("".join(parts), encoding="utf-8")
    print(out, f"{len(comps)} comps")


if __name__ == "__main__":
    main()
