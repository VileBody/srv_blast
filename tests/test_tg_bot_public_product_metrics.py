from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.tg_bot_public import product_metrics as pm
from services.tg_bot_public import admin_product_card
from services.tg_bot_public.credits_db import _web_event_ts, source_economics_row

NOW = datetime(2026, 9, 16, 12, 0, 0)
D = timedelta(days=1)


def _ev(identity, event, days_ago, channel="bot"):
    return pm.Event(identity, event, NOW - days_ago * D, channel)


def _compute(events, payments=(), users=(), spend=0, channel="bot", days=30):
    src = pm.MetricsSource(events=list(events), payments=list(payments), users=list(users), spend_rub=spend)
    return pm.compute(src, channel=channel, date_from=NOW - days * D, date_to=NOW, now=NOW)


def test_active_excludes_users_who_blocked_after_last_action() -> None:
    events = [
        _ev("tg:1", "start", 5),
        _ev("tg:2", "start", 5), _ev("tg:2", "bot_blocked", 4),
        _ev("tg:3", "start", 50),                                     # старый, не активен
        _ev("tg:4", "start", 40), _ev("tg:4", "bot_blocked", 39), _ev("tg:4", "start", 2),  # вернулся после блокировки
    ]
    r = _compute(events)
    assert r["users"]["registered_total"] == 4
    assert r["users"]["total"] == 3          # «все» = без заблокировавших
    assert r["users"]["active_30d"] == 2     # tg:1 и tg:4
    assert r["users"]["blocked"] == 1        # только tg:2


def test_bot_initiated_events_do_not_make_users_active() -> None:
    # Рассылка всей базе не должна «оживлять» пользователей.
    events = [
        _ev("tg:1", "start", 80), _ev("tg:1", "reminder_sent", 2), _ev("tg:1", "sales_pitch", 1),
        _ev("tg:2", "start", 80), _ev("tg:2", "admin_dm", 1), _ev("tg:2", "generation_done", 1),
        _ev("tg:3", "start", 80), _ev("tg:3", "view_packages", 3),
    ]
    r = _compute(events)
    assert r["users"]["active_30d"] == 1     # только tg:3 что-то сделал сам
    assert pm.is_user_event("audio_uploaded") and not pm.is_user_event("lifecycle_test_send")


def test_funnel_percent_is_of_first_step_and_paid_comes_from_payments() -> None:
    events = [
        _ev("tg:1", "start", 3), _ev("tg:1", "audio_uploaded", 3), _ev("tg:1", "generation_done", 3),
        _ev("tg:2", "start", 3), _ev("tg:2", "audio_uploaded", 3),
        _ev("tg:3", "start", 3),
        _ev("tg:4", "start", 3),
    ]
    payments = [pm.Payment("tg:1", 990, NOW - 2 * D)]
    r = _compute(events, payments)
    f = {s["key"]: s for s in r["funnel"]}
    assert f["entered"]["count"] == 4 and f["entered"]["pct_of_first"] == 100
    assert f["uploaded"]["pct_of_first"] == 50
    assert f["received"]["pct_of_first"] == 25
    assert f["paid"]["count"] == 1 and f["paid"]["pct_of_first"] == 25


def test_product_metrics_success_repeat_and_time_to_value() -> None:
    events = [
        _ev("tg:1", "start", 10), _ev("tg:1", "generation_started", 9), _ev("tg:1", "generation_done", 9),
        _ev("tg:1", "generation_started", 2), _ev("tg:1", "generation_done", 2),
        _ev("tg:2", "start", 5), _ev("tg:2", "generation_started", 4), _ev("tg:2", "generation_failed", 4),
        _ev("tg:3", "start", 5), _ev("tg:3", "generation_started", 5), _ev("tg:3", "generation_done", 5),
    ]
    r = _compute(events)
    p = r["product"]
    assert p["generation_started"] == 4
    assert p["generation_done"] == 3
    assert p["success_pct"] == pytest.approx(75.0)
    assert p["creators"] == 2 and p["repeat_creators"] == 1 and p["repeat_pct"] == 50
    assert p["videos_per_creator"] == 1.5
    assert p["ttv_median_hours"] == pytest.approx(12.0)  # медиана из 24ч (tg:1) и 0ч (tg:3)


def test_money_ltv_cac_and_period_split() -> None:
    events = [_ev("tg:1", "start", 60), _ev("tg:2", "start", 10), _ev("tg:3", "start", 10)]
    payments = [
        pm.Payment("tg:1", 1000, NOW - 50 * D),   # вне периода
        pm.Payment("tg:1", 1000, NOW - 5 * D),    # повторная — не новый платящий
        pm.Payment("tg:2", 3000, NOW - 3 * D),    # новый
    ]
    r = _compute(events, payments, spend=6000)
    m = r["money"]
    assert m["revenue_period"] == 4000 and m["revenue_prev"] == 1000
    assert m["payers_period"] == 2 and m["new_payers_period"] == 1
    assert m["ltv"] == 2500                       # 5000 / 2 платящих
    assert m["paid_conversion_pct"] == pytest.approx(200 / 3)
    assert m["cac"] == 6000                       # 6000 / 1 новый платящий
    assert m["ltv_to_cac"] == pytest.approx(2500 / 6000)
    assert m["cost_per_user"] == 3000             # 6000 / 2 новых юзера


def test_cac_is_none_without_spend() -> None:
    r = _compute([_ev("tg:1", "start", 1)], [pm.Payment("tg:1", 100, NOW - D)])
    assert r["money"]["cac"] is None and r["money"]["ltv_to_cac"] is None


def test_retention_is_day_bounded_by_segment_and_ignores_system_events() -> None:
    events = [
        _ev("tg:1", "start", 40), _ev("tg:1", "audio_uploaded", 39),   # вернулся на D1
        _ev("tg:2", "start", 40), _ev("tg:2", "reminder_sent", 39),    # рассылка — не возврат
        _ev("tg:3", "start", 40), _ev("tg:3", "start", 33),            # D7
        _ev("tg:4", "start", 2), _ev("tg:4", "start", 1),              # D1, но для D3+ не созрел
    ]
    payments = [pm.Payment("tg:3", 990, NOW - 30 * D)]
    r = _compute(events, payments)
    seg = {x["key"]: x for x in r["retention"]["segments"]}
    assert r["retention"]["days"] == [1, 3, 7, 14, 30]
    d = {c["day"]: c for c in seg["all"]["cells"]}
    assert d[1]["matured"] == 4 and d[1]["returned"] == 2
    assert d[3]["matured"] == 3 and d[3]["returned"] == 0
    assert d[7]["matured"] == 3 and d[7]["returned"] == 1
    assert seg["paid"]["size"] == 1 and {c["day"]: c for c in seg["paid"]["cells"]}[7]["pct"] == 100
    assert seg["free"]["size"] == 3 and {c["day"]: c for c in seg["free"]["cells"]}[1]["pct"] == pytest.approx(200 / 3)


def test_cohorts_have_future_weeks_as_none_and_week0_100() -> None:
    events = [_ev("tg:1", "start", 8), _ev("tg:1", "start", 0)]
    r = _compute(events)
    cohorts = r["retention"]["cohorts"]
    assert len(cohorts) == pm.COHORT_WEEKS
    row = next(c for c in cohorts if c["size"] == 1)
    assert row["cells"][0] == 100.0
    assert row["cells"][1] == 100.0
    assert row["cells"][-1] is None


def test_all_channel_dedupes_by_telegram_identity() -> None:
    events = [
        _ev("tg:1", "start", 2, "bot"),
        _ev("tg:1", "app_entry", 1, "site"),
        _ev("web:abc", "app_entry", 1, "site"),
    ]
    r = _compute(events, channel="all")
    assert r["users"]["total"] == 2
    assert r["funnel"][0]["count"] == 2


def test_users_table_gives_first_seen_before_first_event() -> None:
    events = [_ev("tg:1", "generation_done", 1)]
    r = _compute(events, users=[("tg:1", NOW - 3 * D)])
    assert r["product"]["ttv_median_hours"] == pytest.approx(48.0)


def test_web_event_ts_parses_iso_string_to_naive_utc() -> None:
    assert _web_event_ts("2026-09-16T12:00:00+03:00") == datetime(2026, 9, 16, 9, 0, 0)
    assert _web_event_ts("2026-09-16T12:00:00Z") == datetime(2026, 9, 16, 12, 0, 0)
    assert _web_event_ts(datetime(2026, 9, 16, 12, tzinfo=timezone.utc)) == datetime(2026, 9, 16, 12)
    with pytest.raises(ValueError):
        _web_event_ts("")


def test_card_renders_without_caps_and_with_all_sections() -> None:
    r = _compute([_ev("tg:1", "start", 1)])
    html = admin_product_card.render_product_card(
        r, channel="bot", active_period="30d", period_label="30 дней", spend_rows=[],
    )
    for needle in ("Пользователи", "Активные за 30 дн", "Готовые ролики", "LTV", "CAC", "Платные", "Бесплатные", "D7", "Воронка", "Возвращаемость"):
        assert needle in html
    assert "Последние действия" not in html
    assert "от пред." not in html


def test_active_window_switch_changes_active_count() -> None:
    events = [_ev("tg:1", "start", 3), _ev("tg:2", "start", 20), _ev("tg:3", "start", 60)]
    src = pm.MetricsSource(events=events, payments=[])
    r7 = pm.compute(src, channel="bot", date_from=NOW - 30 * D, date_to=NOW, now=NOW, active_days=7)
    r90 = pm.compute(src, channel="bot", date_from=NOW - 30 * D, date_to=NOW, now=NOW, active_days=90)
    rbad = pm.compute(src, channel="bot", date_from=NOW - 30 * D, date_to=NOW, now=NOW, active_days=13)
    assert r7["users"]["active_30d"] == 1 and r7["users"]["active_days"] == 7
    assert r90["users"]["active_30d"] == 3
    assert rbad["users"]["active_days"] == 30  # неизвестное окно → дефолт


def test_source_economics_row_derives_cac_and_roas() -> None:
    row = source_economics_row({"src": "tiktok_link", "users_total": 100, "users_new": 40, "payers_total": 6,
                                "payers_new": 4, "revenue_period": 12000, "revenue_total": 20000, "spend_rub": 8000})
    assert row["cac"] == 2000 and row["cost_per_user"] == 200 and row["roas"] == 1.5
    empty = source_economics_row({"src": "", "users_new": 0, "payers_new": 0, "spend_rub": 0})
    assert empty["source"] == "(без источника)" and empty["cac"] is None and empty["roas"] is None
