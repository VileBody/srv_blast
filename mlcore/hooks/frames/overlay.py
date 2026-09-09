"""Билдер инъектируемого JSX-блока рамки.

Как и у F3, скрипт инлайнится в render_full.jsx одним куском и получает
параметры через `$.global.__BLAST`. Путь к PNG резолвится на ноде из
`__APP_DIR` + relpath (файл туда кладёт media[]-загрузчик).

Блок вставляется ПОСЛЕДНИМ токеном шаблона — после субтитров и после подъёма
F4-оверлея, чтобы рамка гарантированно оказалась верхним слоем.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from mlcore.hooks.frames.catalog import FRAME_IDS

_DIR = Path(__file__).resolve().parent
_SCRIPT = _DIR / "frame.jsx"


def _js(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def build_overlay_jsx(
    *,
    frame_id: str,
    asset_relpath: Optional[str],
    comp_var: str = "MAIN_COMP",
) -> str:
    """Вернуть JSX-блок рамки. Нет ассета => "" (ноль влияния на рендер)."""
    fid = (frame_id or "").strip().lower()
    if not fid:
        return ""
    if fid not in FRAME_IDS:
        raise ValueError(f"unknown frame id: {frame_id!r}")
    rel = (asset_relpath or "").strip().strip("/")
    if not rel:
        # ассет не доехал (нет S3-конфига) — рендерим без рамки, но громко в лог
        return ""

    comp_var = str(comp_var or "MAIN_COMP").strip()
    if not comp_var.isidentifier():
        raise ValueError(f"comp_var must be a JS identifier, got {comp_var!r}")

    parts = [
        "/* ===== Рамка (injected by build worker) ===== */",
        "(function(){",
        f'  if (typeof {comp_var} === "undefined" || !{comp_var}) {{ return; }}',
        f"  var __fr_comp = {comp_var};",
        "  $.global.__BLAST = { targetCompName: __fr_comp.name, framePath: "
        f'(String(__APP_DIR || "") + "/" + {_js(rel)}) }};',
        "  (function(){",
        _SCRIPT.read_text(encoding="utf-8"),
        "  })(); $.global.__BLAST = null;",
        "})();",
    ]
    return "\n".join(parts)
