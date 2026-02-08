from __future__ import annotations

import os

MODE_DEV = "dev"
MODE_PROD = "prod"
_ALLOWED = {MODE_DEV, MODE_PROD}


def get_runtime_mode() -> str:
    """
    Strict runtime mode selector.
    MODE must be explicitly set to: dev | prod.
    """
    mode = (os.environ.get("MODE") or "").strip().lower()
    if mode not in _ALLOWED:
        raise RuntimeError("MODE must be explicitly set to 'dev' or 'prod'")
    return mode

