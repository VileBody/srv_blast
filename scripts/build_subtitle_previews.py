"""Превью стилей субтитров: белый текст на чёрном фоне, без футажа (1080×1920, без подписей).

Пять боевых стилей (jakson / impulse / tape / trendy / brat) собираются ТЕМ ЖЕ кодом, что ролик:
план субтитров → build_text_layers / build_jsx_subtitles_overlay → build_render_plan_v1 →
templates/project_template.j2 (он сам пишет статус в контракте ноды). Отличие от ролика одно —
вместо футажа главная композиция с чёрным фоном.

Тайтлы Kant (kant_two_frames … kant_lani_style) — тот же прод-путь, что trendy/brat:
build_jsx_subtitles_overlay (он сам вшивает .aep тайтла в JSX).

Строки — свои, в духе русского TikTok-рэпа (ночь / трасса / неон / район); у каждого стиля своя.

Рендер — очередь рендер-ноды (POST /render), строго по одному, как build_fx_previews.py.

    python scripts/build_subtitle_previews.py --dry-run
    python scripts/build_subtitle_previews.py --only trendy_5th kant_gum
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("MODE", "dev")

from jinja2 import Environment, FileSystemLoader  # noqa: E402

from app.jsx_subtitles_builder import build_jsx_subtitles_overlay  # noqa: E402
from app.project_builder import _tojson_filter  # noqa: E402
from app.project_config import AE_PROJECT  # noqa: E402
from app.render_plan import build_render_plan_v1  # noqa: E402
from app.text_comp import build_text_layers  # noqa: E402
from build_fx_previews import submit, wait_render  # noqa: E402
from core.subtitles_mode import (  # noqa: E402
    KANT_TITLE_BY_MODE, SUBTITLES_MODE_KANT_TITLES, SUBTITLES_MODE_BRAT_5TH, SUBTITLES_MODE_IMPULSE_2ND, SUBTITLES_MODE_SCENES_3RD,
    SUBTITLES_MODE_TEMPLATE_4TH, SUBTITLES_MODE_TRENDY_5TH,
)

BLACK = [0.0, 0.0, 0.0]
LEAD_IN, TAIL = 0.4, 0.8

# ---- строки (свои, по вайбу; у каждого стиля своя) ----
JAKSON = [(["ночь горит", "неоном"], 2.2), (["я снова", "не дома"], 2.2)]                   # сцены по 2 строки
IMPULSE = [("long", "летим по трассе", 1.8), ("short", "быстрее", 0.9),
           ("long", "город спит без нас", 2.0), ("short", "газ", 0.9)]                         # long ≤ 16 знаков
TAPE = [("я не верю словам", {"словам"}, 1.8), ("решает только дело", {"дело"}, 2.0),
        ("мы выше этих стен", {"выше"}, 1.8)]
TRENDY = ("деньги шум и свет на мне", {"свет"}, 0.42)                                       # (текст, фокус, с/слово)
BRAT = ("тьма меня знает по имени", {"тьма"}, 0.42)
KANT_TITLES = {  # id → фраза (как у trendy/brat: слова подряд; на экраны ≤ 2 слов режет placeLine)
    "two-frames": "тёмный район мы не спим",
    "gum": "сладкий яд на губах",
    "matrix": "система снова сломана",
    "edit": "всё по кайфу без тормозов",
    "vhs": "кассета девяностых крутится",
    "lani-style": "моя ночь мои правила",
}


def _tokens(words: List[str], t: float, dur: float, focus=frozenset()) -> List[Dict[str, Any]]:
    step = dur / len(words)
    return [{"text": w, "t_start": round(t + i * step, 3), "t_end": round(t + (i + 0.9) * step, 3), "focus": w in focus}
            for i, w in enumerate(words)]


def _flow_segments(mode: str) -> List[Dict[str, Any]]:
    segs, t = [], LEAD_IN
    if mode == SUBTITLES_MODE_SCENES_3RD:  # Jakson на сайте = scenes_3rd
        for k, (lines, dur) in enumerate(JAKSON):
            words = " ".join(lines).split()
            segs.append({"id": f"seg_{k + 1}", "text": " ".join(words), "in_point": round(t, 3), "out_point": round(t + dur, 3),
                         "style_tag": "TYPE_1", "lines": lines, "tokens": _tokens(words, t, dur * 0.7),
                         "focus_word": None, "focus_style": None})
            t += dur
    elif mode == SUBTITLES_MODE_IMPULSE_2ND:
        for k, (kind, text, dur) in enumerate(IMPULSE):
            segs.append({"id": f"seg_{k + 1}", "text": text, "in_point": round(t, 3), "out_point": round(t + dur, 3),
                         "style_tag": kind, "lines": [text], "tokens": _tokens(text.split(), t, dur)})
            t += dur
    elif mode == SUBTITLES_MODE_TEMPLATE_4TH:
        for k, (text, focus, dur) in enumerate(TAPE):
            segs.append({"id": f"seg_{k + 1}", "text": text, "in_point": round(t, 3), "out_point": round(t + dur, 3),
                         "style_tag": "tape", "lines": [text], "tokens": _tokens(text.split(), t, dur, focus)})
            t += dur + 0.25
    else:
        raise ValueError(mode)
    return segs


def _comp(spec: Dict[str, Any], name: str, dur: float, *, bg=None) -> Dict[str, Any]:
    out = dict(spec, name=name, dur=dur, workAreaStart=0.0, workAreaDuration=dur)
    if bg is not None:
        out["bgColor"] = bg
    return out


def _text_precomp(text_name: str, main_name: str, dur: float) -> Dict[str, Any]:
    pl = AE_PROJECT["root_precomp_placement"]
    prop = lambda m, v: {"match_name": m, "value": v, "keyframes": [], "expression": None}  # noqa: E731
    return {
        "name": text_name, "type": "precomp", "in_point": 0.0, "out_point": dur, "z_index": 1,
        "props": {"tf_anchor": prop("ADBE Anchor Point", pl["anchor"]), "tf_position": prop("ADBE Position", pl["position"]),
                  "tf_scale": prop("ADBE Scale", pl["scale"]), "tf_rotation": prop("ADBE Rotate Z", pl["rotationZ"]),
                  "tf_opacity": prop("ADBE Opacity", pl["opacity"])},
        "effects": {},
        "text_data": {"layer_meta": {"comp_name_target": main_name, "startTime": 0.0, "enabled": True, "motionBlur": False,
                                     "collapseTransformation": False, "blendingModeCode": "5212"},
                      "precomp_source": {"comp_name": text_name}},
    }


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(str(ROOT / "templates")), autoescape=False)
    env.filters["tojson"] = _tojson_filter
    return env


def prod_style_jsx(mode: str) -> str:
    """Боевой render JSX стиля на чёрном фоне (без футажа)."""
    main, text, mine = "Main Render", "Текст", 'Текст "Mine"'
    overlays = {f"{k}_overlay_js": "" for k in ("f4", "f3", "f2", "f1", "f6", "f5", "frame")}
    jsx5 = mode in (SUBTITLES_MODE_TRENDY_5TH, SUBTITLES_MODE_BRAT_5TH) or mode in SUBTITLES_MODE_KANT_TITLES
    if jsx5:
        if mode in SUBTITLES_MODE_KANT_TITLES:
            text_s, focus, step = KANT_TITLES[KANT_TITLE_BY_MODE[mode]], set(), 0.5
        else:
            text_s, focus, step = TRENDY if mode == SUBTITLES_MODE_TRENDY_5TH else BRAT
        words, t = [], LEAD_IN
        for w in text_s.split():
            words.append({"word": w, "start": round(t, 3), "end": round(t + step * 0.9, 3), "focus": w in focus})
            t += step
        dur = round(words[-1]["end"] + TAIL, 3)
        cfg = {"subtitles_mode": mode}
        text_layers: List[Dict[str, Any]] = []
        # у тайтлов Kant цвет зашит в .aep — fill передавать нельзя (билдер это отвергает)
        fill = None if mode in SUBTITLES_MODE_KANT_TITLES else "#FFFFFF"
        subs = build_jsx_subtitles_overlay(mode=mode, word_timings=words, bpm=120.0, target_comp=main, fill_hex=fill)
    else:
        segs = _flow_segments(mode)
        dur = round(segs[-1]["out_point"] + TAIL, 3)
        cfg = {"subtitles_mode": mode, "subtitle_flow_plan": {"mode": mode, "clip": {"start": 0.0, "end": dur}, "segments": segs}}
        text_layers = build_text_layers(full_edit_config=cfg, text_comp_name=text, mine_comp_name=mine)
        subs = ""
    comps = [_comp(AE_PROJECT["main_comp"], main, dur, bg=BLACK), _comp(AE_PROJECT["text_comp"], text, dur),
             _comp(AE_PROJECT["mine_comp"], mine, dur)]
    plan = build_render_plan_v1(main_comp_name=main, subtitles_mode=mode, comps=comps,
                                footage_layers=[_text_precomp(text, main, dur)], text_layers=text_layers,
                                full_edit_config={"subtitles_mode": mode}, f3_media=[])
    return _env().get_template("project_template.j2").render(**plan.to_ae_payload(), **overlays, jsx_subtitles_js=subs)


def render(node: str, job_id: str, jsx: str, media: List[Dict[str, str]], entry: str, timeout: float) -> Path:
    rid = submit(node, {"job_id": job_id, "render_jsx": jsx, "media": media,
                        "entry_comp": entry, "output_relpath": "work/output.mp4"})
    return wait_render(node, rid, timeout=timeout)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--node", default="http://127.0.0.1:8000")
    ap.add_argument("--out", default=str(ROOT / "outputs/subtitle_previews_v2"), type=Path)
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--timeout", type=float, default=900)
    args = ap.parse_args()

    modes = [SUBTITLES_MODE_SCENES_3RD, SUBTITLES_MODE_IMPULSE_2ND, SUBTITLES_MODE_TEMPLATE_4TH,
             SUBTITLES_MODE_TRENDY_5TH, SUBTITLES_MODE_BRAT_5TH]
    modes += sorted(SUBTITLES_MODE_KANT_TITLES)
    jobs = [(f"subtitles__{m}", m) for m in modes]
    if args.only:
        jobs = [j for j in jobs if j[1] in args.only]
    args.out.mkdir(parents=True, exist_ok=True)
    if args.dry_run:
        for name, what in jobs:
            print(name, what)
        return 0

    failed = []
    for i, (name, what) in enumerate(jobs, 1):
        dst = args.out / f"{name}.mp4"
        if dst.exists() and not args.force:
            print(f"[{i}/{len(jobs)}] skip {name}")
            continue
        t0 = time.time()
        try:
            jsx = prod_style_jsx(what)
            out = render(args.node.rstrip("/"), f"subprev_{name}_{uuid.uuid4().hex[:6]}", jsx, [], "Main Render", args.timeout)
            shutil.copyfile(out, dst)
            print(f"[{i}/{len(jobs)}] ok   {name}  {time.time() - t0:.0f}с", flush=True)
        except Exception as e:  # noqa: BLE001
            failed.append(name)
            print(f"[{i}/{len(jobs)}] FAIL {name}: {e}", flush=True)
    print(f"готово: {len(jobs) - len(failed)}/{len(jobs)}" + (f"; упали: {' '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
