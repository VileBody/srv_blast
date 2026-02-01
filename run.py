#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional, Tuple

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_env() -> None:
    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)
        print(f"[env] loaded: {env_path}")
    else:
        print(f"[env] .env not found at: {env_path} (ok, using process env)")


def _require_env(key: str) -> str:
    v = (os.environ.get(key, "") or "").strip()
    if not v:
        raise RuntimeError(f"Missing required env var: {key}")
    return v


def _path(p: str) -> Path:
    return (REPO_ROOT / p).resolve()


def _pick_audio_source(repo_root: Path) -> Tuple[str, str]:
    """
    Returns (file_name, file_path) for the reference audio track to be placed into footage_config.json.
    Priority:
      1) AUDIO_FILE_PATH env (absolute or relative), if exists
      2) first file in AUDIO_DIR env (default repo_root/audio)
    """
    p_env = (os.environ.get("AUDIO_FILE_PATH", "") or "").strip()
    if p_env:
        p = Path(p_env)
        if not p.is_absolute():
            p = (repo_root / p).resolve()
        if not p.exists():
            raise FileNotFoundError(f"AUDIO_FILE_PATH points to missing file: {p}")
        name = (os.environ.get("AUDIO_FILE_NAME", "") or "").strip() or p.name
        return name, str(p)

    audio_dir = Path(os.environ.get("AUDIO_DIR", str(repo_root / "audio"))).resolve()
    exts = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".mov", ".mp4"}
    if audio_dir.exists():
        files = [p for p in sorted(audio_dir.iterdir()) if p.is_file() and p.suffix.lower() in exts]
        if files:
            p = files[0]
            return p.name, str(p)

    raise RuntimeError(
        "No audio source configured.\n"
        "Set AUDIO_FILE_PATH in .env (recommended), or put an audio file into ./audio/ and set AUDIO_DIR if needed."
    )


def _patch_footage_config_audio(footage_config_path: Path, *, audio_name: str, audio_path: str) -> bool:
    """
    Ensure footage_config.json has a valid audio_only layer:
      - file_name/file_path filled
      - enabled=true (so audio actually plays)
      - audio_enabled=true, video_enabled=false
    Returns True if file was modified.
    """
    d = json.loads(footage_config_path.read_text(encoding="utf-8"))
    layers = d.get("layers")
    if not isinstance(layers, list):
        raise RuntimeError(f"footage_config.json has no layers[]: {footage_config_path}")

    changed = False
    found = False

    for it in layers:
        if not isinstance(it, dict):
            continue
        if str(it.get("type")) != "audio_only":
            continue

        found = True

        if it.get("file_name") != audio_name:
            it["file_name"] = audio_name
            changed = True

        if it.get("file_path") != audio_path:
            it["file_path"] = audio_path
            changed = True

        # critical: layer must be enabled to play audio
        if bool(it.get("enabled", False)) is not True:
            it["enabled"] = True
            changed = True

        # make intent explicit
        if bool(it.get("audio_enabled", True)) is not True:
            it["audio_enabled"] = True
            changed = True

        if bool(it.get("video_enabled", False)) is not False:
            it["video_enabled"] = False
            changed = True

    if not found:
        raise RuntimeError(
            "footage_config.json has no layer with type='audio_only'. "
            "Either add it, or adjust the pipeline expectations."
        )

    if changed:
        footage_config_path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Blast pipeline: Gemini -> configs -> AE JSX")
    parser.add_argument("--skip-llm", action="store_true", help="Do not call Gemini, only build AE project from existing data/*.json")
    parser.add_argument("--skip-ae", action="store_true", help="Do not build AE JSX, only generate configs via Gemini")
    parser.add_argument("--full-edit", default="data/full_edit_config.json", help="Path to full_edit_config.json relative to repo root")
    parser.add_argument("--footage", default="data/footage_config.json", help="Path to footage_config.json relative to repo root")
    parser.add_argument("--out-dir", default="out", help="Output directory relative to repo root")
    args = parser.parse_args()

    _load_env()

    full_edit_config_path = _path(args.full_edit)
    footage_config_path = _path(args.footage)
    out_dir = _path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1) LLM step: generate configs
    if not args.skip_llm:
        _require_env("GEMINI_API_KEY")
        _require_env("GEMINI_MODEL")

        from mlcore.gemini_orchestrator import build_all_via_gemini_one_call  # noqa: E402

        out_map = build_all_via_gemini_one_call()
        print("\n[OK] Gemini generated:")
        for k, p in out_map.items():
            print(f"  - {k}: {p}")

    # Ensure required config files exist before AE build
    if not full_edit_config_path.exists():
        raise FileNotFoundError(f"Missing: {full_edit_config_path}")
    if not footage_config_path.exists():
        raise FileNotFoundError(f"Missing: {footage_config_path}")

    # 1.5) Patch audio layer in footage_config.json (so audio actually plays in AE)
    audio_name, audio_path = _pick_audio_source(REPO_ROOT)
    changed = _patch_footage_config_audio(footage_config_path, audio_name=audio_name, audio_path=audio_path)
    print(f"[audio] source: {audio_name} @ {audio_path}")
    print(f"[audio] patched footage_config.json: {'YES' if changed else 'NO (already ok)'}")

    # 2) AE build step: produce render_full.jsx
    if not args.skip_ae:
        from app.project_builder import build_full_project  # noqa: E402

        out_json, out_jsx = build_full_project(
            repo_root=REPO_ROOT,
            full_edit_config_path=full_edit_config_path,
            footage_config_path=footage_config_path,
            out_dir=out_dir,
        )

        print("\n[OK] AE project build:")
        print(f"  - json: {out_json}")
        print(f"  - jsx:  {out_jsx}")
        print(f"  - AE logs: {out_dir / 'logs'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
