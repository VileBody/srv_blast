"""Прогрев превью каталогов (scripts/prewarm_web_previews.py): делает недостающее, повтор — no-op."""
from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

from tests.test_web_asr_preview import _env

REPO_ROOT = Path(__file__).resolve().parents[1]


class _FakeBackend:
    def __init__(self, store, mp) -> None:
        self.store, self.mp, self.downloads = store, mp, []

    def media_store(self):
        return self.store

    def catalog_sources(self) -> list[str]:
        return ["s3://assets/previews/vibes/neon.mp4", "s3://fx/fx_assets/frames/exclude.png"]

    def catalog_versions(self, locators):
        return {loc: "etag1" for loc in locators}

    def download_locator(self, locator: str, path: Path) -> None:
        self.downloads.append(locator)
        path.write_bytes(b"SRC")

    def source_url(self, locator: str) -> str:
        return "https://s3.example/" + locator[5:]


def test_prewarm_builds_missing_copies_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
    _env(monkeypatch)
    mp = importlib.import_module("app.media_proxy")
    pb = importlib.import_module("app.production_backend")
    store = mp.LocalStore(tmp_path / "cache")
    backend = _FakeBackend(store, mp)
    monkeypatch.setattr(pb, "get_backend", lambda: backend)
    monkeypatch.setattr(mp, "transcode", lambda src, dst, args: dst.write_bytes(b"OUT"))
    monkeypatch.setattr(mp, "transcode_frame", lambda src, dst, at, scale=None: dst.write_bytes(b"JPG"))
    spec = importlib.util.spec_from_file_location("prewarm_web_previews", REPO_ROOT / "scripts" / "prewarm_web_previews.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    assert script.main(["--dry-run"]) == 0 and backend.downloads == []
    assert script.main([]) == 0
    clip = mp.preview_name("s3://assets/previews/vibes/neon.mp4", "etag1")
    assert store.has(clip) and store.has(mp.preview_poster_name("s3://assets/previews/vibes/neon.mp4", "etag1"))
    assert store.has(mp.image_name("s3://fx/fx_assets/frames/exclude.png", "etag1"))
    assert len(backend.downloads) == 2
    assert script.main([]) == 0 and len(backend.downloads) == 2  # всё готово — ничего не качаем
    assert "сделать 0" in capsys.readouterr().out
    sys.modules.pop("prewarm_web_previews", None)
