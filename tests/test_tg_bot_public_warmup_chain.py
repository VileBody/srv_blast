"""Contract tests for the public-bot warm-up conversion chain."""

from pathlib import Path


def test_chain_uses_callback_buttons_and_three_distinct_stages() -> None:
    from services.tg_bot_public.warmup_chain import (
        CALLBACK_PREFIX,
        keyboard_for_next,
        message_for_stage,
    )

    assert all(message_for_stage(stage).strip() for stage in (1, 2, 3))
    assert "с 15 до 100 роликов" in message_for_stage(1)
    assert "бесплатный тестовый режим" in message_for_stage(1)
    assert "обновляем условия подписки" in message_for_stage(3)
    assert "100 роликов в месяц за 1 990 ₽" in message_for_stage(3)
    assert "от генерации до публикации и анализа результатов" in message_for_stage(3)
    assert keyboard_for_next(1, is_test=True).inline_keyboard[0][0].callback_data == f"{CALLBACK_PREFIX}test:2"
    assert keyboard_for_next(2, is_test=False).inline_keyboard[0][0].callback_data == f"{CALLBACK_PREFIX}prod:3"
    assert keyboard_for_next(3, is_test=False) is None


def test_callback_repairs_missing_delivery_progress() -> None:
    from services.tg_bot_public.warmup_chain import callback_progress

    assert callback_progress(0, 2) == (True, 1)
    assert callback_progress(1, 2) == (True, 1)
    assert callback_progress(0, 3) == (True, 2)
    assert callback_progress(2, 3) == (True, 2)
    assert callback_progress(3, 3) == (False, 3)


def test_public_app_has_safe_test_and_explicit_production_entrypoints() -> None:
    src = Path("services/tg_bot_public/app.py").read_text(encoding="utf-8")
    assert 'Command("warmup_test")' in src
    assert 'Command("warmup_stats")' in src
    assert 'Command("warmup_send")' in src
    assert 'command_parts[1].strip().upper() == "CONFIRM"' in src
    assert "warmup_stage_1_failed" in src


def test_progress_is_separated_between_test_and_production() -> None:
    src = Path("services/tg_bot_public/credits_db.py").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS warmup_progress" in src
    assert "PRIMARY KEY (campaign, tg_id, is_test)" in src
    assert "GREATEST(warmup_progress.stage, EXCLUDED.stage)" in src
    assert "VALUES ($1, $2, $3, $4::SMALLINT," in src
    assert "CASE WHEN $4::SMALLINT >= 3 THEN NOW() END" in src
