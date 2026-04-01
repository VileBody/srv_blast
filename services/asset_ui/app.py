from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from services.orchestrator.asset_routes import create_asset_router

from .config import AssetUISettings

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_UI_DIST_DIR = REPO_ROOT / "asset_ui" / "dist"


def _resolve_ui_dist_dir() -> Path:
    raw = (os.getenv("ASSET_UI_DIST_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return DEFAULT_UI_DIST_DIR


def _validate_ui_dist_dir(dist_dir: Path) -> None:
    if not dist_dir.is_dir():
        raise RuntimeError(
            "asset-ui frontend build not found: "
            f"{dist_dir} (expected `asset_ui/dist`)."
        )
    index_path = dist_dir / "index.html"
    if not index_path.is_file():
        raise RuntimeError(
            f"asset-ui index file not found: {index_path}."
        )


def create_app(settings: AssetUISettings) -> FastAPI:
    # Keep env contract validation via AssetUISettings, even if most fields are
    # consumed by the API router internals rather than this bootstrap layer.
    _ = settings

    dist_dir = _resolve_ui_dist_dir()
    _validate_ui_dist_dir(dist_dir)

    app = FastAPI(title="Asset UI", version="1.0.0")

    # React UI calls relative `api/...`, so expose asset API at /api.
    app.include_router(create_asset_router(prefix="/api"))

    # Serve Vite build (index + static assets) from root.
    app.mount("/", StaticFiles(directory=str(dist_dir), html=True), name="asset-ui-frontend")
    return app
