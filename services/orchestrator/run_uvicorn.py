# services/orchestrator/run_uvicorn.py
from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = (os.environ.get("ORCH_HOST", "0.0.0.0") or "0.0.0.0").strip()
    port = int((os.environ.get("ORCH_PORT", "8000") or "8000").strip())
    uvicorn.run("services.orchestrator.app:app", host=host, port=port, reload=False, log_level="info")


if __name__ == "__main__":
    main()
