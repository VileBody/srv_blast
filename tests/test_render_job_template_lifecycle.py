from pathlib import Path


JOB_TEMPLATE = Path("render_templates/job_template.jsx")
AE_RUNTIME = Path("windows/render-node-runtime/ae_sdk.py")


def test_job_builder_keeps_project_open_for_render() -> None:
    template = JOB_TEMPLATE.read_text(encoding="utf-8")

    assert "app.project.close(" not in template
    assert "writeStatus(\"OK\", msg)" in template
    assert "Keep the saved project open for aerender" in template


def test_afterfx_queue_reuses_project_and_closes_only_after_render() -> None:
    runtime = AE_RUNTIME.read_text(encoding="utf-8")

    wrapper_start = runtime.index("def _write_afterfx_queue_wrapper(")
    wrapper_end = runtime.index("def _run_afterfx(", wrapper_start)
    wrapper = runtime[wrapper_start:wrapper_end]

    render_call = wrapper.index("app.project.renderQueue.render()")
    success_status = wrapper.index('writeStatus("OK"', render_call)
    cleanup_close = wrapper.index(
        "app.project.close(CloseOptions.DO_NOT_SAVE_CHANGES)"
    )

    assert "app.open(aepFile)" not in wrapper
    assert "builder did not leave the expected AEP open" in wrapper
    assert render_call < success_status < cleanup_close
