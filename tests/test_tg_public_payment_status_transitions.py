from services.tg_bot_public.admin_panel import (
    _payment_status_rank,
    _should_apply_payment_status_update,
)


def test_payment_status_rank_order() -> None:
    assert _payment_status_rank("NEW") < _payment_status_rank("AUTHORIZED")
    assert _payment_status_rank("AUTHORIZED") < _payment_status_rank("CONFIRMED")
    assert _payment_status_rank("CONFIRMED") <= _payment_status_rank("REFUNDED")


def test_payment_status_update_prevents_downgrade() -> None:
    assert _should_apply_payment_status_update("AUTHORIZED", "CONFIRMED") is True
    assert _should_apply_payment_status_update("CONFIRMED", "AUTHORIZED") is False
    assert _should_apply_payment_status_update("REFUNDED", "CONFIRMED") is False


def test_payment_status_update_allows_unknown_or_empty_current() -> None:
    assert _should_apply_payment_status_update("", "AUTHORIZED") is True
    assert _should_apply_payment_status_update("SOMETHING_NEW", "AUTHORIZED") is True
    assert _should_apply_payment_status_update("CONFIRMED", "") is False
