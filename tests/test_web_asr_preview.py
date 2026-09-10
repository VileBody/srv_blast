"""Примерка субтитров в веб-визарде: мок-ASR, ручки /api/wizard/asr/*, проводка в оркестратор."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.test_web_production_backend import _FakeHttp, _backend, _config, _job

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_BACKEND = REPO_ROOT / "web_app" / "backend"


def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(WEB_BACKEND))
    monkeypatch.setenv("MODE", "dev")
    monkeypatch.setenv("BLAST_BACKEND_MODE", "mock")
    monkeypatch.setenv("APP_URL", "http://localhost:5173")
    monkeypatch.setenv("BLAST_SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("BLAST_CORS_ORIGINS", "http://localhost:5173")
    monkeypatch.setenv("BLAST_PERSIST", "0")
    monkeypatch.setenv("BLAST_REQUIRE_AUTH", "0")
    monkeypatch.setenv("BLAST_CSRF", "0")
    monkeypatch.setenv("BLAST_RATE_LIMIT", "0")


def _asr_module(monkeypatch: pytest.MonkeyPatch):
    _env(monkeypatch)
    sys.modules.pop("app.asr_preview", None)
    return importlib.import_module("app.asr_preview")


def test_mock_words_cover_window_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    asr = _asr_module(monkeypatch)
    words = asr.mock_words("раз два три\nчетыре пять", 10.0, 20.0)
    assert [w["text"] for w in words] == ["раз", "два", "три", "четыре", "пять"]
    assert words[0]["tStart"] == 10.0
    assert words[-1]["tEnd"] <= 20.0
    for left, right in zip(words, words[1:]):
        assert left["tEnd"] <= right["tStart"]
        assert left["tEnd"] > left["tStart"]
    # пауза после строки шире, чем между словами внутри строки
    assert words[3]["tStart"] - words[2]["tEnd"] > words[1]["tStart"] - words[0]["tEnd"]
    assert asr.mock_words("", 10.0, 20.0) == []


def test_preview_key_changes_with_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    asr = _asr_module(monkeypatch)
    base = asr.preview_key("s3://b/t.mp3", 10.0, 20.0, "текст")
    assert base == asr.preview_key("s3://b/t.mp3", 10.0, 20.0, "текст")
    assert base != asr.preview_key("s3://b/t.mp3", 10.0, 21.0, "текст")
    assert base != asr.preview_key("s3://b/t.mp3", 10.0, 20.0, "другой")
    assert base != asr.preview_key("s3://b/other.mp3", 10.0, 20.0, "текст")


def test_stage_data_asr_rejects_foreign_key(monkeypatch: pytest.MonkeyPatch) -> None:
    asr = _asr_module(monkeypatch)
    block = {"key": "abc", "jobId": "j1", "words": [], "edited": True}
    assert asr.stage_data_asr({"asr": block}, expected_key="abc") is block
    assert asr.stage_data_asr({"asr": block}, expected_key="zzz") is None
    assert asr.stage_data_asr({"asr": {**block, "jobId": None}}, expected_key="abc") is None
    assert asr.stage_data_asr({}, expected_key="abc") is None


def test_focus_words_and_conversions(monkeypatch: pytest.MonkeyPatch) -> None:
    asr = _asr_module(monkeypatch)
    words = [
        {"text": "раз", "tStart": 1.0, "tEnd": 1.4, "focus": True},
        {"text": "два", "tStart": 1.5, "tEnd": 1.9},
    ]
    assert asr.focus_words(words) == [{"text": "раз", "t_start": 1.0}]
    orch = asr.words_to_orchestrator(words)
    assert orch[1] == {"text": "два", "t_start": 1.5, "t_end": 1.9}
    assert asr.words_from_orchestrator(orch)[0] == {"text": "раз", "tStart": 1.0, "tEnd": 1.4}


class _AsrHttp(_FakeHttp):
    def __init__(self) -> None:
        super().__init__()
        self.puts: list[tuple[str, dict[str, Any]]] = []

    def put(self, url: str, *, json: dict[str, Any]):
        self.puts.append((url, json))
        from tests.test_web_production_backend import _Response

        return _Response({"job_id": "asr-1", "words_count": len(json["words"]), "clip_start_abs": 10.0, "clip_end_abs": 25.0})


def _asr_job(module: Any, *, edited: bool) -> dict[str, Any]:
    job = _job()
    job["renderJob"]["track"]["segment"] = {"from": 10.0, "to": 25.0}
    job["renderJob"]["lyrics"] = {"full": "полный текст", "fragment": "раз два три"}
    asr = importlib.import_module("app.asr_preview")
    key = asr.preview_key("s3://raw-audio/raw/track.mp3", 10.0, 25.0, "раз два три")
    job["stageData"] = {
        "final": {"accentColor": "#8b6fe6"},
        "fragment": "раз два три",
        "lyrics": "полный текст",
        "asr": {
            "key": key,
            "jobId": "asr-1",
            "edited": edited,
            "words": [
                {"text": "раз", "tStart": 10.1, "tEnd": 10.5},
                {"text": "два", "tStart": 10.6, "tEnd": 11.0, "focus": True},
                {"text": "три", "tStart": 11.2, "tEnd": 11.7},
            ],
        },
    }
    return job


def test_enqueue_reuses_asr_preview_and_pushes_edits(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_web_production_backend import _module

    module = _module(monkeypatch)
    backend = _backend(module, _config(module, stage1_backend="local_ctc"))
    backend._http = _AsrHttp()
    job = _asr_job(module, edited=True)

    backend.enqueue_job(job)

    # правки ушли в оркестратор ДО первой вариации
    assert len(backend._http.puts) == 1
    url, body = backend._http.puts[0]
    assert url.endswith("/jobs/asr-1/asr-words")
    assert body["words"][1] == {"text": "два", "t_start": 10.6, "t_end": 11.0}
    # первая вариация берёт Stage 1 из примерки, фокус-слова уезжают контрактом FocusWord
    first = backend._http.posts[0]
    assert first["reuse_text_job_id"] == "asr-1"
    assert first["user_focus_words"] == [{"text": "два", "t_start": 10.6}]
    assert first["target_fragment"] == "раз два три"

    backend._http.states["orch-1"] = {"status": "SUCCEEDED", "stage": "poll", "result": {"output_url": "s3://o/1.mp4"}}
    backend.sync_job(job)
    # вторая вариация — от первой, как и раньше
    assert backend._http.posts[1]["reuse_text_job_id"] == "orch-1"


def test_enqueue_skips_edit_push_when_not_edited(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_web_production_backend import _module

    module = _module(monkeypatch)
    backend = _backend(module, _config(module, stage1_backend="local_ctc"))
    backend._http = _AsrHttp()
    backend.enqueue_job(_asr_job(module, edited=False))
    assert backend._http.puts == []
    assert backend._http.posts[0]["reuse_text_job_id"] == "asr-1"


def test_enqueue_ignores_stale_asr_block(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_web_production_backend import _module

    module = _module(monkeypatch)
    backend = _backend(module, _config(module, stage1_backend="local_ctc"))
    backend._http = _AsrHttp()
    job = _asr_job(module, edited=True)
    # окно поменяли после примерки — блок про другой ключ
    job["renderJob"]["track"]["segment"] = {"from": 10.0, "to": 26.0}
    backend.enqueue_job(job)
    assert backend._http.puts == []
    assert "reuse_text_job_id" not in backend._http.posts[0]
    assert "user_focus_words" not in backend._http.posts[0]


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    _env(monkeypatch)
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    main = importlib.import_module("app.main")
    from fastapi.testclient import TestClient

    with TestClient(main.app) as tc:
        yield tc, main


def test_asr_endpoints_mock_flow(client) -> None:
    tc, main = client
    # без трека примерять нечего (демо-воркспейс приходит с треком — снимаем)
    main.store.ws().saved_tracks.clear()
    r = tc.post("/api/wizard/asr/start", json={"clipFrom": "00:10", "clipTo": "00:20", "fragment": "раз два", "lyrics": ""})
    assert r.status_code == 200
    assert r.json()["asr"]["status"] == "IDLE"

    main.store.save_track("song.mp3", audio_hash="h")
    r = tc.post("/api/wizard/asr/start", json={"clipFrom": "00:10", "clipTo": "00:20", "fragment": "раз два\nтри", "lyrics": ""})
    asr = r.json()["asr"]
    assert asr["status"] == "COMPLETED"
    assert [w["text"] for w in asr["words"]] == ["раз", "два", "три"]
    assert asr["clipStart"] == 10.0 and asr["clipEnd"] == 20.0
    key = asr["key"]

    # тот же ключ — та же примерка, другой текст — другой ключ
    r = tc.get("/api/wizard/asr", params={"clipFrom": "00:10", "clipTo": "00:20", "fragment": "раз два\nтри"})
    assert r.json()["asr"]["key"] == key
    r = tc.get("/api/wizard/asr", params={"clipFrom": "00:10", "clipTo": "00:20", "fragment": "иное"})
    assert r.json()["asr"]["status"] == "IDLE"
    assert r.json()["asr"]["key"] != key

    # без текста fragment → берётся lyrics (то же правило, что у рендера)
    r = tc.post("/api/wizard/asr/start", json={"clipFrom": "00:10", "clipTo": "00:20", "fragment": "", "lyrics": "весь текст"})
    assert [w["text"] for w in r.json()["asr"]["words"]] == ["весь", "текст"]
