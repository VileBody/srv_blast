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


def test_recycle_asks_ae_to_quit_instead_of_killing_it() -> None:
    """taskkill registers as a crash, and the next AE start then blocks on the
    modal "Crash Repair Options" dialog, which the modal watcher cannot dismiss
    (the window carries no title, so the watcher never even sees it)."""
    runtime = (RUNTIME_DIR / "ae_sdk.py").read_text(encoding="utf-8")

    recycle = runtime.index("def _recycle_afterfx_session")
    body = runtime[recycle : runtime.index("def _clear_ae_crash_flags")]

    assert "app.quit()" in body
    assert "CloseOptions.DO_NOT_SAVE_CHANGES" in body
    # kill stays available, but only once a clean quit has failed
    assert body.index("app.quit()") < body.index("_terminate_afterfx_session")


def test_ae_process_probe_covers_both_image_names() -> None:
    """The GUI runs as AfterFX.com on the render node; probing only AfterFX.exe
    reports a live session as gone and turns the recycle into a no-op."""
    runtime = (RUNTIME_DIR / "ae_sdk.py").read_text(encoding="utf-8")
    probe = runtime[runtime.index("def _afterfx_is_running") : runtime.index("def _recycle_afterfx_session")]

    assert "AfterFX.exe" in probe
    assert "AfterFX.com" in probe


def test_crash_flags_are_cleared_before_a_job_can_launch_ae() -> None:
    runtime = (RUNTIME_DIR / "ae_sdk.py").read_text(encoding="utf-8")

    clear = runtime.index("self._clear_ae_crash_flags()\n                    self._maybe_reset_ae_project")
    assert clear > 0


def test_footage_and_audio_are_normalised_before_ae_sees_them() -> None:
    """AE indexes frames from every source it imports. VFR clips and the
    90000/1 cover-art stream some MP3s carry are what it chokes on with
    "internal structure inconsistency (seq) (25 :: 8)"."""
    runtime = (RUNTIME_DIR / "ae_sdk.py").read_text(encoding="utf-8")
    prepare = runtime[runtime.index("def _prepare_files") : runtime.index("def _strip_audio_cover_art")]

    assert "_normalize_footage_to_cfr" in prepare
    assert "_strip_audio_cover_art" in prepare
    # both must run on the downloaded file, before the project paths are patched
    assert prepare.index("_normalize_footage_to_cfr") < prepare.index("_patch_project_paths")


def test_wedged_afterfx_fails_one_job_instead_of_the_node() -> None:
    """Without an idle guard a hung AE holds _RENDER_LOCK until
    AFTERFX_RUN_TIMEOUT_S (7200s on the node) and every queued job waits."""
    runtime = (RUNTIME_DIR / "ae_sdk.py").read_text(encoding="utf-8")
    run = runtime[runtime.index("def _run_afterfx") : runtime.index("def _wait_for_status")]

    assert "AFTERFX_IDLE_TIMEOUT_S" in run
    assert "AfterFX idle timeout" in run
    # the idle check has to precede the total-timeout branch to be reachable
    assert run.index("idle_timeout_s > 0") < run.index("timeout_s > 0 and (now - started_at) > timeout_s")


def test_brat_keyframes_use_the_comp_frame_rate() -> None:
    """BRAT quantised its reveal keyframes to a hardcoded 30fps grid while the
    comps run at 23.976, so every keyframe landed between frames -- across the
    per-word precomps it builds, that is the mixed-rate timing AE rejects."""
    brat = (Path(__file__).resolve().parents[1] / "5th_template" / "brat_subtitles.jsx").read_text(encoding="utf-8")

    bind = brat.index("CONFIG.fps = srcComp.frameRate")
    grid = brat.index("var fr = 1.0 / CONFIG.fps;", bind)
    assert bind < grid


def test_builder_template_opens_no_undo_group() -> None:
    """AE does not nest undo groups. The injected overlay scripts each open
    their own, so an outer group around them left AE with more endUndoGroup
    calls than begins -- "Undo group mismatch, will attempt to fix", which
    rewrites AE's undo stack. A render node never needs undo."""
    root = Path(__file__).resolve().parents[1]
    template = (root / "templates" / "project_template.j2").read_text(encoding="utf-8")

    code = "\n".join(
        line for line in template.splitlines() if not line.lstrip().startswith("//")
    )
    assert "beginUndoGroup" not in code
    assert "endUndoGroup" not in code


def test_each_overlay_gets_its_own_scope() -> None:
    """Overlays are concatenated into one script. With their helpers at top
    level, hoisting made the last declaration of a name win for everybody: the
    F1 sound hook ran BRAT's pickFile and looked for subtitles.json instead of
    its sound file."""
    import sys

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from app.project_builder import _isolate_overlay_js

    wrapped = _isolate_overlay_js("f1", "function pickFile(){ return null; }")
    assert wrapped.startswith("// --- f1 (isolated scope) ---")
    assert "(function () {" in wrapped and "})();" in wrapped
    assert _isolate_overlay_js("f2", "") == ""


def test_bundled_scripts_cannot_open_a_modal() -> None:
    """A dialog on the render node blocks AE until someone clicks it, and
    nobody is watching. Dev-only scripts are not bundled and may still alert."""
    root = Path(__file__).resolve().parents[1]
    offenders = []
    for folder in ("mlcore/hooks", "5th_template"):
        for jsx in (root / folder).rglob("*.jsx"):
            if jsx.name == "test_sounds.jsx":  # manual tool, never bundled
                continue
            for num, line in enumerate(jsx.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("//"):
                    continue
                if "alert(" in line or "openDialog" in line:
                    offenders.append(f"{jsx.relative_to(root)}:{num}")
    assert not offenders, offenders


def test_watcher_dismisses_untitled_ae_dialogs() -> None:
    """AE's blocking dialogs (Crash Repair Options, "one chance to save your
    project") carry no window title, so the watcher used to discard them before
    it ever looked. A real AE window always has a title, so an untitled visible
    window owned by AfterFX is one of those dialogs; Enter takes its default
    button. Verified on the node: dialog detected and dismissed in the same
    second, job completed normally."""
    root = Path(__file__).resolve().parents[1]
    watcher = (root / "windows" / "render-node-runtime" / "ae_modal_watcher.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert "untitled_dialog_detected" in watcher
    assert "SendKeys(\"{ENTER}\")" in watcher
    # the enumeration must keep title-less windows instead of returning early
    enum = watcher[watcher.index("GetWindowTextLength") : watcher.index("GetWindowThreadProcessId")]
    assert "if ($len -le 0) {\n      return $true\n    }" not in enum


def test_idle_watchdog_watches_the_builder_heartbeat() -> None:
    """The status file, project.aep and output.mp4 only appear near the end of a
    job. On a complex build nothing else moved for minutes, so the idle guard
    read a healthy build as silence and killed it. hb.txt and run_live.log are
    the builder's own heartbeat and are the only things that move meanwhile."""
    runtime = (RUNTIME_DIR / "ae_sdk.py").read_text(encoding="utf-8")
    run = runtime[runtime.index("def _run_afterfx") : runtime.index("def _wait_for_status")]

    assert 'job_dir / "hb.txt"' in run
    assert 'job_dir / "run_live.log"' in run


def test_finished_renders_survive_an_agent_restart() -> None:
    """The registry lived only in memory, so restarting the agent turned a
    finished-but-uncollected render into a 404 and refunded a video that had
    actually rendered (job 6cc3ef6a). In-flight renders must come back as a
    failure with a reason rather than vanish."""
    node_main = (RUNTIME_DIR / "main.py").read_text(encoding="utf-8")

    assert "_persist_locked" in node_main
    assert "_restore" in node_main
    assert "render agent restarted while this job was in flight" in node_main
    # every terminal transition has to reach the disk, not just the happy path
    assert node_main.count("self._persist_locked()") >= 4


def test_failed_render_does_not_block_a_retry() -> None:
    """A failed record used to be handed straight back on re-dispatch, so the
    job was never given to AE again — it just got the old failure until the
    record aged out (a day). accepted/running/succeeded keep their idempotency,
    which is what protects against duplicate renders."""
    node_main = (RUNTIME_DIR / "main.py").read_text(encoding="utf-8")

    assert "_drop_failed_locked" in node_main
    submit = node_main[node_main.index("def submit(") : node_main.index("def _run(")]
    # both idempotency checks must consult it, not just the second one
    assert submit.count("_drop_failed_locked") == 2

    drop = node_main[node_main.index("def _drop_failed_locked") : node_main.index("def _payload_hash")]
    assert 'st.status != "failed"' in drop  # only failures are forgotten


def test_render_record_can_be_forgotten_but_not_while_running() -> None:
    node_main = (RUNTIME_DIR / "main.py").read_text(encoding="utf-8")

    assert '@app.delete("/render/{render_id}")' in node_main
    forget = node_main[node_main.index("def forget(") : node_main.index("def stats(")]
    assert 'return "running"' in forget
    assert "self._persist_locked()" in forget
