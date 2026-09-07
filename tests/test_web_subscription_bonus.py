from datetime import datetime, timezone

from services.tg_bot_public.credits_db import completed_subscription_months, earned_subscription_bonuses


def test_bonus_months_use_full_calendar_months_and_cap_at_three() -> None:
    start = datetime(2026, 1, 31, tzinfo=timezone.utc)
    assert completed_subscription_months(start, datetime(2026, 2, 28, tzinfo=timezone.utc)) == 0
    assert completed_subscription_months(start, datetime(2026, 3, 31, tzinfo=timezone.utc)) == 2
    assert completed_subscription_months(start, datetime(2027, 1, 31, tzinfo=timezone.utc)) == 3


def test_bonus_months_do_not_depend_on_timezone_awareness() -> None:
    assert completed_subscription_months(
        datetime(2026, 5, 5),
        datetime(2026, 6, 5),
    ) == 1


def test_bonus_requires_a_confirmed_renewal_for_each_elapsed_month() -> None:
    start = datetime(2026, 1, 31, tzinfo=timezone.utc)
    now = datetime(2026, 5, 31, tzinfo=timezone.utc)
    assert earned_subscription_bonuses(start, 1, now) == 0
    assert earned_subscription_bonuses(start, 2, now) == 1
    assert earned_subscription_bonuses(start, 3, now) == 2
    assert earned_subscription_bonuses(start, 4, now) == 3
