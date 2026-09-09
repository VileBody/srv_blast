from __future__ import annotations

import sys
from pathlib import Path

RUNTIME_DIR = Path(__file__).resolve().parents[1] / "windows" / "render-node-runtime"
sys.path.insert(0, str(RUNTIME_DIR))

from ae_sdk import AeRenderer  # noqa: E402


def test_every_written_script_disables_exit_after_launch_and_eval(tmp_path: Path) -> None:
    """`afterfx.exe -r <script>` makes AE quit once the script is evaluated, and
    quitting with a modified project open raises the blocking "Save changes ...
    before closing?" modal that wedges the node for every following job."""
    jsx = tmp_path / "render.jsx"
    AeRenderer._write_jsx_file(jsx, "// builder\n")

    text = jsx.read_text(encoding="utf-8-sig")
    assert text.startswith("try { app.exitAfterLaunchAndEval = false; } catch (e) {}")
    assert "// builder" in text


def test_preamble_is_not_stacked_on_rewrite(tmp_path: Path) -> None:
    """_patch_project_paths rewrites an already-written script."""
    jsx = tmp_path / "render.jsx"
    AeRenderer._write_jsx_file(jsx, "// builder\n")
    AeRenderer._write_jsx_file(jsx, jsx.read_text(encoding="utf-8-sig"))

    assert jsx.read_text(encoding="utf-8-sig").count("exitAfterLaunchAndEval") == 1


def test_bom_is_still_stripped_from_incoming_script(tmp_path: Path) -> None:
    jsx = tmp_path / "render.jsx"
    AeRenderer._write_jsx_file(jsx, "﻿// builder\n")

    assert jsx.read_text(encoding="utf-8-sig").count("﻿") == 0


def test_ae_process_is_recycled_after_each_job() -> None:
    """A warm AE session dies on the third job with "internal structure
    inconsistency (seq) (25 :: 8)", so each job gets its own process."""
    runtime = (RUNTIME_DIR / "ae_sdk.py").read_text(encoding="utf-8")

    recycle = runtime.index('self._env_bool("AE_RECYCLE_AFTER_JOB", True)')
    post_reset = runtime.index('self._maybe_reset_ae_project(tag=f"{spec.job_id}_post")')

    # The reset is the fallback branch, reached only with recycling turned off.
    assert recycle < post_reset
    assert "def _terminate_afterfx_session" in runtime
