"""Лаб пользовательских параметров jakson: размер / позиция / тень / обводка / цвета.

Композиция на (пара шрифтов × группа параметров): внутри по очереди варианты
группы (маркер с именем на старте), в каждом варианте три сцены подряд —
TYPE_1 (две строки), TYPE_2 (акцент пары), TYPE_4 (ударное слово). Боевой билдер
+ боевой шаблон, без рендера.

    python scripts/subtitle_font_lab/build_jakson_params_lab.py --out-aep ".../jakson-params-lab.aep"
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

from app import scenes_3rd_reference_builder as jakson  # noqa: E402
from app.project_builder import _tojson_filter  # noqa: E402
from app.project_config import AE_PROJECT  # noqa: E402
from app.render_plan import build_render_plan_v1  # noqa: E402
from app.subtitle_font_layout import JaksonTextParams, check_pair, jakson_layout  # noqa: E402
from app.text_comp import build_text_layers  # noqa: E402
from core.subtitles_mode import SUBTITLES_MODE_SCENES_3RD_SINGLE_STEP as MODE  # noqa: E402

PAIRS = [
    ("Point + Katherine", "Point-SemiBold", "Point-ExtraBold", "Katherine-Plus"),
    ("Bebas + Simphony", "BebasNeueBold", None, "SimphonyRegular"),
    ("Cormorant + Princess Diana", "CormorantSC-Medium", None, "PrincessDiana"),
]

BASE = JaksonTextParams()
# Смотр 2026-09-29: обводки нет вовсе; «выше» нет; «ниже» — только 16:9 (здесь 9:16);
# акцентный цвет один на TYPE_2 и TYPE_4 (не больше двух цветов в кадре).
GROUPS = {
    "01 размер": [("large (авто)", replace(BASE, size="large")), ("medium −10%", replace(BASE, size="medium")),
                  ("small −20%", replace(BASE, size="small"))],
    "02 высота": [("compact 80%", replace(BASE, height="compact")), ("normal", replace(BASE, height="normal")),
                  ("tall 130%", replace(BASE, height="tall"))],
    "03 позиция": [(name, replace(BASE, position=name)) for name in ("center", "left", "right")],
    "04 тень": [(name, replace(BASE, shadow=name)) for name in ("none", "soft", "strong")],
    "05 цвета": [("по умолчанию (белый + красный)", BASE),
                 ("акцент розовый", replace(BASE, accent_color="#FF5FA8")),
                 ("акцент жёлтый", replace(BASE, accent_color="#FFD23F"))],
}

# (тип, строки, фокус-слово, стиль фокуса)
SCENES = [
    ("TYPE_1", [["душа", "щемит"], ["всё", "пройдёт"]], None, None),
    ("TYPE_2", [["ты", "моя"], ["первая", "любовь"]], "любовь", "italic"),
    ("TYPE_4", [["навсегда"]], "навсегда", "red"),
]
WORD_STEP = 0.45
LEAD_IN = 0.40
SCENE_GAP = 0.45
SLOT = 9.0


def _segments(t0: float, tag: str) -> List[Dict[str, Any]]:
    segs, t = [], t0 + LEAD_IN
    for k, (stype, lines, focus, fstyle) in enumerate(SCENES):
        words = [w for line in lines for w in line]
        tokens, start = [], t
        for w in words:
            tokens.append({"text": w, "t_start": round(t, 3), "t_end": round(t + WORD_STEP * 0.9, 3)})
            t += WORD_STEP
        end = round(tokens[-1]["t_end"] + (1.0 if stype == "TYPE_4" else 0.6), 3)
        segs.append({"id": f"seg_{tag}{k + 1}", "text": " ".join(words), "in_point": round(start, 3),
                     "out_point": end, "style_tag": stype, "lines": [" ".join(l) for l in lines],
                     "tokens": tokens, "focus_word": focus, "focus_style": fstyle,
                     "reason": "lab" if stype == "TYPE_4" else None})
        t = end + SCENE_GAP
    if t > t0 + SLOT:
        raise RuntimeError(f"scenes do not fit SLOT={SLOT}s (need {t - t0:.2f}s)")
    return segs


def _comp_jsx(env: Environment, pair: tuple, group: str, variants: list, metrics: Path):
    pair_label, base_ps, focus_ps, accent_ps = pair
    check_pair(base_ps, accent_ps)
    tag = f"{pair_label} · {group}"
    main_name, text_name, mine_name = f"Параметры · {tag}", f"Текст · {tag}", f'Текст "Mine" · {tag}'
    text_layers: List[Dict[str, Any]] = []
    markers = []
    for i, (vlabel, params) in enumerate(variants):
        layout = jakson_layout(base_ps, focus_ps, accent_font=accent_ps, params=params, path=metrics)
        jakson.apply_font_layout(layout)
        try:
            cfg = {"subtitles_mode": MODE, "subtitle_flow_plan": {
                "mode": MODE, "clip": {"start": 0.0, "end": SLOT * len(variants)},
                "segments": _segments(i * SLOT, f"{i}_")}}
            text_layers += build_text_layers(full_edit_config=cfg, text_comp_name=text_name, mine_comp_name=mine_name)
        finally:
            jakson.apply_font_layout(None)
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
    var main = find("Параметры · " + tag);
    if (!main) continue;
    main.parentFolder = folders[c.pair];
    for (var j = 0; j < c.markers.length; j++)
      main.markerProperty.setValueAtTime(c.markers[j].t, new MarkerValue(c.markers[j].label));
    var t = find("Текст · " + tag); if (t) t.parentFolder = inner;
    var m = find('Текст "Mine" · ' + tag); if (m) m.parentFolder = inner;
  }
  if (app.project.renderQueue.numItems !== 0) throw new Error("params lab must not touch the render queue");
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

    parts = ["// jakson params lab — scripts/subtitle_font_lab/build_jakson_params_lab.py\n" + lab.CLEAN_GUARD_JS]
    comps: Dict[str, Dict[str, Any]] = {}
    for pair in PAIRS:
        for group, variants in GROUPS.items():
            tag, jsx, markers = _comp_jsx(env, pair, group, variants, args.metrics)
            parts.append(f"// ===== {tag} =====\n{jsx}\n")
            comps[tag] = {"pair": pair[0], "markers": markers}
    parts.append(_finalize_jsx(args.out_aep, comps))

    args.out_dir.mkdir(parents=True, exist_ok=True)
    out = args.out_dir / "jakson_params_lab.jsx"
    out.write_text("".join(parts), encoding="utf-8")
    print(out, f"{len(comps)} comps")


if __name__ == "__main__":
    main()
