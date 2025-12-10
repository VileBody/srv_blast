import logging
from pathlib import Path


def setup_logging(log_dir: Path | None = None) -> None:
    """
    Базовая настройка логгера для всего пайплайна.

    Если передан log_dir — дополнительно лог пишется в файл pipeline.log.
    """
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler()]

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / "pipeline.log", encoding="utf-8")
        handlers.append(file_handler)

    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=handlers,
    )
