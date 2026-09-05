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
        footage_artists={"Неон": "electro_synthwave"},
        footage_catalog=(
            {
                "id": "neon",
                "name": "Неон",
                "previewUrl": "s3://assets/previews/neon.mp4",
                "score": 1.0,
            },
        ),
        photo_catalog=(
            {
                "id": "neon-photo",
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

    def post(self, _url: str, *, json: dict[str, Any]) -> _Response:
        self.posts.append(json)
        return _Response({"job_id": f"orch-{len(self.posts)}"})

    def get(self, url: str) -> _Response:
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
    return backend


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


def test_unknown_https_output_is_not_a_download_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module(monkeypatch)
    backend = _backend(module, _config(module))

    with pytest.raises(module.ProductionBackendError, match="configured S3 endpoint"):
        backend.download_url("https://cdn.example/result.mp4", "video-1")


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
                    "rotationTagsGroup": "New_York",
                    "renderPreset": "wide",
                    "bgMode": "footage",
                },
            },
        ),
        selector_by_mode={
            "footage": {
                "Нью-Йорк": {
                    "rotationTheme": "collection",
                    "rotationTagsGroup": "New_York",
                    "renderPreset": "wide",
                    "bgMode": "footage",
                }
            },
            "photo": {},
        },
        default_artist_id="electro_synthwave",
    )
    backend = _backend(module, config)
    job = _job()
    variation = job["renderJob"]["variations"][0]
    variation["background"] = {"mode": "footage", "groups": ["Нью-Йорк"]}

    payload = backend._request_payload(job=job, variation=variation, index=1, total=1, master_id=None)

    assert payload["rotation_theme"] == "collection"
    assert payload["rotation_tags_group"] == "New_York"
    assert payload["render_preset"] == "wide"
    assert payload["bg_mode"] == "footage"
    # Профиль артиста всё ещё нужен Stage 2, даже когда группа закреплена.
    assert payload["footage_artist_id"] == "electro_synthwave"


def test_vertical_stays_vertical_without_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    """Каталог старого, артистового формата обязан продолжать работать.

    Без selector пара rotation пустая (оркестратор сам выбирает подгруппу), а
    геометрия — vertical: неверный формат хуже исторического.
    """
    module = _module(monkeypatch)
    backend = _backend(module, _config(module))
    job = _job()
    variation = job["renderJob"]["variations"][0]
    variation["background"] = {"mode": "footage", "groups": ["Неон"]}

    payload = backend._request_payload(job=job, variation=variation, index=1, total=1, master_id=None)

    # Пустые поля payload вычищаются: закреплять «никакую» группу нельзя, иначе
    # оркестратор получил бы половину пары и не понял бы, чего от него хотят.
    assert "rotation_theme" not in payload
    assert "rotation_tags_group" not in payload
    assert payload["render_preset"] == "vertical"
    assert payload["footage_artist_id"] == "electro_synthwave"


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
        default_artist_id="electro_synthwave",
    )
    backend = _backend(module, config)

    job = _job()
    variation = job["renderJob"]["variations"][0]
    variation["background"] = {"mode": "footage", "groups": [shared]}
    payload = backend._request_payload(job=job, variation=variation, index=1, total=1, master_id=None)
    assert payload["bg_mode"] == "footage"
    assert payload["rotation_theme"] == "visual"

    variation["background"] = {"mode": "photo", "groups": [shared]}
    payload = backend._request_payload(job=job, variation=variation, index=1, total=1, master_id=None)
    assert payload["bg_mode"] == "photo"
    assert payload["rotation_theme"] == "photo"


def test_web_tariffs_match_public_payment_credit_grants(monkeypatch: pytest.MonkeyPatch) -> None:
    _module(monkeypatch)
    billing = importlib.import_module("app.billing_backend")
    assert billing.PLANS["BLAST"].credits == 100
    assert billing.PLANS["GLOW"].credits == 400
    assert billing.PLANS["IMPULSE"].credits is None

    admin_source = (REPO_ROOT / "services" / "tg_bot_public" / "admin_panel.py").read_text(
        encoding="utf-8"
    )
    assert '"Бласт": 100' in admin_source
    assert '"Глоу": 400' in admin_source
    assert '"Импульс": 100_000' in admin_source
