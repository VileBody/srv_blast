"""Превью F3-эффектов (склейки и стилизации) на сырых клипах одной группы вайба.

Формат (1080×1920, без подписей):
  склейка     — 2 с: клип A 1 с → переход на склейке 1.0 с → клип B 1 с (оба из одной группы);
  стилизация  — 3 с одного клипа: первые 1.5 с со стилизацией, вторые 1.5 с без неё.

Эффект ставится тем же блоком, что в проде (overlay.build_overlay_jsx): переход — через путь
монтажного стола на склейку 1.0 с, стилизация — окном [0, 1.5]. Так превью совпадает с роликом.

Исходники — кеш клипов рендер-ноды (C:\\ae_jobs\\_cfr_cache, сырой футаж из прода, уже CFR):
клипы раскладываются по актуальным группам вайба (load_visual_catalog) через clip_ids из
data/footage_bucket_previews.json. Группа и клипы для эффекта выбираются по хэшу id —
перегенерация даёт те же кадры.

Рендер — очередь самой ноды (POST /render, inline JSX + media[]), строго по одному превью:
нода боевая, её слоты делятся с роликами пользователей. Клипы отдаются ноде временным
HTTP-сервером на 127.0.0.1 (нода на этой же машине); S3 не нужен ни для входа, ни для выхода.

    python scripts/build_fx_previews.py --dry-run          # план: эффект → группа → клипы
    python scripts/build_fx_previews.py --only sh_twitch_flicker fx_cross_glitch
    python scripts/build_fx_previews.py                    # все (пропускает готовые; --force)
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import http.server
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mlcore.footage_visual_catalog import load_visual_catalog  # noqa: E402
from mlcore.hooks.f3_effect import overlay  # noqa: E402

TEMPLATE = ROOT / "templates/fx_preview/fx_preview_template.jsx"
BUCKET_STORE = ROOT / "data/footage_bucket_previews.json"
W, H, FPS = 1080, 1920, 23.976
CUT_AT = 1.0          # склейка: по 1 с с каждой стороны
STYLE_LEN, STYLE_SPLIT = 3.0, 1.5
SOURCE_START = 1.0    # первую секунду клипа пропускаем (часто затемнение/вход)


def _h(*parts: str) -> str:
    return hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()


def vibe_pools(cache_dir: Path, *, min_clips: int = 2) -> dict[str, list[Path]]:
    """Актуальная группа вайба → клипы из кеша ноды (только группы с ≥ min_clips)."""
    cached: dict[str, Path] = {}
    for p in cache_dir.glob("*.mp4"):
        m = re.match(r"(\d{6,})", p.name)
        if m:
            cached.setdefault(m.group(1), p)
    store = json.loads(BUCKET_STORE.read_text(encoding="utf-8"))
    records = store.get("previews", store)
    pools: dict[str, list[Path]] = {}
    for bucket in load_visual_catalog():
        bid = str(getattr(bucket, "bucket_id", ""))
        rec = records.get(bid) or {}
        clips = [cached[c.rstrip("_")] for c in rec.get("clip_ids") or [] if c.rstrip("_") in cached]
        if len(clips) >= min_clips:
            pools[bid] = clips
    if not pools:
        raise SystemExit(f"в {cache_dir} нет клипов ни одной актуальной группы вайба")
    return pools


def effects() -> list[tuple[str, str]]:
    """(категория превью, id) — все выбираемые склейки и стилизации."""
    out = [("effect_transition", t) for t in overlay.F3_TRANSITIONS]
    out += [("effect_extra", e) for e in overlay.F3_EXTRAS]
    return out


SCENE_THRESHOLD = 0.3


@functools.lru_cache(maxsize=None)
def has_cut(path: Path, dur: float) -> bool:
    """Есть ли смена сцены в [SOURCE_START, +dur) клипа: пины часто сами смонтированы,
    а «до/после» стилизации и кадры склейки должны быть одним планом."""
    r = subprocess.run(
        ["ffmpeg", "-v", "info", "-ss", str(SOURCE_START), "-t", str(dur), "-i", str(path),
         "-vf", f"select='gt(scene,{SCENE_THRESHOLD})',showinfo", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="ignore")
    return "pts_time:" in r.stderr


def plan_for(category: str, effect_id: str, pools: dict[str, list[Path]]) -> dict:
    buckets = sorted(pools)
    bucket = buckets[int(_h("bucket", effect_id), 16) % len(buckets)]
    clips = sorted(pools[bucket], key=lambda p: _h("clip", effect_id, p.name))
    need = CUT_AT if category == "effect_transition" else STYLE_LEN
    # чистые планы вперёд (порядок внутри — тот же хэш); если чистых мало — добираем как есть
    clips = [c for c in clips if not has_cut(c, need)] + [c for c in clips if has_cut(c, need)]
    if category == "effect_transition":
        parts = [(clips[0], 0.0, CUT_AT), (clips[1], CUT_AT, CUT_AT)]
        duration = 2 * CUT_AT
    else:
        parts = [(clips[0], 0.0, STYLE_LEN)]
        duration = STYLE_LEN
    return {"category": category, "id": effect_id, "bucket": bucket, "duration": duration,
            "clips": [{"file": p, "at": at, "dur": dur} for p, at, dur in parts]}


def overlay_jsx(plan: dict) -> str:
    eid, end = plan["id"], plan["duration"]
    if plan["category"] == "effect_extra":
        return overlay.build_overlay_jsx(extra_ranges=[{"id": eid, "start": 0.0, "end": STYLE_SPLIT}], drop_time=end)
    if eid in overlay.F3_CUT_TRANSITIONS:
        return overlay.build_overlay_jsx(cut_transitions=[{"t": CUT_AT, "id": eid}], drop_time=end)
    # layer_shake не работает по списку склеек — общий переход на весь ролик
    return overlay.build_overlay_jsx(transition=eid, drop_time=end)


def render_jsx(plan: dict) -> tuple[str, list[dict], list[tuple[Path, str]]]:
    """JSX задания, media[] для ноды и (источник, ascii-имя) для раздачи."""
    clips, served = [], []
    for n, c in enumerate(plan["clips"]):
        name = f"clip{n}_{_h(c['file'].name)[:10]}.mp4"
        served.append((c["file"], name))
        clips.append({"relpath": f"media/video/{name}", "source_start": SOURCE_START, "at": c["at"], "dur": c["dur"]})
    spec = {"width": W, "height": H, "fps": FPS, "duration": plan["duration"], "comp_name": "FX Preview", "clips": clips}
    text = TEMPLATE.read_text(encoding="utf-8")
    for marker in ("/*__PREVIEW_DATA__*/", "/*__F3_OVERLAY__*/"):
        if text.count(marker) != 1:
            raise RuntimeError(f"{TEMPLATE.name}: маркер {marker} должен встречаться ровно один раз")
    text = text.replace("/*__PREVIEW_DATA__*/", "var PREVIEW = " + json.dumps(spec, ensure_ascii=False) + ";", 1)
    text = text.replace("/*__F3_OVERLAY__*/", overlay_jsx(plan), 1)
    return text, [{"relpath": c["relpath"]} for c in clips], served


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D401
        pass


def serve(directory: Path) -> tuple[http.server.ThreadingHTTPServer, int]:
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=str(directory)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def wait_node_idle(node: str, *, max_wait: float = 3600, poll: float = 15) -> None:
    """Ждать, пока нода готова и простаивает: превью идут только в паузах боевых рендеров."""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            r = requests.get(f"{node}/health", timeout=10).json().get("render") or {}
            if r.get("ready") and not r.get("running") and not r.get("queued") and not r.get("unfinished"):
                return
        except requests.RequestException:
            pass
        time.sleep(poll)
    raise RuntimeError(f"нода не освободилась за {max_wait:.0f}с")


def submit(node: str, payload: dict, *, attempts: int = 20) -> str:
    """POST /render; 503 (нода занята/перезапускает AE) — ждём простоя и повторяем."""
    for _ in range(attempts):
        wait_node_idle(node)
        r = requests.post(f"{node}/render", json=payload, timeout=60)
        if r.status_code == 503:
            time.sleep(30)
            continue
        r.raise_for_status()
        return str(r.json()["render_id"])
    raise RuntimeError("нода отвечает 503 на все попытки")


def wait_render(node: str, rid: str, *, timeout: float) -> Path:
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = requests.get(f"{node}/render/{rid}", timeout=30).json()
        if st.get("status") == "succeeded" and st.get("success"):
            src = Path(str(st.get("output_path") or ""))
            if not src.is_file():
                raise RuntimeError(f"нода сообщила успех, но файла нет: {src}")
            return src
        if st.get("status") == "failed" or (st.get("status") == "succeeded" and not st.get("success")):
            raise RuntimeError(str(st.get("message") or st))
        time.sleep(3)
    raise RuntimeError(f"таймаут {timeout:.0f}с (render_id={rid})")


def render_one(plan: dict, *, node: str, stage: Path, port: int, out: Path, timeout: float) -> Path:
    jsx, media, served = render_jsx(plan)
    for src, name in served:
        dst = stage / name
        if not dst.exists():
            shutil.copyfile(src, dst)
    for m in media:
        m["url"] = f"http://127.0.0.1:{port}/{Path(m['relpath']).name}"
    job_id = f"fxprev_{plan['id']}_{uuid.uuid4().hex[:6]}"
    rid = submit(node, {"job_id": job_id, "render_jsx": jsx, "media": media,
                        "entry_comp": "FX Preview", "output_relpath": "work/output.mp4"})
    dst = out / f"{plan['category']}__{plan['id']}.mp4"
    shutil.copyfile(wait_render(node, rid, timeout=timeout), dst)
    return dst


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--node", default="http://127.0.0.1:8000")
    ap.add_argument("--cache", default=r"C:\ae_jobs\_cfr_cache", type=Path)
    ap.add_argument("--out", default=str(ROOT / "outputs/fx_previews_v2"), type=Path)
    ap.add_argument("--only", nargs="*", default=None, help="id эффектов")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--timeout", type=float, default=600)
    args = ap.parse_args()

    pools = vibe_pools(args.cache)
    plans = [plan_for(c, e, pools) for c, e in effects() if not args.only or e in args.only]
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "plan.json").write_text(json.dumps(
        [{**p, "clips": [{**c, "file": c["file"].name} for c in p["clips"]]} for p in plans],
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"групп вайба с клипами: {len(pools)}; превью: {len(plans)}")
    if args.dry_run:
        for p in plans:
            print(f"  {p['category']:<18} {p['id']:<24} {p['bucket']:<38} {' | '.join(c['file'].name[:22] for c in p['clips'])}")
        return 0

    stage = args.out / "_serve"
    stage.mkdir(exist_ok=True)
    srv, port = serve(stage)
    failed = []
    try:
        for i, p in enumerate(plans, 1):
            dst = args.out / f"{p['category']}__{p['id']}.mp4"
            if dst.exists() and not args.force:
                print(f"[{i}/{len(plans)}] skip {p['id']} (есть)")
                continue
            t0 = time.time()
            try:
                render_one(p, node=args.node.rstrip("/"), stage=stage, port=port, out=args.out, timeout=args.timeout)
                print(f"[{i}/{len(plans)}] ok   {p['id']}  {time.time() - t0:.0f}с", flush=True)
            except Exception as e:  # noqa: BLE001 — одно упавшее превью не останавливает остальные
                failed.append(p["id"])
                print(f"[{i}/{len(plans)}] FAIL {p['id']}: {e}", flush=True)
    finally:
        srv.shutdown()
        shutil.rmtree(stage, ignore_errors=True)
    print(f"готово: {len(plans) - len(failed)}/{len(plans)}" + (f"; упали: {' '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
