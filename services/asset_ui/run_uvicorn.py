from __future__ import annotations

import uvicorn

from .app import create_app
from .config import load_settings


def main() -> None:
    settings = load_settings()
    app = create_app(settings)
    uvicorn.run(app, host="0.0.0.0", port=settings.port, reload=False, log_level="info")


if __name__ == "__main__":
    main()

