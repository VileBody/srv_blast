"""Правила бесплатного безлимита на трек (services/tg_bot_public/track_unlimited.py)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services.tg_bot_public import track_unlimited as tu

T0 = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
H = timedelta(hours=1)


def _q(now, *, unlocked=T0, tripwire=False, batches=()):
    return tu.evaluate(now=now, unlocked_at=unlocked, tripwire=tripwire, batches=list(batches))


def test_locked_without_unlock():
    q = _q(T0, unlocked=None)
    assert not q.allowed and q.reason == tu.REASON_LOCKED


def test_first_cooldown_counts_from_the_paid_trial_batch():
    trial = tu.TrackBatch(T0 - 10 * 60 * timedelta(seconds=1), 5, "credits")
    q = _q(T0, batches=[trial])
    assert q.reason == tu.REASON_COOLDOWN and q.available_at == trial.created_at + 4 * H
    assert _q(trial.created_at + 4 * H, batches=[trial]).allowed


def test_first_day_is_two_batches_four_hours_apart():
    b1 = tu.TrackBatch(T0 + H, 5, "free")
    assert _q(T0 + 2 * H, batches=[b1]).reason == tu.REASON_COOLDOWN
    q = _q(T0 + 5 * H, batches=[b1])
    assert q.allowed and q.max_videos == 5
    b2 = tu.TrackBatch(T0 + 5 * H, 3, "free")
    q = _q(T0 + 10 * H, batches=[b1, b2])
    assert q.reason == tu.REASON_DAILY_LIMIT and q.available_at == T0 + 24 * H


def test_after_first_day_five_videos_per_rolling_day():
    day2 = T0 + 30 * H
    b1 = tu.TrackBatch(day2, 3, "free")
    q = _q(day2 + H, batches=[b1])
    assert q.allowed and q.max_videos == 2  # без перезарядки после первых суток
    b2 = tu.TrackBatch(day2 + H, 2, "free")
    q = _q(day2 + 2 * H, batches=[b1, b2])
    assert q.reason == tu.REASON_DAILY_LIMIT and q.available_at == day2 + 24 * H
    q = _q(day2 + 24 * H + timedelta(minutes=1), batches=[b1, b2])
    assert q.allowed and q.max_videos == 3


def test_tripwire_lifts_every_limit():
    batches = [tu.TrackBatch(T0 + H * i, 25, "free") for i in range(5)]
    q = _q(T0 + 5 * H, tripwire=True, batches=batches)
    assert q.allowed and q.max_videos == tu.TRIPWIRE_BATCH_CAP and q.tripwire


def test_credit_batches_do_not_eat_the_daily_quota():
    day2 = T0 + 30 * H
    paid = tu.TrackBatch(day2, 5, "credits")
    assert _q(day2 + H, batches=[paid]).max_videos == 5
