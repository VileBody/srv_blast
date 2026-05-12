"""Single dispatch point that decides which flow a user belongs to.

Paid-active users go through the legacy credit-product flow untouched.
Everyone else lands in the season flow.
"""
from __future__ import annotations

import logging
import time
from typing import Literal, Optional

log = logging.getLogger("tg_bot_botapi.season.flow_router")

Flow = Literal["existing", "season"]

_REF_PREFIX = "ref_"


def determine_flow(account_status: str, paid_until: float) -> Flow:
    """Return which flow the user should be routed into.

    `paid_active` with a non-expired `paid_until` → legacy flow.
    Everything else (new_free / exhausted_free / paid_churned) → season flow.
    """
    if account_status == "paid_active" and paid_until > time.time():
        return "existing"
    return "season"


def parse_start_param(raw: Optional[str]) -> Optional[int]:
    """Parse a `?start=ref_<chat_id>` deep-link payload.

    Returns the inviter's chat_id, or None if the payload is missing /
    malformed / not a referral. We accept only digits to keep the surface
    tight; any other payload is ignored.
    """
    if not raw:
        return None
    payload = raw.strip()
    if not payload.startswith(_REF_PREFIX):
        return None
    digits = payload[len(_REF_PREFIX):]
    if not digits.isdigit():
        return None
    try:
        chat_id = int(digits)
    except ValueError:
        return None
    if chat_id <= 0:
        return None
    return chat_id
