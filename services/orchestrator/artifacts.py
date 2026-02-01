# services/orchestrator/artifacts.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


@dataclass(frozen=True)
class JobPaths:
    data_dir: Path
    out_dir: Path
    logs_dir: Path

    @property
    def full_edit_config(self) -> Path:
        return self.data_dir / "full_edit_config.json"

    @property
    def footage_config(self) -> Path:
        return self.data_dir / "footage_config.json"

    @property
    def audio_plan(self) -> Path:
        return self.data_dir / "audio_plan.json"

    @property
    def render_jsx(self) -> Path:
        return self.out_dir / "render_full.jsx"

    @property
    def render_payload(self) -> Path:
        return self.out_dir / "final_render_instructions_full.json"

    def manifest(self) -> Dict[str, Any]:
        """
        Stable manifest for external render nodes.
        For now: local paths (later: S3 URLs).
        """
        return {
            "data_dir": str(self.data_dir.resolve()),
            "out_dir": str(self.out_dir.resolve()),
            "files": {
                "audio_plan": str(self.audio_plan.resolve()),
                "full_edit_config": str(self.full_edit_config.resolve()),
                "footage_config": str(self.footage_config.resolve()),
                "render_jsx": str(self.render_jsx.resolve()),
                "render_payload": str(self.render_payload.resolve()),
            },
        }


def make_job_paths(*, work_dir: str, output_dir: str, job_id: str) -> JobPaths:
    work_root = Path(work_dir).resolve()
    out_root = Path(output_dir).resolve()

    data_dir = work_root / "jobs" / job_id / "data"
    out_dir = out_root / "jobs" / job_id / "out"
    logs_dir = out_dir / "logs"

    data_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    return JobPaths(data_dir=data_dir, out_dir=out_dir, logs_dir=logs_dir)
