"""Лаб brat (шрифт фиксирован, пар нет): курсив фокус-слова, тень под каждым словом
против прежней плашки, цвет фокус-слов. Боевой brat_subtitles.jsx + значения движка
(`app/subtitle_font_layout.brat_layout`), без рендера.

Заодно замер: у каждого слова считается базовая линия и низ чернил в кадре; разброс
по строке пишется в <Folder.temp>/blast_font_lab/brat_baseline_report.txt
(слова в одной строке обязаны стоять на одном уровне).

    python scripts/subtitle_font_lab/build_brat_lab.py --out-aep ".../brat-lab.aep"
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

from app.jsx_subtitles_builder import build_jsx_subtitles_overlay  # noqa: E402
from app.subtitle_font_layout import JaksonTextParams, brat_layout  # noqa: E402
from core.subtitles_mode import SUBTITLES_MODE_BRAT_5TH as MODE  # noqa: E402

# светлый фон — там, где тень белому тексту нужнее всего
BG_LIGHT = [0.72, 0.72, 0.74]
TEXT = ("я не уйду отсюда пока не скажешь правду всё что было между нами "
        "ёжится в груди щемящей болью")
FOCUS = {"правду", "щемящей"}
WORD_DUR = 0.42
LEAD_IN = 0.3
BPM = 120.0

BASE = JaksonTextParams()
# (группа, подпись, параметры | None = прежняя плашка, курсив фокус-слова)
VARIANTS = [
    ("01 фокус-слово", "как все (прод)", BASE, None),
    ("01 фокус-слово", "Arial Narrow Italic", BASE, "italic"),
    ("01 фокус-слово", "Arial Narrow Bold Italic", BASE, "bold_italic"),
    ("01 фокус-слово", "наклон тем же шрифтом (faux)", BASE, "faux_italic"),
    ("02 тень под словом", "нет", replace(BASE, shadow="none"), "italic"),
    ("02 тень под словом", "мягкая", replace(BASE, shadow="soft"), "italic"),
    ("02 тень под словом", "сильная", replace(BASE, shadow="strong"), "italic"),
    ("02 тень под словом", "было: плашка под строкой", None, None),
    ("03 курсив + цвет", "Italic + розовый", replace(BASE, accent_color="#FF5FA8"), "italic"),
    ("03 курсив + цвет", "Bold Italic + жёлтый", replace(BASE, accent_color="#FFD23F"), "bold_italic"),
]


def _words() -> List[Dict[str, Any]]:
    out, t = [], LEAD_IN
    for w in TEXT.split(" "):
        out.append({"word": w, "start": round(t, 3), "end": round(t + WORD_DUR * 0.9, 3), "focus": w in FOCUS})
        t += WORD_DUR
    return out


def _variant_jsx(idx: int, group: str, label: str, params, focus_style) -> Dict[str, Any]:
    tag = f"{group} · {label}"
    main = f"Brat · {tag}"
    words = _words()
    dur = round(words[-1]["end"] + 0.8, 3)
    if params is None:   # прежний вид: плашка, без тени
        style: Dict[str, Any] = {"contrastPlate": True, "wordShadow": False, "strictFont": True}
    else:
        style = brat_layout(params=params, focus_style=focus_style).jsx_config()
    # уникальные имена генерируемых компов: cleanup brat не должен снести соседний вариант
    style.update({"textCompName": f"СУБТИТРЫ {idx:02d}", "contrastCompName": f"BRAT CONTRAST {idx:02d}"})
    body = build_jsx_subtitles_overlay(mode=MODE, word_timings=words, bpm=BPM, target_comp=main,
                                       style_config=style)
    setup = """
(function () {
  var c = app.project.items.addComp(%s, 1080, 1920, 1, %s, 23.976);
  var bg = c.layers.addSolid(%s, "bg", 1080, 1920, 1, %s);
  bg.locked = true;
})();
$.global.__BLAST_FILL = null; $.global.__BLAST_SUBS_BLEND = null; $.global.__BLAST_BPM = null;
""" % (json.dumps(main, ensure_ascii=False), dur, json.dumps(BG_LIGHT), dur)
    return {"tag": tag, "group": group, "main": main, "text_comp": style["textCompName"],
            "jsx": setup + body + "\n$.global.__LAB_RESULTS.push(%s + ': ' + String($.global.__BLAST_SUBS_RESULT));\n"
                   % json.dumps(main, ensure_ascii=False)}


def _old_script_variant() -> Dict[str, Any]:
    """Скрипт brat из HEAD (до правки) — для замера «было/стало» по высоте слов."""
    import subprocess
    src = subprocess.run(["git", "show", "HEAD:5th_template/brat_subtitles.jsx"], cwd=lab.REPO,
                         capture_output=True, check=True).stdout.decode("utf-8")
    src = src.replace("INTERACTIVE:     true", "INTERACTIVE:     false", 1).replace("DEBUG:           true",
                                                                                  "DEBUG:           false", 1)
    group, main = "00 было", "Brat · 00 было · скрипт до правки"
    words = _words()
    dur = round(words[-1]["end"] + 0.8, 3)
    prelude = ("$.global.__BLAST_SUBS_JSON = %s;\n$.global.__BLAST_TARGET_COMP = %s;\n$.global.__BLAST_BPM = %r;\n"
               "$.global.__BLAST_FILL = null; $.global.__BLAST_SUBS_BLEND = null; $.global.__BLAST_STYLE = null;\n"
               % (json.dumps({"word_timings": words}, ensure_ascii=False), json.dumps(main, ensure_ascii=False), BPM))
    setup = """
(function () {
  var c = app.project.items.addComp(%s, 1080, 1920, 1, %s, 23.976);
  var bg = c.layers.addSolid(%s, "bg", 1080, 1920, 1, %s);
  bg.locked = true;
})();
""" % (json.dumps(main, ensure_ascii=False), dur, json.dumps(BG_LIGHT), dur)
    push = "\n$.global.__LAB_RESULTS.push(%s + ': (old script)');\n" % json.dumps(main, ensure_ascii=False)
    return {"tag": main, "group": group, "main": main, "text_comp": "СУБТИТРЫ", "jsx": setup + prelude + src + push}


def _finalize_jsx(out_aep: str, variants: List[Dict[str, Any]], report: str) -> str:
    meta = [{"group": v["group"], "main": v["main"], "text_comp": v["text_comp"]} for v in variants]
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
  var DESC = /[дрруфцщ]/;
  var lines = [];
  var folders = {}, inner = app.project.items.addFolder("_brat_generated");
  for (var v = 0; v < vs.length; v++) {
    var main = find(vs[v].main);
    if (!main) throw new Error("brat lab: main comp missing " + vs[v].main);
    if (!folders[vs[v].group]) folders[vs[v].group] = app.project.items.addFolder(vs[v].group);
    main.parentFolder = folders[vs[v].group];
    var tc = find(vs[v].text_comp);
    if (!tc) throw new Error("brat lab: text comp missing " + vs[v].text_comp);
    // замер: базовая линия и низ чернил слов без выносных, по строкам b.r
    var rows = {};
    for (var li = 1; li <= tc.numLayers; li++) {
      var N = tc.layer(li);
      var m = /^BRAT WORD (\\d+\\.\\d+)\\.(\\d+)$/.exec(N.name);
      if (!m) continue;
      var wc = N.source, T = null;
      for (var k = 1; k <= wc.numLayers; k++) if (wc.layer(k) instanceof TextLayer) T = wc.layer(k);
      if (!T) throw new Error("brat lab: no text in " + wc.name);
      var tp = T.property("ADBE Transform Group"), np = N.property("ADBE Transform Group");
      var r = T.sourceRectAtTime(0, false);
      var ty = tp.property("ADBE Position").value[1] - tp.property("ADBE Anchor Point").value[1];
      var ny = np.property("ADBE Position").value[1], na = np.property("ADBE Anchor Point").value[1];
      var s = np.property("ADBE Scale").value[1] / 100.0;
      var base = ny + (ty - na) * s;
      var txt = String(T.property("ADBE Text Properties").property("ADBE Text Document").value.text);
      if (!rows[m[1]]) rows[m[1]] = { base: [], ink: [], words: [] };
      rows[m[1]].base.push(base);
      rows[m[1]].words.push(txt + "(" + wc.height + ")");
      if (!DESC.test(txt)) rows[m[1]].ink.push(ny + (ty + r.top + r.height - na) * s);
    }
    function spread(a) { if (a.length < 2) return 0; var lo = a[0], hi = a[0]; for (var q = 1; q < a.length; q++) { lo = Math.min(lo, a[q]); hi = Math.max(hi, a[q]); } return hi - lo; }
    for (var key in rows) {
      lines.push(vs[v].main + "\\trow " + key + "\\tbaseline spread " + spread(rows[key].base).toFixed(3) +
                 "\\tink-bottom spread " + spread(rows[key].ink).toFixed(3) + "\\t" + rows[key].words.join(" "));
    }
  }
  for (var i = app.project.numItems; i >= 1; i--) {
    var it = app.project.item(i);
    if (it instanceof CompItem && it.parentFolder === app.project.rootFolder && !/^Brat · /.test(it.name)) it.parentFolder = inner;
  }
  var f = new File(%s);
  f.encoding = "UTF-8";
  if (!f.open("w")) throw new Error("brat lab: cannot write report");
  f.write(lines.join("\\n") + "\\n\\n==== run results ====\\n" + $.global.__LAB_RESULTS.join("\\n----\\n") + "\\n");
  f.close();
  if (app.project.renderQueue.numItems !== 0) throw new Error("brat lab must not touch the render queue");
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
    report = str(args.out_dir / "brat_baseline_report.txt")
    variants = [_old_script_variant()] + [_variant_jsx(i + 1, g, l, p, fs) for i, (g, l, p, fs) in enumerate(VARIANTS)]
    parts = ["// brat lab — scripts/subtitle_font_lab/build_brat_lab.py\n" + lab.CLEAN_GUARD_JS
             + "\n$.global.__LAB_RESULTS = [];\n"]
    parts += [f"// ===== {v['tag']} =====\n{v['jsx']}" for v in variants]
    parts.append(_finalize_jsx(args.out_aep, variants, report))
    out = args.out_dir / "brat_lab.jsx"
    out.write_text("".join(parts), encoding="utf-8")
    print(out, f"{len(variants)} variants")


if __name__ == "__main__":
    main()
