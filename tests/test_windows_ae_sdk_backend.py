from __future__ import annotations

import sys
from pathlib import Path

import pytest

RUNTIME_DIR = Path(__file__).resolve().parents[1] / "windows" / "render-node-runtime"
sys.path.insert(0, str(RUNTIME_DIR))

from ae_sdk import AeRenderer  # noqa: E402


def test_render_backend_must_be_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AE_RENDER_BACKEND", raising=False)

    with pytest.raises(RuntimeError, match="AE_RENDER_BACKEND must be explicitly set"):
        AeRenderer._render_backend()


@pytest.mark.parametrize("value", ["afterfx_queue", "AFTERFX_QUEUE", "aerender"])
def test_render_backend_accepts_supported_values(
    value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AE_RENDER_BACKEND", value)

    assert AeRenderer._render_backend() == value.lower()


def test_afterfx_queue_wrapper_renders_inside_full_ae(tmp_path: Path) -> None:
    renderer = AeRenderer(base_dir=tmp_path)
    job_dir = tmp_path / "job" / "app"
    source_jsx = job_dir / "render.jsx"
    source_jsx.parent.mkdir(parents=True)
    source_jsx.write_text("// builder", encoding="utf-8")

    wrapper_path = renderer._write_afterfx_queue_wrapper(
        job_dir=job_dir,
        source_jsx_path=source_jsx,
        entry_comp="Comp 1",
        output_relpath="work/output.mp4",
    )
    wrapper = wrapper_path.read_text(encoding="utf-8-sig")

    assert "$.evalFile(new File(CFG.source_jsx_path))" in wrapper
    assert '"builder_status_path"' in wrapper
    assert "ae_status.txt" in wrapper
    assert '"status_path"' in wrapper
    assert "render_queue_status.txt" in wrapper
    assert "parseStatus(readText(CFG.builder_status_path))" in wrapper
    assert "app.project.renderQueue.items.add(comp)" in wrapper
    assert "app.project.renderQueue.render()" in wrapper


def test_render_output_rejects_mp4_header_only(tmp_path: Path) -> None:
    renderer = AeRenderer(base_dir=tmp_path)
    output_path = tmp_path / "output.mp4"
    output_path.write_bytes(
        bytes.fromhex("00000018667479706d703432000000006d7034326d703431")
    )

    assert not renderer._wait_for_output(
        output_path,
        timeout_seconds=0,
        stable_seconds=0,
    )
