# render_v1/assembler.py
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Tuple

from render_v1.assembler_core import build_project_payload_from_composition

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEMPLATE_ENGINE_PATH = REPO_ROOT / "render_templates" / "template_engine.jsx"
DEFAULT_INJECT_MARKER = "/*__PYTHON_DATA_INJECT__*/"


def build_render_jsx_from_project_data_json(
    project_data_json: str,
    *,
    template_path: Path = DEFAULT_TEMPLATE_ENGINE_PATH,
    inject_marker: str = DEFAULT_INJECT_MARKER,
) -> str:
    """Inject PROJECT_DATA JSON into an ExtendScript template to produce render.jsx."""

    template = template_path.read_text(encoding="utf-8")
    inject = "var PROJECT_DATA = " + project_data_json + ";\n"
    if inject_marker not in template:
        raise ValueError(f"Inject marker not found in template: {inject_marker}")

    return template.replace(inject_marker, inject)


def build_render_jsx_from_composition(
    composition: Dict[str, Any],
    *,
    template_path: Path = DEFAULT_TEMPLATE_ENGINE_PATH,
    inject_marker: str = DEFAULT_INJECT_MARKER,
) -> Tuple[Dict[str, Any], str, str]:
    """High-level one-shot: composition.json -> (raw_payload, project_data_json, render.jsx)."""

    raw_payload, project_data_json = build_project_payload_from_composition(composition)
    render_jsx = build_render_jsx_from_project_data_json(
        project_data_json,
        template_path=template_path,
        inject_marker=inject_marker,
    )
    return raw_payload, project_data_json, render_jsx
