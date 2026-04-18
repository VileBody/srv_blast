from pathlib import Path


def test_public_app_has_referral_bonus_hook_for_parity() -> None:
    src = Path("services/tg_bot_public/app.py").read_text(encoding="utf-8")
    assert "async def _maybe_grant_referral_bonus_after_generation" in src

