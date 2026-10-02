"""Воронка сайта после генерации: квиз, действия, безлимит на трек, квоты (app/funnel.py)."""
from __future__ import annotations

import asyncio
import importlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_web_asr_preview import _env


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    _env(monkeypatch)
    saved = {n: m for n, m in sys.modules.items() if n == "app" or n.startswith("app.")}
    for name in saved:
        sys.modules.pop(name, None)
    main = importlib.import_module("app.main")
    main.funnel._MEMORY.__init__()  # чистая память воронки на каждый тест
    from fastapi.testclient import TestClient

    with TestClient(main.app) as tc:
        yield tc, main
    for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
        sys.modules.pop(name, None)
    sys.modules.update(saved)


def _track_with_hash(main, audio_hash: str = "h" * 64) -> dict:
    track = main.store.save_track("song.mp3", s3_url="s3://raw/x.mp3", playback_url="/x.mp3", audio_hash=audio_hash)
    return track


def test_survey_follows_the_bot_branches_and_ends_with_a_clean_bridge(client) -> None:
    tc, _ = client
    assert tc.post("/api/funnel/survey", json={"questionId": "q1", "answerId": "none"}).json() == {"next": "q2", "done": False}
    assert tc.post("/api/funnel/survey", json={"questionId": "q2", "answerId": "no_edit"}).json()["next"] == "q3"
    done = tc.post("/api/funnel/survey", json={"questionId": "q3", "answerId": "money"}).json()
    assert done["done"] is True and done["branch"] == "money"
    # мостик из бота свёрстан под Telegram: на сайте без ручных переносов и стрелки-эмодзи
    assert "\n" not in done["bridge"] and "\U0001f447" not in done["bridge"]
    state = tc.get("/api/funnel/state").json()
    assert state["survey"]["completed"] is True and state["survey"]["branch"] == "money"


def test_unknown_answer_is_rejected(client) -> None:
    tc, _ = client
    r = tc.post("/api/funnel/survey", json={"questionId": "q1", "answerId": "nope"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "survey_unknown_answer"


def test_unlock_needs_both_actions_and_stays_on_one_track(client) -> None:
    tc, main = client
    track = _track_with_hash(main)
    other = _track_with_hash(main, "o" * 64)
    r = tc.post("/api/funnel/unlock", json={"trackId": track["id"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "unlock_actions_missing"
    assert tc.post("/api/funnel/actions/channel").json() == {"subscribed": True}  # mock: подписка есть
    tc.post("/api/funnel/actions/manager")
    state = tc.post("/api/funnel/unlock", json={"trackId": track["id"]}).json()
    assert state["unlimited"]["trackId"] == track["id"]
    assert state["unlimited"]["quota"]["allowed"] is True and state["unlimited"]["quota"]["maxVideos"] == 5
    r = tc.post("/api/funnel/unlock", json={"trackId": other["id"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "unlimited_other_track"


def test_plan_generation_uses_quota_then_credits_then_refuses(client) -> None:
    _, main = client
    funnel = main.funnel
    run = asyncio.run
    tg = 42
    assert run(funnel.plan_generation(tg, "h", 5, 0))[0] == "credits"  # безлимита нет
    run(funnel.repo().unlock_track_unlimited(tg, "h"))
    assert run(funnel.plan_generation(tg, "h", 5, 0))[0] == "free"
    run(funnel.repo().record_track_batch(tg_id=tg, audio_hash="h", job_id="j1", videos=5, mode="free"))
    # перезарядка 4 часа: кредитов нет — отказ с причиной для окна у кружка
    with pytest.raises(funnel.TrackLimitError) as exc:
        run(funnel.plan_generation(tg, "h", 5, 0))
    assert exc.value.code == "cooldown" and exc.value.quota.available_at is not None
    # кредиты есть — идём за кредиты, квоту не трогаем
    assert run(funnel.plan_generation(tg, "h", 3, 10))[0] == "credits"
    # больше, чем за раз: отказ «не больше N»
    run(funnel.repo().drop_track_batch("j1"))
    with pytest.raises(funnel.TrackLimitError) as exc:
        run(funnel.plan_generation(tg, "h", 6, 0))
    assert exc.value.code == "track_batch_cap"


def test_failed_videos_return_to_the_track_quota(client) -> None:
    _, main = client
    repo = main.funnel.repo()
    run = asyncio.run
    run(repo.unlock_track_unlimited(7, "h"))
    run(repo.record_track_batch(tg_id=7, audio_hash="h", job_id="j", videos=5, mode="free"))
    run(repo.release_track_batch("j", 5))
    rows = run(repo.list_track_batches(7, "h"))
    assert rows[0]["videos"] == 0


def test_quota_endpoint_and_tripwire_mock_refusal(client) -> None:
    tc, main = client
    track = _track_with_hash(main)
    assert tc.get(f"/api/funnel/quota?trackId={track['id']}").json() == {"quota": None}
    r = tc.post("/api/funnel/tripwire", json={"trackId": track["id"], "returnPath": "/app/projects/p1", "idempotencyKey": "k" * 16})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "payments_unavailable"
    bad = tc.post("/api/funnel/tripwire", json={"trackId": track["id"], "returnPath": "https://evil.example", "idempotencyKey": "k" * 16})
    assert bad.status_code == 422  # вернуть после оплаты можно только внутрь приложения


def test_ratings_round_trip(client) -> None:
    tc, main = client
    job = {"id": "job_x", "videos": [{"id": "v1"}, {"id": "v2"}], "projectId": "p", "userId": main.store.current_user_id()}
    main.store.JOBS[job["id"]] = job
    main.store.get_job = lambda job_id: job if job_id == "job_x" else None
    assert tc.post("/api/funnel/rating", json={"videoId": "v1", "jobId": "job_x", "score": 4, "reasons": ["transitions"]}).json() == {"ok": True}
    assert tc.post("/api/funnel/rating", json={"videoId": "v2", "jobId": "job_x", "score": 11}).status_code == 422
    ratings = tc.get("/api/funnel/ratings?jobId=job_x").json()["ratings"]
    assert ratings == {"v1": {"score": 4, "reasons": ["transitions"], "comment": ""}}
    # оценка батча складывается из оценок роликов: job.rating и generation_rated живы
    tc.post("/api/funnel/rating", json={"videoId": "v2", "jobId": "job_x", "score": 9})
    assert job["rating"] == 6.5 and job["videoRatings"] == {"v1": 4, "v2": 9}
    events = [e for e in main.analytics.EVENTS if e["name"] == "generation_rated"]
    assert events[-1]["props"] == {"jobId": "job_x", "rating": 6.5, "scale": 10, "videos": 2}


def test_rating_someone_elses_job_is_404(client) -> None:
    tc, main = client
    main.store.JOBS["job_y"] = {"id": "job_y", "videos": [], "userId": "someone-else"}
    r = tc.post("/api/funnel/rating", json={"videoId": "v1", "jobId": "job_y", "score": 5})
    assert r.status_code == 404


def test_tripwire_package_is_known_to_billing() -> None:
    from services.tg_bot_public.credits_db import normalize_package_code, package_video_credits
    from services.tg_bot_public.track_unlimited import TRIPWIRE_PACKAGE

    assert normalize_package_code(TRIPWIRE_PACKAGE) == TRIPWIRE_PACKAGE
    assert package_video_credits(TRIPWIRE_PACKAGE) == 0


def test_production_image_ships_the_bot_modules_credits_db_imports() -> None:
    """credits_db импортирует track_unlimited, funnel — marketing_texts: без них прод-сайт не стартует."""
    from pathlib import Path

    dockerfile = (Path(__file__).resolve().parents[1] / "web_app/backend/Dockerfile.production").read_text(encoding="utf-8")
    for module in ("credits_db.py", "track_unlimited.py", "marketing_texts.py"):
        assert f"services/tg_bot_public/{module}" in dockerfile, module


def test_quota_view_carries_tripwire_offer(client) -> None:
    _, main = client
    from services.tg_bot_public import track_unlimited as tu

    now = datetime(2026, 10, 3, tzinfo=timezone.utc)
    q = tu.evaluate(now=now, unlocked_at=now - timedelta(hours=1), tripwire=False,
                    batches=[tu.TrackBatch(now - timedelta(minutes=30), 5, "credits")])
    view = main.funnel.quota_view(q)
    assert view["reason"] == "cooldown" and view["tripwirePriceRub"] == 399 and view["tripwireBatchCap"] == 25


@pytest.fixture
def dev_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BLAST_DEV_TOOLS", "1")
    yield from client.__wrapped__(monkeypatch)


def test_dev_funnel_demo_scenes_build_real_states(dev_client) -> None:
    """DEV-сцены для показа воронки на живых страницах: каждая собирает своё состояние."""
    tc, main = dev_client
    expected = {
        "generating": None, "results": None, "unlocked": "ok", "cooldown": "cooldown",
        "daily": "daily_limit", "credits-out": None, "other-track": "ok",
    }
    for scene, reason in expected.items():
        body = tc.post(f"/api/dev/funnel-demo/{scene}").json()
        assert body["open"].startswith("/app/"), scene
        main.store.use_user(main.store.current_user_id())
        state = tc.get("/api/funnel/state").json()
        quota = (state["unlimited"] or {}).get("quota")
        assert (quota or {}).get("reason") == reason, scene
    assert tc.post("/api/dev/funnel-demo/nope").status_code == 422
