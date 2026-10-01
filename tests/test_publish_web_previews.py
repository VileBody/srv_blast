from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import publish_web_previews as pub  # noqa: E402
from validate_web_preview_catalog_env import read_env  # noqa: E402

FX_GROUPS = ("effect_hook__", "effect_transition__", "effect_extra__", "motion__", "shape__")


def _env(path: Path) -> Path:
    fx = [{"id": p + "x", "name": "x", "previewUrl": "s3://b/old.mp4"} for p in FX_GROUPS]
    fx.append({"id": "effect_transition__snap_wipe", "name": "Снап", "previewUrl": "s3://b/old.mp4",
               "plane": "fx", "selector": {"effectTransition": "snap_wipe"}})
    subs = [{"id": "brat_5th", "name": "Brat", "plane": "subtitle", "previewUrl": "s3://b/old.mp4",
             "selector": {"subtitlesMode": "brat_5th"}}]
    values = {
        "S3_BUCKET_ASSET_STORAGE": "bucket",
        "S3_WEB_ASSET_PREFIX": "app/web/",
        "S3_ACCESS_KEY_ID": "secret-id",
        "WEB_FOOTAGE_CATALOG_JSON": json.dumps([{"id": i, "plane": i} for i in ("vibes", "cine16x9", "films")]),
        "WEB_FX_CATALOG_JSON": json.dumps(fx, ensure_ascii=False),
        "WEB_SUBTITLE_CATALOG_JSON": json.dumps(subs),
        "WEB_SUBTITLE_MODE_MAP_JSON": json.dumps({"Brat": "brat_5th"}),
    }
    path.write_text("# prod\n" + "".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    path.chmod(0o600)
    return path


def _stage(tmp: Path) -> Path:
    stage = tmp / "stage"
    (stage / "previews").mkdir(parents=True)
    manifest = [
        {"plane": "fx", "category": "effect_transition", "id": "snap_wipe", "file": "a.mp4", "name": "Снап", "in_registry": True},
        {"plane": "fx", "category": "effect_extra", "id": "cc_tritone_red", "file": "b.mp4", "name": "Красный тритон", "in_registry": True},
        {"plane": "fx", "category": "effect_transition", "id": "layer_shake", "file": "c.mp4", "name": "layer_shake", "in_registry": False},
        {"plane": "subtitle", "mode": "brat_5th", "file": "d.mp4", "name": "Brat"},
        {"plane": "subtitle", "mode": "kant_gum", "file": "e.mp4", "name": "Bubble", "new_style": True},
    ]
    for m in manifest:
        (stage / "previews" / m["file"]).write_bytes(b"mp4")
    (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return stage


class FakeS3:
    def __init__(self):
        self.keys: list[str] = []

    def put_object(self, *, Bucket, Key, Body, ContentType):
        assert Bucket == "bucket" and ContentType == "video/mp4"
        self.keys.append(Key)

    def head_object(self, *, Bucket, Key):
        assert Key in self.keys


@pytest.fixture
def s3(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr(pub, "s3_client", lambda env: fake)
    return fake


def test_updates_existing_adds_registry_items_and_skips_the_rest(tmp_path, s3, capsys):
    env_file = _env(tmp_path / ".env.production")
    assert pub.main([str(env_file), "--stage", str(_stage(tmp_path))]) == 0

    env = read_env(env_file)
    fx = {i["id"]: i for i in json.loads(env["WEB_FX_CATALOG_JSON"])}
    assert fx["effect_transition__snap_wipe"]["previewUrl"] == "s3://bucket/app/web/previews/fx/v2/effect_transition__snap_wipe.mp4"
    assert fx["effect_extra__cc_tritone_red"]["selector"] == {"effectExtra": "cc_tritone_red"}
    assert "effect_transition__layer_shake" not in fx           # нет на сайте — не добавляем
    subs = {i["id"]: i for i in json.loads(env["WEB_SUBTITLE_CATALOG_JSON"])}
    assert subs["brat_5th"]["previewUrl"].endswith("/previews/subtitle/v2/brat_5th.mp4")
    assert "kant_gum" not in subs                                # новый стиль — только по флагу
    assert json.loads(env["WEB_SUBTITLE_MODE_MAP_JSON"]) == {"Brat": "brat_5th"}
    assert len(s3.keys) == 3
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600 or sys.platform == "win32"
    assert list(tmp_path.glob(".env.production.bak-previews-*"))
    assert "secret-id" not in capsys.readouterr().out


def test_new_subtitle_styles_go_to_catalog_and_mode_map_only_with_flag(tmp_path, s3):
    env_file = _env(tmp_path / ".env.production")
    pub.main([str(env_file), "--stage", str(_stage(tmp_path)), "--with-new-subtitle-styles", "--wave", "v3"])
    env = read_env(env_file)
    subs = {i["id"]: i for i in json.loads(env["WEB_SUBTITLE_CATALOG_JSON"])}
    assert subs["kant_gum"]["name"] == "Bubble"
    assert subs["kant_gum"]["previewUrl"].endswith("/previews/subtitle/v3/kant_gum.mp4")
    assert json.loads(env["WEB_SUBTITLE_MODE_MAP_JSON"])["Bubble"] == "kant_gum"


def test_invalid_catalog_uploads_nothing_and_keeps_env(tmp_path, s3):
    env_file = _env(tmp_path / ".env.production")
    text = env_file.read_text(encoding="utf-8").replace('"plane": "films"', '"plane": "other"')
    env_file.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        pub.main([str(env_file), "--stage", str(_stage(tmp_path))])
    assert s3.keys == []
    assert env_file.read_text(encoding="utf-8") == text


def test_missing_preview_file_fails_before_upload(tmp_path, s3):
    env_file = _env(tmp_path / ".env.production")
    stage = _stage(tmp_path)
    (stage / "previews" / "b.mp4").unlink()
    with pytest.raises(SystemExit, match="нет файла превью"):
        pub.main([str(env_file), "--stage", str(stage)])
    assert s3.keys == []
