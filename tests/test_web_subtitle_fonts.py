"""Шрифты субтитров для превью сайта: только настоящие файлы с нашего сервера, без подмен."""
from __future__ import annotations

import importlib
import importlib.util
import io
import sys
from pathlib import Path

import pytest

from tests.test_web_asr_preview import _env

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _own_app_package():
    """Пакет `app` здесь — веб-бэкенд; корень репо тоже `app`: подменяем на время теста."""
    saved = {n: m for n, m in sys.modules.items() if n == "app" or n.startswith("app.")}
    for name in saved:
        sys.modules.pop(name, None)
    yield
    for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
        sys.modules.pop(name, None)
    sys.modules.update(saved)


@pytest.fixture
def fonts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _env(monkeypatch)
    monkeypatch.setenv("WEB_SUBTITLE_FONTS_DIR", str(tmp_path))
    module = importlib.import_module("app.subtitle_fonts")
    module.reset_cache()
    return module, tmp_path


def _script():
    spec = importlib.util.spec_from_file_location("upload_subtitle_fonts", REPO_ROOT / "scripts" / "upload_subtitle_fonts_to_s3.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_required_fonts_cover_every_style_and_match_the_upload_script(fonts) -> None:
    module, _ = fonts
    need = module.required_fonts()
    # стандартные шрифты стилей, выбираемые в каталоге и шрифт brat с курсивами фокуса
    for ps in ("Point-SemiBold", "Point-Light", "Montserrat-BoldItalic", "Montserrat-Bold",
               "Katherine-Plus", "ArialNarrow", "ArialNarrow-Italic", "ArialNarrow-BoldItalic"):
        assert ps in need, ps
    # не выбираемые нигде (roles пусто) — не нужны
    assert "DriveHearts" not in need
    # скрипт заливки держит свой список (не импортирует движок) — он обязан совпадать
    assert _script().required_fonts() == need


def test_mock_serves_only_uploaded_files(fonts) -> None:
    module, folder = fonts
    (folder / "Point-SemiBold.woff2").write_bytes(b"wOF2-semibold")
    (folder / "Unknown-Font.woff2").write_bytes(b"x")
    files = module.font_files(production=False)
    assert files == {"Point-SemiBold": "/api/wizard/subtitle-font/Point-SemiBold.woff2?v=13"}
    assert module.read_font("Point-SemiBold", production=False) == b"wOF2-semibold"
    with pytest.raises(FileNotFoundError):
        module.read_font("Point-Light", production=False)       # нужен, но не залит
    with pytest.raises(KeyError):
        module.read_font("../../etc/passwd", production=False)  # имя только из каталога
    with pytest.raises(KeyError):
        module.read_font("Unknown-Font", production=False)


class _FakeS3:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects
        self.lists = 0
        self.gets = 0

    def get_paginator(self, name: str):
        assert name == "list_objects_v2"
        fake = self

        class _P:
            def paginate(self, Bucket: str, Prefix: str):
                fake.lists += 1
                assert Bucket == "fx-bucket"
                yield {"Contents": [{"Key": k, "Size": len(v)} for k, v in fake.objects.items() if k.startswith(Prefix)]}

        return _P()

    def get_object(self, Bucket: str, Key: str):
        self.gets += 1
        return {"Body": io.BytesIO(self.objects[Key])}


def test_production_lists_s3_once_and_caches_bytes(fonts, monkeypatch: pytest.MonkeyPatch) -> None:
    module, _ = fonts
    monkeypatch.setenv("FX_ASSETS_S3_BUCKET", "fx-bucket")
    monkeypatch.setenv("FX_ASSETS_S3_PREFIX", "fx_assets/")
    s3 = _FakeS3({"fx_assets/fonts/subtitles/Montserrat-Bold.woff2": b"montserrat",
                  "fx_assets/frames/exclude.png": b"png"})
    store = {"production": True, "s3": s3, "asset_bucket": "assets"}
    assert module.font_files(**store) == {"Montserrat-Bold": "/api/wizard/subtitle-font/Montserrat-Bold.woff2?v=10"}
    assert module.read_font("Montserrat-Bold", **store) == b"montserrat"
    assert module.read_font("Montserrat-Bold", **store) == b"montserrat"
    assert (s3.lists, s3.gets) == (1, 1)        # листинг на 5 минут, байты — в памяти
    with pytest.raises(FileNotFoundError):
        module.read_font("Point-Light", **store)


def test_endpoints(fonts) -> None:
    _, folder = fonts
    (folder / "ArialNarrow.woff2").write_bytes(b"narrow")
    main = importlib.import_module("app.main")
    from fastapi.testclient import TestClient

    with TestClient(main.app) as tc:
        catalog = tc.get("/api/wizard/subtitle-fonts").json()
        assert catalog["files"] == {"ArialNarrow": "/api/wizard/subtitle-font/ArialNarrow.woff2?v=6"}
        assert "ArialNarrow" in catalog["required"] and catalog["fonts"]
        ok = tc.get("/api/wizard/subtitle-font/ArialNarrow.woff2?v=6")
        assert ok.status_code == 200 and ok.content == b"narrow"
        assert ok.headers["content-type"] == "font/woff2"
        assert "immutable" in ok.headers["cache-control"]
        assert tc.get("/api/wizard/subtitle-font/Point-Light.woff2").status_code == 404
        assert tc.get("/api/wizard/subtitle-font/ArialNarrow.ttf").status_code == 404
