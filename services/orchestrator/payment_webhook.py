"""
Payment webhook handler for the orchestrator FastAPI app.

POST /payments/webhook   — payment provider callback (HMAC-SHA256 verified)
POST /payments/activate  — admin manual activation (Bearer token)

Both paths share the same UserStore (PostgreSQL) so one order_id / activation_id
can NEVER credit a user twice regardless of which path fires first.

Unlock is fully decoupled from Telegram:
  this handler writes only to PostgreSQL.
  The bot's polling loop detects the credit change and notifies the user.
  A Telegram outage therefore CANNOT leave a paid user stuck in an old stage.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from services.tg_bot_botapi.user_store import UserStore

log = logging.getLogger("orchestrator.payment_webhook")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class PaymentWebhookRequest(BaseModel):
    order_id: str
    chat_id: int
    credits: int
    status: str              # Only "CONFIRMED" triggers a credit
    amount_value: str = ""   # informational
    amount_currency: str = ""


class ManualActivateRequest(BaseModel):
    activation_id: str       # unique — prevents double-activation across both paths
    chat_id: int
    credits: int
    note: str = ""


class PaymentWebhookResponse(BaseModel):
    ok: bool
    already_done: bool = False
    new_balance: int = 0
    message: str = ""


# ---------------------------------------------------------------------------
# HMAC verification (fail-closed: empty secret → always 403)
# ---------------------------------------------------------------------------

def _verify_hmac(secret: str, raw_body: bytes, signature_header: str) -> bool:
    if not secret:
        return False
    prefix = "sha256="
    if not signature_header.startswith(prefix):
        return False
    provided = signature_header[len(prefix):]
    computed = hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(computed, provided)


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def make_payment_router(
    user_store: UserStore,
    *,
    webhook_secret: str,
    admin_token: str = "",
) -> APIRouter:
    router = APIRouter(prefix="/payments", tags=["payments"])

    @router.post("/webhook", response_model=PaymentWebhookResponse)
    async def payment_webhook(
        request: Request,
        x_webhook_signature: str = Header(default=""),
    ) -> PaymentWebhookResponse:
        raw_body = await request.body()

        if not _verify_hmac(webhook_secret, raw_body, x_webhook_signature):
            log.warning(
                "payment_webhook_hmac_failed sig=%r body_len=%d",
                x_webhook_signature[:40], len(raw_body),
            )
            raise HTTPException(status_code=403, detail="Invalid signature")

        try:
            payload = PaymentWebhookRequest.model_validate(json.loads(raw_body))
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Invalid payload: {exc}")

        if payload.status.upper() != "CONFIRMED":
            log.info(
                "payment_webhook_ignored order=%s status=%s",
                payload.order_id, payload.status,
            )
            return PaymentWebhookResponse(ok=True, message=f"status={payload.status} ignored")

        if payload.credits <= 0:
            raise HTTPException(status_code=422, detail="credits must be > 0")

        note = (
            f"payment order={payload.order_id} "
            f"amount={payload.amount_value}{payload.amount_currency}"
        ).strip()

        # Ensure user row exists before crediting.
        await user_store.ensure_profile(payload.chat_id)

        ok, already_done, new_balance = await user_store.confirm_payment(
            order_id=payload.order_id,
            chat_id=payload.chat_id,
            credits=payload.credits,
            note=note,
        )
        if not ok:
            raise HTTPException(status_code=500, detail="Failed to process payment")

        log.info(
            "payment_webhook_ok order=%s chat=%s credits=%d already_done=%s new_balance=%d",
            payload.order_id, payload.chat_id, payload.credits, already_done, new_balance,
        )
        return PaymentWebhookResponse(
            ok=True,
            already_done=already_done,
            new_balance=new_balance,
            message="already_confirmed" if already_done else "confirmed",
        )

    @router.post("/activate", response_model=PaymentWebhookResponse)
    async def manual_activate(
        req: ManualActivateRequest,
        x_admin_token: str = Header(default=""),
    ) -> PaymentWebhookResponse:
        if not admin_token or not hmac.compare_digest(x_admin_token, admin_token):
            raise HTTPException(status_code=403, detail="Invalid admin token")

        if req.credits <= 0:
            raise HTTPException(status_code=422, detail="credits must be > 0")

        await user_store.ensure_profile(req.chat_id)

        ok, already_done, new_balance = await user_store.manual_activate(
            activation_id=req.activation_id,
            chat_id=req.chat_id,
            credits=req.credits,
            note=req.note,
        )
        if not ok:
            raise HTTPException(status_code=500, detail="Failed to process activation")

        log.info(
            "manual_activate_ok id=%s chat=%s credits=%d already_done=%s new_balance=%d",
            req.activation_id, req.chat_id, req.credits, already_done, new_balance,
        )
        return PaymentWebhookResponse(
            ok=True,
            already_done=already_done,
            new_balance=new_balance,
            message="already_activated" if already_done else "activated",
        )

    return router
