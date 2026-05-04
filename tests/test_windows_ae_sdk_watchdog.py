from __future__ import annotations

import os
import stat
import sys
import time
from pathlib import Path

import pytest

RUNTIME_DIR = Path(__file__).resolve().parents[1] / "windows" / "render-node-runtime"
sys.path.insert(0, str(RUNTIME_DIR))

from ae_sdk import AeRenderer  # noqa: E402


def _sleeping_executable(tmp_path: Path) -> Path:
    script = tmp_path / "fake_aerender.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import time\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def _mark_old(path: Path) -> None:
    old = time.time() - 10
    os.utime(path, (old, old))


def test_aerender_idle_accepts_finished_project_log_and_stable_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AERENDER_IDLE_TIMEOUT_S", "0.05")
    monkeypatch.setenv("AERENDER_WATCHDOG_POLL_S", "0.05")
    monkeypatch.setenv("AERENDER_OUTPUT_STABLE_S", "0.01")

    job_dir = tmp_path / "job" / "app"
    work_dir = job_dir / "work"
    log_dir = work_dir / "project.aep Logs"
    log_dir.mkdir(parents=True)

    project_path = work_dir / "project.aep"
    project_path.write_bytes(b"aep")

    output_path = work_dir / "output.mp4"
    output_path.write_bytes(b"complete mp4")
    _mark_old(output_path)

    ae_log = log_dir / "AE render.txt"
    ae_log.write_text(
        '5/3/2026 10:19:45 PM: Finished composition "Comp 1".\n'
        "Total Time Elapsed: 57 Seconds\n",
        encoding="utf-8",
    )
    _mark_old(ae_log)

    renderer = AeRenderer(base_dir=tmp_path, aerender_bin=_sleeping_executable(tmp_path))
    renderer._run_aerender(
        project_path=project_path,
        job_id="job1",
        entry_comp="Comp 1",
        output_path=output_path,
        job_dir=job_dir,
    )

    stdout_log = job_dir / "logs" / "aerender.stdout.log"
    assert "idle_timeout_s=0.05" in stdout_log.read_text(encoding="utf-8")


def test_aerender_idle_still_fails_without_finished_project_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AERENDER_IDLE_TIMEOUT_S", "0.05")
    monkeypatch.setenv("AERENDER_WATCHDOG_POLL_S", "0.05")
    monkeypatch.setenv("AERENDER_OUTPUT_STABLE_S", "0.01")

    job_dir = tmp_path / "job" / "app"
    work_dir = job_dir / "work"
    work_dir.mkdir(parents=True)
    project_path = work_dir / "project.aep"
    project_path.write_bytes(b"aep")
    output_path = work_dir / "output.mp4"
    output_path.write_bytes(b"partial mp4")
    _mark_old(output_path)

    renderer = AeRenderer(base_dir=tmp_path, aerender_bin=_sleeping_executable(tmp_path))
    with pytest.raises(RuntimeError, match="aerender timeout idle>0.05s without progress"):
        renderer._run_aerender(
            project_path=project_path,
            job_id="job2",
            entry_comp="Comp 1",
            output_path=output_path,
            job_dir=job_dir,
        )
