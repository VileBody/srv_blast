from __future__ import annotations

import dataclasses
import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_BACKEND = REPO_ROOT / "web_app" / "backend"


def _module(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.syspath_prepend(str(WEB_BACKEND))
    monkeypatch.setenv("MODE", "dev")
    monkeypatch.setenv("BLAST_BACKEND_MODE", "mock")
    monkeypatch.setenv("APP_URL", "http://localhost:5173")
    monkeypatch.setenv("BLAST_SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("BLAST_CORS_ORIGINS", "http://localhost:5173")
    sys.modules.pop("app.production_backend", None)
    sys.modules.pop("app.runtime", None)
    return importlib.import_module("app.production_backend")


def _config(module: Any, *, stage1_backend: str = "gemini"):
    return module.ProductionConfig(
        orchestrator_url="http://orchestrator:8000",
        s3_endpoint_url="https://s3.twcstorage.ru",
        s3_access_key_id="access",
        s3_secret_access_key="secret",
        s3_region="ru-1",
        raw_audio_bucket="raw-audio",
        raw_audio_prefix="raw",
        asset_bucket="assets",
        asset_prefix="app/blast808",
        stage1_backend=stage1_backend,
        subtitle_modes={"Impulse": "impulse_2nd"},
        selector_by_mode={"footage": {"Неон": {"rotationTheme": "visual", "rotationTagsGroup": "neon", "renderPreset": "vertical"}}},
        footage_catalog=(
            {
                "id": "visual:neon",
                "plane": "vibes",
                "name": "Неон",
                "previewUrl": "s3://assets/previews/neon.mp4",
                "score": 1.0,
            },
        ),
        photo_catalog=(
            {
                "id": "photo:neon",
                "name": "Неон",
                "previewUrl": "https://cdn.example/neon.jpg",
                "score": 1.0,
            },
        ),
        subtitle_catalog=(
            {
                "id": "impulse",
                "name": "Impulse",
                "previewUrl": "s3://assets/previews/impulse.mp4",
                "score": 1.0,
            },
        ),
    )


class _FakeS3:
    def __init__(self) -> None:
        self.presigns: list[dict[str, Any]] = []
        self.downloads: list[dict[str, str]] = []

    def generate_presigned_url(self, operation: str, *, Params: dict[str, Any], ExpiresIn: int):
        self.presigns.append({"operation": operation, "params": Params, "expires": ExpiresIn})
        return "https://signed.example/download"

    def download_file(self, bucket: str, key: str, filename: str) -> None:
        self.downloads.append({"bucket": bucket, "key": key, "filename": filename})
        Path(filename).write_bytes(b"video")


class _Response:
    def __init__(self, body: dict[str, Any], status_code: int = 200) -> None:
        self._body = body
        self.status_code = status_code
        self.text = str(body)

    def json(self) -> dict[str, Any]:
        return self._body


class _FakeHttp:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []
        self.states: dict[str, dict[str, Any]] = {}
        self.supports_custom_sources = True

    def post(self, _url: str, *, json: dict[str, Any]) -> _Response:
        self.posts.append(json)
        return _Response({"job_id": f"orch-{len(self.posts)}"})

    def get(self, url: str) -> _Response:
        if url.endswith("/openapi.json"):
            properties = {"custom_footage_sources": {}} if self.supports_custom_sources else {}
            return _Response({"components": {"schemas": {"SendAudioS3Request": {"properties": properties}}}})
        return _Response(self.states[url.rsplit("/", 1)[-1]])


def _job() -> dict[str, Any]:
    variations = [
        {
            "subtitle": {"style": "Impulse", "color": "#ffffff"},
            "background": {"mode": "footage", "groups": ["Неон"]},
            "hook": {"family": None, "dropTime": None, "resolved": {}, "config": {}},
        }
        for _ in range(2)
    ]
    return {
        "id": "web-job",
        "projectId": "project-1",
        "stageData": {"final": {"accentColor": "#8b6fe6"}},
        "renderJob": {
            "track": {"s3Key": "s3://raw-audio/raw/track.mp3", "segment": None},
            "lyrics": {"full": "полный текст", "fragment": ""},
            "variations": variations,
        },
        "videos": [
            {"id": "video-1", "status": "PENDING", "progress": 0},
            {"id": "video-2", "status": "PENDING", "progress": 0},
        ],
    }


def _backend(module: Any, config: Any):
    backend = object.__new__(module.ProductionBackend)
    backend.config = config
    backend._s3 = _FakeS3()
    backend._http = _FakeHttp()
    backend._custom_sources_contract_verified = False
    return backend


def test_personal_sources_refuse_an_outdated_orchestrator(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module(monkeypatch)
    backend = _backend(module, _config(module))
    backend._http.supports_custom_sources = False
    job = _job()
    job["renderJob"]["track"]["segment"] = {"from": 0.0, "to": 10.0}
    job["renderJob"]["variations"][0]["background"] = {
        "mode": "upload", "groups": [], "sourceFormat": "9:16",
        "sourceAssets": [{
            "s3Key": "s3://assets/users/mine.mp4", "width": 1080,
            "height": 1920, "duration": 15.0,
        }],
    }

    with pytest.raises(module.ProductionBackendError, match="does not support personal footage"):
        backend.enqueue_job(job)

    assert backend._http.posts == []


def test_variations_are_enqueued_sequentially(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module(monkeypatch)
    backend = _backend(module, _config(module))
    job = _job()

    backend.enqueue_job(job)
    assert len(backend._http.posts) == 1
    assert job["videos"][0]["orchestratorJobId"] == "orch-1"
    assert "orchestratorJobId" not in job["videos"][1]

    backend._http.states["orch-1"] = {
        "status": "SUCCEEDED",
        "stage": "poll",
        "result": {"output_url": "s3://outputs/jobs/one.mp4"},
    }
    backend.sync_job(job)

    assert len(backend._http.posts) == 2
    assert backend._http.posts[1]["reuse_text_job_id"] == "orch-1"
    assert job["videos"][1]["orchestratorJobId"] == "orch-2"
    assert job["videos"][0]["outputLocator"] == "s3://outputs/jobs/one.mp4"
    assert job["videos"][0]["playbackUrl"] == "https://signed.example/download"
    assert backend._s3.presigns[-2]["params"]["ResponseContentType"] == "video/mp4"
    assert "ResponseContentDisposition" not in backend._s3.presigns[-2]["params"]


def test_local_ctc_requires_exact_fragment_and_window(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module(monkeypatch)
    backend = _backend(module, _config(module, stage1_backend="local_ctc"))
    job = _job()

    with pytest.raises(module.ProductionBackendError, match="explicit clip window"):
        backend.enqueue_job(job)

    job["renderJob"]["track"]["segment"] = {"from": 10.0, "to": 25.0}
    with pytest.raises(module.ProductionBackendError, match="exact target fragment"):
        backend.enqueue_job(job)


def test_timeweb_https_output_is_resigned_as_attachment(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module(monkeypatch)
    backend = _backend(module, _config(module))

    url = backend.download_url(
        "https://s3.twcstorage.ru/output-bucket/jobs/result.mp4?old=signature",
        "video-1",
    )

    assert url == "https://signed.example/download"
    params = backend._s3.presigns[-1]["params"]
    assert params["Bucket"] == "output-bucket"
    assert params["Key"] == "jobs/result.mp4"
    assert params["ResponseContentDisposition"] == 'attachment; filename="video-1.mp4"'


def test_timeweb_output_has_separate_inline_playback_url(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module(monkeypatch)
    backend = _backend(module, _config(module))

    url = backend.playback_url(
        "https://s3.twcstorage.ru/output-bucket/jobs/result.mp4?old=signature",
        "video-1",
    )

    assert url == "https://signed.example/download"
    params = backend._s3.presigns[-1]["params"]
    assert params == {
        "Bucket": "output-bucket",
        "Key": "jobs/result.mp4",
        "ResponseContentType": "video/mp4",
    }


def test_unknown_https_output_is_not_a_download_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module(monkeypatch)
    backend = _backend(module, _config(module))

    with pytest.raises(module.ProductionBackendError, match="configured S3 endpoint"):
        backend.download_url("https://cdn.example/result.mp4", "video-1")


def test_effect_scope_and_slow_shutter_extension_reach_orchestrator(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module(monkeypatch)
    backend = _backend(module, _config(module))
    job = _job()
    job["renderJob"]["track"]["segment"] = {"from": 0.0, "to": 10.0}
    variation = job["renderJob"]["variations"][0]
    variation["hook"] = {
        "family": "effects",
        "dropTime": 5.0,
        "resolved": {
            "hook": "flash_slow_shutter",
            "extra": "analog_glitch",
            "extraFull": True,
            "hookExtend": "to_end",
        },
        "config": {},
    }

    payload = backend._request_payload(
        job=job,
        variation=variation,
        index=0,
        total=2,
        master_id=None,
    )

    assert payload["effect_extra_full"] is True
    assert payload["effect_hook_extend"] == "to_end"


def test_tiktok_file_upload_downloads_only_from_configured_s3(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _module(monkeypatch)
    backend = _backend(module, _config(module))
    destination = tmp_path / "post.mp4"

    result = backend.download_video(
        "https://s3.twcstorage.ru/output-bucket/jobs/result.mp4?signature=secret",
        destination,
    )

    assert result == destination
    assert destination.read_bytes() == b"video"
    assert backend._s3.downloads == [
        {
            "bucket": "output-bucket",
            "key": "jobs/result.mp4",
            "filename": str(destination),
        }
    ]

    with pytest.raises(module.ProductionBackendError, match="configured S3 endpoint"):
        backend.download_video("https://cdn.example/result.mp4", destination)


def test_preview_catalog_presigns_s3_and_keeps_explicit_https(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module(monkeypatch)
    backend = _backend(module, _config(module))

    footage = backend.preview_catalog("footage")
    photos = backend.preview_catalog("photo")

    assert footage[0]["previewUrl"] == "https://signed.example/download"
    assert backend._s3.presigns[-1]["params"] == {
        "Bucket": "assets",
        "Key": "previews/neon.mp4",
    }
    assert photos[0]["previewUrl"] == "https://cdn.example/neon.jpg"


def test_f1_and_f5_hooks_use_orchestrator_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module(monkeypatch)
    backend = _backend(module, _config(module))
    job = _job()
    job["renderJob"]["track"]["segment"] = {"from": 10.0, "to": 20.0}
    thought = job["renderJob"]["variations"][0]
    thought["hook"] = {
        "family": "thought",
        "dropTime": 12.5,
        "resolved": {},
        "config": {"thought": "Эхо"},
    }

    payload = backend._request_payload(
        job=job,
        variation=thought,
        index=1,
        total=2,
        master_id=None,
    )
    assert payload["hook_device"] == "lyric_echo"

    sound = job["renderJob"]["variations"][1]
    sound["hook"] = {
        "family": "sound",
        "dropTime": 12.5,
        "resolved": {},
        "config": {"soundUrl": "s3://assets/app/blast808/sound.wav"},
    }
    payload = backend._request_payload(
        job=job,
        variation=sound,
        index=2,
        total=2,
        master_id="orch-1",
    )
    assert payload["f1_sound_url"] == "s3://assets/app/blast808/sound.wav"
    assert payload["reuse_text_job_id"] == "orch-1"


def test_no_hook_does_not_require_drop_timing(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module(monkeypatch)
    backend = _backend(module, _config(module))
    job = _job()
    variation = job["renderJob"]["variations"][0]
    variation["hook"] = {
        "family": "none",
        "dropTime": None,
        "resolved": {"transition": "snap_wipe", "extra": "analog_glitch", "extraFull": True},
        "config": {"effectGlue": "Щелчок", "effectStyle": "Глитч"},
    }

    payload = backend._request_payload(job=job, variation=variation, index=1, total=1, master_id=None)

    assert payload["hook_enabled"] is False
    assert "user_drop_t" not in payload
    assert payload["effect_transition"] == "snap_wipe"
    assert payload["effect_extra"] == "analog_glitch"


@pytest.mark.parametrize("media_type", ["video", "photo"])
def test_semantic_ranking_preserves_preview_order_and_rejects_catalog_drift(monkeypatch, media_type):
    module = _module(monkeypatch)
    config = _config(module)
    original = dict((config.photo_catalog if media_type == "photo" else config.footage_catalog)[0])
    second = {**original, "id": "second", "name": "Second"}
    catalog = (original, second)
    config = dataclasses.replace(config, **{
        "photo_catalog" if media_type == "photo" else "footage_catalog": catalog
    })
    backend = _backend(module, config)
    response = {"buckets": [{"bucket_id": "second"}, {"bucket_id": original["id"]}]}

    def rank(url, *, json):
        assert url.endswith("/footage/rank-buckets")
        assert json == {"lyrics": "ночной город", "mood": "", "top": 0, "media_type": media_type, "pool": "vibes"}
        return _Response(response)

    monkeypatch.setattr(backend._http, "post", rank)
    result = backend.ranked_backgrounds(lyrics="ночной город", media_type=media_type)
    assert [item["id"] for item in result] == ["second", original["id"]]
    assert all(item["previewUrl"].startswith("https://") for item in result)
    response["buckets"] = [{"bucket_id": "unknown"}]
    with pytest.raises(module.ProductionBackendError, match="does not match"):
        backend.ranked_backgrounds(lyrics="ночной город", media_type=media_type)


@pytest.mark.parametrize(
    ("web_color", "renderer_color"),
    [("#f6f5fd", "white"), ("#05010f", "black"), ("#00ff00", "green")],
)
def test_web_solid_palette_maps_to_renderer_planes(
    monkeypatch: pytest.MonkeyPatch,
    web_color: str,
    renderer_color: str,
) -> None:
    module = _module(monkeypatch)
    backend = _backend(module, _config(module))
    job = _job()
    variation = job["renderJob"]["variations"][0]
    variation["background"] = {"mode": "color", "groups": [], "color": web_color}

    payload = backend._request_payload(
        job=job,
        variation=variation,
        index=1,
        total=2,
        master_id=None,
    )

    assert "footage_artist_id" not in payload
    assert payload["bg_mode"] == "solid"
    assert payload["bg_solid_color"] == renderer_color


def test_catalog_parsing_keeps_and_validates_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    """`selector` обязан пережить разбор каталога.

    Разбор пересобирает запись из белого списка полей, и selector в нём сначала
    отсутствовал — карта selector_by_mode получалась пустой, ProductionConfig.load
    падал на «ни selector, ни маппинга артиста», а если бы не падал, выбор бакета
    снова ни на что не влиял бы. Ловим это на уровне парсера, а не на проде.
    """
    module = _module(monkeypatch)
    good = json.dumps([
        {
            "id": "collection:cine16x9__New_York",
            "name": "Нью-Йорк",
            "previewUrl": "s3://assets/previews/ny.mp4",
            "selector": {
                "rotationTheme": "collection",
                "rotationTagsGroup": "New_York",
                "renderPreset": "wide",
                "bgMode": "footage",
            },
        }
    ], ensure_ascii=False)
    monkeypatch.setenv("WEB_FOOTAGE_CATALOG_JSON", good)
    parsed = module._json_catalog("WEB_FOOTAGE_CATALOG_JSON")
    assert parsed[0]["selector"]["rotationTagsGroup"] == "New_York"
    assert parsed[0]["selector"]["renderPreset"] == "wide"

    # Половина пары rotation оркестратору ничего не скажет.
    half = json.dumps([
        {
            "id": "x", "name": "X", "previewUrl": "s3://assets/x.mp4",
            "selector": {"rotationTheme": "collection"},
        }
    ], ensure_ascii=False)
    monkeypatch.setenv("WEB_FOOTAGE_CATALOG_JSON", half)
    with pytest.raises(module.ProductionBackendError, match="rotationTheme and rotationTagsGroup"):
        module._json_catalog("WEB_FOOTAGE_CATALOG_JSON")

    # Неизвестная геометрия хуже исторической: 16:9 в вертикали режется в треть.
    bad_preset = json.dumps([
        {
            "id": "x", "name": "X", "previewUrl": "s3://assets/x.mp4",
            "selector": {"renderPreset": "portrait"},
        }
    ], ensure_ascii=False)
    monkeypatch.setenv("WEB_FOOTAGE_CATALOG_JSON", bad_preset)
    with pytest.raises(module.ProductionBackendError, match="renderPreset"):
        module._json_catalog("WEB_FOOTAGE_CATALOG_JSON")


def test_legacy_footage_catalog_ids_restore_their_planes(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module(monkeypatch)
    monkeypatch.setenv("WEB_FOOTAGE_CATALOG_JSON", json.dumps([
        {"id": "visual:forest", "name": "Forest", "previewUrl": "s3://assets/v.mp4"},
        {"id": "collection:cine16x9__NY", "name": "NY", "previewUrl": "s3://assets/w.mp4"},
        {"id": "collection:films__drive", "name": "Drive", "previewUrl": "s3://assets/f.mp4"},
    ]))

    parsed = module._json_catalog("WEB_FOOTAGE_CATALOG_JSON")

    assert [item["plane"] for item in parsed] == ["vibes", "cine16x9", "films"]


def test_fx_catalog_requires_one_supported_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module(monkeypatch)
    monkeypatch.setenv("WEB_FX_CATALOG_JSON", json.dumps([{
        "id": "effect_hook__hook_light",
        "name": "Молния",
        "plane": "fx",
        "previewUrl": "s3://assets/previews/fx/light.mp4",
        "selector": {"effectHook": "hook_light"},
    }], ensure_ascii=False))
    item = module._json_catalog("WEB_FX_CATALOG_JSON")[0]
    assert item["plane"] == "fx"
    assert item["selector"] == {"effectHook": "hook_light"}

    monkeypatch.setenv("WEB_FX_CATALOG_JSON", json.dumps([{
        "id": "bad", "name": "Bad", "previewUrl": "s3://assets/bad.mp4",
        "selector": {"effectHook": "hook_light", "unknown": "x"},
    }]))
    with pytest.raises(module.ProductionBackendError, match="exactly one supported FX field"):
        module._json_catalog("WEB_FX_CATALOG_JSON")


def test_warmup_video_and_custom_sources_reach_orchestrator(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module(monkeypatch)
    config = _config(module)
    backend = _backend(module, config)
    job = _job()
    job["renderJob"]["track"]["segment"] = {"from": 10.0, "to": 20.0}
    variation = job["renderJob"]["variations"][0]
    variation["background"] = {
        "mode": "footage",
        "groups": [],
        "sourceFormat": "9:16",
        "sourceAssets": [
            {"s3Key": "s3://assets/users/a.mp4", "width": 1080, "height": 1920, "duration": 15.0},
        ],
    }
    variation["hook"] = {
        "family": "warmup",
        "dropTime": 15.0,
        "resolved": {},
        "config": {
            "warmupKind": "video", "videoUrl": "s3://assets/users/intro.mp4",
            "videoWidth": 1080, "videoHeight": 1920, "videoDuration": 3.0,
            "videoHasAudio": True,
        },
    }

    payload = backend._request_payload(job=job, variation=variation, index=1, total=1, master_id=None)
    assert "footage_artist_id" not in payload
    assert payload["f6_video_url"] == "s3://assets/users/intro.mp4"
    assert payload["custom_footage_sources"] == [{
        "url": "s3://assets/users/a.mp4", "width": 1080, "height": 1920, "duration": 15.0,
    }]
    assert payload["render_preset"] == "vertical"


def test_bucket_selector_pins_rotation_and_geometry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Выбор бакета на сайте должен закреплять группу и формат, а не только артиста.

    Пара rotation_theme/rotation_tags_group — единственный способ сказать
    оркестратору «бери клипы РОВНО из этой группы»; без неё Stage 2 выбирает по
    профилю артиста, и выбор в визарде ни на что не влияет. render_preset обязан
    приезжать из записи каталога: у коллекций 16:9 он wide, и вертикальный кадр
    обрезал бы их в треть ширины.
    """
    module = _module(monkeypatch)
    config = _config(module)
    config = dataclasses.replace(
        config,
        footage_catalog=(
            {
                "id": "collection:cine16x9__New_York",
                "name": "Нью-Йорк",
                "previewUrl": "s3://assets/previews/ny.mp4",
                "score": 1.0,
                "selector": {
                    "rotationTheme": "collection",
                    "rotationTagsGroup": "cine16x9__New_York",
                    "renderPreset": "wide",
                    "bgMode": "footage",
                },
            },
        ),
        selector_by_mode={
            "footage": {
                "Нью-Йорк": {
                    "rotationTheme": "collection",
                    "rotationTagsGroup": "cine16x9__New_York",
                    "renderPreset": "wide",
                    "bgMode": "footage",
                }
            },
            "photo": {},
        },
    )
    backend = _backend(module, config)
    job = _job()
    variation = job["renderJob"]["variations"][0]
    variation["background"] = {"mode": "footage", "groups": ["Нью-Йорк"]}

    payload = backend._request_payload(job=job, variation=variation, index=1, total=1, master_id=None)

    assert payload["rotation_theme"] == "collection"
    assert payload["rotation_tags_group"] == "cine16x9__New_York"
    assert payload["render_preset"] == "wide"
    assert payload["bg_mode"] == "footage"
    # Коллекция сама задаёт точный пул. Артист из тегового флоу здесь заставил
    # бы Stage 2 искать отсутствующий artist_id у коллекционных клипов.
    assert "footage_artist_id" not in payload


def test_missing_selector_is_rejected_instead_of_selecting_by_artist(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module(monkeypatch)
    backend = _backend(module, dataclasses.replace(_config(module), selector_by_mode={}))
    job = _job()
    with pytest.raises(module.ProductionBackendError, match="exact rotation selector required"):
        backend.validate_job(job)


def test_submit_payload_requires_server_audio_locator(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module(monkeypatch)
    backend = _backend(module, _config(module))
    job = _job()
    job["renderJob"]["track"]["s3Key"] = ""

    with pytest.raises(module.ProductionBackendError, match="valid S3 audio locator"):
        backend.validate_job(job)


def test_same_label_in_footage_and_photo_does_not_cross_wire(monkeypatch: pytest.MonkeyPatch) -> None:
    """Одинаковая подпись в двух каталогах не должна уводить выбор в чужой рендер.

    В боевом каталоге такие есть: «Тёмный лес / туман» и «Портрет девушки /
    светлый» встречаются и в футаже, и в фото. В общем словаре по имени фото
    затирало футаж, и выбор футажа уезжал бы в фото-флоу (bg_mode=photo,
    геометрия 4:3) — молча, без единой ошибки.
    """
    module = _module(monkeypatch)
    shared = "Тёмный лес / туман"
    config = dataclasses.replace(
        _config(module),
        selector_by_mode={
            "footage": {shared: {
                "rotationTheme": "visual", "rotationTagsGroup": "forest_fog_dark",
                "renderPreset": "vertical", "bgMode": "footage",
            }},
            "photo": {shared: {
                "rotationTheme": "photo", "rotationTagsGroup": "forest_fog_dark",
                "renderPreset": "vertical", "bgMode": "photo",
            }},
        },
    )
    backend = _backend(module, config)

    job = _job()
    variation = job["renderJob"]["variations"][0]
    variation["background"] = {"mode": "footage", "groups": [shared]}
    payload = backend._request_payload(job=job, variation=variation, index=1, total=1, master_id=None)
    assert payload["bg_mode"] == "footage"
    assert payload["rotation_theme"] == "visual"
    assert "footage_artist_id" not in payload

    variation["background"] = {"mode": "photo", "groups": [shared]}
    payload = backend._request_payload(job=job, variation=variation, index=1, total=1, master_id=None)
    assert payload["bg_mode"] == "photo"
    assert payload["rotation_theme"] == "photo"
    assert "footage_artist_id" not in payload


def test_web_tariffs_match_public_payment_credit_grants(monkeypatch: pytest.MonkeyPatch) -> None:
    _module(monkeypatch)
    billing = importlib.import_module("app.billing_backend")
    credits = importlib.import_module("services.tg_bot_public.credits_db")
    assert billing.PLANS["BLAST"].credits == 100
    assert billing.PLANS["GLOW"].credits == 400
    assert billing.PLANS["IMPULSE"].credits is None
    assert credits.package_video_credits("15") == 100
    assert credits.package_video_credits("30") == 400
    assert credits.package_video_credits("50") == 100_000
