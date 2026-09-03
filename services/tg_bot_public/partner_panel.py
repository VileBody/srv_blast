"""Partner cabinet — read-only funnel/sales analytics + a self-serve UTM-link
builder for traffic partners.

Mounted onto the same FastAPI app/process as the admin panel (see
``admin_panel.build_app``) under ``/partner/*``, but with its own
cookie-session auth so a partner login can never double as an admin
credential. Partners see only their own attributed users (enforced in the
credits_db query layer, not just in the UI) and cannot act on them — no
credit grants, no messaging, no resets. Analysis only.
"""

from __future__ import annotations

import html as html_mod
import json
import logging
import secrets
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from urllib.parse import quote as url_quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

if TYPE_CHECKING:
    from .config import Settings
    from .credits_db import CreditsDB
    from .state_store import RedisChatStateStore as StateStore

log = logging.getLogger("partner_panel")

_COOKIE_NAME = "partner_sid"
_SESSION_PREFIX = "partner:session:"
_SESSION_TTL_S = 30 * 24 * 3600
_LINK_PREFIX = "p_"

_FUNNEL_ORDER = [
    "start", "subscription_ok", "audio_uploaded", "generation_started",
    "generation_done", "rate_video", "view_packages", "purchase_intent",
    "payment_confirmed", "subscription_charged",
]
_FUNNEL_LABELS = {
    "start": "Старт бота",
    "subscription_ok": "Подписка на канал",
    "audio_uploaded": "Аудио загружено",
    "generation_started": "Генерация запущена",
    "generation_done": "Видео готово",
    "rate_video": "Оценка видео",
    "view_packages": "Смотрел пакеты",
    "purchase_intent": "Заявка на покупку",
    "payment_confirmed": "Оплата подтверждена",
    "subscription_charged": "Подписка списана",
}
_FUNNEL_COLORS = [
    "#8b6fe6", "#7a63d6", "#38bdf8", "#34d399", "#22c19d",
    "#fbbf24", "#fb923c", "#f97316", "#34d399", "#22c19d",
]
_RATING_LABELS = {"low": "1-4", "mid_low": "5-6", "high": "7-10"}
_RATING_COLORS = {"low": "#fb7185", "mid_low": "#fbbf24", "high": "#34d399"}

_JOB_STATUS_LABELS = {
    "queued": "В очереди",
    "running": "В процессе",
    "build": "Сборка",
    "render": "Рендер",
    "succeeded": "Готово",
    "failed": "Ошибка",
    "cancelled": "Отменено",
}


# ── Theme (mirrors services/tg_bot_public/admin_panel.py _BASE_HEAD tokens
# so /admin and /partner read as one product; duplicated rather than
# imported to keep this module a self-contained, independently testable
# surface). ──────────────────────────────────────────────────────────────

_PARTNER_STYLE = """
@font-face { font-family:'Point'; src:url('/admin/static/fonts/Point-Regular.ttf') format('truetype'); font-weight:400; font-style:normal; font-display:swap; }
@font-face { font-family:'Point'; src:url('/admin/static/fonts/Point-Book.ttf') format('truetype'); font-weight:350; font-style:normal; font-display:swap; }
@font-face { font-family:'Point'; src:url('/admin/static/fonts/PointBold.ttf') format('truetype'); font-weight:700; font-style:normal; font-display:swap; }
@font-face { font-family:'Point'; src:url('/admin/static/fonts/Point-SemiBold.ttf') format('truetype'); font-weight:600; font-style:normal; font-display:swap; }

:root {
  --bg: #05010f; --surface: #120b26; --surface-2: #150f25;
  --border: rgba(139,111,230,.16); --border-strong: rgba(139,111,230,.34);
  --text: #f6f5fd; --text-70: rgba(246,245,253,.72); --text-50: rgba(246,245,253,.52); --text-35: rgba(246,245,253,.35);
  --g-start: #8b6fe6; --g-end: #5f42b9; --accent: #8b6fe6; --accent-soft: rgba(139,111,230,.14);
  --ok: #34d399; --ok-bg: rgba(52,211,153,.14);
  --warn: #fbbf24; --warn-bg: rgba(251,191,36,.14);
  --danger: #fb7185; --danger-bg: rgba(251,113,133,.14);
  --info: #38bdf8; --info-bg: rgba(56,189,248,.14);
  --r-card: 16px; --r-btn: 10px; --r-input: 8px; --r-badge: 6px;
  --shadow: 0 12px 32px rgba(5,1,15,.45);
}
* { box-sizing: border-box; }
html { color-scheme: dark; }
body { font-family: 'Point', system-ui, sans-serif; max-width: 1200px; margin: 0 auto; padding: 0 1.25rem 2.5rem; background: var(--bg); color: var(--text); -webkit-font-smoothing: antialiased; }
h1 { margin: 1.75rem 0 1.1rem; font-weight: 700; letter-spacing: -0.01em; }
h2 { margin: 1.6rem 0 0.6rem; font-weight: 600; font-size: 1.15em; }
h3 { margin: 1.2rem 0 0.5rem; color: var(--text-70); font-weight: 600; font-size: 1em; }
p { color: var(--text-70); }
code { font-family: ui-monospace, monospace; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

@keyframes fadeUp { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
@media (prefers-reduced-motion: no-preference) {
  .card, .stat-tile { animation: fadeUp .45s ease both; }
  .header a, button, .btn, tr, input, .copy-btn { transition: background .16s ease, color .16s ease, transform .12s ease, opacity .16s ease, border-color .16s ease; }
}

.header { background: var(--surface-2); padding: 0.85rem 1.4rem; margin: 0 -1.25rem; display: flex; align-items: center; flex-wrap: wrap; gap: 0.35rem; border-radius: 0 0 var(--r-card) var(--r-card); border-bottom: 1px solid var(--border); }
.header .brand { font-weight: 700; font-size: 1.15em; margin-right: 1.25rem; text-decoration: none; background: linear-gradient(90deg, var(--g-start), var(--g-end)); -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent; }
.header a { color: var(--text-70); text-decoration: none; padding: 6px 11px; border-radius: 999px; font-size: 0.85em; }
.header a:hover { background: var(--accent-soft); color: var(--text); }
.header a.active { background: var(--accent-soft); color: var(--text); box-shadow: inset 0 0 0 1px var(--border-strong); }
.header .spacer { margin-left: auto; }
.header .partner-name { color: var(--text-50); font-size: 0.85em; padding: 6px 4px; }

.card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--r-card); padding: 1.3rem 1.5rem; margin: 1rem 0; box-shadow: var(--shadow); }

.stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 0.5rem 0 1rem; }
.stat-tile { background: var(--surface); border: 1px solid var(--border); border-radius: var(--r-card); padding: 1rem 1.2rem; }
.stat-tile .stat-label { color: var(--text-50); font-size: 0.78em; text-transform: uppercase; letter-spacing: .04em; margin-bottom: 6px; }
.stat-tile .stat-value { font-size: 1.7em; font-weight: 700; }
.stat-tile .stat-sub { color: var(--text-50); font-size: 0.8em; margin-top: 4px; }
.stat-tile.accent .stat-value { background: linear-gradient(90deg, var(--g-start), var(--g-end)); -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent; }

.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; margin: 0.5rem 0; }
th, td { border-bottom: 1px solid var(--border); padding: 9px 12px; text-align: left; font-size: 0.9em; }
th { color: var(--text-50); font-weight: 600; text-transform: uppercase; font-size: 0.72em; letter-spacing: .04em; border-bottom: 1px solid var(--border-strong); }
tr:hover td { background: rgba(139,111,230,.06); }

form { display: inline; }
input[type=text], input[type=password], input[type=number] { padding: 9px 12px; border: 1px solid var(--border); border-radius: var(--r-input); font-size: 0.9em; background: var(--surface); color: var(--text); font-family: inherit; }
input::placeholder { color: var(--text-35); }
input:focus-visible, button:focus-visible, a:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
button, .btn { padding: 9px 18px; cursor: pointer; border: none; border-radius: var(--r-btn); font-size: 0.9em; font-family: inherit; font-weight: 500; background: linear-gradient(90deg, var(--g-start), var(--g-end)); color: #fff; }
button:hover, .btn:hover { opacity: .88; }
button:active, .btn:active { transform: scale(.98); }
.copy-btn { background: var(--surface); border: 1px solid var(--border); color: var(--text-70); padding: 6px 12px; font-size: 0.82em; }
.copy-btn:hover { background: var(--accent-soft); opacity: 1; color: var(--text); }

.badge { display: inline-block; padding: 3px 9px; border-radius: var(--r-badge); font-size: 0.82em; font-weight: 600; }
.badge-ok { background: var(--ok-bg); color: var(--ok); }
.badge-warn { background: var(--warn-bg); color: var(--warn); }
.badge-zero { background: var(--danger-bg); color: var(--danger); }
.badge-stage { background: var(--accent-soft); color: #c9b8f5; }

.funnel-bar-wrap { text-align: center; margin: 4px 0; }
.funnel-bar { display: inline-flex; justify-content: space-between; align-items: center; padding: 7px 18px; border-radius: var(--r-btn); color: #fff; font-size: 0.88em; min-width: 160px; font-weight: 500; box-shadow: 0 4px 14px rgba(5,1,15,.35); }
.funnel-bar .flabel { text-align: left; }
.funnel-bar .fcount { font-weight: 700; margin-left: 12px; white-space: nowrap; }

.chart-row { display: flex; flex-wrap: wrap; gap: 1.5rem; align-items: flex-start; }
.chart-box { flex: 0 0 260px; }
.chart-box canvas { max-width: 260px; max-height: 260px; }
.funnel-box { flex: 1; min-width: 320px; }
.trend-box canvas { max-height: 220px; }

.info-box { background: var(--accent-soft); border-left: 3px solid var(--accent); padding: 1rem 1.2rem; border-radius: 0 var(--r-input) var(--r-input) 0; margin: 1rem 0; font-size: 0.9em; line-height: 1.65; color: var(--text-70); }
.info-box code { background: rgba(139,111,230,.18); color: var(--text); padding: 2px 6px; border-radius: 4px; font-size: 0.9em; }

.flash { padding: .7rem 1rem; border-radius: var(--r-input); margin: .75rem 0; font-size: .9em; }
.flash-ok { background: var(--ok-bg); color: var(--ok); }
.flash-err { background: var(--danger-bg); color: var(--danger); }

.pagination { display: flex; gap: 4px; align-items: center; margin: 1rem 0; flex-wrap: wrap; }
.pagination a, .pagination span { padding: 5px 11px; border-radius: var(--r-input); font-size: 0.9em; }
.pagination a { background: var(--surface); color: var(--text-70); border: 1px solid var(--border); }
.pagination a:hover { background: var(--accent-soft); text-decoration: none; }
.pagination .current { background: linear-gradient(90deg, var(--g-start), var(--g-end)); color: #fff; font-weight: 600; }

.login-wrap { max-width: 380px; margin: 14vh auto 0; }
.login-wrap .card { text-align: center; }
.login-wrap form { display: flex; flex-direction: column; gap: 12px; margin-top: 1rem; }
.login-wrap input { width: 100%; }
.login-wrap .brand { display: block; font-weight: 700; font-size: 1.4em; margin-bottom: .2rem; background: linear-gradient(90deg, var(--g-start), var(--g-end)); -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent; }

@media (max-width: 768px) {
  body { padding: 0 0.75rem 1.5rem; }
  .header { padding: 0.6rem; margin: 0 -0.75rem; border-radius: 0; }
  .chart-row { flex-direction: column; }
  .chart-box { flex: auto; width: 100%; }
  th, td { padding: 6px 8px; font-size: 0.8em; }
}
"""

_NAV_ITEMS = [
    ("/partner/", "Дашборд"),
    ("/partner/links", "Ссылки"),
    ("/partner/users", "Пользователи"),
    ("/partner/jobs", "Джобы"),
    ("/partner/payouts", "Выплаты"),
]


def _nav_html(active: str) -> str:
    links = "".join(
        f'<a href="{href}"{" class=\"active\"" if href == active else ""}>{label}</a>'
        for href, label in _NAV_ITEMS
    )
    return links


def _page(title: str, body: str, *, active: str = "", partner_name: str = "") -> str:
    nav = _nav_html(active)
    name_html = f'<span class="partner-name">{html_mod.escape(partner_name)}</span>' if partner_name else ""
    return f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Партнёрский кабинет — Blast</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>{_PARTNER_STYLE}</style></head><body>
<div class="header">
  <a href="/partner/" class="brand">Blast Partners</a>
  {nav}
  <div class="spacer"></div>
  {name_html}
  <a href="/partner/logout">Выйти</a>
</div>
<h1>{title}</h1>
{body}
</body></html>"""


def _login_page(*, error: str = "") -> str:
    err_html = f'<div class="flash flash-err">{html_mod.escape(error)}</div>' if error else ""
    return f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Вход — Blast Partners</title>
<style>{_PARTNER_STYLE}</style></head><body>
<div class="login-wrap">
  <div class="card">
    <span class="brand">Blast Partners</span>
    <p>Кабинет для партнёров по трафику</p>
    {err_html}
    <form method="post" action="/partner/login">
      <input type="text" name="login" placeholder="Логин" required autofocus>
      <input type="password" name="password" placeholder="Пароль" required>
      <button type="submit">Войти</button>
    </form>
  </div>
</div>
</body></html>"""


def _fmt_rub(amount: int) -> str:
    return f"{int(amount):,}".replace(",", " ")


def _pagination_html(page: int, total_pages: int, base_url: str) -> str:
    if total_pages <= 1:
        return ""
    sep = "&" if "?" in base_url else "?"
    parts = []
    if page > 1:
        parts.append(f'<a href="{base_url}{sep}page={page - 1}">&laquo; Назад</a>')
    parts.append(f'<span class="current">{page} / {total_pages}</span>')
    if page < total_pages:
        parts.append(f'<a href="{base_url}{sep}page={page + 1}">Вперёд &raquo;</a>')
    return f'<div class="pagination">{"".join(parts)}</div>'


def build_router(credits_db: "CreditsDB", state_store: "StateStore", settings: "Settings") -> APIRouter:
    router = APIRouter()

    async def _create_session(partner_id: int) -> str:
        token = secrets.token_urlsafe(32)
        await state_store.redis.set(f"{_SESSION_PREFIX}{token}", str(partner_id), ex=_SESSION_TTL_S)
        return token

    async def _resolve_session(token: str) -> Optional[int]:
        if not token:
            return None
        raw = await state_store.redis.get(f"{_SESSION_PREFIX}{token}")
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    async def _current_partner(request: Request) -> Dict[str, Any]:
        token = request.cookies.get(_COOKIE_NAME, "")
        partner_id = await _resolve_session(token)
        if partner_id is None:
            raise HTTPException(status_code=303, headers={"Location": "/partner/login"})
        partner = await credits_db.get_partner(partner_id)
        if not partner or str(partner.get("status", "")) != "active":
            raise HTTPException(status_code=303, headers={"Location": "/partner/login"})
        return partner

    @router.get("/partner/login", response_class=HTMLResponse)
    async def login_form(request: Request) -> str:
        token = request.cookies.get(_COOKIE_NAME, "")
        if await _resolve_session(token) is not None:
            raise HTTPException(status_code=303, headers={"Location": "/partner/"})
        return _login_page()

    @router.post("/partner/login", response_class=HTMLResponse)
    async def login_submit(login: str = Form(...), password: str = Form(...)):
        from .credits_db import verify_partner_password

        partner = await credits_db.get_partner_by_login(login)
        if not partner or not verify_partner_password(password, str(partner["password_hash"])):
            return HTMLResponse(_login_page(error="Неверный логин или пароль"), status_code=401)
        if str(partner.get("status", "")) != "active":
            return HTMLResponse(_login_page(error="Доступ приостановлен, обратитесь к менеджеру"), status_code=403)
        token = await _create_session(int(partner["id"]))
        resp = RedirectResponse("/partner/", status_code=303)
        resp.set_cookie(
            _COOKIE_NAME, token, max_age=_SESSION_TTL_S, httponly=True, samesite="lax",
            secure=bool(getattr(settings, "tg_bot_api_env", "") == "prod"),
        )
        return resp

    @router.get("/partner/logout")
    async def logout(request: Request):
        token = request.cookies.get(_COOKIE_NAME, "")
        if token:
            await state_store.redis.delete(f"{_SESSION_PREFIX}{token}")
        resp = RedirectResponse("/partner/login", status_code=303)
        resp.delete_cookie(_COOKIE_NAME)
        return resp

    # ── Dashboard ──────────────────────────────────────────────────

    @router.get("/partner/", response_class=HTMLResponse)
    async def dashboard(partner: Dict[str, Any] = Depends(_current_partner)) -> str:
        partner_id = int(partner["id"])
        tg_ids = await credits_db.list_partner_user_ids(partner_id)
        funnel_raw = await credits_db.funnel_reach_counts_for_users(tg_ids) if tg_ids else []
        rating_raw = await credits_db.rating_distribution_for_users(tg_ids) if tg_ids else []
        commission = await credits_db.partner_commission_summary(partner_id)
        trend = await credits_db.partner_revenue_timeseries(partner_id, days=30)
        top_links = (await credits_db.partner_link_stats(partner_id))[:5]

        funnel_map = {r["event"]: r["count"] for r in funnel_raw}
        max_funnel = max(funnel_map.values()) if funnel_map else 1
        first_cnt = funnel_map.get(_FUNNEL_ORDER[0], 0) or 1
        funnel_html = ""
        for i, event in enumerate(_FUNNEL_ORDER):
            cnt = funnel_map.get(event, 0)
            pct = max(15, cnt / max_funnel * 100) if max_funnel > 0 else 15
            conv = (cnt / first_cnt * 100) if first_cnt else 0
            color = _FUNNEL_COLORS[i] if i < len(_FUNNEL_COLORS) else "#8b6fe6"
            label = _FUNNEL_LABELS.get(event, event)
            funnel_html += (
                f'<div class="funnel-bar-wrap"><div class="funnel-bar" style="width:{pct:.0f}%;background:{color}">'
                f'<span class="flabel">{label}</span><span class="fcount">{cnt} <small>({conv:.0f}%)</small></span>'
                f'</div></div>\n'
            )

        rating_map = {r["rating"]: r["count"] for r in rating_raw}
        rating_keys = ["low", "mid_low", "high"]
        rating_total = sum(rating_map.values())
        rating_labels_js = json.dumps([_RATING_LABELS[k] for k in rating_keys])
        rating_data_js = json.dumps([rating_map.get(k, 0) for k in rating_keys])
        rating_colors_js = json.dumps([_RATING_COLORS[k] for k in rating_keys])

        trend_days_js = json.dumps([t["day"][5:] for t in trend])
        trend_starts_js = json.dumps([t["starts"] for t in trend])
        trend_purchases_js = json.dumps([t["purchases"] for t in trend])

        links_rows = "".join(
            f"<tr><td>{html_mod.escape(l['label'] or l['code'])}</td>"
            f"<td>{l['starts_count']}</td><td>{l['paying_users']}</td>"
            f"<td><strong>{_fmt_rub(l['commission_rub'])} &#8381;</strong></td></tr>"
            for l in top_links
        )

        body = f"""
        <div class="stat-grid">
          <div class="stat-tile accent"><div class="stat-label">Заработано</div><div class="stat-value">{_fmt_rub(commission['earned_rub'])} &#8381;</div>
            <div class="stat-sub">{commission['first_count']} первых + {commission['repeat_count']} повторных покупок</div></div>
          <div class="stat-tile"><div class="stat-label">Выплачено</div><div class="stat-value">{_fmt_rub(commission['paid_rub'])} &#8381;</div></div>
          <div class="stat-tile"><div class="stat-label">К выплате</div><div class="stat-value" style="color:var(--ok)">{_fmt_rub(commission['due_rub'])} &#8381;</div></div>
          <div class="stat-tile"><div class="stat-label">Приведено пользователей</div><div class="stat-value">{len(tg_ids)}</div></div>
        </div>

        <div class="card">
          <h2>Условия по продажам</h2>
          <p style="margin:0">50% с первой покупки пользователя и 20% с каждой следующей — включая автосписания по подписке (каждое списание учитывается отдельно).</p>
        </div>

        <div class="card">
          <div class="chart-row">
            <div class="funnel-box"><h2>Воронка</h2>{funnel_html if funnel_html else '<p>Пока нет данных — приведите первого пользователя по своей ссылке.</p>'}</div>
            <div class="chart-box"><h3>Оценки видео</h3>
              {'<p>Нет данных</p>' if rating_total == 0 else '<canvas id="ratingChart"></canvas>'}
            </div>
          </div>
        </div>

        <div class="card trend-box">
          <h2>Динамика за 30 дней</h2>
          <canvas id="trendChart"></canvas>
        </div>

        <div class="card">
          <h2>Лучшие ссылки</h2>
          <div class="table-wrap">
          <table><tr><th>Ссылка</th><th>Переходов</th><th>Купили</th><th>Комиссия</th></tr>
          {links_rows if links_rows else '<tr><td colspan="4">Ссылок пока нет — <a href="/partner/links">создайте первую</a>.</td></tr>'}
          </table>
          </div>
        </div>

        <script>
        {'' if rating_total == 0 else f'''
        new Chart(document.getElementById("ratingChart"), {{
          type: "doughnut",
          data: {{ labels: {rating_labels_js}, datasets: [{{ data: {rating_data_js}, backgroundColor: {rating_colors_js}, borderWidth: 2, borderColor: "#120b26" }}] }},
          options: {{ responsive: true, plugins: {{ legend: {{ position: "bottom", labels: {{ color: "#f6f5fd", padding: 14, font: {{ size: 12 }} }} }} }} }}
        }});
        '''}
        new Chart(document.getElementById("trendChart"), {{
          type: "line",
          data: {{
            labels: {trend_days_js},
            datasets: [
              {{ label: "Старты", data: {trend_starts_js}, borderColor: "#8b6fe6", backgroundColor: "rgba(139,111,230,.18)", tension: .3, fill: true }},
              {{ label: "Покупки", data: {trend_purchases_js}, borderColor: "#34d399", backgroundColor: "rgba(52,211,153,.18)", tension: .3, fill: true }}
            ]
          }},
          options: {{
            responsive: true,
            scales: {{
              x: {{ ticks: {{ color: "#9a90bf" }}, grid: {{ color: "rgba(139,111,230,.08)" }} }},
              y: {{ ticks: {{ color: "#9a90bf" }}, grid: {{ color: "rgba(139,111,230,.08)" }}, beginAtZero: true }}
            }},
            plugins: {{ legend: {{ labels: {{ color: "#f6f5fd" }} }} }}
          }}
        }});
        </script>
        """
        return _page("Дашборд", body, active="/partner/", partner_name=str(partner.get("name") or partner.get("login") or ""))

    # ── Links ──────────────────────────────────────────────────────

    @router.get("/partner/links", response_class=HTMLResponse)
    async def links_page(request: Request, partner: Dict[str, Any] = Depends(_current_partner)) -> str:
        partner_id = int(partner["id"])
        links = await credits_db.list_partner_links(partner_id)
        bot_username = getattr(settings, "tg_bot_username", "") or "blast808bot"
        err = html_mod.escape(str(request.query_params.get("err", "")).strip())
        rows = ""
        for l in links:
            deep_link = f"https://t.me/{bot_username}?start={url_quote(l['code'])}"
            rows += (
                f"<tr><td>{html_mod.escape(l['label'] or '(без названия)')}</td>"
                f"<td><code>{html_mod.escape(deep_link)}</code> "
                f"<button type=\"button\" class=\"copy-btn\" onclick=\"navigator.clipboard.writeText('{deep_link}')\">Копировать</button></td>"
                f"<td>{l['starts_count']}</td></tr>"
            )
        err_html = f'<div class="flash flash-err">{err}</div>' if err else ""
        body = f"""
        {err_html}
        <div class="card">
          <h2>Новая ссылка</h2>
          <p>Ссылка ведёт в бот с вашей UTM-меткой. Всё, что придёт по ней, закрепляется за вами навсегда — включая повторные покупки.</p>
          <form method="post" action="/partner/links/new" style="display:flex;gap:10px;flex-wrap:wrap;margin-top:.75rem">
            <input type="text" name="label" placeholder="Название (например: Instagram Reels)" required style="flex:1;min-width:220px">
            <button type="submit">Создать ссылку</button>
          </form>
        </div>
        <div class="card">
          <h2>Мои ссылки</h2>
          <div class="table-wrap">
          <table><tr><th>Название</th><th>Ссылка</th><th>Переходов</th></tr>
          {rows if rows else '<tr><td colspan="3">Пока нет ни одной ссылки</td></tr>'}
          </table>
          </div>
        </div>
        """
        return _page("Ссылки", body, active="/partner/links", partner_name=str(partner.get("name") or partner.get("login") or ""))

    @router.post("/partner/links/new")
    async def create_link(partner: Dict[str, Any] = Depends(_current_partner), label: str = Form(...)):
        partner_id = int(partner["id"])
        base_slug = "".join(c for c in label.lower().replace(" ", "_") if c.isalnum() or c == "_")[:24] or "link"
        for _ in range(5):
            code = f"{_LINK_PREFIX}{base_slug}_{secrets.token_hex(3)}"
            existing = await credits_db.get_partner_link_by_code(code)
            if not existing:
                await credits_db.create_partner_link(partner_id, code, label)
                return RedirectResponse("/partner/links", status_code=303)
        return RedirectResponse("/partner/links?err=" + url_quote("Не удалось создать ссылку, попробуйте другое название"), status_code=303)

    # ── Users (read-only) ────────────────────────────────────────────

    @router.get("/partner/users", response_class=HTMLResponse)
    async def users_page(request: Request, partner: Dict[str, Any] = Depends(_current_partner)) -> str:
        partner_id = int(partner["id"])
        try:
            page = max(1, int(str(request.query_params.get("page", "1"))))
        except ValueError:
            page = 1
        per_page = 50
        total = await credits_db.count_partner_users(partner_id)
        users = await credits_db.partner_users(partner_id, limit=per_page, offset=(page - 1) * per_page)
        rows = "".join(
            f"<tr><td><a href='/partner/users/{u['tg_id']}'>{html_mod.escape(u['username'] or str(u['tg_id']))}</a></td>"
            f"<td><span class='badge {'badge-ok' if u['credits'] > 0 else 'badge-zero'}'>{u['credits']}</span></td>"
            f"<td>{html_mod.escape(u['partner_link_code'])}</td>"
            f"<td>{u['created_at']}</td></tr>"
            for u in users
        )
        total_pages = max(1, (total + per_page - 1) // per_page)
        body = f"""
        <div class="card">
          <p style="margin:0">Только просмотр — без действий над аккаунтами. Всего пользователей: <strong>{total}</strong>.</p>
        </div>
        <div class="card">
          <div class="table-wrap">
          <table><tr><th>Пользователь</th><th>Кредиты</th><th>Ссылка</th><th>Дата регистрации</th></tr>
          {rows if rows else '<tr><td colspan="4">Пока нет пользователей</td></tr>'}
          </table>
          </div>
          {_pagination_html(page, total_pages, '/partner/users')}
        </div>
        """
        return _page("Пользователи", body, active="/partner/users", partner_name=str(partner.get("name") or partner.get("login") or ""))

    @router.get("/partner/users/{tg_id}", response_class=HTMLResponse)
    async def user_detail(tg_id: int, partner: Dict[str, Any] = Depends(_current_partner)) -> str:
        partner_id = int(partner["id"])
        user = await credits_db.get_partner_user(partner_id, tg_id)
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        activity = await credits_db.get_activity(tg_id=tg_id, limit=30)
        rows = "".join(
            f"<tr><td>{html_mod.escape(str(a.get('event', '')))}</td>"
            f"<td>{html_mod.escape(str(a.get('detail', '')))}</td>"
            f"<td>{html_mod.escape(str(a.get('created_at', '')))}</td></tr>"
            for a in activity
        )
        body = f"""
        <p><a href="/partner/users">&laquo; Все пользователи</a></p>
        <div class="card">
          <h2>{html_mod.escape(user['username'] or str(user['tg_id']))}</h2>
          <p>tg_id: <code>{user['tg_id']}</code> &nbsp;|&nbsp; Кредиты: <strong>{user['credits']}</strong>
          &nbsp;|&nbsp; Регистрация: {user['created_at']} &nbsp;|&nbsp; По ссылке: <code>{html_mod.escape(user['partner_link_code'])}</code></p>
        </div>
        <div class="card">
          <h2>Активность</h2>
          <div class="table-wrap">
          <table><tr><th>Событие</th><th>Детали</th><th>Когда</th></tr>
          {rows if rows else '<tr><td colspan="3">Активности пока нет</td></tr>'}
          </table>
          </div>
        </div>
        """
        return _page("Пользователь", body, active="/partner/users", partner_name=str(partner.get("name") or partner.get("login") or ""))

    # ── Jobs ───────────────────────────────────────────────────────

    @router.get("/partner/jobs", response_class=HTMLResponse)
    async def jobs_page(request: Request, partner: Dict[str, Any] = Depends(_current_partner)) -> str:
        partner_id = int(partner["id"])
        try:
            page = max(1, int(str(request.query_params.get("page", "1"))))
        except ValueError:
            page = 1
        active_only = str(request.query_params.get("active", "")).strip() == "1"
        per_page = 30
        summary = await credits_db.partner_jobs_summary(partner_id)
        total_jobs = await credits_db.count_partner_jobs(partner_id, active_only=active_only)
        jobs = await credits_db.partner_jobs(partner_id, active_only=active_only, limit=per_page, offset=(page - 1) * per_page)
        chips = "".join(
            f'<div class="stat-tile"><div class="stat-label">{_JOB_STATUS_LABELS.get(status, status)}</div><div class="stat-value">{cnt}</div></div>'
            for status, cnt in sorted(summary.items(), key=lambda kv: -kv[1])
        )
        rows = "".join(
            f"<tr><td>{html_mod.escape(j['username'] or str(j['tg_id']))}</td>"
            f"<td><span class='badge badge-stage'>{_JOB_STATUS_LABELS.get(j['status'], j['status'])}</span></td>"
            f"<td>{j['versions_total']}</td><td>{html_mod.escape(j['current_stage'])}</td>"
            f"<td>{j['updated_at']}</td></tr>"
            for j in jobs
        )
        toggle_href = "/partner/jobs" if active_only else "/partner/jobs?active=1"
        toggle_label = "Показать все" if active_only else "Только активные"
        body = f"""
        <div class="stat-grid">{chips if chips else '<div class="stat-tile"><div class="stat-label">Джобов пока нет</div><div class="stat-value">0</div></div>'}</div>
        <div class="card">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.5rem">
            <h2 style="margin:0">{'Активные джобы' if active_only else 'Все джобы'}</h2>
            <a href="{toggle_href}" class="btn">{toggle_label}</a>
          </div>
          <div class="table-wrap">
          <table><tr><th>Пользователь</th><th>Статус</th><th>Версий</th><th>Стадия</th><th>Обновлено</th></tr>
          {rows if rows else '<tr><td colspan="5">Джобов нет</td></tr>'}
          </table>
          </div>
          {_pagination_html(page, max(1, (total_jobs + per_page - 1) // per_page), f'/partner/jobs{"?active=1" if active_only else ""}')}
        </div>
        """
        return _page("Джобы", body, active="/partner/jobs", partner_name=str(partner.get("name") or partner.get("login") or ""))

    # ── Payouts ────────────────────────────────────────────────────

    @router.get("/partner/payouts", response_class=HTMLResponse)
    async def payouts_page(partner: Dict[str, Any] = Depends(_current_partner)) -> str:
        partner_id = int(partner["id"])
        commission = await credits_db.partner_commission_summary(partner_id)
        payouts = await credits_db.list_partner_payouts(partner_id)
        rows = "".join(
            f"<tr><td>{_fmt_rub(p['amount_rub'])} &#8381;</td><td>{html_mod.escape(p['note'])}</td><td>{p['created_at']}</td></tr>"
            for p in payouts
        )
        body = f"""
        <div class="stat-grid">
          <div class="stat-tile accent"><div class="stat-label">Заработано всего</div><div class="stat-value">{_fmt_rub(commission['earned_rub'])} &#8381;</div></div>
          <div class="stat-tile"><div class="stat-label">Выплачено</div><div class="stat-value">{_fmt_rub(commission['paid_rub'])} &#8381;</div></div>
          <div class="stat-tile"><div class="stat-label">К выплате</div><div class="stat-value" style="color:var(--ok)">{_fmt_rub(commission['due_rub'])} &#8381;</div></div>
        </div>
        <div class="card">
          <h2>История выплат</h2>
          <div class="table-wrap">
          <table><tr><th>Сумма</th><th>Комментарий</th><th>Дата</th></tr>
          {rows if rows else '<tr><td colspan="3">Выплат пока не было</td></tr>'}
          </table>
          </div>
        </div>
        """
        return _page("Выплаты", body, active="/partner/payouts", partner_name=str(partner.get("name") or partner.get("login") or ""))

    return router
