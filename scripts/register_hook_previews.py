#!/usr/bin/env python3
"""Register example previews for the HOOK / SHAPE / EFFECT / SUBTITLE menu options.

Unlike footage buckets, these reels are pre-made by hand (already captioned). We
only need to send each to Telegram once and record the file_id(s), then the bot
shows them at the matching menu step.

Mapping key = "<category>:<bot_id>", where bot_id is the exact id the bot uses
(f4 device / f2 shape / f3 effect_hook|transition|extra / subtitles mode). Output:
data/hook_previews.json keyed by that composite, with {file_id, file_id_public}.

Run on the box that holds the example folders, with the bot tokens + backlog chat:
  TG_BOT_TOKEN=... TG_PREVIEW_SOURCE_BOT_TOKEN=... FOOTAGE_PREVIEW_BACKLOG_CHAT_ID=...
  python scripts/register_hook_previews.py            # all
  python scripts/register_hook_previews.py --only motion:swipe shape:rhomb
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

import requests

ROOT = Path(__file__).resolve().parents[1]

log = logging.getLogger("register_hook_previews")

DEFAULT_EXAMPLES_ROOT = Path(r"C:\Users\Пользователь\Desktop\АЕ")
STORE_PATH = ROOT / "data" / "hook_previews.json"

# key "category:bot_id" -> (folder under examples-root, filename, RU label)
EXAMPLES: Dict[str, Tuple[str, str, str]] = {
    # F4 «Движение» (device)
    "motion:swipe":       ("Хуки/Движение/Примеры", "swipeinbeat1.mp4", "Свайп"),
    "motion:tap":         ("Хуки/Движение/Примеры", "tapinbeat.mp4", "Тап"),
    "motion:pinch":       ("Хуки/Движение/Примеры", "pinch.mp4", "Зум"),
    "motion:holdfinger":  ("Хуки/Движение/Примеры", "holdfinger.mp4", "Задержи палец"),
    "motion:head":        ("Хуки/Движение/Примеры", "head.mp4", "Качай головой"),
    # F2 «Объект» (shape)
    "shape:rhomb":   ("Хуки/Лого и шейпы/Шейпы примеры", "examplerhomb.mp4", "Ромб"),
    "shape:square":  ("Хуки/Лого и шейпы/Шейпы примеры", "examplesquare.mp4", "Квадрат"),
    "shape:star1":   ("Хуки/Лого и шейпы/Шейпы примеры", "examplestar1.mp4", "Звезда-10"),
    "shape:star2":   ("Хуки/Лого и шейпы/Шейпы примеры", "examplestar2.mp4", "Звезда-5"),
    "shape:elipse":  ("Хуки/Лого и шейпы/Шейпы примеры", "examplecircle.mp4", "Эллипс"),
    # F3 «Эффект» — hook
    "effect_hook:hook_light":         ("Хуки/Эффекты/hook/Примеры хуков", "examplelight.mp4", "Молния"),
    "effect_hook:shutter_effect":     ("Хуки/Эффекты/hook/Примеры хуков", "exampleshuttereffect.mp4", "Затвор"),
    "effect_hook:flash_slow_shutter": ("Хуки/Эффекты/hook/Примеры хуков", "exampleflashslowahutter.mp4", "Слоу-шаттер"),
    "effect_hook:negative_zoom":      ("Хуки/Эффекты/hook/Примеры хуков", "examplenegativezoom.mp4", "Негатив-зум"),
    # F3 «Эффект» — transition
    "effect_transition:snap_wipe":     ("Хуки/Эффекты/transitions/Примеры переходов", "snapwipe.mp4", "Снап-вайп"),
    "effect_transition:minimax":       ("Хуки/Эффекты/transitions/Примеры переходов", "minimax.mp4", "Минимакс"),
    "effect_transition:invert_flash":  ("Хуки/Эффекты/transitions/Примеры переходов", "invertsflash.mp4", "Инверт"),
    "effect_transition:extract_flash": ("Хуки/Эффекты/transitions/Примеры переходов", "extractflashes.mp4", "Экстракт"),
    "effect_transition:flash_on_cuts": ("Хуки/Эффекты/transitions/Примеры переходов", "flashoncuts.mp4", "Вспышки"),
    # F3 «Эффект» — extra (stylize)
    "effect_extra:xerox":        ("Хуки/Эффекты/stylize/примеры стилей", "xerox.mp4", "Ксерокс"),
    "effect_extra:analog_glitch": ("Хуки/Эффекты/stylize/примеры стилей", "analogglitch.mp4", "Аналог-глитч"),
    "effect_extra:neon_extract": ("Хуки/Эффекты/stylize/примеры стилей", "neonextract.mp4", "Неон"),
    "effect_extra:old_camera":   ("Хуки/Эффекты/stylize/примеры стилей", "oldcamera.mp4", "Старая камера"),
    "effect_extra:blackwhite":   ("Хуки/Эффекты/stylize/примеры стилей", "blackwhite.mp4", "Ч/Б"),
    "effect_extra:crystal_glow": ("Хуки/Эффекты/stylize/примеры стилей", "crystalglow.mp4", "Crystal Glow"),
    "effect_extra:night_vision": ("Хуки/Эффекты/stylize/примеры стилей", "nightvision.mp4", "Night Vision"),
    "effect_extra:wave":         ("Хуки/Эффекты/stylize/примеры стилей", "wave.mp4", "Wave"),
    # Subtitles (caption = label, so it shows "Пример: Trendy" / "Пример: Brat")
    "subtitles:trendy_5th":   ("Субтитры примеры", "trendy.mp4", "Пример: Trendy"),
    "subtitles:brat_5th":     ("Субтитры примеры", "brat.MP4", "Пример: Brat"),
    "subtitles:impulse_2nd":  ("Субтитры примеры", "impulse.mp4", "Пример: Impulse"),
    "subtitles:scenes_3rd":   ("Субтитры примеры", "jakson.MP4", "Пример: Jakson"),
    "subtitles:template_4th": ("Субтитры примеры", "tape.mp4", "Пример: Tape"),
    # Kant Tools (kantfx) — примеры: Хуки/Эффекты/kantfx/примеры/<id>.mp4
    "effect_extra:cc_tritone_red":                 ("Хуки/Эффекты/kantfx/примеры", "cc_tritone_red.mp4", "Красный тритон"),
    "effect_extra:cc_neutral_tritone":             ("Хуки/Эффекты/kantfx/примеры", "cc_neutral_tritone.mp4", "Нейтральный тритон"),
    "effect_extra:cc_tritone_exposure":            ("Хуки/Эффекты/kantfx/примеры", "cc_tritone_exposure.mp4", "Тритон с пересветом"),
    "effect_extra:cc_colorista_looks":             ("Хуки/Эффекты/kantfx/примеры", "cc_colorista_looks.mp4", "Колориста"),
    "effect_extra:cc_lumetri_curves_looks":        ("Хуки/Эффекты/kantfx/примеры", "cc_lumetri_curves_looks.mp4", "Кино-грейд"),
    "effect_extra:cc_lumetri_contrast":            ("Хуки/Эффекты/kantfx/примеры", "cc_lumetri_contrast.mp4", "Контраст"),
    "effect_extra:cc_lumetri_mb":                  ("Хуки/Эффекты/kantfx/примеры", "cc_lumetri_mb.mp4", "Люметри"),
    "effect_extra:cc_looks_exposure_grain":        ("Хуки/Эффекты/kantfx/примеры", "cc_looks_exposure_grain.mp4", "Яркий лук"),
    "effect_extra:fx_paint_bucket":                ("Хуки/Эффекты/kantfx/примеры", "fx_paint_bucket.mp4", "Заливка"),
    "effect_extra:fx_cartoon_thermal":             ("Хуки/Эффекты/kantfx/примеры", "fx_cartoon_thermal.mp4", "Тепловизор"),
    "effect_extra:fx_invert_curves":               ("Хуки/Эффекты/kantfx/примеры", "fx_invert_curves.mp4", "Инверсия"),
    "effect_extra:cc_lumetri_looks_grain":         ("Хуки/Эффекты/kantfx/примеры", "cc_lumetri_looks_grain.mp4", "Мягкая плёнка"),
    "effect_extra:cc_looks_curves_grain":          ("Хуки/Эффекты/kantfx/примеры", "cc_looks_curves_grain.mp4", "Плёночный грейд"),
    "effect_extra:cc_double_looks_grain":          ("Хуки/Эффекты/kantfx/примеры", "cc_double_looks_grain.mp4", "Двойной лук"),
    "effect_extra:cc_film_grain_looks_a":          ("Хуки/Эффекты/kantfx/примеры", "cc_film_grain_looks_a.mp4", "Зерно A"),
    "effect_extra:cc_film_grain_looks_b":          ("Хуки/Эффекты/kantfx/примеры", "cc_film_grain_looks_b.mp4", "Зерно B"),
    "effect_extra:cc_mb_suite_grain":              ("Хуки/Эффекты/kantfx/примеры", "cc_mb_suite_grain.mp4", "Мэджик Буллет"),
    "effect_extra:cc_looks_curves_noise":          ("Хуки/Эффекты/kantfx/примеры", "cc_looks_curves_noise.mp4", "Шумный лук"),
    "effect_extra:fx_film_damage":                 ("Хуки/Эффекты/kantfx/примеры", "fx_film_damage.mp4", "Плёнка"),
    "effect_extra:fx_universe_vhs":                ("Хуки/Эффекты/kantfx/примеры", "fx_universe_vhs.mp4", "VHS"),
    "effect_extra:fx_scanlines_displace":          ("Хуки/Эффекты/kantfx/примеры", "fx_scanlines_displace.mp4", "Скан-линии"),
    "effect_extra:fx_etching_fisheye":             ("Хуки/Эффекты/kantfx/примеры", "fx_etching_fisheye.mp4", "Гравюра"),
    "effect_extra:fx_luma_key_noise":              ("Хуки/Эффекты/kantfx/примеры", "fx_luma_key_noise.mp4", "Люма-шум"),
    "effect_extra:fx_cross_glitch":                ("Хуки/Эффекты/kantfx/примеры", "fx_cross_glitch.mp4", "Кросс-глитч"),
    "effect_extra:fx_jpeg_damage":                 ("Хуки/Эффекты/kantfx/примеры", "fx_jpeg_damage.mp4", "JPEG-артефакты"),
    "effect_extra:fx_jpeg_tritone":                ("Хуки/Эффекты/kantfx/примеры", "fx_jpeg_tritone.mp4", "JPEG-тритон"),
    "effect_extra:fx_chroma_distort":              ("Хуки/Эффекты/kantfx/примеры", "fx_chroma_distort.mp4", "Хроматика"),
    "effect_extra:fx_bcc_displace":                ("Хуки/Эффекты/kantfx/примеры", "fx_bcc_displace.mp4", "Смещение"),
    "effect_extra:fx_turbulent_noise":             ("Хуки/Эффекты/kantfx/примеры", "fx_turbulent_noise.mp4", "Турбулентность"),
    "effect_extra:fx_signal_mesh":                 ("Хуки/Эффекты/kantfx/примеры", "fx_signal_mesh.mp4", "Сигнал"),
    "effect_extra:fx_directional_blur":            ("Хуки/Эффекты/kantfx/примеры", "fx_directional_blur.mp4", "Смаз"),
    "effect_extra:fx_mojo_glow_grade":             ("Хуки/Эффекты/kantfx/примеры", "fx_mojo_glow_grade.mp4", "Моджо-свечение"),
    "effect_extra:fx_hotspot_flicker":             ("Хуки/Эффекты/kantfx/примеры", "fx_hotspot_flicker.mp4", "Блики"),
    "effect_extra:fx_sapphire_flicker":            ("Хуки/Эффекты/kantfx/примеры", "fx_sapphire_flicker.mp4", "Мерцание"),
    "effect_extra:fx_lens_blur":                   ("Хуки/Эффекты/kantfx/примеры", "fx_lens_blur.mp4", "Линза"),
    "effect_transition:sh_twitch_flicker":         ("Хуки/Эффекты/kantfx/примеры", "sh_twitch_flicker.mp4", "Твич"),
    "effect_transition:sh_chroma_lens_shake":      ("Хуки/Эффекты/kantfx/примеры", "sh_chroma_lens_shake.mp4", "Хрома-тряска"),
    "effect_transition:sh_dissolve_shake":         ("Хуки/Эффекты/kantfx/примеры", "sh_dissolve_shake.mp4", "Тряска-растворение"),
    "effect_transition:sh_tile_shake":             ("Хуки/Эффекты/kantfx/примеры", "sh_tile_shake.mp4", "Тряска с повтором"),
    "effect_transition:sh_puddle_shake":           ("Хуки/Эффекты/kantfx/примеры", "sh_puddle_shake.mp4", "Рябь"),
    "effect_transition:sh_puddle_warp_shake":      ("Хуки/Эффекты/kantfx/примеры", "sh_puddle_warp_shake.mp4", "Рябь с искажением"),
    "effect_transition:of_edge_radial":            ("Хуки/Эффекты/kantfx/примеры", "of_edge_radial.mp4", "Контур"),
    "effect_transition:of_exposure_flicker":       ("Хуки/Эффекты/kantfx/примеры", "of_exposure_flicker.mp4", "Пересвет"),
    "effect_transition:of_invert_flash":           ("Хуки/Эффекты/kantfx/примеры", "of_invert_flash.mp4", "Негатив-вспышка"),
    "effect_transition:of_linear_wipe":            ("Хуки/Эффекты/kantfx/примеры", "of_linear_wipe.mp4", "Шторка"),
    "effect_transition:of_rgb_glitch_burst":       ("Хуки/Эффекты/kantfx/примеры", "of_rgb_glitch_burst.mp4", "RGB-взрыв"),
    "effect_transition:of_fisheye_scan":           ("Хуки/Эффекты/kantfx/примеры", "of_fisheye_scan.mp4", "Рыбий глаз"),
}


def _probe_video_dims(path: Path) -> Tuple[int, int, int]:
    """(width, height, duration_sec) via ffprobe; (0,0,0) if unavailable. Telegram
    needs explicit width/height or it can mis-render aspect (e.g. files without
    display_aspect_ratio metadata get squished)."""
    import shutil as _sh
    import subprocess as _sp
    ffprobe = (os.environ.get("FFPROBE_BIN") or "").strip() or _sh.which("ffprobe") or "ffprobe"
    try:
        out = _sp.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height:format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=60,
        ).stdout.split()
        w = int(float(out[0])); h = int(float(out[1]))
        dur = int(float(out[2])) if len(out) > 2 else 0
        return w, h, dur
    except Exception:
        return 0, 0, 0


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def capture_telegram_file_id(*, token: str, chat_id: str, video_path: Path) -> str:
    """Send the video (no caption) via `token`; return the resulting file_id.
    Direct connection (bypass the Windows system SOCKS proxy — same as the S3/
    asset_ui calls; Telegram is reachable directly here). Sends explicit
    width/height/duration so Telegram renders the correct (9:16) aspect."""
    sess = requests.Session()
    sess.trust_env = False
    url = f"https://api.telegram.org/bot{token}/sendVideo"
    w, h, dur = _probe_video_dims(video_path)
    with open(video_path, "rb") as fh:
        files = {"video": (video_path.name, fh, "video/mp4")}
        data = {"chat_id": str(chat_id), "supports_streaming": "true"}
        if w and h:
            data["width"] = str(w)
            data["height"] = str(h)
        if dur:
            data["duration"] = str(dur)
        resp = sess.post(url, data=data, files=files, timeout=300)
    resp.raise_for_status()
    payload = resp.json()
    if not payload.get("ok"):
        raise RuntimeError(f"telegram sendVideo not ok: {payload}")
    fid = str(((payload.get("result") or {}).get("video") or {}).get("file_id") or "").strip()
    if not fid:
        raise RuntimeError(f"telegram sendVideo returned no video.file_id: {payload}")
    return fid


def _capture_both(video_path: Path) -> Tuple[str, str]:
    backlog = (os.environ.get("FOOTAGE_PREVIEW_BACKLOG_CHAT_ID")
               or os.environ.get("MANAGER_CHAT_ID") or "").strip()
    file_id = file_id_public = ""
    team = (os.environ.get("TG_BOT_TOKEN") or "").strip()
    if team and backlog:
        file_id = capture_telegram_file_id(token=team, chat_id=backlog, video_path=video_path)
    else:
        log.warning("file_id skipped (TG_BOT_TOKEN / backlog chat not set)")
    pub = (os.environ.get("TG_PREVIEW_SOURCE_BOT_TOKEN") or "").strip()
    pub_chat = (os.environ.get("TG_PREVIEW_SOURCE_CHAT_ID") or backlog).strip()
    if pub and pub_chat:
        file_id_public = capture_telegram_file_id(token=pub, chat_id=pub_chat, video_path=video_path)
    else:
        log.info("file_id_public skipped (TG_PREVIEW_SOURCE_BOT_TOKEN / chat not set)")
    return file_id, file_id_public


def _load_store() -> Dict:
    if STORE_PATH.exists():
        obj = json.loads(STORE_PATH.read_text(encoding="utf-8"))
        if isinstance(obj, dict) and isinstance(obj.get("previews"), dict):
            return obj
    return {"version": 1, "previews": {}}


def _save_store(store: Dict) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    previews = store.get("previews") or {}
    ordered = {k: previews[k] for k in sorted(previews.keys())}
    STORE_PATH.write_text(
        json.dumps({"version": 1, "previews": ordered}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="Register hook/shape/effect/subtitle example previews")
    ap.add_argument("--only", nargs="*", default=None, help="specific keys, e.g. motion:swipe shape:rhomb")
    ap.add_argument("--force", action="store_true", help="re-send even if file_id already set")
    ap.add_argument("--examples-root", default=str(DEFAULT_EXAMPLES_ROOT))
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    root = Path(args.examples_root)
    keys = args.only if args.only else list(EXAMPLES.keys())
    unknown = [k for k in keys if k not in EXAMPLES]
    if unknown:
        raise SystemExit(f"unknown keys: {unknown}")

    store = _load_store()
    sent = skipped = missing = failed = 0
    for key in keys:
        folder, filename, label = EXAMPLES[key]
        path = root / folder / filename
        if not path.exists():
            log.warning("missing example for %s: %s", key, path)
            missing += 1
            continue
        existing = (store.get("previews") or {}).get(key) or {}
        if not args.force and str(existing.get("file_id") or "").strip():
            log.info("skip %s (file_id already set)", key)
            skipped += 1
            continue
        try:
            file_id, file_id_public = _capture_both(path)
        except Exception as e:
            log.exception("FAILED %s: %r", key, e)
            failed += 1
            continue
        store.setdefault("previews", {})[key] = {
            "label": label,
            "file_id": file_id,
            "file_id_public": file_id_public,
            "source": f"{folder}/{filename}",
            "built_at": _now_iso(),
        }
        _save_store(store)
        log.info("registered %s file_id=%s public=%s", key,
                 (file_id[:12] + "…") if file_id else "-",
                 (file_id_public[:12] + "…") if file_id_public else "-")
        sent += 1
    log.info("done: sent=%d skipped=%d missing=%d failed=%d store=%s",
             sent, skipped, missing, failed, STORE_PATH)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
