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
    # Subtitles (caption = label, so it shows "Пример: Trendy" / "Пример: Brat")
    "subtitles:trendy_5th": ("Субтитры примеры", "trendy.mp4", "Пример: Trendy"),
    "subtitles:brat_5th":   ("Субтитры примеры", "brat.MP4", "Пример: Brat"),
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
