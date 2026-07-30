from pathlib import Path


JOB_TEMPLATE = Path("render_templates/job_template.jsx")
AE_RUNTIME = Path("windows/render-node-runtime/ae_sdk.py")


def test_job_builder_keeps_project_open_for_render() -> None:
    template = JOB_TEMPLATE.read_text(encoding="utf-8")

    assert "app.project.close(" not in template
    assert "writeStatus(\"OK\", msg)" in template
    assert "Keep the saved project open for aerender" in template


def test_runtime_closes_project_only_in_post_job_cleanup() -> None:
    runtime = AE_RUNTIME.read_text(encoding="utf-8")

    render_call = runtime.index("self._run_aerender(")
    post_cleanup = runtime.index(
        'self._best_effort_reset_ae_project(tag=f"{spec.job_id}_post")'
    )
    cleanup_close = runtime.index(
        "app.project.close(CloseOptions.DO_NOT_SAVE_CHANGES)"
    )

    assert render_call < post_cleanup
    assert cleanup_close < render_call
