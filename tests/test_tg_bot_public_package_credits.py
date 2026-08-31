from __future__ import annotations

import pytest

from services.tg_bot_public.credits_db import package_video_credits


@pytest.mark.parametrize(
    ("package", "expected"),
    [
        ("5", 5),
        ("Триал", 5),
        ("15", 100),
        ("Бласт", 100),
        ("30", 400),
        ("Глоу", 400),
        ("50", 100_000),
        ("Импульс", 100_000),
    ],
)
def test_persisted_package_spelling_gets_current_video_allowance(package: str, expected: int) -> None:
    assert package_video_credits(package) == expected


def test_unknown_package_never_silently_gets_stale_five_credit_default() -> None:
    with pytest.raises(ValueError, match="unknown payment package"):
        package_video_credits("legacy-mystery")


def test_photo_source_button_has_no_sticker() -> None:
    from services.tg_bot_public.app import BTN_BG_PICTURES_PHOTO

    assert BTN_BG_PICTURES_PHOTO == "Фото"
