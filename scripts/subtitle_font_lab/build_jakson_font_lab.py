"""Превью основных шрифтов в стиле jakson: одна сцена на шрифт, без рендера.

Слои строит БОЕВОЙ билдер (`app/scenes_3rd_reference_builder`) с раскладкой от
метрик шрифта (`app/subtitle_font_layout`), JSX — БОЕВОЙ шаблон
`templates/project_template.j2`. Никакой отдельной «лабораторной» вёрстки:
что видно в композиции, то и поедет в прод при выборе этого шрифта.

На каждый шрифт — свой главный комп «Jakson · <шрифт>» (серый фон, чтобы была
видна тень) с прекомпом «Текст · <шрифт>». Очередь рендера не трогаем.

    python scripts/subtitle_font_lab/build_jakson_font_lab.py \
        --metrics config/styles/subtitle_font_metrics.json \
        --out-aep "C:/.../jakson-font-lab.aep"

Пишет `<Folder.temp>/blast_font_lab/jakson_font_lab.jsx` и сводку раскладки
`layout_summary.tsv` рядом. JSX запускается в AE: File → Scripts → Run Script
File (или `AfterFX.com -r`), когда AE свободен.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from jinja2 import Environment, FileSystemLoader  # noqa: E402

from app import scenes_3rd_reference_builder as jakson  # noqa: E402
from app.project_builder import _tojson_filter  # noqa: E402
from app.project_config import AE_PROJECT  # noqa: E402
from app.render_plan import build_render_plan_v1  # noqa: E402
from app.subtitle_font_layout import jakson_layout, load_catalog  # noqa: E402
from app.text_comp import build_text_layers  # noqa: E402
from core.subtitles_mode import SUBTITLES_MODE_SCENES_3RD_SINGLE_STEP  # noqa: E402

# (группа, подпись, PostScript основного, PostScript фокусного для TYPE_4) — из каталога
# config/styles/subtitle_font_catalog.json: основные не-скрипты (скрипт-как-основной — отдельный режим).
_GROUP_BY_CATEGORY = {"sans_system": "01_CORE_SANS", "display": "02_DISPLAY", "editorial": "03_EDITORIAL"}
MAIN_FONTS = [
    (_GROUP_BY_CATEGORY[row["category"]], row["label"], ps, row.get("focus_ps"))
    for ps, row in load_catalog().items()
    if "base" in (row.get("roles") or []) and row["category"] in _GROUP_BY_CATEGORY
]

# Худший случай для межстрочного: выносные Д/Щ в первой строке, Ё/Й во второй.
SCENE_LINES = [["душа", "щемит"], ["всё", "пройдёт"]]
SCENE_START = 0.50
WORD_STEP = 0.55
SCENE_TAIL = 1.60
COMP_DUR = 5.0
BG_GREY = [0.32, 0.33, 0.35]

# Только чистый проект: пустой несохранённый ИЛИ наш сохранённый лаб «jakson-|impulse-|tape-|trendy-|brat-*»
# без изменений (закрытие ничего не теряет). Чужую сессию/рендер не трогаем.
CLEAN_GUARD_JS = """(function () {
  var p = app.project;
  if (!p || p.renderQueue.numItems !== 0) throw new Error('font lab: render queue is busy');
  if (p.file) {
    if (p.dirty || !/^(jakson|impulse|tape|trendy|brat)-/.test(String(p.file.name)))
      throw new Error('font lab: foreign or modified project is open: ' + p.file.fsName);
    p.close(CloseOptions.DO_NOT_SAVE_CHANGES);
    app.newProject();
  } else if (p.numItems !== 0) {
    throw new Error('font lab: AE must have a clean empty project (someone is using AE?)');
  }
})();
"""


def _flow_plan() -> Dict[str, Any]:
    words = [w for line in SCENE_LINES for w in line]
    tokens = []
    t = SCENE_START
    for w in words:
        tokens.append({"text": w, "t_start": round(t, 3), "t_end": round(t + WORD_STEP * 0.9, 3)})
        t += WORD_STEP
    end = round(tokens[-1]["t_end"] + SCENE_TAIL, 3)
    return {
        "mode": SUBTITLES_MODE_SCENES_3RD_SINGLE_STEP,
        "clip": {"start": 0.0, "end": COMP_DUR},
        "segments": [{
            "id": "seg_1",
            "text": " ".join(words),
            "in_point": SCENE_START,
            "out_point": end,
            "style_tag": "TYPE_1",
            "lines": [" ".join(line) for line in SCENE_LINES],
            "tokens": tokens,
            "focus_word": None,
            "focus_style": None,
        }],
    }


def _comp(spec: Dict[str, Any], name: str, *, bg=None) -> Dict[str, Any]:
    out = dict(spec)
    out.update({"name": name, "dur": COMP_DUR, "workAreaStart": 0.0, "workAreaDuration": COMP_DUR})
    if bg is not None:
        out["bgColor"] = bg
    return out


def _text_precomp(text_name: str, main_name: str) -> Dict[str, Any]:
    pl = AE_PROJECT["root_precomp_placement"]
    prop = lambda m, v: {"match_name": m, "value": v, "keyframes": [], "expression": None}  # noqa: E731
    return {
        "name": text_name, "type": "precomp", "in_point": 0.0, "out_point": COMP_DUR, "z_index": 1,
        "props": {
            "tf_anchor": prop("ADBE Anchor Point", pl["anchor"]),
            "tf_position": prop("ADBE Position", pl["position"]),
            "tf_scale": prop("ADBE Scale", pl["scale"]),
            "tf_rotation": prop("ADBE Rotate Z", pl["rotationZ"]),
            "tf_opacity": prop("ADBE Opacity", pl["opacity"]),
        },
        "effects": {},
        "text_data": {
            "layer_meta": {"comp_name_target": main_name, "startTime": 0.0, "enabled": True,
                           "motionBlur": False, "collapseTransformation": False, "blendingModeCode": "5212"},
            "precomp_source": {"comp_name": text_name},
        },
    }


def _font_jsx(env: Environment, label: str, ps: str, focus_ps: str | None, metrics: Path) -> tuple[str, Dict[str, Any]]:
    layout = jakson_layout(ps, focus_ps, path=metrics)
    jakson.apply_font_layout(layout)
    try:
        main_name = f"Jakson · {label}"
        text_name = f"Текст · {label}"
        mine_name = f'Текст "Mine" · {label}'
        cfg = {"subtitles_mode": SUBTITLES_MODE_SCENES_3RD_SINGLE_STEP, "subtitle_flow_plan": _flow_plan()}
        text_layers = build_text_layers(full_edit_config=cfg, text_comp_name=text_name, mine_comp_name=mine_name)
    finally:
        jakson.apply_font_layout(None)

    comps = [
        _comp(AE_PROJECT["main_comp"], main_name, bg=BG_GREY),
        _comp(AE_PROJECT["text_comp"], text_name),
        _comp(AE_PROJECT["mine_comp"], mine_name),
    ]
    plan = build_render_plan_v1(
        main_comp_name=main_name, subtitles_mode=SUBTITLES_MODE_SCENES_3RD_SINGLE_STEP, comps=comps,
        footage_layers=[_text_precomp(text_name, main_name)], text_layers=text_layers,
        full_edit_config=cfg, f3_media=[],
    )
    overlays = {k: "" for k in ("f4", "f3", "f2", "f1", "f6", "f5", "frame", "jsx_subtitles")}
    jsx = env.get_template("project_template.j2").render(
        **plan.to_ae_payload(), **{f"{k}_overlay_js" if k != "jsx_subtitles" else "jsx_subtitles_js": v
                                   for k, v in overlays.items()},
    )
    summary = {
        "label": label, "font": ps, "size_base": layout.size_base, "size_line2": layout.size_line2,
        "leading_type1": layout.leading_type1, "leading_single": layout.leading_single,
        "cap_h_px": layout.cap_h_base, "advance_px": layout.advance_base,
        "line_chars_type1": layout.line_chars_type1, "line_chars_two_groups": layout.line_chars_two_groups,
    }
    return jsx, summary


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
  var textFolder = app.project.items.addFolder("_text_precomps");
  for (var g in groups) {
    var folder = app.project.items.addFolder(g);
    for (var j = 0; j < groups[g].length; j++) {
      var label = groups[g][j];
      var main = find("Jakson · " + label);
      if (main) main.parentFolder = folder;
      var t = find("Текст · " + label);
      if (t) t.parentFolder = textFolder;
      var m = find('Текст "Mine" · ' + label);
      if (m) m.parentFolder = textFolder;
    }
  }
  if (app.project.renderQueue.numItems !== 0) throw new Error("font lab must not touch the render queue");
  app.project.save(new File(%s));
})();
""" % (json.dumps(groups, ensure_ascii=False), json.dumps(out_aep, ensure_ascii=False))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--metrics", type=Path, default=REPO / "config" / "styles" / "subtitle_font_metrics.json")
    ap.add_argument("--out-aep", required=True)
    ap.add_argument("--out-dir", type=Path, default=Path(tempfile.gettempdir()) / "blast_font_lab")
    args = ap.parse_args()

    env = Environment(loader=FileSystemLoader(str(REPO / "templates")), autoescape=False)
    env.filters["tojson"] = _tojson_filter

    parts: List[str] = [
        "// jakson font lab — generated by scripts/subtitle_font_lab/build_jakson_font_lab.py\n" + CLEAN_GUARD_JS
    ]
    groups: Dict[str, List[str]] = {}
    rows = []
    for group, label, ps, focus_ps in MAIN_FONTS:
        jsx, summary = _font_jsx(env, label, ps, focus_ps, args.metrics)
        parts.append(f"// ===== {label} ({ps}) =====\n{jsx}\n")
        groups.setdefault(group, []).append(label)
        rows.append(summary)
    parts.append(_finalize_jsx(args.out_aep, groups))

    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_jsx = args.out_dir / "jakson_font_lab.jsx"
    out_jsx.write_text("".join(parts), encoding="utf-8")
    cols = list(rows[0])
    tsv = "\t".join(cols) + "\n" + "".join("\t".join(str(r[c]) for c in cols) + "\n" for r in rows)
    (args.out_dir / "layout_summary.tsv").write_text(tsv, encoding="utf-8")
    print(out_jsx)
    print(tsv)


if __name__ == "__main__":
    os.environ.setdefault("MODE", "dev")
    main()
