"""Lightweight admin web panel — runs as a background asyncio task inside the bot process."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import uvicorn
from fastapi import FastAPI, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
if TYPE_CHECKING:
    from .config import Settings
    from .credits_db import CreditsDB
    from .state_store import StateStore
    from .tbank_client import TBankClient

log = logging.getLogger("admin_panel")

# ── Readable stage names ─────────────────────────────────────────────────

_STAGE_LABELS = {
    "IDLE": "Завершено",
    "WAIT_START": "Ожидает старта",
    "WAIT_SUBSCRIPTION": "Подписка на канал",
    "WAIT_AUDIO": "Загрузка аудио",
    "WAIT_LYRICS_CHOICE": "Выбор текста",
    "WAIT_LYRICS_TEXT": "Ввод текста",
    "WAIT_FRAGMENT_CHOICE": "Выбор фрагмента",
    "WAIT_FRAGMENT_TEXT": "Ввод фрагмента",
    "WAIT_CONFIRM_TEXT": "Подтверждение текста",
    "WAIT_SUBTITLES_MODE": "Выбор субтитров",
    "WAIT_CONFIRM_MODE": "Подтверждение режима",
    "WAIT_VERSIONS": "Выбор версий",
    "WAIT_CONFIRM": "Подтверждение генерации",
    "PROCESSING": "Генерация",
    "WAIT_NEXT": "Ожидает следующего",
    "RATE_VIDEO": "Оценка видео #1",
    "FEEDBACK_LOW": "Фидбек (низкая оценка)",
    "SALES_PITCH": "Питч",
    "PACKAGES_OFFER": "Предложение пакетов",
    "PACKAGE_DETAILS": "Детали пакета",
    "ALL_PACKAGES": "Все пакеты",
    "PACKAGE_INFO": "Инфо о пакете",
    "WHY_NOT": "Почему неактуально",
    "NOT_ACTUAL_REASON": "Причина отказа",
    "CASES_TECH": "Кейсы и технология",
    "TRY_FULL": "Попробовать полностью",
    "REFERRAL_ASK": "Реферал",
    "WAIT_REFERRAL_TAG": "Ввод тега друга",
    "WAITING_REFERRAL": "Ожидание друга",
    "RATE_VIDEO_2": "Оценка видео #2",
    "FEEDBACK_LOW_2": "Фидбек #2",
    "LAST_STEP_FORM": "Последний шаг (форма)",
    "POST_SURVEY": "После опроса",
    "KEEP_IN_TOUCH": "На связи",
    "REMIND_RELEASE": "Напоминание о релизе",
    "NO_FRIENDS_FORM": "Форма (нет друзей)",
}

_EVENT_LABELS = {
    "start": "Старт бота",
    "subscription_ok": "Подписка подтверждена",
    "audio_uploaded": "Аудио загружено",
    "generation_started": "Генерация запущена",
    "generation_done": "Генерация завершена",
    "rate_video": "Оценка видео",
    "sales_pitch": "Просмотр питча",
    "view_packages": "Просмотр пакетов",
    "select_package": "Выбор пакета",
    "purchase_intent": "Заявка на покупку",
    "referral_sent": "Реферал отправлен",
    "referral_matched": "Реферал сработал",
    "survey_opened": "Открыл форму",
    "survey_done": "Прошёл форму",
    "keep_in_touch": "На связи",
    "reminder_sent": "Напоминание отправлено",
    "no_credits": "Нет кредитов",
}

# ── HTML templates (inline to keep it self-contained) ────────────────────

_BASE_HEAD = """
<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Blast Admin</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 1rem; background: #f8f9fa; }
  h1, h2, h3 { color: #333; }
  table { border-collapse: collapse; width: 100%; margin: 1rem 0; }
  th, td { border: 1px solid #ddd; padding: 8px 12px; text-align: left; }
  th { background: #e9ecef; }
  tr:nth-child(even) { background: #f8f9fa; }
  a { color: #0066cc; text-decoration: none; }
  a:hover { text-decoration: underline; }
  .nav { margin-bottom: 1.5rem; }
  .nav a { margin-right: 1rem; font-weight: 600; }
  form { display: inline; }
  input[type=number], input[type=text] { padding: 4px 8px; width: 80px; }
  button { padding: 4px 12px; cursor: pointer; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.85em; }
  .badge-ok { background: #d4edda; color: #155724; }
  .badge-zero { background: #f8d7da; color: #721c24; }
  .badge-stage { background: #cce5ff; color: #004085; }
  .funnel { display: flex; flex-wrap: wrap; gap: 8px; margin: 1rem 0; }
  .funnel-item { background: #fff; border: 1px solid #ddd; border-radius: 6px; padding: 8px 14px; text-align: center; }
  .funnel-item .count { font-size: 1.4em; font-weight: bold; color: #333; }
  .funnel-item .label { font-size: 0.8em; color: #666; }
</style></head><body>
<div class="nav">
  <a href="/admin/">Dashboard</a>
  <a href="/admin/users">Users</a>
  <a href="/admin/activity">Activity</a>
  <a href="/admin/transactions">Transactions</a>
  <a href="/admin/payments">Payments</a>
</div>
"""
_BASE_FOOT = "</body></html>"


def _page(title: str, body: str) -> str:
    return f"{_BASE_HEAD}<h1>{title}</h1>{body}{_BASE_FOOT}"


def _stage_label(stage: str) -> str:
    return _STAGE_LABELS.get(stage, stage)


def _event_label(event: str) -> str:
    return _EVENT_LABELS.get(event, event)


def build_app(
    credits_db: "CreditsDB",
    state_store: "StateStore",
    settings: "Settings",
    tbank_client: "TBankClient | None" = None,
    bot_ref: "list | None" = None,
) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None)
    security = HTTPBasic()

    def _check_auth(creds: HTTPBasicCredentials = Depends(security)) -> str:
        if creds.password != settings.admin_panel_password:
            raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})
        return creds.username

    # ── Dashboard ─────────────────────────────────────────────────────

    @app.get("/admin/", response_class=HTMLResponse)
    async def dashboard(_user: str = Depends(_check_auth)) -> str:
        total = await credits_db.count_users()
        users = await credits_db.list_users(limit=10)

        # Funnel summary from Redis
        all_states = await state_store.list_all_states()
        stage_counts: dict[str, int] = {}
        for s in all_states:
            stage = s.stage
            stage_counts[stage] = stage_counts.get(stage, 0) + 1

        funnel_html = ""
        for stage, cnt in sorted(stage_counts.items(), key=lambda x: -x[1]):
            label = _stage_label(stage)
            funnel_html += f'<div class="funnel-item"><div class="count">{cnt}</div><div class="label">{label}</div></div>'

        # Funnel summary from activity log
        activity_funnel = await credits_db.funnel_summary()
        af_html = ""
        for row in activity_funnel:
            label = _event_label(row["event"])
            af_html += f'<div class="funnel-item"><div class="count">{row["count"]}</div><div class="label">{label}</div></div>'

        rows = ""
        for u in users:
            badge = "badge-ok" if u["credits"] > 0 else "badge-zero"
            uname = f"@{u['username']}" if u["username"] else str(u["tg_id"])
            rows += (
                f"<tr><td><a href='/admin/users/{u['tg_id']}'>{uname}</a></td>"
                f"<td>{u['tg_id']}</td>"
                f"<td><span class='badge {badge}'>{u['credits']}</span></td>"
                f"<td>{u['updated_at']}</td></tr>"
            )

        # Recent activity
        recent = await credits_db.get_activity(limit=10)
        act_rows = ""
        for a in recent:
            act_rows += (
                f"<tr><td><a href='/admin/users/{a['tg_id']}'>{a['tg_id']}</a></td>"
                f"<td>{_event_label(a['event'])}</td>"
                f"<td>{a['detail']}</td>"
                f"<td>{a['created_at']}</td></tr>"
            )

        body = f"""
        <p>Total users: <strong>{total}</strong></p>

        <h2>Воронка (текущий этап)</h2>
        <div class="funnel">{funnel_html if funnel_html else '<p>Нет данных</p>'}</div>

        <h2>Воронка (последнее действие)</h2>
        <div class="funnel">{af_html if af_html else '<p>Нет данных</p>'}</div>

        <h2>Последние пользователи</h2>
        <table><tr><th>Username</th><th>tg_id</th><th>Credits</th><th>Updated</th></tr>
        {rows}</table>

        <h2>Последние действия</h2>
        <table><tr><th>tg_id</th><th>Событие</th><th>Детали</th><th>Дата</th></tr>
        {act_rows}</table>
        """
        return _page("Blast Admin", body)

    # ── Users list ────────────────────────────────────────────────────

    @app.get("/admin/users", response_class=HTMLResponse)
    async def users_list(request: Request, _user: str = Depends(_check_auth)) -> str:
        page = int(request.query_params.get("page", "1"))
        per_page = 50
        offset = (page - 1) * per_page
        users = await credits_db.list_users(limit=per_page, offset=offset)
        total = await credits_db.count_users()

        # Get current stages from Redis
        all_states = await state_store.list_all_states()
        stages_map = {s.chat_id: s.stage for s in all_states}

        rows = ""
        for u in users:
            badge = "badge-ok" if u["credits"] > 0 else "badge-zero"
            uname = f"@{u['username']}" if u["username"] else str(u["tg_id"])
            stage = stages_map.get(u["tg_id"], "—")
            stage_lbl = _stage_label(stage) if stage != "—" else "—"
            rows += (
                f"<tr><td><a href='/admin/users/{u['tg_id']}'>{uname}</a></td>"
                f"<td>{u['tg_id']}</td>"
                f"<td><span class='badge {badge}'>{u['credits']}</span></td>"
                f"<td><span class='badge badge-stage'>{stage_lbl}</span></td>"
                f"<td>{u['created_at']}</td>"
                f"<td>{u['updated_at']}</td></tr>"
            )
        pages = max(1, (total + per_page - 1) // per_page)
        nav = ""
        if page > 1:
            nav += f"<a href='?page={page - 1}'>&laquo; Prev</a> "
        nav += f"Page {page}/{pages} "
        if page < pages:
            nav += f"<a href='?page={page + 1}'>Next &raquo;</a>"

        body = f"""
        <p>Total: {total}</p>
        <table><tr><th>Username</th><th>tg_id</th><th>Credits</th><th>Этап</th><th>Created</th><th>Updated</th></tr>
        {rows}</table>
        <p>{nav}</p>
        """
        return _page("Users", body)

    # ── User detail ───────────────────────────────────────────────────

    @app.get("/admin/users/{tg_id}", response_class=HTMLResponse)
    async def user_detail(tg_id: int, _user: str = Depends(_check_auth)) -> str:
        user = await credits_db.get_user(tg_id)
        if not user:
            raise HTTPException(404, "User not found")
        uname = f"@{user['username']}" if user["username"] else str(tg_id)

        # Current stage from Redis
        st = await state_store.get(tg_id)
        stage_lbl = _stage_label(st.stage) if st else "—"

        txs = await credits_db.get_transactions(tg_id, limit=50)
        tx_rows = ""
        for t in txs:
            sign = "+" if t["amount"] > 0 else ""
            tx_rows += (
                f"<tr><td>{t['id']}</td><td>{sign}{t['amount']}</td>"
                f"<td>{t['reason']}</td><td>{t['admin_note']}</td><td>{t['created_at']}</td></tr>"
            )

        # Activity log
        acts = await credits_db.get_activity(tg_id, limit=50)
        act_rows = ""
        for a in acts:
            act_rows += (
                f"<tr><td>{a['id']}</td><td>{_event_label(a['event'])}</td>"
                f"<td>{a['detail']}</td><td>{a['created_at']}</td></tr>"
            )

        body = f"""
        <h2>{uname} (id: {tg_id})</h2>
        <p>Credits: <strong>{user['credits']}</strong> |
           Этап: <span class="badge badge-stage">{stage_lbl}</span> |
           Created: {user['created_at']} | Updated: {user['updated_at']}</p>
        <h3>Выдать кредиты</h3>
        <form method="post" action="/admin/users/{tg_id}/credits">
          <input type="number" name="amount" value="0" min="-1000" max="10000">
          <input type="text" name="reason" placeholder="reason" style="width:150px">
          <button type="submit">Add credits</button>
        </form>

        <h3>Действия</h3>
        <table><tr><th>#</th><th>Событие</th><th>Детали</th><th>Дата</th></tr>
        {act_rows if act_rows else '<tr><td colspan="4">Нет данных</td></tr>'}</table>

        <h3>Транзакции</h3>
        <table><tr><th>#</th><th>Amount</th><th>Reason</th><th>Note</th><th>Date</th></tr>
        {tx_rows}</table>
        """
        return _page(f"User {uname}", body)

    @app.post("/admin/users/{tg_id}/credits")
    async def user_add_credits(tg_id: int, amount: int = Form(...), reason: str = Form("admin_panel"), _user: str = Depends(_check_auth)) -> RedirectResponse:
        await credits_db.add_credits(tg_id, amount, reason, admin_note=f"via panel by {_user}")
        return RedirectResponse(f"/admin/users/{tg_id}", status_code=303)

    # ── Activity log ──────────────────────────────────────────────────

    @app.get("/admin/activity", response_class=HTMLResponse)
    async def activity_list(request: Request, _user: str = Depends(_check_auth)) -> str:
        page = int(request.query_params.get("page", "1"))
        per_page = 50
        offset = (page - 1) * per_page
        acts = await credits_db.get_activity(limit=per_page, offset=offset)
        rows = ""
        for a in acts:
            rows += (
                f"<tr><td>{a['id']}</td>"
                f"<td><a href='/admin/users/{a['tg_id']}'>{a['tg_id']}</a></td>"
                f"<td>{_event_label(a['event'])}</td>"
                f"<td>{a['detail']}</td>"
                f"<td>{a['created_at']}</td></tr>"
            )
        body = f"""
        <table><tr><th>#</th><th>tg_id</th><th>Событие</th><th>Детали</th><th>Дата</th></tr>
        {rows}</table>
        <p><a href="?page={page + 1}">Next page &raquo;</a></p>
        """
        return _page("Activity Log", body)

    # ── Transactions ──────────────────────────────────────────────────

    @app.get("/admin/transactions", response_class=HTMLResponse)
    async def transactions_list(request: Request, _user: str = Depends(_check_auth)) -> str:
        page = int(request.query_params.get("page", "1"))
        per_page = 50
        offset = (page - 1) * per_page
        txs = await credits_db.get_transactions(limit=per_page, offset=offset)
        rows = ""
        for t in txs:
            sign = "+" if t["amount"] > 0 else ""
            rows += (
                f"<tr><td>{t['id']}</td><td>{t['tg_id']}</td><td>{sign}{t['amount']}</td>"
                f"<td>{t['reason']}</td><td>{t['admin_note']}</td><td>{t['created_at']}</td></tr>"
            )
        body = f"""
        <table><tr><th>#</th><th>tg_id</th><th>Amount</th><th>Reason</th><th>Note</th><th>Date</th></tr>
        {rows}</table>
        <p><a href="?page={page + 1}">Next page &raquo;</a></p>
        """
        return _page("Transactions", body)

    # ── Payments list ────────────────────────────────────────────────

    @app.get("/admin/payments", response_class=HTMLResponse)
    async def payments_list(request: Request, _user: str = Depends(_check_auth)) -> str:
        page = int(request.query_params.get("page", "1"))
        per_page = 50
        offset = (page - 1) * per_page
        pays = await credits_db.get_payments(limit=per_page, offset=offset)
        rows = ""
        for p in pays:
            status_cls = "badge-ok" if p["status"] == "CONFIRMED" else "badge-zero"
            rows += (
                f"<tr><td>{p['id']}</td>"
                f"<td><a href='/admin/users/{p['tg_id']}'>{p['tg_id']}</a></td>"
                f"<td>{p['order_id']}</td>"
                f"<td>{p['amount_rub']}₽</td>"
                f"<td>{p['package']}</td>"
                f"<td><span class='badge {status_cls}'>{p['status']}</span></td>"
                f"<td>{p['created_at']}</td></tr>"
            )
        body = f"""
        <table><tr><th>#</th><th>tg_id</th><th>Order</th><th>Amount</th><th>Package</th><th>Status</th><th>Date</th></tr>
        {rows}</table>
        <p><a href="?page={page + 1}">Next page &raquo;</a></p>
        """
        return _page("Payments", body)

    # ── T-Bank webhook (no auth — called by T-Bank servers) ───────

    @app.post("/api/tbank/notify")
    async def tbank_notify(request: Request):
        from fastapi.responses import PlainTextResponse
        try:
            data = await request.json()
        except Exception:
            return PlainTextResponse("OK", status_code=200)

        log.info("tbank notify: %s", data)

        # Verify token — reject if no client configured or invalid signature
        if not tbank_client:
            log.warning("tbank notify: no tbank_client configured, rejecting")
            return PlainTextResponse("OK", status_code=200)
        if not tbank_client.verify_notification(data):
            log.warning("tbank notify: invalid token")
            return PlainTextResponse("OK", status_code=200)

        order_id = str(data.get("OrderId", ""))
        status = str(data.get("Status", ""))
        payment_id = str(data.get("PaymentId", ""))

        if not order_id:
            return PlainTextResponse("OK", status_code=200)

        # Dedup check
        if payment_id and await credits_db.is_payment_processed(payment_id, status):
            log.info("tbank notify: duplicate payment_id=%s status=%s", payment_id, status)
            return PlainTextResponse("OK", status_code=200)

        # Update payment status
        await credits_db.update_payment_status(order_id, status, payment_id)

        # Get payment info for notifications
        payment = await credits_db.get_payment(order_id)
        tg_id = payment["tg_id"] if payment else 0
        pkg = payment["package"] if payment else "?"
        amount_rub = payment["amount_rub"] if payment else 0

        # Notify manager about every status change
        if bot_ref and bot_ref[0] and settings.manager_chat_id and payment:
            status_labels = {
                "CONFIRMED": "Оплачено",
                "AUTHORIZED": "Авторизовано",
                "REJECTED": "Отклонено",
                "REFUNDED": "Возврат",
                "REVERSED": "Отмена",
                "NEW": "Создан",
            }
            status_label = status_labels.get(status, status)
            emoji = {"CONFIRMED": "✅", "REJECTED": "❌", "REFUNDED": "🔄", "REVERSED": "🔄"}.get(status, "📋")
            try:
                user_info = await credits_db.get_user(tg_id)
                uname = f"@{user_info['username']}" if user_info and user_info.get("username") else str(tg_id)
                await bot_ref[0].send_message(
                    settings.manager_chat_id,
                    f"{emoji} Статус оплаты: {status_label}\n\n"
                    f"Пользователь: {uname}\n"
                    f"Пакет: {pkg}\n"
                    f"Сумма: {amount_rub}₽\n"
                    f"Order: {order_id}",
                )
            except Exception as e:
                log.warning("tbank notify: failed to notify manager: %s", e)

        # On confirmed payment — grant credits & redirect to generation
        if status == "CONFIRMED" and payment:
            # Credits based on package
            credits_map = {
                "Триал": 5,
                "Бласт": 15,
                "Глоу": 30,
                "Импульс": 50,
            }
            credits_to_add = credits_map.get(pkg, 5)

            await credits_db.add_credits(
                tg_id, credits_to_add,
                reason="payment",
                admin_note=f"pkg={pkg} order={order_id} amount={amount_rub}₽",
            )
            await credits_db.log_event(tg_id, "payment_confirmed", f"{pkg} — {amount_rub}₽")
            log.info("payment confirmed tg_id=%s pkg=%s credits=+%s", tg_id, pkg, credits_to_add)

            # Notify user + move to generation flow
            if bot_ref and bot_ref[0]:
                try:
                    from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
                    bal = await credits_db.get_balance(tg_id)
                    await bot_ref[0].send_message(
                        tg_id,
                        f"✅ Оплата прошла! Пакет «{pkg}» активирован.\n"
                        f"Начислено {credits_to_add} генераций.\n\n"
                        f"Доступно генераций: {bal}\n\n"
                        "Отправь трек аудио-файлом, и я соберу клип.",
                        reply_markup=ReplyKeyboardMarkup(
                            keyboard=[[KeyboardButton(text="Отправить трек")]],
                            resize_keyboard=True,
                        ),
                    )
                    await bot_ref[0].send_message(tg_id, "Пришли аудио в формате mp3.")
                    # Move user state to WAIT_AUDIO
                    await state_store.reset_to_wait_audio(tg_id)
                except Exception as e:
                    log.warning("tbank notify: failed to notify user %s: %s", tg_id, e)

        return PlainTextResponse("OK", status_code=200)

    return app


async def start_admin_panel(
    credits_db: "CreditsDB",
    state_store: "StateStore",
    settings: "Settings",
    tbank_client: "TBankClient | None" = None,
    bot_ref: "list | None" = None,
) -> None:
    """Run the admin panel as an async background task."""
    app = build_app(credits_db, state_store, settings, tbank_client, bot_ref)
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=settings.admin_panel_port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    log.info("admin panel starting on port %s", settings.admin_panel_port)
    await server.serve()
