"""T-Bank (Tinkoff) acquiring API client."""

from __future__ import annotations

import hashlib
import hmac
import logging
from pathlib import Path
import ssl
from typing import Any, Dict, List, Optional, Tuple

import certifi
import httpx

log = logging.getLogger("tbank")

TBANK_INIT_URL = "https://securepay.tinkoff.ru/v2/Init"
TBANK_CHARGE_URL = "https://securepay.tinkoff.ru/v2/Charge"
TBANK_GET_CARD_LIST_URL = "https://securepay.tinkoff.ru/v2/GetCardList"
TBANK_CHECK_ORDER_URL = "https://securepay.tinkoff.ru/v2/CheckOrder"

_CERTS_DIR = Path(__file__).with_name("certs")
TBANK_CA_CERT_PATHS = (
    _CERTS_DIR / "russian_trusted_root_ca.pem",
    _CERTS_DIR / "russian_trusted_sub_ca.pem",
)


class TBankClient:
    def __init__(self, terminal_key: str, password: str, notify_url: str = "") -> None:
        self._terminal_key = terminal_key
        self._password = password
        self._notify_url = notify_url
        self._ssl_context = self._build_ssl_context()

    @staticmethod
    def _build_ssl_context() -> ssl.SSLContext:
        """Trust the Russian CA only for T-Bank requests.

        T-Bank's acquiring endpoint is signed by Russian Trusted CA, which is
        intentionally absent from certifi. Loading it into this dedicated
        context avoids weakening TLS validation for the bot's other clients.
        """
        context = ssl.create_default_context(cafile=certifi.where())
        missing = [str(path) for path in TBANK_CA_CERT_PATHS if not path.is_file()]
        if missing:
            raise RuntimeError(f"T-Bank CA certificate files are missing: {missing}")
        for path in TBANK_CA_CERT_PATHS:
            context.load_verify_locations(cafile=str(path))
        return context

    def _http_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=15, verify=self._ssl_context)

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
        success_url: str = "",
        fail_url: str = "",
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
            # Per T-Bank acquiring docs: for a parent recurrent payment the
            # DATA.OperationInitiatorType field must equal "1" (CIT — payment
            # initiated by the customer). Without it the terminal accepts the
            # transaction but does not save card details, so RebillId comes
            # back empty and subsequent Charge calls become impossible.
            params["DATA"] = {"OperationInitiatorType": "1"}
        if self._notify_url:
            params["NotificationURL"] = self._notify_url
        if success_url:
            params["SuccessURL"] = success_url
        if fail_url:
            params["FailURL"] = fail_url

        params["Token"] = self._make_token(params)

        async with self._http_client() as client:
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
            # Per T-Bank acquiring docs: child (recurring) charges must declare
            # DATA.OperationInitiatorType="R" (MIT COF Recurring). Without this
            # tag the Charge step rejects the request or downgrades the trans-
            # action class.
            "DATA": {"OperationInitiatorType": "R"},
        }
        if self._notify_url:
            params["NotificationURL"] = self._notify_url

        params["Token"] = self._make_token(params)

        async with self._http_client() as client:
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

        async with self._http_client() as client:
            resp = await client.post(TBANK_CHARGE_URL, json=params)
            if resp.status_code != 200:
                log.error("tbank charge failed status=%s body=%s", resp.status_code, resp.text)
                return False, f"HTTP {resp.status_code}"
            data = resp.json()
            if not data.get("Success"):
                err = f"{data.get('ErrorCode', '')}: {data.get('Message', '')} {data.get('Details', '')}"
                log.error("tbank charge error: %s", err)
                return False, err
            # T-Bank distinguishes request-level success (`Success=true` = the
            # API accepted our call) from operation-level outcome (`Status`).
            # A REJECTED Charge — e.g. insufficient funds — still returns
            # Success=true, so we MUST gate on Status before granting credits.
            status = str(data.get("Status", "")).strip().upper()
            if status not in {"CONFIRMED", "AUTHORIZED"}:
                err = (
                    f"Charge Status={status} ErrorCode={data.get('ErrorCode', '')} "
                    f"{data.get('Message', '')} {data.get('Details', '')}"
                ).strip()
                log.error("tbank charge non-final status: %s", err)
                return False, err
            log.info(
                "tbank charge ok payment_id=%s rebill_id=%s status=%s",
                payment_id, rebill_id, status,
            )
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

        async with self._http_client() as client:
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

        async with self._http_client() as client:
            resp = await client.post("https://securepay.tinkoff.ru/v2/GetState", json=params)
            if resp.status_code != 200:
                return None
            return resp.json()

    async def get_card_list(self, customer_key: str) -> List[Dict[str, Any]]:
        """Call GetCardList — saved cards of a customer, including RebillId.

        This is the only way to obtain a RebillId after the fact: GetState
        never returns it, and the notification that carries it can be lost
        (webhook downtime, 5xx, signature mismatch). The card itself stays
        bound to CustomerKey on the T-Bank side, so the key is recoverable.

        Returns [] on any error — callers treat "no cards" and "call failed"
        the same way (nothing to recover automatically).
        """
        params: Dict[str, Any] = {
            "TerminalKey": self._terminal_key,
            "CustomerKey": str(customer_key),
        }
        params["Token"] = self._make_token(params)

        async with self._http_client() as client:
            resp = await client.post(TBANK_GET_CARD_LIST_URL, json=params)
            if resp.status_code != 200:
                log.error(
                    "tbank get_card_list failed status=%s body=%s",
                    resp.status_code, resp.text,
                )
                return []
            try:
                data = resp.json()
            except Exception as e:
                log.error("tbank get_card_list bad json: %s", e)
                return []
            # Success returns a bare JSON array; errors return an object.
            if isinstance(data, dict):
                log.error(
                    "tbank get_card_list error: %s %s details=%s",
                    data.get("ErrorCode"),
                    data.get("Message"),
                    data.get("Details"),
                )
                return []
            if not isinstance(data, list):
                log.error("tbank get_card_list unexpected payload type=%s", type(data).__name__)
                return []
            return [c for c in data if isinstance(c, dict)]

    async def check_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Return the latest payment reported for an acquiring order."""
        params: Dict[str, Any] = {
            "TerminalKey": self._terminal_key,
            "OrderId": str(order_id),
        }
        params["Token"] = self._make_token(params)

        async with self._http_client() as client:
            resp = await client.post(TBANK_CHECK_ORDER_URL, json=params)
            if resp.status_code != 200:
                log.error("tbank check_order failed status=%s body=%s", resp.status_code, resp.text)
                return None
            data = resp.json()
            if not data.get("Success"):
                log.error(
                    "tbank check_order error: %s %s details=%s",
                    data.get("ErrorCode"),
                    data.get("Message"),
                    data.get("Details"),
                )
                return None
            payments = data.get("Payments", [])
            if not payments:
                return None
            latest = payments[-1]
            return latest if isinstance(latest, dict) else None

    async def find_rebill_id(self, customer_key: str) -> str:
        """Best saved RebillId for a customer, or "" if the card is not bound.

        Cards with Status="A" (active) win over inactive ones; among equals the
        most recently bound card (highest numeric CardId) wins, which is the
        card the customer paid with last. An empty result means the payment
        never bound a card — SBP/QR, T-Pay or a card that declined binding —
        and no amount of retrying will produce a key.
        """
        cards = await self.get_card_list(customer_key)

        def _card_id(card: Dict[str, Any]) -> int:
            try:
                return int(str(card.get("CardId", "0")).strip() or 0)
            except ValueError:
                return 0

        usable = [c for c in cards if str(c.get("RebillId", "") or "").strip()]
        if not usable:
            log.warning(
                "tbank find_rebill_id: no bound card with RebillId customer_key=%s cards=%s",
                customer_key, len(cards),
            )
            return ""
        usable.sort(
            key=lambda c: (str(c.get("Status", "")).strip().upper() == "A", _card_id(c)),
        )
        return str(usable[-1].get("RebillId", "")).strip()

    def verify_notification(self, data: Dict[str, Any]) -> bool:
        """Verify Token from T-Bank webhook notification."""
        received_token = data.get("Token", "")
        if not received_token:
            return False
        expected = self._make_token(data)
        return hmac.compare_digest(received_token, expected)
