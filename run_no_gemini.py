#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# на всякий: пусть .env грузится (не обязательно, но удобно)
load_dotenv(dotenv_path=ROOT / ".env", override=False)

from app.project_builder import build_full_project  # noqa: E402


def main() -> None:
    out_json, out_jsx = build_full_project(
        repo_root=ROOT,
        full_edit_config_path=ROOT / "data" / "full_edit_config.json",
        footage_config_path=ROOT / "data" / "footage_config.json",
        out_dir=ROOT / "out",
    )

    print(f"OK: {out_json}")
    print(f"OK: {out_jsx}")
    print(f"AE logs should appear in: {ROOT / 'out' / 'logs'}")


if __name__ == "__main__":
    main()
