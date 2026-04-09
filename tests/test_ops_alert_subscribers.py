from services.orchestrator.ops_alert_subscribers import (
    is_terminal_telegram_delivery_error,
    parse_subscriber_command,
)


def test_parse_subscriber_command_activate_variants() -> None:
    assert parse_subscriber_command("/start") == "activate"
    assert parse_subscriber_command("/start abc") == "activate"
    assert parse_subscriber_command("/subscribe") == "activate"
    assert parse_subscriber_command("/subscribe now") == "activate"


def test_parse_subscriber_command_deactivate_variants() -> None:
    assert parse_subscriber_command("/stop") == "deactivate"
    assert parse_subscriber_command("/unsubscribe") == "deactivate"
    assert parse_subscriber_command("/unsubscribe later") == "deactivate"


def test_parse_subscriber_command_ignore_other_text() -> None:
    assert parse_subscriber_command("") == ""
    assert parse_subscriber_command("hello") == ""
    assert parse_subscriber_command("/help") == ""


def test_terminal_telegram_delivery_errors() -> None:
    assert is_terminal_telegram_delivery_error(status_code=400, description="Bad Request: chat not found")
    assert is_terminal_telegram_delivery_error(
        status_code=400,
        description="Forbidden: bot was blocked by the user",
    )
    assert not is_terminal_telegram_delivery_error(status_code=429, description="Too Many Requests")
    assert not is_terminal_telegram_delivery_error(status_code=400, description="Bad Request: message text is empty")
