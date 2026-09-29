"""Лаб trendy: шрифты каталога, пары (фокус-слово акцентным шрифтом: растянутое ×4
или в естественных пропорциях), параметры (размер, высота, позиция, тень, цвет).
Боевой trendy_subtitles.jsx + значения движка (`app/subtitle_font_layout.trendy_layout`),
без рендера. Одна композиция на вариант, папки по группам.

    python scripts/subtitle_font_lab/build_trendy_lab.py --out-aep ".../trendy-lab.aep"
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build_jakson_font_lab as lab  # noqa: E402  (кладёт корень репо в sys.path)

from app.jsx_subtitles_builder import build_jsx_subtitles_overlay  # noqa: E402
from app.subtitle_font_layout import (  # noqa: E402
    JaksonTextParams,
    TRENDY_REFERENCE_FONT,
    load_catalog,
    trendy_layout,
)
from core.subtitles_mode import SUBTITLES_MODE_TRENDY_5TH as MODE  # noqa: E402

TEXT = "я вернусь к тебе всё пройдёт щемящее чувство"
FOCUS = {"вернусь", "чувство"}
WORD_DUR = 0.55
LEAD_IN = 0.2

BASE = JaksonTextParams()
_GROUP = {"sans_system": "01a гротески", "display": "01b дисплейные", "editorial": "01c с засечками",
          "script": "01d скрипты"}
PAIRS = [("Point + Katherine", "Point-SemiBold", "Katherine-Plus"),
         ("Bebas + Simphony", "BebasNeueBold", "SimphonyRegular"),
         ("Cormorant + Princess Diana", "CormorantSC-Medium", "PrincessDiana")]


def _variants() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = [
        {"group": "00 прод", "label": "прод-скрипт без настроек (Montserrat Bold)", "style": None},
        {"group": "00 прод", "label": "движок, Montserrat Bold (должно совпасть)",
         "font": TRENDY_REFERENCE_FONT, "params": BASE},
    ]
    for ps, row in load_catalog().items():
        if "base" in (row.get("roles") or []) and "trendy" not in (row.get("excluded_styles") or []):
            out.append({"group": _GROUP[row["category"]], "label": row["label"], "font": ps, "params": BASE})
    for label, base, acc in PAIRS:
        out.append({"group": "02 пары", "label": f"{label} · акцент ×4", "font": base, "accent": acc,
                    "stretch": True, "params": BASE})
        out.append({"group": "02 пары", "label": f"{label} · акцент без растяжения", "font": base, "accent": acc,
                    "stretch": False, "params": BASE})
    m = TRENDY_REFERENCE_FONT
    for n in ("large", "medium", "small"):
        out.append({"group": "03 размер", "label": n, "font": m, "params": replace(BASE, size=n)})
    for n in ("compact", "normal", "tall"):
        out.append({"group": "04 высота (Cormorant)", "label": n, "font": "CormorantSC-Medium",
                    "params": replace(BASE, height=n)})
    for n in ("center", "left", "right"):
        out.append({"group": "05 позиция", "label": n, "font": m, "params": replace(BASE, position=n)})
    for n in ("none", "soft", "strong"):
        out.append({"group": "06 тень", "label": n, "font": m, "params": replace(BASE, shadow=n)})
    for label, hexc in (("фокус розовым", "#FF5FA8"), ("фокус жёлтым", "#FFD23F")):
        out.append({"group": "07 цвет", "label": label, "font": m, "params": replace(BASE, accent_color=hexc)})
    out.append({"group": "07 цвет", "label": "Point + Katherine, фокус розовым", "font": "Point-SemiBold",
                "accent": "Katherine-Plus", "stretch": False, "params": replace(BASE, accent_color="#FF5FA8")})
    return out


def _words() -> List[Dict[str, Any]]:
    out, t = [], LEAD_IN
    for w in TEXT.split(" "):
        out.append({"word": w, "start": round(t, 3), "end": round(t + WORD_DUR * 0.9, 3), "focus": w in FOCUS})
        t += WORD_DUR
    return out


def _variant_jsx(v: Dict[str, Any]) -> Dict[str, Any]:
    main = f"Trendy · {v['group']} · {v['label']}"
    words = _words()
    dur = round(words[-1]["end"] + 0.6, 3)
    style: Optional[Dict[str, Any]] = None
    if "font" in v:
        style = trendy_layout(v["font"], params=v["params"], accent_font=v.get("accent"),
                              accent_stretch=v.get("stretch", True)).jsx_config()
    body = build_jsx_subtitles_overlay(mode=MODE, word_timings=words, target_comp=main, style_config=style)
    setup = """
(function () {
  var c = app.project.items.addComp(%s, 1080, 1920, 1, %s, 23.976);
  var bg = c.layers.addSolid(%s, "bg", 1080, 1920, 1, %s);
  bg.locked = true;
})();
$.global.__BLAST_FILL = null; $.global.__BLAST_SUBS_BLEND = null; $.global.__BLAST_SUBS_RESULT = null;
""" % (json.dumps(main, ensure_ascii=False), dur, json.dumps(lab.BG_GREY), dur)
    push = "\n$.global.__LAB_RESULTS.push(%s + ': ' + String($.global.__BLAST_SUBS_RESULT));\n" % json.dumps(
        main, ensure_ascii=False)
    return {"group": v["group"], "main": main, "jsx": setup + body + push}


def _finalize_jsx(out_aep: str, variants: List[Dict[str, Any]], report: str) -> str:
    meta = [{"group": v["group"], "main": v["main"]} for v in variants]
    return """
(function () {
  var vs = %s;
  function find(name) {
    for (var i = 1; i <= app.project.numItems; i++) {
      var it = app.project.item(i);
      if (it instanceof CompItem && it.name === name) return it;
    }
    return null;
  }
  var folders = {};
  for (var v = 0; v < vs.length; v++) {
    var main = find(vs[v].main);
    if (!main) throw new Error("trendy lab: comp missing " + vs[v].main);
    if (!folders[vs[v].group]) folders[vs[v].group] = app.project.items.addFolder(vs[v].group);
    main.parentFolder = folders[vs[v].group];
  }
  var f = new File(%s);
  f.encoding = "UTF-8";
  if (!f.open("w")) throw new Error("trendy lab: cannot write report");
  f.write($.global.__LAB_RESULTS.join("\\n----\\n") + "\\n");
  f.close();
  if (app.project.renderQueue.numItems !== 0) throw new Error("trendy lab must not touch the render queue");
  app.project.save(new File(%s));
})();
""" % (json.dumps(meta, ensure_ascii=False), json.dumps(report, ensure_ascii=False),
       json.dumps(out_aep, ensure_ascii=False))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-aep", required=True)
    ap.add_argument("--out-dir", type=Path, default=Path(tempfile.gettempdir()) / "blast_font_lab")
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    report = str(args.out_dir / "trendy_lab_report.txt")
    variants = [_variant_jsx(v) for v in _variants()]
    parts = ["// trendy lab — scripts/subtitle_font_lab/build_trendy_lab.py\n" + lab.CLEAN_GUARD_JS
             + "\n$.global.__LAB_RESULTS = [];\n"]
    parts += [f"// ===== {v['main']} =====\n{v['jsx']}" for v in variants]
    parts.append(_finalize_jsx(args.out_aep, variants, report))
    out = args.out_dir / "trendy_lab.jsx"
    out.write_text("".join(parts), encoding="utf-8")
    print(out, f"{len(variants)} variants")


if __name__ == "__main__":
    main()
