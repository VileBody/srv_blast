from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_BACKEND = REPO_ROOT / "web_app" / "backend"


def _module(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(WEB_BACKEND))
    sys.modules.pop("app.tiktok_api", None)
    return importlib.import_module("app.tiktok_api")


def test_direct_post_info_contains_review_fields_and_omits_aigc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module(monkeypatch)

    post_info = module.build_video_post_info(
        title="new track",
        privacy_level="PUBLIC_TO_EVERYONE",
        comments=True,
        duet=False,
        stitch=True,
        cover_timestamp_ms=1250,
        brand_content=True,
        brand_organic=False,
    )

    assert post_info == {
        "title": "new track",
        "privacy_level": "PUBLIC_TO_EVERYONE",
        "disable_duet": True,
        "disable_stitch": False,
        "disable_comment": False,
        "video_cover_timestamp_ms": 1250,
        "brand_content_toggle": True,
        "brand_organic_toggle": False,
    }
    assert "is_aigc" not in post_info


@pytest.mark.parametrize(
    ("setting", "creator_flag", "error_code"),
    (
        ("comments", "comment_disabled", "comment_unavailable"),
        ("duet", "duet_disabled", "duet_unavailable"),
        ("stitch", "stitch_disabled", "stitch_unavailable"),
    ),
)
def test_creator_disabled_interaction_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    setting: str,
    creator_flag: str,
    error_code: str,
) -> None:
    module = _module(monkeypatch)
    requested = {"comments": False, "duet": False, "stitch": False}
    requested[setting] = True

    with pytest.raises(module.TikTokPostValidationError) as caught:
        module.validate_video_post_settings(
            {
                "privacy_level_options": ["PUBLIC_TO_EVERYONE"],
                creator_flag: True,
            },
            privacy_level="PUBLIC_TO_EVERYONE",
            brand_content=False,
            **requested,
        )

    assert caught.value.code == error_code


def test_branded_content_cannot_be_private(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module(monkeypatch)

    with pytest.raises(module.TikTokPostValidationError) as caught:
        module.validate_video_post_settings(
            {"privacy_level_options": ["SELF_ONLY"]},
            privacy_level="SELF_ONLY",
            comments=False,
            duet=False,
            stitch=False,
            brand_content=True,
        )

    assert caught.value.code == "branded_content_private"


def test_unavailable_creator_privacy_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module(monkeypatch)

    with pytest.raises(module.TikTokPostValidationError) as caught:
        module.validate_video_post_settings(
            {"privacy_level_options": ["SELF_ONLY"]},
            privacy_level="PUBLIC_TO_EVERYONE",
            comments=False,
            duet=False,
            stitch=False,
            brand_content=False,
        )

    assert caught.value.code == "privacy_unavailable"


def _config_module(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(WEB_BACKEND))
    sys.modules.pop("app.tiktok_config", None)
    return importlib.import_module("app.tiktok_config")


def _load_config(monkeypatch: pytest.MonkeyPatch, allowed: str) -> Any:
    module = _config_module(monkeypatch)
    monkeypatch.setattr(module, "_env_loaded", True, raising=False)
    monkeypatch.setenv("TIKTOK_CLIENT_KEY", "sandbox-key")
    monkeypatch.setenv("TIKTOK_CLIENT_SECRET", "sandbox-secret")
    monkeypatch.setenv("TIKTOK_UPLOAD_SOURCE", "FILE_UPLOAD")
    monkeypatch.setenv("TIKTOK_ALLOWED_USER_IDS", allowed)
    return module.load()


def test_sandbox_access_is_limited_to_listed_people(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _load_config(monkeypatch, "user_1, Owner@Blast808.com ,7654321")

    assert cfg.configured
    # список принимает id, почту и telegram chat id, регистр не важен
    assert cfg.allows("user_1", None, None)
    assert cfg.allows("user_9", "owner@blast808.com", None)
    assert cfg.allows("user_9", None, 7654321)
    # чужой человек в песочницу не попадает: для него интеграция «не настроена»
    assert not cfg.allows("user_9", "someone@example.com", 111)
    assert not cfg.allows(None, None, None)


def test_empty_allowlist_closes_the_integration_and_star_opens_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # пусто = никому: «случайно всем» в песочнице хуже, чем «никому»
    assert not _load_config(monkeypatch, "").allows("user_1", "owner@blast808.com", 1)
    # состояние после одобрения заявки — открыть всем можно только явной звёздочкой
    assert _load_config(monkeypatch, "*").allows("whoever", None, None)
