from datetime import datetime, timezone

from services.tg_bot_public.credits_db import completed_subscription_months


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
