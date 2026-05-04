from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

import requests

from s3_utils import (
    download_file_from_s3,
    generate_presigned_url,
    parse_s3_url,
    upload_file_to_s3,
)

log = logging.getLogger(__name__)

_RENDER_LOCK = threading.Lock()
_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}


@dataclass
class MediaFileSpec:
    url: str
    relpath: str


@dataclass
class AeJobSpec:
    job_id: str
    render_jsx: str
    media_files: List[MediaFileSpec]

    entry_comp: str = "Main Render"
    output_relpath: str = "work/output.mp4"

    output_s3_bucket: Optional[str] = None
    output_s3_key: Optional[str] = None


@dataclass
class AeJobResult:
    job_id: str
    success: bool
    message: str
    app_dir: Path
    output_path: Optional[Path] = None
    output_s3_url: Optional[str] = None
    artifacts_s3_uri: Optional[str] = None


def _is_remote(u: str) -> bool:
    s = (u or "").strip().lower()
    return s.startswith("http://") or s.startswith("https://") or s.startswith("s3://")


def _read_remote_text(url: str) -> str:
    u = (url or "").strip()
    if not _is_remote(u):
        raise RuntimeError(f"Expected remote URL, got: {u!r}")

    if u.startswith("s3://"):
        bucket, key = parse_s3_url(u)
        with tempfile.TemporaryDirectory(prefix="ae_remote_") as td:
            tmp = Path(td) / "remote.txt"
            download_file_from_s3(bucket=bucket, key=key, dest=tmp)
            return tmp.read_text(encoding="utf-8", errors="replace")

    resp = requests.get(u, timeout=120)
    resp.raise_for_status()
    return resp.text


def _is_audio_by_meta(layer: Dict[str, Any]) -> bool:
    td = layer.get("text_data") if isinstance(layer.get("text_data"), dict) else {}
    meta = td.get("layer_meta") if isinstance(td.get("layer_meta"), dict) else {}
    return bool(meta.get("audioEnabled")) is True


def _is_audio_by_ext(file_name: str) -> bool:
    return Path(file_name).suffix.lower() in _AUDIO_EXTS


def _expected_audio_name_from_payload(footage_layers: List[Dict[str, Any]]) -> str:
    for layer in footage_layers:
        if not isinstance(layer, dict):
            continue
        td = layer.get("text_data") if isinstance(layer.get("text_data"), dict) else {}
        src = td.get("source_footage") if isinstance(td.get("source_footage"), dict) else {}
        fn = str(src.get("file_name") or "").strip()
        if not fn:
            continue
        if _is_audio_by_meta(layer) or _is_audio_by_ext(fn):
            return fn
    return ""


def _collect_media_specs_from_render_payload(payload_text: str, audio_url: str) -> List[MediaFileSpec]:
    obj = json.loads(payload_text)
    if not isinstance(obj, dict):
        raise RuntimeError("render payload JSON root must be object")

    footage_layers = obj.get("footage_layers")
    if not isinstance(footage_layers, list):
        raise RuntimeError("render payload missing footage_layers[]")

    out: List[MediaFileSpec] = []
    seen: set[str] = set()

    aurl = (audio_url or "").strip()
    if not _is_remote(aurl):
        raise RuntimeError(f"audio_url must be remote (http/https/s3), got: {aurl!r}")

    audio_name = _expected_audio_name_from_payload(footage_layers)
    if not audio_name:
        raw_name = (aurl.split("?")[0].rstrip("/").split("/")[-1] or "audio").strip()
        audio_name = (unquote(raw_name) or raw_name).strip()

    rel_audio = f"media/audio/{audio_name}"
    out.append(MediaFileSpec(url=aurl, relpath=rel_audio))
    seen.add(rel_audio)

    for layer in footage_layers:
        if not isinstance(layer, dict):
            continue

        td = layer.get("text_data") if isinstance(layer.get("text_data"), dict) else {}
        src = td.get("source_footage") if isinstance(td.get("source_footage"), dict) else {}
        fn = str(src.get("file_name") or "").strip()
        if not fn:
            continue
        if _is_audio_by_meta(layer) or _is_audio_by_ext(fn):
            continue

        remote_url = str(src.get("remote_url") or "").strip()
        file_path = str(src.get("file_path") or "").strip()

        url = remote_url if _is_remote(remote_url) else file_path if _is_remote(file_path) else ""
        if not url:
            raise RuntimeError(
                "Footage has no remote url (s3/http). "
                f"file_name={fn!r} remote_url={remote_url!r} file_path={file_path!r}"
            )

        rel = f"media/video/{fn}"
        if rel in seen:
            continue
        seen.add(rel)
        out.append(MediaFileSpec(url=url, relpath=rel))

    return out


class AeRenderer:
    def __init__(
        self,
        base_dir: str | Path,
        afterfx_bin: Optional[str | Path] = None,
        aerender_bin: Optional[str | Path] = None,
    ) -> None:
        self.base_dir = Path(base_dir).absolute()
        self.base_dir.mkdir(parents=True, exist_ok=True)

        env = os.environ
        self.afterfx_bin = str(afterfx_bin or env.get("AFTERFX_BIN") or "AfterFX.com")

        if aerender_bin is not None:
            self.aerender_bin = str(aerender_bin)
        else:
            aerender_env = env.get("AERENDER_BIN")
            if aerender_env:
                self.aerender_bin = aerender_env
            else:
                aft = Path(self.afterfx_bin)
                self.aerender_bin = str(aft.with_name("aerender.exe")) if aft.name.lower().startswith("afterfx") else "aerender"

        self.debug_jsx_dir = os.getenv("AE_DEBUG_JSX_DIR")

    @staticmethod
    def _append_message(base: str, extra: str) -> str:
        b = (base or "").strip()
        e = (extra or "").strip()
        if not e:
            return b
        if not b:
            return e
        return f"{b}; {e}"

    @staticmethod
    def _env_bool(key: str, default: bool = False) -> bool:
        raw = (os.getenv(key) or "").strip().lower()
        if not raw:
            return default
        return raw in {"1", "true", "yes", "on"}

    @staticmethod
    def _env_float(key: str, default: float) -> float:
        raw = (os.getenv(key) or "").strip()
        if not raw:
            return float(default)
        try:
            return float(raw)
        except ValueError:
            return float(default)

    @staticmethod
    def _job_root_dir(app_dir: Path) -> Path:
        # expected: .../<job_id>/app -> upload/delete whole <job_id>
        if app_dir.name.lower() == "app":
            return app_dir.parent
        return app_dir

    def _best_effort_reset_ae_project(self, tag: str) -> None:
        cleanup_jsx = r"""
    (function () {
    try { app.beginSuppressDialogs(); } catch(_) {}
    try {
        if (app.project) {
        try { app.project.close(CloseOptions.DO_NOT_SAVE_CHANGES); } catch(_) {}
        }
        try { app.newProject(); } catch(_) {}
    } catch(_) {}
    try { app.endSuppressDialogs(false); } catch(_) {}
    })();
    """.strip()

        try:
            # persistent folder; no auto-delete -> no lock error on temp cleanup
            cleanup_dir = self.base_dir / "_ae_cleanup_scripts"
            cleanup_dir.mkdir(parents=True, exist_ok=True)

            # unique file name to avoid rewrite collision on locked file
            jsx_path = cleanup_dir / f"cleanup_{tag}_{int(time.time() * 1000)}.jsx"
            jsx_path.write_text(cleanup_jsx, encoding="utf-8")

            proc = subprocess.run(
                [self.afterfx_bin, "-r", str(jsx_path)],
                env=os.environ.copy(),
                capture_output=True,
                text=True,
                timeout=180,
            )
            if proc.returncode != 0:
                log.warning(
                    "AE cleanup failed tag=%s rc=%s stderr_tail=%s",
                    tag, proc.returncode, (proc.stderr or "")[-2000:]
                )

            # best-effort prune old cleanup scripts
            try:
                now = time.time()
                for f in cleanup_dir.glob("cleanup_*.jsx"):
                    if now - f.stat().st_mtime > 24 * 3600:
                        try:
                            f.unlink()
                        except Exception:
                            pass
            except Exception:
                pass

        except Exception as e:
            log.warning("AE cleanup exception tag=%s err=%r", tag, e)


    def _upload_job_folder_to_s3(self, *, app_dir: Path, job_id: str) -> Optional[str]:
        """
        Upload full job folder (../<job_id>) as tar.gz to S3.
        Controlled by env:
          S3_JOB_ARTIFACTS_UPLOAD=1
          S3_BUCKET_JOB_ARTIFACTS=...
          S3_JOB_ARTIFACTS_PREFIX=ae_jobs_artifacts (optional)
        """
        if not self._env_bool("S3_JOB_ARTIFACTS_UPLOAD", False):
            return None

        bucket = (os.getenv("S3_BUCKET_JOB_ARTIFACTS") or "").strip()
        if not bucket:
            raise RuntimeError("S3_JOB_ARTIFACTS_UPLOAD=1 but S3_BUCKET_JOB_ARTIFACTS is empty")

        prefix = (os.getenv("S3_JOB_ARTIFACTS_PREFIX") or "ae_jobs_artifacts").strip().strip("/")
        job_root = self._job_root_dir(app_dir)
        if not job_root.exists():
            raise RuntimeError(f"job root does not exist for artifacts upload: {job_root}")

        with tempfile.TemporaryDirectory(prefix=f"ae_artifacts_{job_id}_") as td:
            archive_base = Path(td) / f"{job_id}_job_folder"
            archive_path_str = shutil.make_archive(
                base_name=str(archive_base),
                format="gztar",
                root_dir=str(job_root.parent),
                base_dir=job_root.name,
            )
            archive_path = Path(archive_path_str)
            key = f"{prefix}/{job_id}/{archive_path.name}"

            upload_file_to_s3(
                bucket=bucket,
                key=key,
                path=archive_path,
                content_type="application/gzip",
            )

        return f"s3://{bucket}/{key}"

    def _maybe_cleanup_local_job_dir(self, *, app_dir: Path, artifacts_uploaded_ok: bool) -> None:
        """
        Optional local cleanup after artifacts upload.
        Controlled by env:
          S3_JOB_ARTIFACTS_DELETE_LOCAL_AFTER_UPLOAD=1
        """
        delete_enabled = self._env_bool("S3_JOB_ARTIFACTS_DELETE_LOCAL_AFTER_UPLOAD", False)
        if not delete_enabled:
            return

        upload_enabled = self._env_bool("S3_JOB_ARTIFACTS_UPLOAD", False)
        if delete_enabled and not upload_enabled:
            raise RuntimeError(
                "S3_JOB_ARTIFACTS_DELETE_LOCAL_AFTER_UPLOAD=1 requires S3_JOB_ARTIFACTS_UPLOAD=1"
            )
        if upload_enabled and not artifacts_uploaded_ok:
            raise RuntimeError("skip cleanup: artifacts upload failed")

        job_root = self._job_root_dir(app_dir)
        if job_root.exists():
            shutil.rmtree(job_root)

    def _finalize_result_artifacts(self, res: AeJobResult) -> AeJobResult:
        uploaded_uri: Optional[str] = None

        try:
            uploaded_uri = self._upload_job_folder_to_s3(app_dir=res.app_dir, job_id=res.job_id)
            if uploaded_uri:
                res.artifacts_s3_uri = uploaded_uri
                res.message = self._append_message(res.message, f"artifacts={uploaded_uri}")
        except Exception as e:
            log.exception("artifacts upload error for job %s", res.job_id)
            res.message = self._append_message(res.message, f"artifacts_upload_error: {e}")

        try:
            self._maybe_cleanup_local_job_dir(app_dir=res.app_dir, artifacts_uploaded_ok=bool(uploaded_uri))
            if self._env_bool("S3_JOB_ARTIFACTS_DELETE_LOCAL_AFTER_UPLOAD", False):
                res.message = self._append_message(res.message, "local_job_dir_deleted=1")
        except Exception as e:
            log.exception("artifacts cleanup error for job %s", res.job_id)
            res.message = self._append_message(res.message, f"artifacts_cleanup_error: {e}")

        return res

    def run_job(self, spec: AeJobSpec) -> AeJobResult:
        with _RENDER_LOCK:
            job_dir = self.base_dir / spec.job_id / "app"
            jsx_path = job_dir / "render.jsx"
            output_path = job_dir / spec.output_relpath

            log.info("=== AE RENDER START job_id=%s ===", spec.job_id)
            log.info("Job dir: %s", job_dir)

            self._best_effort_reset_ae_project(tag=f"{spec.job_id}_pre")

            if job_dir.exists():
                shutil.rmtree(job_dir)
            job_dir.mkdir(parents=True, exist_ok=True)

            message_parts: list[str] = []
            result: AeJobResult | None = None

            def _fail(msg: str) -> AeJobResult:
                return AeJobResult(
                    job_id=spec.job_id,
                    success=False,
                    message=str(msg),
                    app_dir=job_dir,
                )

            try:
                # 1) prepare files
                try:
                    self._prepare_files(
                        job_dir=job_dir,
                        jsx_path=jsx_path,
                        render_jsx=spec.render_jsx,
                        media_files=spec.media_files,
                    )
                except Exception as e:
                    log.exception("prepare/download error for job %s", spec.job_id)
                    result = _fail(f"prepare/download error: {e}")

                # debug jsx
                if result is None and self.debug_jsx_dir:
                    try:
                        dbg_dir = Path(self.debug_jsx_dir)
                        dbg_dir.mkdir(parents=True, exist_ok=True)
                        dbg_jsx = dbg_dir / f"{spec.job_id}.jsx"
                        shutil.copy2(jsx_path, dbg_jsx)
                    except Exception as e:
                        log.warning("Failed to save debug JSX for job %s: %s", spec.job_id, e)

                # 2) run AfterFX jsx builder
                if result is None:
                    try:
                        self._run_afterfx(
                            job_dir=job_dir,
                            jsx_path=jsx_path,
                            job_id=spec.job_id,
                            entry_comp=spec.entry_comp,
                            output_relpath=spec.output_relpath,
                        )
                    except Exception as e:
                        log.exception("AfterFX error for job %s", spec.job_id)
                        result = _fail(f"AfterFX error: {e}")

                # 3) read ae_status
                aep_path: Optional[str] = None
                comp_name: Optional[str] = None
                if result is None:
                    ok, aep_path, comp_name, status_msg = self._wait_for_status(job_dir, spec.job_id)
                    if not ok:
                        result = _fail(status_msg)

                # 4) run aerender
                if result is None:
                    project_path = Path(aep_path).resolve() if aep_path else (job_dir / f"debug_{spec.job_id}.aep").resolve()
                    if not project_path.exists():
                        result = _fail(f"AEP file {project_path} not found after OK status")
                    else:
                        comp_for_render = comp_name or spec.entry_comp
                        try:
                            self._run_aerender(
                                project_path=project_path,
                                job_id=spec.job_id,
                                entry_comp=comp_for_render,
                                output_path=output_path,
                                job_dir=job_dir,
                            )
                        except Exception as e:
                            log.exception("aerender error for job %s", spec.job_id)
                            result = _fail(f"aerender error: {e}")

                # 5) wait output
                if result is None:
                    if not self._wait_for_output(output_path):
                        result = _fail(f"output file {output_path} did not appear or is not stable in time")

                # 6) optional upload rendered mp4
                if result is None:
                    s3_url: Optional[str] = None
                    if spec.output_s3_bucket and spec.output_s3_key and output_path.exists() and output_path.stat().st_size > 0:
                        try:
                            upload_file_to_s3(
                                bucket=spec.output_s3_bucket,
                                key=spec.output_s3_key,
                                path=output_path,
                                content_type="video/mp4",
                            )
                            s3_url = generate_presigned_url(
                                bucket=spec.output_s3_bucket,
                                key=spec.output_s3_key,
                                expires_in=3600 * 24,
                            )
                            message_parts.append(f"uploaded to {s3_url}")
                        except Exception as e:
                            log.exception("s3 upload error for job %s", spec.job_id)
                            message_parts.append(f"s3 upload error: {e}")
                    else:
                        message_parts.append("output file not uploaded (missing bucket/key or file not found)")

                    final_message = "ok" if not message_parts else "ok; " + "; ".join(message_parts)
                    log.info("=== AE RENDER END job_id=%s ===", spec.job_id)

                    result = AeJobResult(
                        job_id=spec.job_id,
                        success=True,
                        message=final_message,
                        app_dir=job_dir,
                        output_path=output_path if output_path.exists() else None,
                        output_s3_url=s3_url,
                    )

            except Exception as e:
                log.exception("Unexpected renderer error for job %s", spec.job_id)
                result = _fail(f"unexpected renderer error: {e}")
            finally:
                # hard reset after every job to avoid project accumulation in AfterFX session
                self._best_effort_reset_ae_project(tag=f"{spec.job_id}_post")

            if result is None:
                result = _fail("unknown renderer failure")

            return self._finalize_result_artifacts(result)

    def _prepare_files(self, job_dir: Path, jsx_path: Path, render_jsx: str, media_files: List[MediaFileSpec]) -> None:
        (job_dir / "media" / "audio").mkdir(parents=True, exist_ok=True)
        (job_dir / "media" / "video").mkdir(parents=True, exist_ok=True)
        (job_dir / "work").mkdir(parents=True, exist_ok=True)

        jsx_path.write_text(render_jsx, encoding="utf-8")

        for m in media_files:
            dest = job_dir / m.relpath
            dest.parent.mkdir(parents=True, exist_ok=True)
            self._download_any(m.url, dest)

        self._patch_project_paths(jsx_path, job_dir)

    def _download_any(self, url: str, dest: Path) -> None:
        u = (url or "").strip()
        if u.startswith("s3://"):
            bucket, key = parse_s3_url(u)
            download_file_from_s3(bucket=bucket, key=key, dest=dest)
            return

        max_attempts = int(os.getenv("HTTP_DOWNLOAD_MAX_ATTEMPTS", "6") or "6")
        base_backoff_s = float(os.getenv("HTTP_DOWNLOAD_BACKOFF_S", "1") or "1")
        timeout_s = float(os.getenv("HTTP_DOWNLOAD_TIMEOUT_S", "300") or "300")
        retry_statuses = {429, 500, 502, 503, 504}

        last_err: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                resp = requests.get(u, stream=True, timeout=timeout_s)
                try:
                    code = int(resp.status_code)
                    if code in retry_statuses:
                        _ = resp.content
                        raise requests.HTTPError(f"HTTP {code} for {u}", response=resp)

                    resp.raise_for_status()
                    with open(dest, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                    return
                finally:
                    resp.close()
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as e:
                last_err = e
                if isinstance(e, requests.HTTPError):
                    r = getattr(e, "response", None)
                    status = int(getattr(r, "status_code", 0) or 0)
                    if status not in retry_statuses:
                        raise
                if attempt >= max_attempts:
                    break
                sleep_s = min(30.0, base_backoff_s * (2 ** (attempt - 1)))
                time.sleep(sleep_s)

        raise RuntimeError(f"HTTP download failed after {max_attempts} attempts: {u} ({last_err!r})")

    def _patch_project_paths(self, jsx_path: Path, app_dir: Path) -> None:
        text = jsx_path.read_text(encoding="utf-8")
        marker = "var PROJECT_DATA"
        idx = text.find(marker)
        if idx == -1:
            return
        end_idx = text.find("};", idx)
        if end_idx == -1:
            return
        blob = text[idx: end_idx + 2]

        def is_abs(path_str: str) -> bool:
            return bool(path_str and (path_str.startswith("/") or path_str.startswith("\\") or (len(path_str) >= 2 and path_str[1] == ":")))

        def repl(m: re.Match) -> str:
            orig = m.group(1)
            if is_abs(orig):
                return m.group(0)
            full = (app_dir / orig).resolve().as_posix()
            return f'"path": "{full}"'

        new_blob = re.sub(r'"path"\s*:\s*"([^"]*)"', repl, blob)
        if new_blob != blob:
            jsx_path.write_text(text[:idx] + new_blob + text[end_idx + 2:], encoding="utf-8")

    def _run_afterfx(self, job_dir: Path, jsx_path: Path, job_id: str, entry_comp: str, output_relpath: str) -> None:
        env = os.environ.copy()
        env["APP_DIR"] = str(job_dir)
        env["JOB_ID"] = job_id
        env["COMP_NAME"] = entry_comp
        env["OUTPUT_REL"] = output_relpath

        cmd = [self.afterfx_bin, "-r", str(jsx_path)]
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"AfterFX failed with code {proc.returncode}\n"
                f"stdout:\n{proc.stdout}\n"
                f"stderr:\n{proc.stderr}"
            )

    def _wait_for_status(self, job_dir: Path, job_id: str, timeout_seconds: int = 300) -> Tuple[bool, Optional[str], Optional[str], str]:
        status_path = job_dir / "ae_status.txt"
        start = time.time()
        while True:
            if status_path.exists():
                text = status_path.read_text(encoding="utf-8", errors="ignore")
                if text:
                    lines = text.splitlines()
                    status = (lines[0].strip() if lines else "").upper()
                    rest = lines[1:]
                    msg = "\n".join(rest).strip()
                    aep_path = None
                    comp_name = None
                    for line in rest:
                        s = line.strip()
                        if s.lower().startswith("aep="):
                            aep_path = s[4:].strip()
                        elif s.lower().startswith("compname="):
                            comp_name = s[len("compName="):].strip()
                    if status == "OK":
                        return True, aep_path, comp_name, msg
                    if status == "ERROR":
                        return False, None, None, f"AE script reported ERROR: {msg}"
            if time.time() - start > timeout_seconds:
                return False, None, None, f"Timeout waiting for ae_status.txt for job {job_id}"
            time.sleep(0.5)

    @staticmethod
    def _file_progress_sig(path: Path) -> tuple[int, int]:
        try:
            st = path.stat()
        except FileNotFoundError:
            return (-1, -1)
        except Exception:
            return (-1, -1)
        return (int(st.st_size), int(st.st_mtime_ns))

    @staticmethod
    def _tail_text(path: Path, max_bytes: int = 64 * 1024) -> str:
        try:
            with open(path, "rb") as f:
                try:
                    f.seek(0, os.SEEK_END)
                    size = f.tell()
                    f.seek(max(0, size - max_bytes), os.SEEK_SET)
                except Exception:
                    pass
                data = f.read()
                text = data.decode("utf-8", errors="replace")
                if "\x00" not in text:
                    return text
                try:
                    return data.decode("utf-16", errors="replace")
                except Exception:
                    return text
        except Exception:
            return ""

    @staticmethod
    def _project_log_dir(project_path: Path) -> Path:
        return project_path.parent / f"{project_path.name} Logs"

    def _project_logs(self, project_path: Path) -> list[Path]:
        log_dir = self._project_log_dir(project_path)
        try:
            return sorted(
                [p for p in log_dir.glob("*.txt") if p.is_file()],
                key=lambda p: p.stat().st_mtime_ns,
            )
        except Exception:
            return []

    def _project_logs_progress_sig(self, project_path: Path) -> tuple[int, int, int]:
        total_size = 0
        newest_mtime = -1
        count = 0
        for path in self._project_logs(project_path):
            size, mtime = self._file_progress_sig(path)
            if size < 0:
                continue
            count += 1
            total_size += size
            newest_mtime = max(newest_mtime, mtime)
        return (count, total_size, newest_mtime)

    def _aerender_progress_sig(
        self,
        *,
        project_path: Path,
        output_path: Path,
        stdout_log_path: Path,
        stderr_log_path: Path,
    ) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int], tuple[int, int, int]]:
        return (
            self._file_progress_sig(output_path),
            self._file_progress_sig(stdout_log_path),
            self._file_progress_sig(stderr_log_path),
            self._project_logs_progress_sig(project_path),
        )

    def _project_log_reports_finished(self, project_path: Path) -> bool:
        for path in reversed(self._project_logs(project_path)):
            text = self._tail_text(path)
            if "Finished composition" in text and "Total Time Elapsed" in text:
                return True
        return False

    @staticmethod
    def _output_file_is_stable(output_path: Path, *, stable_seconds: float = 3.0) -> bool:
        try:
            st = output_path.stat()
        except Exception:
            return False
        if st.st_size <= 0:
            return False
        return (time.time() - float(st.st_mtime)) >= float(stable_seconds)

    def _render_completed_despite_idle(self, *, project_path: Path, output_path: Path) -> bool:
        return self._project_log_reports_finished(project_path) and self._output_file_is_stable(
            output_path,
            stable_seconds=max(1.0, self._env_float("AERENDER_OUTPUT_STABLE_S", 3.0)),
        )

    @staticmethod
    def _terminate_process(proc: subprocess.Popen[Any], *, reason: str) -> None:
        pid = getattr(proc, "pid", None)
        if pid and os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=30,
                    check=False,
                )
            except Exception:
                pass

        try:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=10)
        except Exception:
            try:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=10)
            except Exception:
                pass

    def _run_aerender(self, project_path: Path, job_id: str, entry_comp: str, output_path: Path, job_dir: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        logs_dir = job_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        stdout_log_path = logs_dir / "aerender.stdout.log"
        stderr_log_path = logs_dir / "aerender.stderr.log"

        total_timeout_s = self._env_float("AERENDER_TIMEOUT_S", 7200.0)
        idle_timeout_s = self._env_float("AERENDER_IDLE_TIMEOUT_S", 300.0)
        watchdog_poll_s = max(0.1, self._env_float("AERENDER_WATCHDOG_POLL_S", 5.0))

        cmd = [self.aerender_bin, "-project", str(project_path), "-comp", entry_comp, "-output", str(output_path)]
        started_at = time.time()
        rc: int | None = None
        with open(stdout_log_path, "w", encoding="utf-8", errors="replace") as f_out, open(
            stderr_log_path, "w", encoding="utf-8", errors="replace"
        ) as f_err:
            f_out.write(
                f"[aerender] job_id={job_id} started_at={started_at:.3f} "
                f"timeout_s={total_timeout_s} idle_timeout_s={idle_timeout_s} poll_s={watchdog_poll_s}\n"
            )
            f_out.write(f"[aerender] cmd={' '.join(cmd)}\n")
            f_out.flush()

            proc = subprocess.Popen(cmd, env=os.environ.copy(), stdout=f_out, stderr=f_err)
            last_sig = self._aerender_progress_sig(
                project_path=project_path,
                output_path=output_path,
                stdout_log_path=stdout_log_path,
                stderr_log_path=stderr_log_path,
            )
            last_progress_at = time.time()

            while True:
                rc = proc.poll()
                now = time.time()
                sig = self._aerender_progress_sig(
                    project_path=project_path,
                    output_path=output_path,
                    stdout_log_path=stdout_log_path,
                    stderr_log_path=stderr_log_path,
                )
                if sig != last_sig:
                    last_sig = sig
                    last_progress_at = now

                if rc is not None:
                    break

                if total_timeout_s > 0 and (now - started_at) > total_timeout_s:
                    self._terminate_process(proc, reason=f"total_timeout>{total_timeout_s}s")
                    raise RuntimeError(
                        f"aerender timeout total>{total_timeout_s}s; "
                        f"logs={stdout_log_path};{stderr_log_path}"
                    )

                if idle_timeout_s > 0 and (now - last_progress_at) > idle_timeout_s:
                    if self._render_completed_despite_idle(project_path=project_path, output_path=output_path):
                        log.warning(
                            "aerender idle watchdog saw completed AE render; accepting output and terminating stuck process "
                            "job_id=%s output=%s",
                            job_id,
                            output_path,
                        )
                        self._terminate_process(proc, reason=f"completed_after_idle>{idle_timeout_s}s")
                        rc = 0
                        break

                    self._terminate_process(proc, reason=f"idle_timeout>{idle_timeout_s}s")
                    raise RuntimeError(
                        f"aerender timeout idle>{idle_timeout_s}s without progress; "
                        f"logs={stdout_log_path};{stderr_log_path}"
                    )

                time.sleep(watchdog_poll_s)

            f_out.flush()
            f_err.flush()

        stdout = self._tail_text(stdout_log_path)
        stderr = self._tail_text(stderr_log_path)
        has_text_error = any(m in stdout or m in stderr for m in ["aerender ERROR:", "After Effects error:"])

        if rc != 0 or has_text_error:
            raise RuntimeError(
                f"aerender failed (code={rc}, error_markers={has_text_error}); "
                f"logs={stdout_log_path};{stderr_log_path}"
            )

    def _wait_for_output(self, output_path: Path, timeout_seconds: int = 1800, stable_seconds: int = 3) -> bool:
        start = time.time()
        last_size = -1
        last_change = time.time()
        while True:
            if output_path.exists():
                try:
                    size = output_path.stat().st_size
                except Exception:
                    size = -1

                if size != last_size:
                    last_size = size
                    last_change = time.time()
                else:
                    if size > 0 and (time.time() - last_change) >= stable_seconds:
                        return True

            if time.time() - start > timeout_seconds:
                return False
            time.sleep(1.0)


def make_job_spec_from_payload(payload: dict) -> AeJobSpec:
    import uuid

    job_id = str(payload.get("job_id") or uuid.uuid4().hex).strip()

    has_inline = bool(payload.get("render_jsx"))
    has_s3_refs = bool(payload.get("render_jsx_s3_uri") or payload.get("render_payload_s3_uri") or payload.get("audio_url"))

    if has_inline and has_s3_refs:
        raise RuntimeError("Invalid payload: use either inline mode (render_jsx+media) or s3-ref mode, not both")

    entry_comp = payload.get("entry_comp", "Main Render")
    output_relpath = payload.get("output_relpath", "work/output.mp4")
    output_s3_bucket = payload.get("output_s3_bucket")
    output_s3_key = payload.get("output_s3_key")

    if has_s3_refs:
        render_jsx_s3_uri = str(payload.get("render_jsx_s3_uri") or "").strip()
        render_payload_s3_uri = str(payload.get("render_payload_s3_uri") or "").strip()
        audio_url = str(payload.get("audio_url") or "").strip()

        if not render_jsx_s3_uri or not render_payload_s3_uri or not audio_url:
            raise RuntimeError("s3-ref mode requires render_jsx_s3_uri, render_payload_s3_uri, audio_url")

        render_jsx = _read_remote_text(render_jsx_s3_uri)
        render_payload_text = _read_remote_text(render_payload_s3_uri)
        media = _collect_media_specs_from_render_payload(render_payload_text, audio_url=audio_url)
    else:
        render_jsx = payload.get("render_jsx")
        if not isinstance(render_jsx, str) or not render_jsx.strip():
            raise RuntimeError("inline mode requires non-empty render_jsx")
        media = [MediaFileSpec(url=m["url"], relpath=m["relpath"]) for m in payload.get("media", [])]

    return AeJobSpec(
        job_id=job_id,
        render_jsx=render_jsx,
        media_files=media,
        entry_comp=entry_comp,
        output_relpath=output_relpath,
        output_s3_bucket=output_s3_bucket,
        output_s3_key=output_s3_key,
    )
