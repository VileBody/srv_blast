"""Shared production billing adapter.

The public Telegram bot already owns the T-Bank payment ledger and the user
credit balance.  The web application deliberately uses those same services
and tables, keyed by the verified Telegram chat id.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .runtime import SETTINGS


class BillingError(RuntimeError):
    pass


class PaymentInitError(BillingError):
    def __init__(self, code: str, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.code = str(code)
        self.status_code = int(status_code)


class InsufficientCredits(BillingError):
    def __init__(self, available: int) -> None:
        super().__init__(f"insufficient credits: available={available}")
        self.available = available


class TrackQuotaExhausted(BillingError):
    pass


def _required(name: str) -> str:
    value = str(os.getenv(name) or "").strip()
    if not value:
        raise BillingError(f"billing_backend: {name} is required")
    return value


@dataclass(frozen=True)
class Plan:
    code: str
    payment_name: str
    price_rub: int
    credits: int | None
    tracks: int
    kind: str
    duration_days: int | None = None


PLANS = {
    "BLAST": Plan("BLAST", "Бласт", 1990, 100, 4, "subscription"),
    "GLOW": Plan("GLOW", "Глоу", 7990, 400, 10, "product"),
    "IMPULSE": Plan("IMPULSE", "Импульс", 29990, None, 24, "product", 365),
}

_PAYMENT_PLAN = {
    "15": "BLAST", "бласт": "BLAST",
    "30": "GLOW", "глоу": "GLOW",
    "50": "IMPULSE", "импульс": "IMPULSE",
}


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


class BillingBackend:
    def __init__(self) -> None:
        if SETTINGS.backend != "production":
            raise BillingError("billing_backend: production backend is not enabled")
        try:
            from services.tg_bot_public.credits_db import CreditsDB
            from services.tg_bot_public.tbank_client import TBankClient
        except ImportError as exc:
            raise BillingError(
                "billing_backend: public bot billing package is not installed in the image"
            ) from exc
        self._db = CreditsDB(_required("CREDITS_DB_URL"))
        self._tbank = TBankClient(
            _required("TBANK_TERMINAL_KEY"),
            _required("TBANK_PASSWORD"),
            notify_url=_required("TBANK_NOTIFY_URL"),
        )

    async def init(self) -> None:
        await self._db.init()
        pool = self._db._pool_or_fail()
        async with pool.acquire() as conn:
            # The ledger entries make web reservation/refund idempotent even
            # when FastAPI or the browser retries after a lost response.
            await conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_tx_web_job_once "
                "ON transactions(tg_id, reason, context_order_id) "
                "WHERE reason IN ('web_generation_reserve', 'web_generation_refund') "
                "AND context_order_id <> ''"
            )

    async def close(self) -> None:
        await self._db.close()

    async def healthcheck(self) -> None:
        pool = self._db._pool_or_fail()
        async with pool.acquire() as conn:
            if await conn.fetchval("SELECT 1") != 1:
                raise BillingError("billing database healthcheck failed")

    async def ensure_user(self, tg_id: int, username: str = "") -> None:
        await self._db.ensure_user(int(tg_id), username)

    async def snapshot(self, tg_id: int) -> dict[str, Any]:
        tg_id = int(tg_id)
        await self.ensure_user(tg_id)
        balance = await self._db.get_balance(tg_id)
        track_balance = await self._db.get_track_balance(tg_id)
        track_unlimited = await self._db.is_track_unlimited(tg_id)
        bonuses_claimed = await self._db.count_web_subscription_bonuses(tg_id)
        tracks_used = await self._db.count_user_tracks(tg_id)
        payments = await self._db.get_payments(tg_id=tg_id, limit=100)
        confirmed = next(
            (item for item in payments if str(item.get("status") or "").upper() == "CONFIRMED"),
            None,
        )
        tier = _PAYMENT_PLAN.get(str((confirmed or {}).get("package") or "").lower(), "TRIAL")
        active_sub = await self._db.get_active_subscription(tg_id)
        latest_sub = await self._latest_subscription(tg_id)

        if tier == "TRIAL":
            total = 5
            tracks_total = 1
            plan_kind = "trial"
            billing_status = "trial"
            started_at = None
            renews_at = None
            expires_at = None
            cancel_at_period_end = False
        else:
            plan = PLANS[tier]
            total = plan.credits
            tracks_total = plan.tracks
            plan_kind = plan.kind
            billing_status = "active"
            started_at = (active_sub or latest_sub or {}).get("created_at") or (confirmed or {}).get("created_at")
            renews_at = _iso((active_sub or {}).get("next_charge_at"))
            cancel_at_period_end = bool(
                plan.kind == "subscription"
                and latest_sub
                and latest_sub.get("status") == "cancelled"
            )
            if cancel_at_period_end:
                billing_status = "canceled"
            elif latest_sub and latest_sub.get("status") == "paused":
                billing_status = "past_due"
            expires_at = None
            if plan.duration_days and started_at:
                try:
                    start = datetime.fromisoformat(str(started_at))
                    expires_at = _iso(start + timedelta(days=plan.duration_days))
                except ValueError:
                    expires_at = None

        return {
            "tier": tier,
            "creditsTotal": total,
            "creditsUsed": 0 if total is None else max(0, total - balance),
            "creditsLeft": balance,
            "tracksTotal": None if track_unlimited else max(tracks_total, tracks_used + track_balance),
            "tracksUsed": tracks_used,
            "tracksLeft": track_balance,
            "isActive": True,
            "billingStatus": billing_status,
            "cancelAtPeriodEnd": cancel_at_period_end,
            "planKind": plan_kind,
            "startedAt": started_at,
            "renewsAt": renews_at,
            "expiresAt": expires_at,
            "lastPaymentError": None,
            "bonusesClaimed": bonuses_claimed,
        }

    async def can_upload_track(self, tg_id: int, audio_hash: str) -> bool:
        if await self._db.has_track_hash(int(tg_id), audio_hash):
            return True
        if await self._db.is_track_unlimited(int(tg_id)):
            return True
        return await self._db.get_track_balance(int(tg_id)) > 0

    async def claim_bonus(self, tg_id: int) -> dict[str, Any]:
        await self._db.claim_web_subscription_bonus(int(tg_id))
        return await self.snapshot(int(tg_id))

    async def consume_track(self, tg_id: int, audio_hash: str) -> str:
        result = await self._db.consume_track_slot(int(tg_id), audio_hash)
        if result == "blocked":
            raise TrackQuotaExhausted("no unique-track credits available")
        return result

    async def reserve(self, tg_id: int, job_id: str, amount: int) -> int:
        tg_id = int(tg_id)
        amount = int(amount)
        if amount < 1:
            raise BillingError("reservation amount must be positive")
        pool = self._db._pool_or_fail()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO users (tg_id, username) VALUES ($1, '') "
                    "ON CONFLICT (tg_id) DO NOTHING",
                    tg_id,
                )
                existing = await conn.fetchval(
                    "SELECT amount FROM transactions WHERE tg_id = $1 "
                    "AND reason = 'web_generation_reserve' AND context_order_id = $2",
                    tg_id,
                    job_id,
                )
                if existing is not None:
                    return int(await conn.fetchval("SELECT credits FROM users WHERE tg_id = $1", tg_id) or 0)
                balance = int(
                    await conn.fetchval(
                        "SELECT credits FROM users WHERE tg_id = $1 FOR UPDATE", tg_id
                    )
                    or 0
                )
                if balance < amount:
                    raise InsufficientCredits(balance)
                remaining = balance - amount
                await conn.execute(
                    "UPDATE users SET credits = $1, updated_at = NOW() WHERE tg_id = $2",
                    remaining,
                    tg_id,
                )
                await conn.execute(
                    "INSERT INTO transactions "
                    "(tg_id, amount, reason, admin_note, actor, context_order_id) "
                    "VALUES ($1, $2, 'web_generation_reserve', $3, 'blast_web', $4)",
                    tg_id,
                    -amount,
                    f"web job={job_id}",
                    job_id,
                )
                return remaining

    async def refund(self, tg_id: int, job_id: str, amount: int) -> int:
        tg_id = int(tg_id)
        amount = int(amount)
        if amount < 1:
            return await self._db.get_balance(tg_id)
        # The shared ledger has a global uniqueness constraint on non-empty
        # context_order_id. Reserve and refund are two distinct transactions,
        # so their ledger identities must also be distinct.
        refund_context = f"{job_id}:refund"
        pool = self._db._pool_or_fail()
        async with pool.acquire() as conn:
            async with conn.transaction():
                existing = await conn.fetchval(
                    "SELECT amount FROM transactions WHERE tg_id = $1 "
                    "AND reason = 'web_generation_refund' AND context_order_id = $2",
                    tg_id,
                    refund_context,
                )
                if existing is not None:
                    return int(await conn.fetchval("SELECT credits FROM users WHERE tg_id = $1", tg_id) or 0)
                reserved = await conn.fetchval(
                    "SELECT amount FROM transactions WHERE tg_id = $1 "
                    "AND reason = 'web_generation_reserve' AND context_order_id = $2",
                    tg_id,
                    job_id,
                )
                if reserved is None:
                    return int(await conn.fetchval("SELECT credits FROM users WHERE tg_id = $1", tg_id) or 0)
                amount = min(amount, abs(int(reserved)))
                row = await conn.fetchrow(
                    "UPDATE users SET credits = credits + $1, updated_at = NOW() "
                    "WHERE tg_id = $2 RETURNING credits",
                    amount,
                    tg_id,
                )
                if row is None:
                    raise BillingError(f"cannot refund unknown Telegram user {tg_id}")
                await conn.execute(
                    "INSERT INTO transactions "
                    "(tg_id, amount, reason, admin_note, actor, context_order_id) "
                    "VALUES ($1, $2, 'web_generation_refund', $3, 'blast_web', $4)",
                    tg_id,
                    amount,
                    f"web job={job_id}",
                    refund_context,
                )
                return int(row["credits"])

    async def create_order(
        self,
        *,
        tg_id: int,
        package_type: str,
        email: str,
        recurrent_accepted: bool,
        idempotency_key: str,
    ) -> dict[str, str]:
        plan = PLANS.get(str(package_type or "").upper())
        if plan is None:
            raise BillingError(f"unknown package {package_type!r}")
        recurrent = plan.kind == "subscription"
        if recurrent and not recurrent_accepted:
            raise BillingError("recurrent payment consent is required for BLAST")
        clean_key = str(idempotency_key or "").strip()
        if not clean_key:
            raise PaymentInitError(
                "payment_idempotency_required",
                "payment idempotency key is required",
                status_code=422,
            )
        digest = hashlib.sha256(f"{int(tg_id)}:{clean_key}".encode("utf-8")).hexdigest()[:16]
        order_id = f"{int(tg_id)}-{plan.code.lower()}-web-{digest}"
        intent, created = await self._db.claim_web_payment_intent(
            order_id=order_id,
            tg_id=int(tg_id),
            amount_rub=plan.price_rub,
            package=plan.payment_name,
            recurrent=recurrent,
            idempotency_key=clean_key,
        )
        if (
            int(intent.get("tg_id", 0)) != int(tg_id)
            or int(intent.get("amount_rub", 0)) != plan.price_rub
            or str(intent.get("package") or "") != plan.payment_name
            or bool(intent.get("is_recurrent", False)) != recurrent
        ):
            raise PaymentInitError(
                "payment_idempotency_conflict",
                "payment idempotency key was already used for another order",
                status_code=409,
            )
        if not created:
            saved_url = str(intent.get("payment_url") or "").strip()
            if saved_url:
                return {"orderId": str(intent["order_id"]), "paymentUrl": saved_url}
            status = str(intent.get("status") or "").strip().upper()
            code = {
                "INIT_IN_PROGRESS": "payment_init_in_progress",
                "INIT_FAILED": "payment_init_failed",
                "INIT_UNKNOWN": "payment_init_unknown",
            }.get(status, "payment_init_unknown")
            status_code = 409 if status == "INIT_IN_PROGRESS" else 503
            raise PaymentInitError(
                code,
                f"payment order {intent['order_id']} has no reusable payment URL (status={status or 'UNKNOWN'})",
                status_code=status_code,
            )
        try:
            url = await self._tbank.create_payment(
                amount_rub=plan.price_rub,
                order_id=order_id,
                description=(
                    f"Подписка «{plan.payment_name}»"
                    if recurrent
                    else f"Пакет «{plan.payment_name}»"
                ),
                email=email,
                recurrent=recurrent,
                customer_key=str(int(tg_id)) if recurrent else "",
                success_url=f"{SETTINGS.app_url}/app/pricing?payment=success",
                fail_url=f"{SETTINGS.app_url}/app/pricing?payment=failed",
            )
        except Exception as exc:
            # A transport failure is ambiguous: T-Bank may have accepted Init
            # before our client lost the response.  Keep this exact order for
            # reconciliation and never create a second one implicitly.
            await self._db.mark_web_payment_init(order_id, "INIT_UNKNOWN", str(exc))
            raise PaymentInitError(
                "payment_init_unknown",
                f"T-Bank Init outcome is unknown for order {order_id}",
                status_code=503,
            ) from exc
        if not url:
            await self._db.mark_web_payment_init(
                order_id,
                "INIT_FAILED",
                "T-Bank Init did not return PaymentURL",
            )
            raise PaymentInitError(
                "payment_init_failed",
                "T-Bank Init did not return PaymentURL",
                status_code=502,
            )
        try:
            saved = await self._db.complete_web_payment_init(order_id, url)
        except Exception as exc:
            raise PaymentInitError(
                "payment_init_unknown",
                f"payment link could not be persisted for order {order_id}",
                status_code=503,
            ) from exc
        if not saved:
            raise PaymentInitError(
                "payment_init_unknown",
                f"payment order {order_id} changed before its link was persisted",
                status_code=409,
            )
        return {"orderId": order_id, "paymentUrl": url}

    async def cancel(self, tg_id: int) -> bool:
        pool = self._db._pool_or_fail()
        async with pool.acquire() as conn:
            tag = await conn.execute(
                "UPDATE subscriptions SET status = 'cancelled', cancelled_at = NOW(), updated_at = NOW() "
                "WHERE id = (SELECT id FROM subscriptions WHERE tg_id = $1 "
                "AND status IN ('active', 'paused') ORDER BY id DESC LIMIT 1)",
                int(tg_id),
            )
        return str(tag).endswith(" 1")

    async def resume(self, tg_id: int) -> bool:
        pool = self._db._pool_or_fail()
        async with pool.acquire() as conn:
            tag = await conn.execute(
                "UPDATE subscriptions SET status = 'active', cancelled_at = NULL, "
                "next_charge_at = GREATEST(next_charge_at, NOW()), updated_at = NOW() "
                "WHERE id = (SELECT id FROM subscriptions WHERE tg_id = $1 "
                "AND status = 'cancelled' AND rebill_id <> '' ORDER BY id DESC LIMIT 1)",
                int(tg_id),
            )
        return str(tag).endswith(" 1")

    async def _latest_subscription(self, tg_id: int) -> dict[str, Any] | None:
        pool = self._db._pool_or_fail()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT package, status, next_charge_at, cancelled_at, created_at FROM subscriptions "
                "WHERE tg_id = $1 ORDER BY id DESC LIMIT 1",
                int(tg_id),
            )
        return dict(row) if row else None


_BILLING: BillingBackend | None = None


def get_billing() -> BillingBackend:
    global _BILLING
    if _BILLING is None:
        _BILLING = BillingBackend()
    return _BILLING


async def close_billing() -> None:
    global _BILLING
    if _BILLING is not None:
        await _BILLING.close()
        _BILLING = None
