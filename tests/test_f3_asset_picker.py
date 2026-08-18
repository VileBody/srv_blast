# -*- coding: utf-8 -*-
"""Unit tests for the F3 «Эффект» asset picker (S3-based catalog)."""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from mlcore.hooks.f3_effect import asset_picker


# ---------- helpers ----------


class _FakeS3Client:
    """In-memory S3 stub: maps prefix -> list of keys."""

    def __init__(self, contents_by_prefix: Dict[str, List[str]]):
        self._by_prefix = dict(contents_by_prefix)
        self.calls: List[Dict[str, Any]] = []

    def list_objects_v2(self, **kwargs):
        self.calls.append(dict(kwargs))
        prefix = kwargs["Prefix"]
        keys = self._by_prefix.get(prefix, [])
        return {
            "Contents": [{"Key": k} for k in keys],
            "IsTruncated": False,
        }


@pytest.fixture(autouse=True)
def _reset_cache():
    asset_picker.reset_cache()
    yield
    asset_picker.reset_cache()


def _set_env(monkeypatch, *, bucket: str = "fx-bucket", prefix: str = "fx_assets"):
    monkeypatch.setenv("FX_ASSETS_S3_BUCKET", bucket)
    monkeypatch.setenv("FX_ASSETS_S3_PREFIX", prefix)


def _patch_client(monkeypatch, fake: _FakeS3Client):
    monkeypatch.setattr(asset_picker, "_make_s3_client", lambda: fake)


# ---------- tests ----------


def test_no_env_returns_empty(monkeypatch):
    monkeypatch.delenv("FX_ASSETS_S3_BUCKET", raising=False)
    monkeypatch.delenv("FX_ASSETS_S3_PREFIX", raising=False)
    out = asset_picker.resolve_assets(hook="hook_light", transition=None, extra=None, seed="abc")
    assert out == {"assets": {}, "media": []}


def test_hook_light_singleton_file(monkeypatch):
    """hook_light has sound.file (myinstants.mp3) — singleton, no pool listing."""
    _set_env(monkeypatch)
    # branding=false for hook_light => no logo expected even if file exists.
    fake = _FakeS3Client({})
    _patch_client(monkeypatch, fake)
    out = asset_picker.resolve_assets(hook="hook_light", transition=None, extra=None, seed="job-1")
    assert out["assets"] == {"hook_sound": "media/audio/myinstants.mp3"}
    assert len(out["media"]) == 1
    item = out["media"][0]
    assert item["url"] == "s3://fx-bucket/fx_assets/sounds/light_sound/myinstants.mp3"
    assert item["relpath"] == "media/audio/myinstants.mp3"
    # singleton resolution must not hit list_objects_v2
    assert fake.calls == []


def test_shutter_pool_pick_deterministic(monkeypatch):
    """shutter_effect uses camera_flash pool; pick is seed-deterministic +
    branding=='built_in' adds a logo singleton."""
    _set_env(monkeypatch)
    fake = _FakeS3Client({
        "fx_assets/sounds/camera_flash/": [
            "fx_assets/sounds/camera_flash/flash_a.wav",
            "fx_assets/sounds/camera_flash/flash_b.wav",
            "fx_assets/sounds/camera_flash/flash_c.wav",
        ],
    })
    _patch_client(monkeypatch, fake)
    out1 = asset_picker.resolve_assets(hook="shutter_effect", transition=None, extra=None, seed="job-1")
    asset_picker.reset_cache()
    out2 = asset_picker.resolve_assets(hook="shutter_effect", transition=None, extra=None, seed="job-1")
    assert out1["assets"] == out2["assets"]  # determinism
    assert out1["assets"]["hook_sound"].startswith("media/audio/flash_")
    # logo present (branding=='built_in')
    assert out1["assets"].get("logo") == "media/audio/group_1245.png" or out1["assets"].get("logo") == "media/img/group_1245.png"
    assert out1["assets"]["logo"].endswith("/group_1245.png")


def test_transition_extra_share_glitch_pool_dedup(monkeypatch):
    """Both invert_flash and warm_map point at sound.pool='glitch'. They must
    pick from the same pool and the resolved media list must dedup by relpath
    when seed paths collide (different seed suffixes => probably different
    files; but if they coincide the picker must not duplicate the entry)."""
    _set_env(monkeypatch)
    # single file in pool — guarantees both slots hit the same relpath
    fake = _FakeS3Client({
        "fx_assets/sounds/glitch/": ["fx_assets/sounds/glitch/g_01.wav"],
    })
    _patch_client(monkeypatch, fake)
    out = asset_picker.resolve_assets(
        hook=None, transition="invert_flash", extra="warm_map", seed="job-x"
    )
    assert out["assets"]["transition_sound"] == "media/audio/g_01.wav"
    assert out["assets"]["extra_sound"] == "media/audio/g_01.wav"
    assert len(out["media"]) == 1  # dedup


def test_pool_listing_cached_across_slots(monkeypatch):
    """Same pool key, two slots => exactly one list_objects_v2 call."""
    _set_env(monkeypatch)
    fake = _FakeS3Client({
        "fx_assets/sounds/glitch/": [
            "fx_assets/sounds/glitch/g_01.wav",
            "fx_assets/sounds/glitch/g_02.wav",
            "fx_assets/sounds/glitch/g_03.wav",
        ],
    })
    _patch_client(monkeypatch, fake)
    asset_picker.resolve_assets(
        hook=None, transition="invert_flash", extra="warm_map", seed="job-y"
    )
    glitch_calls = [c for c in fake.calls if c["Prefix"] == "fx_assets/sounds/glitch/"]
    assert len(glitch_calls) == 1


def test_empty_pool_skips_slot_silently(monkeypatch, caplog):
    _set_env(monkeypatch)
    fake = _FakeS3Client({"fx_assets/sounds/camera_flash/": []})
    _patch_client(monkeypatch, fake)
    out = asset_picker.resolve_assets(hook="shutter_effect", transition=None, extra=None, seed="job")
    assert "hook_sound" not in out["assets"]


def test_unknown_effect_id_skipped(monkeypatch):
    _set_env(monkeypatch)
    fake = _FakeS3Client({})
    _patch_client(monkeypatch, fake)
    out = asset_picker.resolve_assets(hook="does_not_exist", transition=None, extra=None, seed="s")
    assert out == {"assets": {}, "media": []}


def test_relpath_prefix_split_audio_vs_image(monkeypatch):
    _set_env(monkeypatch)
    fake = _FakeS3Client({})
    _patch_client(monkeypatch, fake)
    # shutter_effect singleton-logo via _pick_file
    out = asset_picker.resolve_assets(hook="shutter_effect", transition=None, extra=None, seed="s")
    # branded => logo relpath under media/img/
    assert out["assets"].get("logo") == "media/img/group_1245.png"


def test_blackwhite_glitch_clip_pool(monkeypatch):
    """blackwhite declares clips{pool:glitch_video,count:N} -> extra_clips list."""
    _set_env(monkeypatch)
    fake = _FakeS3Client({
        "fx_assets/video/glitch/": [
            "fx_assets/video/glitch/crt_glitch_3.mp4",
            "fx_assets/video/glitch/crt_glitch_4.mp4",
            "fx_assets/video/glitch/crt_glitch_5.mp4",
            "fx_assets/video/glitch/glitches.mp4",
            "fx_assets/video/glitch/tewnj_1.mp4",
        ],
    })
    _patch_client(monkeypatch, fake)
    out = asset_picker.resolve_assets(hook=None, transition=None, extra="blackwhite", seed="job-bw")

    clips = out["assets"]["extra_clips"]
    # ровно столько, сколько просит манифест, и все разные
    import json as _json
    from pathlib import Path as _Path
    _manifest = _json.loads(
        (_Path(__file__).resolve().parents[1]
         / "mlcore" / "hooks" / "f3_effect" / "manifest.json").read_text(encoding="utf-8")
    )
    want = next(e for e in _manifest["effects"] if e["id"] == "blackwhite")["clips"]["count"]
    assert isinstance(clips, list) and len(clips) == want
    assert len(set(clips)) == want, "clips must be distinct"
    for rel in clips:
        assert rel.startswith("media/video/") and rel.endswith(".mp4")
    # every clip is in the node download list
    assert {m["relpath"] for m in out["media"]} == set(clips)
    for m in out["media"]:
        assert m["url"].startswith("s3://fx-bucket/fx_assets/video/glitch/")


def test_blackwhite_clip_pick_is_deterministic(monkeypatch):
    _set_env(monkeypatch)
    keys = [f"fx_assets/video/glitch/g_{i:02d}.mp4" for i in range(8)]
    fake = _FakeS3Client({"fx_assets/video/glitch/": keys})
    _patch_client(monkeypatch, fake)

    first = asset_picker.resolve_assets(hook=None, transition=None, extra="blackwhite", seed="same")
    asset_picker.reset_cache()
    second = asset_picker.resolve_assets(hook=None, transition=None, extra="blackwhite", seed="same")
    asset_picker.reset_cache()
    other = asset_picker.resolve_assets(hook=None, transition=None, extra="blackwhite", seed="other")

    assert first["assets"]["extra_clips"] == second["assets"]["extra_clips"]
    assert first["assets"]["extra_clips"] != other["assets"]["extra_clips"]


def test_clip_pool_smaller_than_count(monkeypatch):
    """Pool with 2 files and count=4 -> both, no crash (AE side cycles them)."""
    _set_env(monkeypatch)
    fake = _FakeS3Client({
        "fx_assets/video/glitch/": [
            "fx_assets/video/glitch/a.mp4",
            "fx_assets/video/glitch/b.mp4",
        ],
    })
    _patch_client(monkeypatch, fake)
    out = asset_picker.resolve_assets(hook=None, transition=None, extra="blackwhite", seed="s")
    assert len(out["assets"]["extra_clips"]) == 2


def test_empty_clip_pool_leaves_grade_only(monkeypatch):
    _set_env(monkeypatch)
    fake = _FakeS3Client({"fx_assets/video/glitch/": []})
    _patch_client(monkeypatch, fake)
    out = asset_picker.resolve_assets(hook=None, transition=None, extra="blackwhite", seed="s")
    assert "extra_clips" not in out["assets"]
    assert out["media"] == []


def test_extra_without_clip_spec_has_no_clips(monkeypatch):
    """wave declares no clips{} -> slot absent even with the pool populated."""
    _set_env(monkeypatch)
    fake = _FakeS3Client({
        "fx_assets/video/glitch/": ["fx_assets/video/glitch/a.mp4"],
    })
    _patch_client(monkeypatch, fake)
    out = asset_picker.resolve_assets(hook=None, transition=None, extra="wave", seed="s")
    assert "extra_clips" not in out["assets"]
