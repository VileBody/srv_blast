"""Клиент TikTok: Login Kit (OAuth v2) + профиль аккаунта.

Дока: developers.tiktok.com → Login Kit for Web / Manage User Access Tokens.
Ключи берутся ТОЛЬКО из окружения (tiktok_config), секрет в коде не живёт.

Флоу:
  1. build_auth_url() → редиректим юзера на TikTok (state + PKCE лежат в серверной сессии);
  2. TikTok возвращает ?code&state на redirect_uri;
  3. exchange_code() меняет code на access/refresh токен;
  4. fetch_user_info() отдаёт open_id/ник/аватар — их и показываем в ЛК и в модалке постинга.

Если ключей нет (configured == False), вызовы сюда не идут: main.py включает мок-подключение,
чтобы флоу можно было гонять локально без боевого приложения.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import secrets
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .tiktok_config import AUTH_URL, TOKEN_URL, TiktokConfig

USER_INFO_URL = "https://open.tiktokapis.com/v2/user/info/"
USER_FIELDS = "open_id,union_id,avatar_url,display_name"
CREATOR_INFO_URL = "https://open.tiktokapis.com/v2/post/publish/creator_info/query/"
DIRECT_POST_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
PUBLISH_STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
VIDEO_LIST_URL = "https://open.tiktokapis.com/v2/video/list/"
VIDEO_FIELDS = "id,create_time,cover_image_url,share_url,title,video_description,duration,height,width,like_count,comment_count,share_count,view_count"
_TIMEOUT = 15


class TikTokApiError(RuntimeError):
    def __init__(self, code: str, message: str, status: int | None = None):
        super().__init__(message or code)
        self.code = code
        self.status = status


class TikTokPostValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def validate_video_post_settings(
    creator: dict[str, Any],
    *,
    privacy_level: str,
    comments: bool,
    duet: bool,
    stitch: bool,
    brand_content: bool,
) -> None:
    """Validate settings against the latest creator_info response.

    The frontend mirrors these restrictions, but Direct Post must reject a
    hand-crafted request instead of silently changing the creator's choices.
    """
    allowed = creator.get("privacy_level_options") or []
    if allowed and privacy_level not in allowed:
        raise TikTokPostValidationError(
            "privacy_unavailable",
            "The selected privacy level is unavailable for this TikTok account",
        )
    requested_interactions = {
        "comment": (comments, "comment_disabled"),
        "duet": (duet, "duet_disabled"),
        "stitch": (stitch, "stitch_disabled"),
    }
    for interaction, (requested, creator_flag) in requested_interactions.items():
        if requested and bool(creator.get(creator_flag)):
            raise TikTokPostValidationError(
                f"{interaction}_unavailable",
                f"{interaction.title()} is disabled for this TikTok account",
            )
    if brand_content and privacy_level == "SELF_ONLY":
        raise TikTokPostValidationError(
            "branded_content_private",
            "Branded content visibility cannot be set to private",
        )


def build_video_post_info(
    *,
    title: str,
    privacy_level: str,
    comments: bool,
    duet: bool,
    stitch: bool,
    cover_timestamp_ms: int,
    brand_content: bool,
    brand_organic: bool,
) -> dict[str, Any]:
    """Build the exact Direct Post payload audited by TikTok.

    `is_aigc` is deliberately absent. Blast assembles artist-provided and
    licensed media; if generative media is added later, the caller must add an
    explicit per-video AIGC decision instead of changing this default.
    """
    return {
        "title": title,
        "privacy_level": privacy_level,
        "disable_duet": not duet,
        "disable_stitch": not stitch,
        "disable_comment": not comments,
        "video_cover_timestamp_ms": cover_timestamp_ms,
        "brand_content_toggle": bool(brand_content),
        "brand_organic_toggle": bool(brand_organic),
    }


def new_pkce() -> tuple[str, str]:
    """(code_verifier, code_challenge) — PKCE S256, обязателен для web-флоу TikTok."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def build_auth_url(cfg: TiktokConfig, state: str, code_challenge: str) -> str:
    query = urllib.parse.urlencode({
        "client_key": cfg.client_key,
        "scope": cfg.scopes,
        "response_type": "code",
        "redirect_uri": cfg.redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    })
    return f"{AUTH_URL}?{query}"


def _post_form(url: str, data: dict[str, str]) -> dict[str, Any]:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Cache-Control": "no-cache"},
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.load(resp)


def exchange_code(cfg: TiktokConfig, code: str, code_verifier: str) -> dict[str, Any]:
    """code → {access_token, refresh_token, open_id, expires_in, scope}."""
    return _post_form(TOKEN_URL, {
        "client_key": cfg.client_key,
        "client_secret": cfg.client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": cfg.redirect_uri,
        "code_verifier": code_verifier,
    })


def refresh_token(cfg: TiktokConfig, refresh: str) -> dict[str, Any]:
    return _post_form(TOKEN_URL, {
        "client_key": cfg.client_key,
        "client_secret": cfg.client_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh,
    })


def fetch_user_info(access_token: str) -> dict[str, Any]:
    """Профиль подключённого аккаунта (нужен scope user.info.basic)."""
    req = urllib.request.Request(
        f"{USER_INFO_URL}?fields={USER_FIELDS}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        payload = json.load(resp)
    return (payload.get("data") or {}).get("user") or {}


def _json_request(url: str, access_token: str, payload: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            result = json.load(resp)
    except urllib.error.HTTPError as exc:
        try:
            result = json.load(exc)
            error = result.get("error") or {}
            raise TikTokApiError(str(error.get("code") or exc.code), str(error.get("message") or exc.reason), exc.code) from exc
        except (json.JSONDecodeError, AttributeError):
            raise TikTokApiError(str(exc.code), str(exc.reason), exc.code) from exc
    error = result.get("error") or {}
    if error.get("code") not in (None, "ok"):
        raise TikTokApiError(str(error.get("code")), str(error.get("message") or "TikTok API error"))
    return result.get("data") or {}


def query_creator_info(access_token: str) -> dict[str, Any]:
    """Privacy and interaction capabilities required by Direct Post UX."""
    return _json_request(CREATOR_INFO_URL, access_token, {})


def init_direct_post_pull(access_token: str, post_info: dict[str, Any], video_url: str) -> dict[str, Any]:
    if not video_url.startswith("https://"):
        raise ValueError("PULL_FROM_URL requires a public HTTPS video URL")
    return _json_request(DIRECT_POST_INIT_URL, access_token, {
        "post_info": post_info,
        "source_info": {"source": "PULL_FROM_URL", "video_url": video_url},
    })


def init_direct_post_file(access_token: str, post_info: dict[str, Any], video_path: str | Path) -> dict[str, Any]:
    """Initialize FILE_UPLOAD and stream the already-rendered MP4 unchanged."""
    path = Path(video_path)
    size = path.stat().st_size
    if size <= 0:
        raise ValueError("Video file is empty")
    chunk_size = size if size < 5_000_000 else min(10_000_000, size)
    total_chunks = max(1, math.ceil(size / chunk_size))
    data = _json_request(DIRECT_POST_INIT_URL, access_token, {
        "post_info": post_info,
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": size,
            "chunk_size": chunk_size,
            "total_chunk_count": total_chunks,
        },
    })
    upload_url = data.get("upload_url")
    if not upload_url:
        raise TikTokApiError("missing_upload_url", "TikTok did not return an upload URL")
    with path.open("rb") as source:
        offset = 0
        for index in range(total_chunks):
            # Per TikTok's transfer contract, the final chunk may contain the remainder.
            length = chunk_size if index < total_chunks - 1 else size - offset
            body = source.read(length)
            end = offset + len(body) - 1
            req = urllib.request.Request(upload_url, data=body, method="PUT", headers={
                "Content-Type": "video/mp4",
                "Content-Length": str(len(body)),
                "Content-Range": f"bytes {offset}-{end}/{size}",
            })
            with urllib.request.urlopen(req, timeout=90):
                pass
            offset = end + 1
    return data


def fetch_publish_status(access_token: str, publish_id: str) -> dict[str, Any]:
    return _json_request(PUBLISH_STATUS_URL, access_token, {"publish_id": publish_id})


def list_videos(access_token: str, cursor: int | None = None, max_count: int = 20) -> dict[str, Any]:
    payload: dict[str, Any] = {"max_count": max(1, min(20, max_count))}
    if cursor is not None:
        payload["cursor"] = cursor
    query = urllib.parse.urlencode({"fields": VIDEO_FIELDS})
    return _json_request(f"{VIDEO_LIST_URL}?{query}", access_token, payload)
