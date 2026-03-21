#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
import traceback
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import boto3
from botocore.config import Config
from dotenv import load_dotenv
from google.genai import types

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mlcore.bench_asr_v2 import (  # noqa: E402
    best_subsequence_alignment,
    build_error_metrics,
    build_normalized_word_stream,
    compute_global_sdi,
    normalized_words_from_text,
    normalize_word,
    validate_forced_alignment_strict,
    words_to_srt,
)
from mlcore.gemini_client import GeminiClient, GeminiSettings  # noqa: E402
from mlcore.models.stage1_asr import Stage1AsrPayload  # noqa: E402
from mlcore.models.stage1_forced_alignment import Stage1ForcedAlignmentPayload  # noqa: E402
from mlcore.prompts import (  # noqa: E402
    build_stage1a_asr_system_instruction,
    build_stage1a_asr_user_prompt,
    build_stage1a_forced_alignment_system_instruction,
    build_stage1a_forced_alignment_user_prompt,
)


REMOTE_SCAN_CODE = r"""
from pathlib import Path
import json


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _latest_file(paths):
    best = None
    best_ts = -1.0
    for p in paths:
        try:
            ts = float(p.stat().st_mtime)
        except Exception:
            continue
        if ts > best_ts:
            best_ts = ts
            best = p
    return best


def _extract_target_fragment(stage1_obj):
    if not isinstance(stage1_obj, dict):
        return ""
    tf = str(stage1_obj.get("target_fragment") or "").strip()
    if tf:
        return tf
    fa = stage1_obj.get("fragment_analytics")
    if isinstance(fa, dict):
        tf2 = str(fa.get("target_fragment") or "").strip()
        if tf2:
            return tf2
    return ""


def _audio_url_from_footage(footage_obj):
    if not isinstance(footage_obj, dict):
        return ""
    layers = footage_obj.get("layers")
    if not isinstance(layers, list):
        return ""
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        if str(layer.get("type") or "") != "audio_only":
            continue
        fp = str(layer.get("file_path") or "").strip()
        if fp:
            return fp
    return ""


repo = Path(str(ARGS["remote_repo"])).resolve()
out_jobs = repo / "output" / "jobs"
work_jobs = repo / "work" / "jobs"

job_ids = set()
for root in (out_jobs, work_jobs):
    if not root.exists():
        continue
    for it in root.iterdir():
        if it.is_dir():
            job_ids.add(it.name)

rows = []
for job_id in sorted(job_ids):
    out_root = out_jobs / job_id / "out"
    work_root = work_jobs / job_id / "data"
    logs_dir = out_root / "logs"

    stage1_paths = []
    if logs_dir.exists():
        stage1_paths.extend(list(logs_dir.glob("stage1_plan_merged_*.json")))
        stage1_paths.extend(list(logs_dir.glob("stage1_plan_merged.json")))
        stage1_paths.extend(list(logs_dir.glob("stage1_plan_*.json")))
        stage1_paths.extend(list(logs_dir.glob("stage1_plan.json")))
    stage1_path = _latest_file(stage1_paths)

    asr_paths = []
    if logs_dir.exists():
        asr_paths.extend(list(logs_dir.glob("stage1_asr_*.json")))
        asr_paths.extend(list(logs_dir.glob("stage1_asr.json")))
    asr_path = _latest_file(asr_paths)

    work_footage_path = work_root / "footage_config.json"
    out_footage_path = out_root / "footage_config.json"

    target_fragment = ""
    if stage1_path is not None:
        stage1_obj = _load_json(stage1_path)
        target_fragment = _extract_target_fragment(stage1_obj)

    audio_url = ""
    if work_footage_path.exists():
        audio_url = _audio_url_from_footage(_load_json(work_footage_path))
    if not audio_url and out_footage_path.exists():
        audio_url = _audio_url_from_footage(_load_json(out_footage_path))

    ts_candidates = []
    for p in (stage1_path, asr_path, work_footage_path if work_footage_path.exists() else None):
        if p is None:
            continue
        try:
            ts_candidates.append(float(p.stat().st_mtime))
        except Exception:
            pass
    sort_ts = max(ts_candidates) if ts_candidates else 0.0

    rows.append(
        {
            "job_id": job_id,
            "sort_ts": sort_ts,
            "target_fragment": target_fragment,
            "audio_url": audio_url,
            "asr_log_path": str(asr_path) if asr_path is not None else "",
            "source_paths": {
                "output_job_dir": str(out_jobs / job_id),
                "work_job_dir": str(work_jobs / job_id),
                "stage1_plan_path": str(stage1_path) if stage1_path is not None else "",
                "asr_log_path": str(asr_path) if asr_path is not None else "",
                "work_footage_config_path": str(work_footage_path) if work_footage_path.exists() else "",
                "output_footage_config_path": str(out_footage_path) if out_footage_path.exists() else "",
            },
        }
    )

rows.sort(key=lambda x: float(x.get("sort_ts") or 0.0), reverse=True)
print(json.dumps({"jobs": rows}, ensure_ascii=False))
"""


@dataclass(frozen=True)
class RunConfig:
    name: str
    prompt_version: str
    temperature: float


@dataclass
class TrackItem:
    track_id: str
    job_id: str
    audio_url: str
    target_fragment: str
    source_paths: Dict[str, str]
    local_audio_path: Path
    reference_words: List[str]
    uploaded_file: Optional[types.File] = None


@dataclass(frozen=True)
class RunSpec:
    track: TrackItem
    config: RunConfig
    run_idx: int


@dataclass
class RunResult:
    track_id: str
    config_name: str
    run_idx: int
    run_dir: str
    success: bool
    metrics: Optional[Dict[str, Any]] = None
    error: str = ""


RUN_CONFIGS: List[RunConfig] = [
    RunConfig(name="v1_t1", prompt_version="v1", temperature=1.0),
    RunConfig(name="v2_t1", prompt_version="v2", temperature=1.0),
    RunConfig(name="v1_t0", prompt_version="v1", temperature=0.0),
    RunConfig(name="v2_t0", prompt_version="v2", temperature=0.0),
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_env() -> None:
    env_path = ROOT / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)
        print(f"[env] loaded: {env_path}")


def _require_env(key: str) -> str:
    v = (os.environ.get(key) or "").strip()
    if not v:
        raise RuntimeError(f"Missing required env var: {key}")
    return v


def _parse_s3_url(url: str) -> Tuple[str, str]:
    s = str(url or "").strip()
    if not s.startswith("s3://"):
        raise RuntimeError(f"Expected s3:// URL, got {url!r}")
    tail = s[5:]
    if "/" not in tail:
        raise RuntimeError(f"Invalid s3 URL: {url!r}")
    bucket, key = tail.split("/", 1)
    bucket = bucket.strip()
    key = key.strip()
    if not bucket or not key:
        raise RuntimeError(f"Invalid s3 URL: {url!r}")
    return bucket, key


def _is_s3_url(url: str) -> bool:
    return str(url or "").strip().lower().startswith("s3://")


def _safe_track_id(idx: int, job_id: str) -> str:
    tail = "".join(c for c in str(job_id) if c.isalnum())[:8] or "job"
    return f"track_{idx:02d}_{tail}"


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _mean_std(values: Sequence[float]) -> Tuple[Optional[float], Optional[float]]:
    vals = [float(v) for v in values]
    if not vals:
        return None, None
    if len(vals) == 1:
        return vals[0], 0.0
    return statistics.fmean(vals), statistics.stdev(vals)


def _is_transient_error(exc: BaseException) -> bool:
    msg = f"{type(exc).__name__}: {exc}".lower()
    markers = (
        "timed out",
        "timeout",
        "connection reset",
        "connection aborted",
        "broken pipe",
        "temporarily unavailable",
        "service unavailable",
        "rate limit",
        "429",
        "500",
        "503",
        "internal error",
        "unavailable",
    )
    return any(m in msg for m in markers)


def _make_s3_client() -> Any:
    endpoint = (os.environ.get("S3_ENDPOINT_URL") or "").strip() or None
    access_key = (os.environ.get("S3_ACCESS_KEY_ID") or "").strip()
    secret_key = (os.environ.get("S3_SECRET_ACCESS_KEY") or "").strip()
    region = (os.environ.get("S3_REGION") or "ru-1").strip() or "ru-1"

    if bool(access_key) != bool(secret_key):
        raise RuntimeError("S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY must be both set or both empty")

    kwargs: Dict[str, Any] = {
        "service_name": "s3",
        "region_name": region,
        "config": Config(signature_version="s3v4"),
    }
    if endpoint is not None:
        kwargs["endpoint_url"] = endpoint
    if access_key and secret_key:
        kwargs["aws_access_key_id"] = access_key
        kwargs["aws_secret_access_key"] = secret_key
    return boto3.client(**kwargs)


def _download_track_from_s3(s3: Any, *, audio_url: str, dest: Path) -> None:
    bucket, key = _parse_s3_url(audio_url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    s3.download_file(bucket, key, str(dest))


def _run_remote_python(*, remote_host: str, remote_code: str, args: Dict[str, Any]) -> Dict[str, Any]:
    wrapper = (
        "import json\n"
        f"ARGS = json.loads({json.dumps(json.dumps(args, ensure_ascii=False))})\n"
        + remote_code
        + "\n"
    )
    proc = subprocess.run(
        ["ssh", remote_host, "python3", "-"],
        input=wrapper,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "Remote scan failed.\n"
            f"host={remote_host}\n"
            f"rc={proc.returncode}\n"
            f"stdout={proc.stdout[-4000:]}\n"
            f"stderr={proc.stderr[-4000:]}"
        )
    txt = (proc.stdout or "").strip()
    if not txt:
        raise RuntimeError("Remote scan returned empty stdout")
    try:
        return json.loads(txt)
    except Exception as e:
        raise RuntimeError(f"Failed to parse remote scan JSON: {e}; stdout_head={txt[:2000]!r}") from e


def _make_client(
    *,
    api_key: str,
    model: str,
    temperature: float,
    proxy: str,
    timeout_s: float,
) -> GeminiClient:
    return GeminiClient(
        GeminiSettings(
            api_key=api_key,
            model=model,
            temperature=float(temperature),
            proxy=proxy,
            timeout_s=float(timeout_s),
            max_attempts=1,
        )
    )


def _build_prompts(track: TrackItem, cfg: RunConfig) -> Tuple[str, str]:
    if cfg.prompt_version == "v1":
        return (
            build_stage1a_asr_system_instruction(),
            build_stage1a_asr_user_prompt(schema_name="Stage1AsrPayload"),
        )
    if cfg.prompt_version == "v2":
        return (
            build_stage1a_forced_alignment_system_instruction(),
            build_stage1a_forced_alignment_user_prompt(
                reference_text=track.target_fragment,
                schema_name="Stage1ForcedAlignmentPayload",
            ),
        )
    raise RuntimeError(f"Unsupported prompt_version: {cfg.prompt_version!r}")


def _run_one(
    *,
    spec: RunSpec,
    out_dir: Path,
    api_key: str,
    model: str,
    proxy: str,
    timeout_s: float,
    retry_limit: int,
) -> RunResult:
    track = spec.track
    cfg = spec.config
    run_dir = out_dir / "runs" / track.track_id / cfg.name / f"run_{spec.run_idx:02d}"
    run_dir.mkdir(parents=True, exist_ok=True)

    system_prompt, user_prompt = _build_prompts(track, cfg)
    (run_dir / "system_prompt.txt").write_text(system_prompt, encoding="utf-8")
    (run_dir / "user_prompt.txt").write_text(user_prompt, encoding="utf-8")

    if track.uploaded_file is None:
        err = "uploaded file reference is missing for track"
        _write_json(run_dir / "error.json", {"error": err, "track_id": track.track_id, "config": cfg.name})
        return RunResult(
            track_id=track.track_id,
            config_name=cfg.name,
            run_idx=spec.run_idx,
            run_dir=str(run_dir),
            success=False,
            error=err,
        )

    raw_path = run_dir / "raw_response.json"
    parsed_path = run_dir / "parsed_json.json"
    srt_path = run_dir / "word_srt.srt"
    metrics_path = run_dir / "metrics.json"
    meta = {
        "track_id": track.track_id,
        "job_id": track.job_id,
        "audio_url": track.audio_url,
        "config": cfg.name,
        "prompt_version": cfg.prompt_version,
        "temperature": cfg.temperature,
        "model": model,
        "run_idx": spec.run_idx,
        "run_started_at": _utc_now_iso(),
    }

    for attempt in range(1, retry_limit + 2):
        try:
            client = _make_client(
                api_key=api_key,
                model=model,
                temperature=cfg.temperature,
                proxy=proxy,
                timeout_s=timeout_s,
            )

            if cfg.prompt_version == "v1":
                payload = client.generate_structured(
                    schema_model=Stage1AsrPayload,
                    prompt=user_prompt,
                    files=[track.uploaded_file],
                    system_instruction=system_prompt,
                    raw_response_path=raw_path,
                )
                parsed = payload.model_dump(mode="json")
                _write_json(parsed_path, parsed)

                stream = build_normalized_word_stream(parsed.get("transcript_words", []))
                if not stream:
                    raise RuntimeError("v1 transcript_words are empty after normalization")
                hyp = [str(it["norm"]) for it in stream]
                best = best_subsequence_alignment(track.reference_words, hyp)

                chosen_words: List[Dict[str, Any]] = []
                if best.end_idx >= best.start_idx >= 0:
                    chosen_words = [
                        {
                            "text": str(it["text"]),
                            "t_start": float(it["t_start"]),
                            "t_end": float(it["t_end"]),
                        }
                        for it in stream[best.start_idx : best.end_idx + 1]
                    ]
                srt_path.write_text(words_to_srt(chosen_words), encoding="utf-8")

                metrics = build_error_metrics(
                    substitutions=best.substitutions,
                    deletions=best.deletions,
                    insertions=best.insertions,
                    reference_words_count=len(track.reference_words),
                )
                metrics.update(
                    {
                        "hypothesis_words_count": len(hyp),
                        "selected_words_count": len(chosen_words),
                        "best_subsequence_start_idx": best.start_idx,
                        "best_subsequence_end_idx": best.end_idx,
                    }
                )
                _write_json(metrics_path, {"meta": meta, "metrics": metrics})
                return RunResult(
                    track_id=track.track_id,
                    config_name=cfg.name,
                    run_idx=spec.run_idx,
                    run_dir=str(run_dir),
                    success=True,
                    metrics=metrics,
                )

            if cfg.prompt_version == "v2":
                payload = client.generate_structured(
                    schema_model=Stage1ForcedAlignmentPayload,
                    prompt=user_prompt,
                    files=[track.uploaded_file],
                    system_instruction=system_prompt,
                    raw_response_path=raw_path,
                )
                parsed = payload.model_dump(mode="json")
                _write_json(parsed_path, parsed)

                validated, validation_warnings = validate_forced_alignment_strict(payload, track.reference_words)
                words_for_srt = [
                    {"text": w.text, "t_start": float(w.t_start), "t_end": float(w.t_end)}
                    for w in validated
                ]
                srt_path.write_text(words_to_srt(words_for_srt), encoding="utf-8")

                hyp = [normalize_word(w.text) for w in validated]
                s, d, i = compute_global_sdi(track.reference_words, hyp)
                metrics = build_error_metrics(
                    substitutions=s,
                    deletions=d,
                    insertions=i,
                    reference_words_count=len(track.reference_words),
                )
                metrics.update({"hypothesis_words_count": len(hyp)})
                if validation_warnings:
                    metrics["validation_warnings"] = list(validation_warnings)
                _write_json(metrics_path, {"meta": meta, "metrics": metrics})
                return RunResult(
                    track_id=track.track_id,
                    config_name=cfg.name,
                    run_idx=spec.run_idx,
                    run_dir=str(run_dir),
                    success=True,
                    metrics=metrics,
                )

            raise RuntimeError(f"Unsupported prompt version: {cfg.prompt_version!r}")
        except Exception as e:  # noqa: BLE001
            transient = _is_transient_error(e)
            is_last = attempt >= (retry_limit + 1)
            if transient and not is_last:
                backoff = min(30.0, 2.0 * float(attempt))
                time.sleep(backoff)
                continue

            err_payload = {
                "meta": meta,
                "attempt": attempt,
                "retry_limit": retry_limit,
                "transient": transient,
                "error_type": type(e).__name__,
                "error_message": str(e),
                "traceback": traceback.format_exc(),
            }
            _write_json(run_dir / "error.json", err_payload)
            return RunResult(
                track_id=track.track_id,
                config_name=cfg.name,
                run_idx=spec.run_idx,
                run_dir=str(run_dir),
                success=False,
                error=f"{type(e).__name__}: {e}",
            )

    err = "unreachable_retry_loop_state"
    _write_json(run_dir / "error.json", {"meta": meta, "error": err})
    return RunResult(
        track_id=track.track_id,
        config_name=cfg.name,
        run_idx=spec.run_idx,
        run_dir=str(run_dir),
        success=False,
        error=err,
    )


def _aggregate_results(
    *,
    tracks: Sequence[TrackItem],
    run_results: Sequence[RunResult],
    runs_per_config: int,
) -> Dict[str, Any]:
    by_pair: Dict[Tuple[str, str], List[RunResult]] = {}
    by_config: Dict[str, List[RunResult]] = {}
    for rr in run_results:
        by_pair.setdefault((rr.track_id, rr.config_name), []).append(rr)
        by_config.setdefault(rr.config_name, []).append(rr)

    track_config_rows: List[Dict[str, Any]] = []
    for track in tracks:
        for cfg in RUN_CONFIGS:
            key = (track.track_id, cfg.name)
            rows = by_pair.get(key, [])
            oks = [r for r in rows if r.success and isinstance(r.metrics, dict)]
            fails = [r for r in rows if not r.success]
            vals_error = [float(r.metrics["error_pct"]) for r in oks]  # type: ignore[index]
            vals_wrong = [float(r.metrics["wrong_word_pct"]) for r in oks]  # type: ignore[index]
            vals_miss = [float(r.metrics["miss_pct"]) for r in oks]  # type: ignore[index]
            vals_extra = [float(r.metrics["extra_pct"]) for r in oks]  # type: ignore[index]
            err_mean, err_std = _mean_std(vals_error)
            wrong_mean, wrong_std = _mean_std(vals_wrong)
            miss_mean, miss_std = _mean_std(vals_miss)
            extra_mean, extra_std = _mean_std(vals_extra)
            track_config_rows.append(
                {
                    "track_id": track.track_id,
                    "job_id": track.job_id,
                    "config": cfg.name,
                    "runs_total": runs_per_config,
                    "runs_ok": len(oks),
                    "runs_failed": runs_per_config - len(oks),
                    "error_pct_mean": err_mean,
                    "error_pct_stddev": err_std,
                    "wrong_word_pct_mean": wrong_mean,
                    "wrong_word_pct_stddev": wrong_std,
                    "miss_pct_mean": miss_mean,
                    "miss_pct_stddev": miss_std,
                    "extra_pct_mean": extra_mean,
                    "extra_pct_stddev": extra_std,
                    "failed_runs": [
                        {
                            "run_idx": f.run_idx,
                            "run_dir": f.run_dir,
                            "error": f.error,
                        }
                        for f in fails
                    ],
                }
            )

    config_rows: List[Dict[str, Any]] = []
    for cfg in RUN_CONFIGS:
        rows = by_config.get(cfg.name, [])
        oks = [r for r in rows if r.success and isinstance(r.metrics, dict)]
        vals_error = [float(r.metrics["error_pct"]) for r in oks]  # type: ignore[index]
        vals_wrong = [float(r.metrics["wrong_word_pct"]) for r in oks]  # type: ignore[index]
        vals_miss = [float(r.metrics["miss_pct"]) for r in oks]  # type: ignore[index]
        vals_extra = [float(r.metrics["extra_pct"]) for r in oks]  # type: ignore[index]
        err_mean, err_std = _mean_std(vals_error)
        wrong_mean, wrong_std = _mean_std(vals_wrong)
        miss_mean, miss_std = _mean_std(vals_miss)
        extra_mean, extra_std = _mean_std(vals_extra)
        config_rows.append(
            {
                "config": cfg.name,
                "runs_total": len(tracks) * runs_per_config,
                "runs_ok": len(oks),
                "runs_failed": (len(tracks) * runs_per_config) - len(oks),
                "error_pct_mean": err_mean,
                "error_pct_stddev": err_std,
                "wrong_word_pct_mean": wrong_mean,
                "wrong_word_pct_stddev": wrong_std,
                "miss_pct_mean": miss_mean,
                "miss_pct_stddev": miss_std,
                "extra_pct_mean": extra_mean,
                "extra_pct_stddev": extra_std,
            }
        )

    failed_runs = [
        {
            "track_id": rr.track_id,
            "config": rr.config_name,
            "run_idx": rr.run_idx,
            "run_dir": rr.run_dir,
            "error": rr.error,
        }
        for rr in run_results
        if not rr.success
    ]

    return {
        "generated_at_utc": _utc_now_iso(),
        "totals": {
            "tracks": len(tracks),
            "configs": len(RUN_CONFIGS),
            "runs": len(run_results),
            "runs_ok": sum(1 for r in run_results if r.success),
            "runs_failed": sum(1 for r in run_results if not r.success),
        },
        "per_track_config": track_config_rows,
        "per_config": config_rows,
        "failed_runs": failed_runs,
    }


def _write_aggregate_csv(path: Path, aggregate: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows: List[str] = []
    header = (
        "scope,track_id,job_id,config,runs_total,runs_ok,runs_failed,"
        "error_pct_mean,error_pct_stddev,wrong_word_pct_mean,wrong_word_pct_stddev,"
        "miss_pct_mean,miss_pct_stddev,extra_pct_mean,extra_pct_stddev"
    )
    rows.append(header)
    for row in aggregate.get("per_track_config", []):
        vals = [
            "track_config",
            str(row.get("track_id", "")),
            str(row.get("job_id", "")),
            str(row.get("config", "")),
            str(row.get("runs_total", "")),
            str(row.get("runs_ok", "")),
            str(row.get("runs_failed", "")),
            str(row.get("error_pct_mean", "")),
            str(row.get("error_pct_stddev", "")),
            str(row.get("wrong_word_pct_mean", "")),
            str(row.get("wrong_word_pct_stddev", "")),
            str(row.get("miss_pct_mean", "")),
            str(row.get("miss_pct_stddev", "")),
            str(row.get("extra_pct_mean", "")),
            str(row.get("extra_pct_stddev", "")),
        ]
        rows.append(",".join(vals))
    for row in aggregate.get("per_config", []):
        vals = [
            "config",
            "",
            "",
            str(row.get("config", "")),
            str(row.get("runs_total", "")),
            str(row.get("runs_ok", "")),
            str(row.get("runs_failed", "")),
            str(row.get("error_pct_mean", "")),
            str(row.get("error_pct_stddev", "")),
            str(row.get("wrong_word_pct_mean", "")),
            str(row.get("wrong_word_pct_stddev", "")),
            str(row.get("miss_pct_mean", "")),
            str(row.get("miss_pct_stddev", "")),
            str(row.get("extra_pct_mean", "")),
            str(row.get("extra_pct_stddev", "")),
        ]
        rows.append(",".join(vals))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _select_tracks(
    *,
    remote_jobs: Sequence[Dict[str, Any]],
    tracks: int,
) -> List[Dict[str, Any]]:
    picked: List[Dict[str, Any]] = []
    seen_urls: set[str] = set()
    for rec in remote_jobs:
        target_fragment = str(rec.get("target_fragment") or "").strip()
        audio_url = str(rec.get("audio_url") or "").strip()
        asr_path = str(rec.get("asr_log_path") or "").strip()
        if not target_fragment:
            continue
        if not asr_path:
            continue
        if not _is_s3_url(audio_url):
            continue
        if audio_url in seen_urls:
            continue
        seen_urls.add(audio_url)
        picked.append(rec)
        if len(picked) >= tracks:
            break
    return picked


def main() -> int:
    ap = argparse.ArgumentParser(description="ASR/SRT V2 benchmark runner")
    ap.add_argument("--remote-host", default="timeweb-blast")
    ap.add_argument("--remote-repo", default="/home/blast/blast_final")
    ap.add_argument("--tracks", type=int, default=5)
    ap.add_argument("--runs-per-config", type=int, default=10)
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--out-dir", default="work/bench_asr_v2")
    ap.add_argument("--retry-limit", type=int, default=2, help="Transient retries per run, same config only")
    args = ap.parse_args()

    _load_env()
    mode = (os.environ.get("MODE") or "").strip().lower()
    if mode not in {"dev", "prod"}:
        raise RuntimeError("MODE must be explicitly set to dev or prod")

    api_key = _require_env("GEMINI_API_KEY")
    model = _require_env("GEMINI_MODEL_STAGE1_ASR")
    proxy = (os.environ.get("OUTBOUND_PROXY") or "").strip()
    timeout_s = float(os.environ.get("GEMINI_TIMEOUT_S", "120") or "120")

    out_dir = Path(args.out_dir).expanduser()
    if not out_dir.is_absolute():
        out_dir = (ROOT / out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    tracks_dir = out_dir / "tracks"
    dataset_dir = out_dir / "dataset"
    summary_dir = out_dir / "summary"
    tracks_dir.mkdir(parents=True, exist_ok=True)
    dataset_dir.mkdir(parents=True, exist_ok=True)
    summary_dir.mkdir(parents=True, exist_ok=True)

    print(f"[bench] remote_host={args.remote_host}")
    print(f"[bench] remote_repo={args.remote_repo}")
    print(f"[bench] model={model}")
    print(f"[bench] out_dir={out_dir}")

    remote_payload = _run_remote_python(
        remote_host=args.remote_host,
        remote_code=REMOTE_SCAN_CODE,
        args={"remote_repo": args.remote_repo},
    )
    remote_jobs = list(remote_payload.get("jobs") or [])
    if not remote_jobs:
        raise RuntimeError("No jobs discovered on remote host")
    print(f"[bench] remote_jobs_discovered={len(remote_jobs)}")

    selected = _select_tracks(remote_jobs=remote_jobs, tracks=int(args.tracks))
    if len(selected) < int(args.tracks):
        raise RuntimeError(
            "Not enough eligible tracks. "
            f"requested={args.tracks} selected={len(selected)} "
            "(requires non-empty target_fragment, s3 audio_url, and ASR log)"
        )

    s3 = _make_s3_client()
    tracks: List[TrackItem] = []
    selected_dump: List[Dict[str, Any]] = []
    for idx, rec in enumerate(selected, start=1):
        audio_url = str(rec["audio_url"])
        _, key = _parse_s3_url(audio_url)
        suffix = Path(key).suffix or ".bin"
        track_id = _safe_track_id(idx, str(rec.get("job_id") or "job"))
        local_audio = tracks_dir / f"{track_id}{suffix}"
        _download_track_from_s3(s3, audio_url=audio_url, dest=local_audio)
        target_fragment = str(rec.get("target_fragment") or "").strip()
        ref_words = normalized_words_from_text(target_fragment)
        if not ref_words:
            raise RuntimeError(f"Track has empty reference words after normalization: {track_id}")

        item = TrackItem(
            track_id=track_id,
            job_id=str(rec.get("job_id") or ""),
            audio_url=audio_url,
            target_fragment=target_fragment,
            source_paths=dict(rec.get("source_paths") or {}),
            local_audio_path=local_audio,
            reference_words=ref_words,
        )
        tracks.append(item)
        selected_dump.append(
            {
                "track_id": track_id,
                "job_id": item.job_id,
                "audio_url": item.audio_url,
                "target_fragment": item.target_fragment,
                "reference_words_count": len(item.reference_words),
                "local_audio_path": str(item.local_audio_path),
                "source_paths": item.source_paths,
                "sort_ts": rec.get("sort_ts"),
            }
        )

    _write_json(dataset_dir / "selected_tracks.json", selected_dump)
    print(f"[bench] selected_tracks={len(tracks)}")

    upload_client = _make_client(
        api_key=api_key,
        model=model,
        temperature=0.0,
        proxy=proxy,
        timeout_s=timeout_s,
    )
    for t in tracks:
        uploaded = upload_client.upload_files([t.local_audio_path])
        if not uploaded:
            raise RuntimeError(f"Failed to upload track: {t.track_id}")
        t.uploaded_file = uploaded[0]
    print(f"[bench] preuploaded_tracks={len(tracks)}")

    specs: List[RunSpec] = []
    for t in tracks:
        for cfg in RUN_CONFIGS:
            for run_idx in range(1, int(args.runs_per_config) + 1):
                specs.append(RunSpec(track=t, config=cfg, run_idx=run_idx))
    total = len(specs)
    print(f"[bench] total_runs={total} concurrency={int(args.concurrency)}")

    run_results: List[RunResult] = []
    done = 0
    with ThreadPoolExecutor(max_workers=int(args.concurrency)) as ex:
        futures: List[Future[RunResult]] = [
            ex.submit(
                _run_one,
                spec=spec,
                out_dir=out_dir,
                api_key=api_key,
                model=model,
                proxy=proxy,
                timeout_s=timeout_s,
                retry_limit=int(args.retry_limit),
            )
            for spec in specs
        ]
        for fut in as_completed(futures):
            rr = fut.result()
            run_results.append(rr)
            done += 1
            status = "ok" if rr.success else "fail"
            print(f"[bench] run {done}/{total} {rr.track_id} {rr.config_name} #{rr.run_idx:02d} -> {status}")

    aggregate = _aggregate_results(
        tracks=tracks,
        run_results=run_results,
        runs_per_config=int(args.runs_per_config),
    )
    aggregate["params"] = {
        "remote_host": args.remote_host,
        "remote_repo": args.remote_repo,
        "tracks": int(args.tracks),
        "runs_per_config": int(args.runs_per_config),
        "concurrency": int(args.concurrency),
        "out_dir": str(out_dir),
        "retry_limit": int(args.retry_limit),
        "model": model,
    }
    _write_json(summary_dir / "aggregate.json", aggregate)
    _write_aggregate_csv(summary_dir / "aggregate.csv", aggregate)

    totals = aggregate.get("totals") or {}
    print(
        "[bench] done "
        f"runs={totals.get('runs')} ok={totals.get('runs_ok')} failed={totals.get('runs_failed')}"
    )
    print(f"[bench] dataset={dataset_dir / 'selected_tracks.json'}")
    print(f"[bench] summary={summary_dir / 'aggregate.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
