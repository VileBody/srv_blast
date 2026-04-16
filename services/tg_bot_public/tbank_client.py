"""T-Bank (Tinkoff) acquiring API client."""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any, Dict, Optional, Tuple

import httpx

log = logging.getLogger("tbank")

TBANK_INIT_URL = "https://securepay.tinkoff.ru/v2/Init"
TBANK_CHARGE_URL = "https://securepay.tinkoff.ru/v2/Charge"


class TBankClient:
    def __init__(self, terminal_key: str, password: str, notify_url: str = "") -> None:
        self._terminal_key = terminal_key
        self._password = password
        self._notify_url = notify_url

    def _make_token(self, params: Dict[str, Any]) -> str:
        """Generate Token per T-Bank spec: add Password, sort by key, concat values, SHA-256."""
        # Token is computed only from flat string values, skip nested objects
        token_data: Dict[str, str] = {}
        for k, v in params.items():
            if k == "Token":
                continue
            if isinstance(v, (dict, list)):
                continue
            if isinstance(v, bool):
                token_data[k] = "true" if v else "false"
            else:
                token_data[k] = str(v)
        token_data["Password"] = self._password
        sorted_values = "".join(v for _, v in sorted(token_data.items()))
        return hashlib.sha256(sorted_values.encode()).hexdigest()

    def _make_receipt(self, description: str, amount_kop: int, email: str = "") -> Dict[str, Any]:
        """Build receipt object for Init requests."""
        return {
            "Email": email or "noreply@blast808.com",
            "Taxation": "usn_income",
            "Items": [
                {
                    "Name": description[:128],
                    "Price": amount_kop,
                    "Quantity": 1.0,
                    "Amount": amount_kop,
                    "Tax": "none",
                    "PaymentObject": "service",
                    "PaymentMethod": "full_payment",
                },
            ],
        }

    async def create_payment(
        self,
        amount_rub: int,
        order_id: str,
        description: str = "Оплата пакета Blast",
        email: str = "",
        recurrent: bool = False,
        customer_key: str = "",
    ) -> Optional[str]:
        """Call Init endpoint. Returns PaymentURL or None on error.

        If recurrent=True, sets Recurrent=Y and CustomerKey so the card
        is saved for future Charge calls.
        """
        amount_kop = amount_rub * 100
        receipt = self._make_receipt(description, amount_kop, email)

        params: Dict[str, Any] = {
            "TerminalKey": self._terminal_key,
            "Amount": amount_kop,
            "OrderId": order_id,
            "Description": description[:250],
            "Receipt": receipt,
        }
        if recurrent and customer_key:
            params["Recurrent"] = "Y"
            params["CustomerKey"] = customer_key
        if self._notify_url:
            params["NotificationURL"] = self._notify_url

        params["Token"] = self._make_token(params)

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(TBANK_INIT_URL, json=params)
            if resp.status_code != 200:
                log.error("tbank init failed status=%s body=%s", resp.status_code, resp.text)
                return None
            data = resp.json()
            if not data.get("Success"):
                log.error(
                    "tbank init error: %s %s details=%s",
                    data.get("ErrorCode"),
                    data.get("Message"),
                    data.get("Details"),
                )
                return None
            url = data.get("PaymentURL")
            log.info("tbank payment created order=%s url=%s recurrent=%s", order_id, url, recurrent)
            return url

    async def init_for_charge(
        self,
        amount_rub: int,
        order_id: str,
        description: str = "Подписка Blast — ежемесячное списание",
        email: str = "",
    ) -> Optional[str]:
        """Init a payment for subsequent Charge (no PaymentURL needed).

        Returns PaymentId or None on error.
        """
        amount_kop = amount_rub * 100
        receipt = self._make_receipt(description, amount_kop, email)

        params: Dict[str, Any] = {
            "TerminalKey": self._terminal_key,
            "Amount": amount_kop,
            "OrderId": order_id,
            "Description": description[:250],
            "Receipt": receipt,
        }
        if self._notify_url:
            params["NotificationURL"] = self._notify_url

        params["Token"] = self._make_token(params)

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(TBANK_INIT_URL, json=params)
            if resp.status_code != 200:
                log.error("tbank init_for_charge failed status=%s body=%s", resp.status_code, resp.text)
                return None
            data = resp.json()
            if not data.get("Success"):
                log.error(
                    "tbank init_for_charge error: %s %s details=%s",
                    data.get("ErrorCode"),
                    data.get("Message"),
                    data.get("Details"),
                )
                return None
            payment_id = str(data.get("PaymentId", ""))
            log.info("tbank init_for_charge order=%s payment_id=%s", order_id, payment_id)
            return payment_id

    async def charge(
        self,
        payment_id: str,
        rebill_id: str,
    ) -> Tuple[bool, str]:
        """Charge a saved card using RebillId.

        Returns (success, error_message).
        """
        params: Dict[str, Any] = {
            "TerminalKey": self._terminal_key,
            "PaymentId": payment_id,
            "RebillId": rebill_id,
        }
        params["Token"] = self._make_token(params)

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(TBANK_CHARGE_URL, json=params)
            if resp.status_code != 200:
                log.error("tbank charge failed status=%s body=%s", resp.status_code, resp.text)
                return False, f"HTTP {resp.status_code}"
            data = resp.json()
            if not data.get("Success"):
                err = f"{data.get('ErrorCode', '')}: {data.get('Message', '')} {data.get('Details', '')}"
                log.error("tbank charge error: %s", err)
                return False, err
            log.info("tbank charge ok payment_id=%s rebill_id=%s status=%s",
                     payment_id, rebill_id, data.get("Status"))
            return True, ""

    async def cancel_payment(self, payment_id: str, amount_kop: int = 0) -> bool:
        """Call Cancel endpoint. Returns True on success."""
        params: Dict[str, Any] = {
            "TerminalKey": self._terminal_key,
            "PaymentId": payment_id,
        }
        if amount_kop > 0:
            params["Amount"] = amount_kop

        params["Token"] = self._make_token(params)

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post("https://securepay.tinkoff.ru/v2/Cancel", json=params)
            if resp.status_code != 200:
                log.error("tbank cancel failed status=%s body=%s", resp.status_code, resp.text)
                return False
            data = resp.json()
            if not data.get("Success"):
                log.error(
                    "tbank cancel error: %s %s details=%s",
                    data.get("ErrorCode"),
                    data.get("Message"),
                    data.get("Details"),
                )
                return False
            log.info("tbank payment cancelled payment_id=%s", payment_id)
            return True

    async def get_state(self, payment_id: str) -> Optional[Dict[str, Any]]:
        """Call GetState to check payment status."""
        params: Dict[str, Any] = {
            "TerminalKey": self._terminal_key,
            "PaymentId": payment_id,
        }
        params["Token"] = self._make_token(params)

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post("https://securepay.tinkoff.ru/v2/GetState", json=params)
            if resp.status_code != 200:
                return None
            return resp.json()

    def verify_notification(self, data: Dict[str, Any]) -> bool:
        """Verify Token from T-Bank webhook notification."""
        received_token = data.get("Token", "")
        if not received_token:
            return False
        expected = self._make_token(data)
        return hmac.compare_digest(received_token, expected)
