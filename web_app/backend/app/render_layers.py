"""Реальные слои рендера на AE-ноде (за гейтом BLAST_RENDER_MODE).

Слои (spec §1/§5.3):
  1. Assembly — python script_jakson.py: scenes → text_layers + футаж → .aep/комп.
  2. Effects  — пишем срез job.effects в __job.json, зовём run_job.jsx (afterfx headless),
                run_job вешает hook/transition/extra/звук/лого; ждём __status.json.
  3. Render   — aerender → mp4 → S3.

BLAST_RENDER_MODE=mock (по умолчанию) — воркер имитирует тайминги, эти функции не зовутся.
BLAST_RENDER_MODE=real — воркер зовёт эти функции (нужны AE/aerender/python на ноде).

Env-конфиг ноды:
  BLAST_AE_ROOT     — папка с run_job.jsx и manifest.json (…/АЕ/Хуки/Эффекты)
  BLAST_AFTERFX     — путь к afterfx.exe (Effects)
  BLAST_AERENDER    — путь к aerender.exe (Render)
  BLAST_SCRIPT_JAKSON — путь к script_jakson.py (Assembly)
  BLAST_WORK_DIR    — рабочая папка под .aep/скрины/выхлоп
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

MODE = os.getenv("BLAST_RENDER_MODE", "mock")

AE_ROOT = os.getenv("BLAST_AE_ROOT", "")
AFTERFX = os.getenv("BLAST_AFTERFX", "afterfx.exe")
AERENDER = os.getenv("BLAST_AERENDER", "aerender.exe")
SCRIPT_JAKSON = os.getenv("BLAST_SCRIPT_JAKSON", "")
WORK_DIR = os.getenv("BLAST_WORK_DIR", "")

_STATUS_TIMEOUT_S = 600     # ждём run_job не дольше 10 мин
_STATUS_POLL_S = 1.0


def effects_slice(variation: dict[str, Any]) -> dict[str, Any]:
    """Ровно то, что читает run_job.jsx (см. его хедер). null'ы run_job трактует как «нет эффекта»."""
    hook = variation.get("hook", {})
    resolved = hook.get("resolved", {})
    out: dict[str, Any] = {
        "dropTime": hook.get("dropTime"),
        "hook": resolved.get("hook"),
        "transition": resolved.get("transition"),
        "extra": resolved.get("extra"),
    }
    # прокидываем окно extra-грейда, если задано
    bg = variation.get("background", {})
    if bg.get("mode") == "photo" and bg.get("photoStyle"):
        out.setdefault("extra", resolved.get("extra"))
    return out


def _work_dir(job: dict[str, Any], idx: int) -> Path:
    base = Path(WORK_DIR or ".") / job["id"] / f"v{idx}"
    base.mkdir(parents=True, exist_ok=True)
    return base


def write_job_json(job: dict[str, Any], variation: dict[str, Any]) -> Path:
    """Записать срез effects в __job.json рядом с проектом. Путь → в BLAST_JOB для run_job."""
    wd = _work_dir(job, variation["index"])
    path = wd / "__job.json"
    path.write_text(json.dumps(effects_slice(variation), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def run_assembly(job: dict[str, Any], variation: dict[str, Any]) -> Path:
    """Слой 1: собрать комп (текст+футаж). Возвращает путь к .aep. Реальный вызов script_jakson."""
    wd = _work_dir(job, variation["index"])
    aep = wd / "project.aep"
    # scenes.json готовится из variation (subtitle.style/timingSource, lyrics, background.groups)
    scenes = wd / "scenes.json"
    scenes.write_text(json.dumps({
        "lyrics": job.get("renderJob", {}).get("lyrics", {}),
        "subtitle": variation.get("subtitle"),
        "background": variation.get("background"),
        "track": job.get("renderJob", {}).get("track"),
    }, ensure_ascii=False), encoding="utf-8")
    subprocess.run(["python", SCRIPT_JAKSON, str(scenes), "--out", str(aep)], check=True)
    return aep


def run_effects(job: dict[str, Any], variation: dict[str, Any], aep: Path) -> None:
    """Слой 2: run_job.jsx поверх собранной компы (hook/transition/extra/звук/лого)."""
    job_json = write_job_json(job, variation)
    wd = job_json.parent
    status = wd / "__status.json"
    if status.exists():
        status.unlink()
    env = {**os.environ, "BLAST_JOB": str(job_json)}
    # afterfx открывает проект и гоняет run_job.jsx headless
    subprocess.run([AFTERFX, "-noui", "-r", str(Path(AE_ROOT) / "run_job.jsx"), str(aep)],
                   check=True, env=env)
    _wait_status(Path(AE_ROOT) / "__status.json")  # run_job пишет статус рядом с собой


def _wait_status(status_path: Path) -> None:
    deadline = time.time() + _STATUS_TIMEOUT_S
    while time.time() < deadline:
        if status_path.exists():
            try:
                st = json.loads(status_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                st = {}
            if st.get("state") == "done":
                return
            if st.get("state") == "error":
                raise RuntimeError(f"run_job error: {st.get('msg')}")
        time.sleep(_STATUS_POLL_S)
    raise TimeoutError("run_job.jsx: превышен таймаут __status.json")


def run_render(job: dict[str, Any], variation: dict[str, Any], aep: Path) -> str:
    """Слой 3: aerender → mp4 → S3. Возвращает downloadUrl."""
    wd = _work_dir(job, variation["index"])
    out_mp4 = wd / f"{variation['index']}.mp4"
    subprocess.run([AERENDER, "-project", str(aep), "-comp", "Рабочая", "-output", str(out_mp4)], check=True)
    return _upload_s3(job, variation, out_mp4)


def _upload_s3(job: dict[str, Any], variation: dict[str, Any], mp4: Path) -> str:
    # прод: boto3/s3 клиент; ключ = output.s3Prefix/{index}.mp4
    prefix = job.get("renderJob", {}).get("output", {}).get("s3Prefix", f"videos/{job['userId']}/{job['id']}")
    key = f"{prefix}/{variation['index']}.mp4"
    # TODO: s3.upload_file(mp4, BUCKET, key)
    from .mock_store import BASE_S3
    return f"{BASE_S3}/{key}"
