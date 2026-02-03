# mlcore/gemini_orchestrator.py
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import logging

from mlcore.descriptions_bundle import build_descriptions_bundle_from_inventory
from mlcore.gemini_client import GeminiClient, GeminiSettings
from mlcore.gemini_call import call_full_plan_once, pick_audio_files
from mlcore.gemini_postprocess import render_all_steps
from mlcore.prompts import build_system_instruction, build_user_prompt


ROOT = Path(__file__).resolve().parent.parent


def _stamp() -> str:
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S")


def _get_logger() -> logging.Logger:
    logger = logging.getLogger("mlcore.gemini_orchestrator")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        logger.propagate = False
        fmt = logging.Formatter("[%(asctime)s][%(levelname)s] %(message)s")

        log_dir = ROOT / "ml_logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        file_path = log_dir / f"orchestrator_full_{_stamp()}.log"
        fh = logging.FileHandler(file_path, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)

        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        logger.addHandler(sh)

        logger.info("logger_ready file=%s", str(file_path))

    return logger


def _load_footage_inventory(inv_path: Path) -> Dict[str, Any]:
    d = json.loads(inv_path.read_text(encoding="utf-8"))
    if not isinstance(d, dict):
        raise ValueError(f"Invalid inventory JSON: {inv_path}")
    if not isinstance(d.get("assets"), list):
        raise ValueError(f"Inventory must contain 'assets': {inv_path}")
    return d


def _as_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        x = float(v)
        if x <= 0:
            return None
        return x
    except Exception:
        return None


def _compact_assets_for_prompt(inv: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Compact assets list is embedded directly into the user prompt.
    We include duration_sec so Gemini can obey no-gaps feasibility.
    """
    out: List[Dict[str, Any]] = []
    for it in (inv.get("assets") or []):
        if not isinstance(it, dict):
            continue

        fn = str(it.get("file_name") or "").strip()
        sw = it.get("src_w")
        sh = it.get("src_h")

        if not fn or sw is None or sh is None:
            continue

        meta = it.get("meta") if isinstance(it.get("meta"), dict) else {}

        dur = _as_float(it.get("duration_sec"))
        if dur is None:
            dur = _as_float(it.get("duration"))
        if dur is None:
            dur = _as_float(meta.get("duration_sec"))
        if dur is None:
            dur = _as_float(meta.get("duration"))

        row: Dict[str, Any] = {"file_name": fn, "src_w": int(sw), "src_h": int(sh)}
        if dur is not None:
            row["duration_sec"] = float(dur)

        out.append(row)

    if not out:
        raise RuntimeError("No valid assets in footage inventory")
    return out


def _resolve_bundle_path(out_dir_hint: Path) -> Path:
    raw = (os.environ.get("DESCRIPTIONS_BUNDLE_PATH") or "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (ROOT / p).resolve()
        return p.resolve()
    return (out_dir_hint / "descriptions_bundle.json").resolve()


def _read_bundle_inline(bundle_path: Path, *, max_chars: int) -> str:
    s = bundle_path.read_text(encoding="utf-8")
    if len(s) <= max_chars:
        return s
    return s[:max_chars]


def build_all_via_gemini_one_call() -> Dict[str, Path]:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    model = os.environ.get("GEMINI_MODEL", "").strip()
    proxy = os.environ.get("OUTBOUND_PROXY", "").strip()
    temperature = float(os.environ.get("GEMINI_TEMPERATURE", "0") or "0")

    logger = _get_logger()
    logger.info("start build_all_via_gemini_one_call model=%s", model)

    if not api_key:
        raise RuntimeError("Missing GEMINI_API_KEY in env")
    if not model:
        raise RuntimeError("Missing GEMINI_MODEL in env")

    client = GeminiClient(
        GeminiSettings(
            api_key=api_key,
            model=model,
            temperature=temperature,
            proxy=proxy,
            timeout_s=float(os.environ.get("GEMINI_TIMEOUT_S", "120") or "120"),
            max_attempts=int(os.environ.get("GEMINI_MAX_ATTEMPTS", "1") or "1"),
        ),
        logger=logger,
    )

    inv_path = Path(
        os.environ.get("FOOTAGE_INVENTORY_JSON", str(ROOT / "data" / "footage_inventory.json"))
    ).resolve()
    if not inv_path.exists():
        raise FileNotFoundError(f"FOOTAGE_INVENTORY_JSON missing: {inv_path}")

    inv = _load_footage_inventory(inv_path)
    assets_for_prompt = _compact_assets_for_prompt(inv)

    out_dir = Path(os.environ.get("OUT_DIR", str(ROOT / "out"))).resolve()
    logs_dir = out_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    bundle_path = _resolve_bundle_path(out_dir)

    # Ensure bundle exists (fallback)
    if not bundle_path.exists():
        logger.info("bundle missing -> building fallback bundle=%s", str(bundle_path))
        max_assets_env = (os.environ.get("DESCRIPTIONS_BUNDLE_MAX_ASSETS", "") or "").strip()
        max_assets: Optional[int] = int(max_assets_env) if max_assets_env else None
        build_descriptions_bundle_from_inventory(
            inventory_json=inv_path,
            out_path=bundle_path,
            max_assets=max_assets,
        )

    # INLINE bundle text (instead of Files API upload)
    cap_env = (os.environ.get("DESCRIPTIONS_BUNDLE_INLINE_MAX_CHARS") or "").strip()
    cap = int(cap_env) if cap_env else 250_000
    bundle_inline = _read_bundle_inline(bundle_path, max_chars=cap)
    logger.info("bundle_inline chars=%d path=%s", len(bundle_inline), str(bundle_path))

    system_instruction = build_system_instruction()
    user_prompt = build_user_prompt(assets=assets_for_prompt, schema_name="FullPlanPayload")

    # IMPORTANT: pick EXACTLY ONE audio file
    audio_dir = Path(os.environ.get("AUDIO_DIR", str(ROOT / "audio"))).resolve()
    audio_files = pick_audio_files(audio_dir)

    logger.info("audio_files_selected n=%d files=%s", len(audio_files), [p.name for p in audio_files])

    use_cache = (os.environ.get("GEMINI_UPLOAD_CACHE", "1") or "1").strip() not in {"0", "false", "False", "no", "NO"}
    cache_path = (out_dir / "gemini_files_cache.json") if use_cache else None

    raw_path = logs_dir / f"gemini_raw_fullplan_{_stamp()}.json"

    # NEW: dump exact system + prompt we send
    sys_dump = logs_dir / f"gemini_system_fullplan_{_stamp()}.txt"
    prompt_dump = logs_dir / f"gemini_prompt_fullplan_{_stamp()}.txt"

    # Also dump a small meta json (helps quick debugging)
    meta_dump = logs_dir / f"gemini_meta_fullplan_{_stamp()}.json"
    meta_dump.write_text(
        json.dumps(
            {
                "model": model,
                "temperature": temperature,
                "audio_files": [str(p) for p in audio_files],
                "bundle_path": str(bundle_path),
                "bundle_inline_chars": len(bundle_inline),
                "cache_path": str(cache_path) if cache_path else None,
                "raw_response_path": str(raw_path),
                "system_dump": str(sys_dump),
                "prompt_dump": str(prompt_dump),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    logger.info("context audio_files=%d bundle_inline=yes", len(audio_files))
    logger.info("gemini_call_start raw=%s", str(raw_path))
    logger.info("gemini_debug_dumps sys=%s prompt=%s meta=%s", str(sys_dump), str(prompt_dump), str(meta_dump))

    try:
        plan = call_full_plan_once(
            client=client,
            model_name=model,
            system_instruction=system_instruction,
            user_prompt=user_prompt,
            audio_paths=audio_files,
            descriptions_bundle_text=bundle_inline,
            raw_response_path=raw_path,
            cache_path=cache_path,
            prompt_dump_path=prompt_dump,
            system_dump_path=sys_dump,
        )
    except Exception as e:
        logger.warning("gemini_fullplan_failed err=%r -> probe text-only 'u alive?'", e)
        try:
            probe = client.probe_text("u alive?")
            logger.warning("gemini_probe_ok text=%r", (probe or "")[:400])
        except Exception as e2:
            logger.warning("gemini_probe_failed err=%r", e2)
        raise

    logger.info("gemini_call_done audio=%s..%s", plan.audio.clip_start_abs, plan.audio.clip_end_abs)

    outputs = render_all_steps(
        repo_root=ROOT,
        plan=plan,
        footage_inventory_json=inv_path,
        out_dir=out_dir,
        data_dir=Path(os.environ.get("DATA_DIR", str(ROOT / "data"))).resolve(),
    )

    logger.info("render_done %s", {k: str(v) for k, v in outputs.items()})
    return outputs
