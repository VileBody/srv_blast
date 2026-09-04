"""Partner cabinet — read-only funnel/sales analytics + a self-serve UTM-link
builder for traffic partners.

Mounted onto the same FastAPI app/process as the admin panel (see
``admin_panel.build_app``) under ``/partner/*``, but with its own
cookie-session auth so a partner login can never double as an admin
credential. Partners see only their own attributed users (enforced in the
credits_db query layer, not just in the UI) and cannot act on them: no
credit grants, no messaging, no resets. Analysis only.
"""

from __future__ import annotations

import html as html_mod
import json
import logging
import re
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
_SLUG_RE = re.compile(r"^[a-z0-9_]{2,40}$")

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
    "#8b6fe6", "#7f63e0", "#7358d9", "#5f8fe0", "#4fa3d8",
    "#4fb8c0", "#5cc39c", "#6cc47f", "#34d399", "#2fc98d",
]

_EVENT_LABELS = {
    "start": "Старт бота",
    "utm_touch": "Переход по ссылке",
    "subscription_ok": "Подписка подтверждена",
    "audio_uploaded": "Аудио загружено",
    "generation_started": "Генерация запущена",
    "generation_done": "Генерация завершена",
    "generation_failed": "Генерация с ошибкой",
    "rate_video": "Оценка видео",
    "sales_pitch": "Просмотр питча",
    "view_packages": "Просмотр пакетов",
    "select_package": "Выбор пакета",
    "purchase_intent": "Заявка на покупку",
    "purchase_intent_recurrent": "Заявка на подписку",
    "payment_confirmed": "Оплата подтверждена",
    "subscription_charged": "Подписка списана",
    "referral_sent": "Реферал отправлен",
    "referral_matched": "Реферал сработал",
    "survey_opened": "Открыл форму",
    "survey_done": "Прошёл форму",
    "no_credits": "Кончились генерации",
    "initial_grant": "Стартовые генерации",
}

_RATING_BUCKETS = ["low", "mid_low", "high"]
_RATING_LABELS = {"low": "1-4", "mid_low": "5-6", "high": "7-10"}
_RATING_COLORS = {"low": "#fb7185", "mid_low": "#fbbf24", "high": "#34d399"}


# ── Theme ────────────────────────────────────────────────────────────────
# Brand tokens come from the public landing (landing/css/style.css) so the
# cabinet reads as the same product. Everything else here is dashboard
# craft: tabular numerals, sentence-case labels, one accent, semantic
# colour reserved for real state.

_STYLE = """
@font-face { font-family:'Point'; src:url('/admin/static/fonts/Point-Regular.ttf') format('truetype'); font-weight:400; font-style:normal; font-display:swap; }
@font-face { font-family:'Point'; src:url('/admin/static/fonts/Point-Book.ttf') format('truetype'); font-weight:350; font-style:normal; font-display:swap; }
@font-face { font-family:'Point'; src:url('/admin/static/fonts/Point-SemiBold.ttf') format('truetype'); font-weight:600; font-style:normal; font-display:swap; }
@font-face { font-family:'Point'; src:url('/admin/static/fonts/PointBold.ttf') format('truetype'); font-weight:700; font-style:normal; font-display:swap; }

:root {
  --bg: #05010f;
  --surface: #0e0822;
  --surface-2: #151029;
  --surface-3: #1b1436;
  --line: rgba(139,111,230,.14);
  --line-strong: rgba(139,111,230,.28);
  --text: #f6f5fd;
  --muted: rgba(246,245,253,.62);
  --faint: rgba(246,245,253,.38);
  --accent: #8b6fe6;
  --accent-2: #5f42b9;
  --accent-soft: rgba(139,111,230,.12);
  --ok: #34d399;
  --warn: #fbbf24;
  --danger: #fb7185;
  --r-card: 18px;
  --r-ctrl: 10px;
  --shadow: 0 18px 44px rgba(3,0,10,.55);
  --wrap: 1120px;
}

* { box-sizing: border-box; }
html { color-scheme: dark; }
body {
  margin: 0; padding: 0 0 64px;
  background: var(--bg);
  color: var(--text);
  font-family: 'Point', system-ui, -apple-system, sans-serif;
  font-size: 15px; line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}
.wrap { max-width: var(--wrap); margin: 0 auto; padding: 0 24px; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
h1 { font-size: 1.6rem; font-weight: 700; letter-spacing: -.02em; margin: 28px 0 20px; }
h2 { font-size: 1.02rem; font-weight: 600; letter-spacing: -.01em; margin: 0; }
p { margin: 0 0 10px; color: var(--muted); }
code { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: .9em; }
.num { font-variant-numeric: tabular-nums; }

@keyframes rise { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
@media (prefers-reduced-motion: no-preference) {
  .card, .tile { animation: rise .4s cubic-bezier(.22,.68,.36,1) both; }
  .card:nth-child(2), .tile:nth-child(2) { animation-delay: .04s; }
  .card:nth-child(3) { animation-delay: .08s; }
  a, button, .btn, tr, .navlink, .copy { transition: background .16s ease, color .16s ease, border-color .16s ease, transform .1s ease, opacity .16s ease; }
}

/* Top bar, aligned edge-to-edge with the content below it */
.topbar { margin: 20px auto 0; }
.topbar-inner {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  background: var(--surface-2); border: 1px solid var(--line);
  border-radius: var(--r-card);
  /* Point sits high in its em box, so symmetric padding reads as
     top-aligned. The extra top padding centres it optically. */
  padding: 13px 14px 9px;
}
.brand {
  font-weight: 700; font-size: 1.02rem; letter-spacing: -.01em; margin-right: 14px;
  background: linear-gradient(92deg, var(--accent), var(--accent-2));
  -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent;
}
.brand:hover { text-decoration: none; opacity: .85; }
.navlink { color: var(--muted); padding: 7px 13px; border-radius: 999px; font-size: .9rem; }
.navlink:hover { background: var(--accent-soft); color: var(--text); text-decoration: none; }
.navlink.active { background: var(--accent-soft); color: var(--text); box-shadow: inset 0 0 0 1px var(--line-strong); }
.topbar-tail { margin-left: auto; display: flex; align-items: center; gap: 10px; }
.whoami { color: var(--faint); font-size: .85rem; }

/* Cards */
.card {
  background: var(--surface); border: 1px solid var(--line);
  border-radius: var(--r-card); padding: 20px 22px; box-shadow: var(--shadow);
  margin-bottom: 16px;
}
.card-head { display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-bottom: 16px; }
.card-head .sub { color: var(--faint); font-size: .85rem; }

/* Hero stat tiles */
.hero { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px; margin-bottom: 16px; }
.tile {
  background: var(--surface); border: 1px solid var(--line);
  border-radius: var(--r-card); padding: 24px 26px 26px; box-shadow: var(--shadow);
  position: relative;
}
.tile-label { display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: .95rem; margin-bottom: 12px; }
.tile-value { font-size: clamp(2.8rem, 6vw, 3.9rem); font-weight: 700; letter-spacing: -.035em; line-height: 1; font-variant-numeric: tabular-nums; }
.tile-value.grad { background: linear-gradient(92deg, var(--accent), var(--accent-2)); -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent; }

/* Hover hint pill */
.hint {
  display: inline-flex; align-items: center; justify-content: center;
  width: 19px; height: 19px; border-radius: 999px; cursor: help;
  background: var(--accent-soft); color: var(--accent);
  border: 1px solid var(--line-strong); font-size: .72rem; font-weight: 700;
  position: relative; line-height: 1; padding-top: 2px;
}
/* Opens downward: the tiles sit at the top of the page, so an upward
   popover would be cut off by the viewport. */
.hint-pop {
  position: absolute; top: calc(100% + 9px); left: 0;
  width: 280px; padding: 12px 14px; border-radius: 12px;
  background: var(--surface-3); border: 1px solid var(--line-strong);
  color: var(--text); font-size: .82rem; font-weight: 400; line-height: 1.5;
  box-shadow: var(--shadow); opacity: 0; visibility: hidden; z-index: 30;
  text-align: left; padding-top: 12px;
}
.hint:hover .hint-pop, .hint:focus-visible .hint-pop { opacity: 1; visibility: visible; }

/* Funnel */
.funnel { display: flex; flex-direction: column; gap: 9px; }
.frow { display: grid; grid-template-columns: minmax(120px, 168px) 1fr auto; align-items: center; gap: 14px; }
.flabel { color: var(--muted); font-size: .88rem; }
.ftrack { height: 26px; background: var(--surface-2); border-radius: 7px; overflow: hidden; }
.ffill { height: 100%; border-radius: 7px; min-width: 3px; }
.fval { font-variant-numeric: tabular-nums; font-weight: 600; font-size: .9rem; min-width: 76px; text-align: right; }
.fval .pct { color: var(--faint); font-weight: 400; margin-left: 5px; }

/* Charts */
.legend { display: flex; gap: 14px; align-items: center; }
.legend-item { display: flex; align-items: center; gap: 6px; color: var(--muted); font-size: .84rem; }
.legend-dot { width: 9px; height: 9px; border-radius: 3px; }
.donut-wrap { display: flex; flex-direction: column; align-items: center; gap: 12px; }
.donut-wrap canvas { max-width: 190px; max-height: 190px; }
/* The ratings card stretches to the funnel's height (grid row default),
   and the donut centres itself in whatever space is left below the head. */
.two-col > .card { margin-bottom: 0; display: flex; flex-direction: column; }
.two-col > .card > .donut-wrap { flex: 1; justify-content: center; }
/* Chart.js with maintainAspectRatio:false sizes itself from the parent, so
   the parent needs an explicit height or the canvas grows every frame. */
.chart-h { position: relative; height: 260px; }

/* Period switch */
.seg { display: inline-flex; gap: 2px; background: var(--surface-2); border: 1px solid var(--line); border-radius: 999px; padding: 3px; }
.seg a { padding: 5px 13px; border-radius: 999px; font-size: .82rem; color: var(--muted); }
.seg a:hover { color: var(--text); text-decoration: none; }
.seg a.on { background: var(--accent-soft); color: var(--text); box-shadow: inset 0 0 0 1px var(--line-strong); }
.head-tools { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }

/* Period totals above the chart */
.pstats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 16px; margin-bottom: 20px; }
.pstat .k { color: var(--faint); font-size: .84rem; margin-bottom: 5px; }
.pstat .v { font-size: 1.55rem; font-weight: 700; letter-spacing: -.02em; font-variant-numeric: tabular-nums; }
.delta { font-size: .8rem; font-weight: 600; margin-left: 9px; letter-spacing: 0; }
.delta.up { color: var(--ok); }
.delta.down { color: var(--danger); }
.delta.flat { color: var(--faint); }
.donut-note { color: var(--faint); font-size: .84rem; }
.two-col { display: grid; grid-template-columns: 1.45fr 1fr; gap: 16px; margin-bottom: 16px; }
@media (max-width: 900px) { .two-col { grid-template-columns: 1fr; } }

/* Tables */
.table-wrap { overflow-x: auto; margin: 0 -6px; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: 11px 12px; font-size: .9rem; border-bottom: 1px solid var(--line); }
th { color: var(--faint); font-weight: 500; font-size: .82rem; border-bottom: 1px solid var(--line-strong); }
tbody tr:last-child td { border-bottom: none; }
tbody tr:hover td { background: rgba(139,111,230,.05); }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }

/* Controls */
input[type=text], input[type=password] {
  background: var(--surface-2); border: 1px solid var(--line); color: var(--text);
  border-radius: var(--r-ctrl); padding: 11px 13px; font: inherit; font-size: .92rem; width: 100%;
}
input::placeholder { color: var(--faint); }
input:focus-visible, button:focus-visible, a:focus-visible, .hint:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
button, .btn {
  font: inherit; font-size: .92rem; font-weight: 500; cursor: pointer; border: none;
  border-radius: var(--r-ctrl); padding: 11px 20px; color: #fff;
  background: linear-gradient(92deg, var(--accent), var(--accent-2));
}
button:hover, .btn:hover { opacity: .88; text-decoration: none; }
button:active, .btn:active { transform: translateY(1px); }
.copy {
  background: var(--surface-2); border: 1px solid var(--line); color: var(--muted);
  padding: 6px 12px; font-size: .8rem; border-radius: 8px;
}
.copy:hover { background: var(--accent-soft); color: var(--text); opacity: 1; }

/* Link generator */
.gen { display: flex; gap: 10px; align-items: stretch; flex-wrap: wrap; }
.gen-field {
  display: flex; align-items: center; flex: 1; min-width: 320px; cursor: text;
  background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--r-ctrl);
  padding-left: 13px; overflow: hidden;
}
/* Brighter than the placeholder so the fixed part reads as "already
   yours" and the editable part as the thing to fill in. */
.gen-prefix { color: var(--text); font-family: ui-monospace, monospace; font-size: .86rem; white-space: nowrap; user-select: none; }
.gen-field input { border: none; background: transparent; padding-left: 2px; font-family: ui-monospace, monospace; font-size: .86rem; }
.gen-field input:focus-visible { outline: none; }
.gen-field:focus-within { border-color: var(--line-strong); }

/* Badges + notes */
.badge { display: inline-block; padding: 3px 10px; border-radius: 7px; font-size: .8rem; font-weight: 600; }
.badge-ok { background: rgba(52,211,153,.13); color: var(--ok); }
.badge-zero { background: rgba(251,113,133,.13); color: var(--danger); }
.badge-soft { background: var(--accent-soft); color: #c9b8f5; }
.note { background: var(--accent-soft); border-left: 2px solid var(--accent); border-radius: 0 10px 10px 0; padding: 13px 16px; color: var(--muted); font-size: .88rem; line-height: 1.6; }
.note code { background: rgba(139,111,230,.16); color: var(--text); padding: 2px 6px; border-radius: 5px; }
.flash { padding: 12px 15px; border-radius: var(--r-ctrl); margin-bottom: 16px; font-size: .9rem; }
.flash-err { background: rgba(251,113,133,.12); color: var(--danger); border: 1px solid rgba(251,113,133,.3); }
.empty { text-align: center; padding: 26px 20px; color: var(--faint); }

/* User card */
.ucard { background: var(--surface-2); border: 1px solid var(--line); border-radius: 14px; padding: 18px 20px; }
.ucard.sample { border-style: dashed; opacity: .72; }
.ucard-top { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }
.avatar { width: 42px; height: 42px; border-radius: 999px; background: linear-gradient(140deg, var(--accent), var(--accent-2)); display: flex; align-items: center; justify-content: center; font-weight: 700; color: #fff; }
.avatar.blank { background: var(--surface-3); color: var(--faint); }
.ucard-name { font-weight: 600; }
.ucard-sub { color: var(--faint); font-size: .84rem; }
.ucard-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 12px; }
.ucard-item .k { color: var(--faint); font-size: .8rem; margin-bottom: 3px; }
.ucard-item .v { font-weight: 600; font-variant-numeric: tabular-nums; }

/* Pagination */
.pager { display: flex; gap: 6px; align-items: center; margin-top: 16px; }
.pager a, .pager span { padding: 7px 13px; border-radius: 9px; font-size: .88rem; }
.pager a { background: var(--surface-2); color: var(--muted); border: 1px solid var(--line); }
.pager a:hover { background: var(--accent-soft); color: var(--text); text-decoration: none; }
.pager .cur { color: var(--faint); }

/* Login */
.login { max-width: 380px; margin: 15vh auto 0; }
.login .card { padding: 30px 28px; }
.login .brand { display: block; font-size: 1.35rem; margin: 0 0 4px; }
.login form { display: flex; flex-direction: column; gap: 11px; margin-top: 18px; }

@media (max-width: 700px) {
  .wrap { padding: 0 14px; }
  .frow { grid-template-columns: 104px 1fr auto; gap: 9px; }
  .flabel { font-size: .8rem; }
  .card { padding: 17px 15px; }
}
"""

_NAV = [
    ("/partner/", "Дашборд"),
    ("/partner/links", "Ссылки"),
    ("/partner/users", "Пользователи"),
    ("/partner/activity", "Задачи"),
    ("/partner/payouts", "Выплаты"),
]

_COMMISSION_HINT = (
    "50% с первой покупки приведённого пользователя и 20% с каждой следующей. "
    "Автосписания по подписке считаются как отдельные покупки."
)


def _esc(value: Any) -> str:
    return html_mod.escape(str(value if value is not None else ""))


def _rub(amount: Any) -> str:
    return f"{int(amount or 0):,}".replace(",", " ")


def _chrome(title: str, body: str, *, active: str, who: str) -> str:
    nav = "".join(
        f'<a href="{href}" class="navlink{" active" if href == active else ""}">{label}</a>'
        for href, label in _NAV
    )
    return f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)} — Blast Partners</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>{_STYLE}</style></head><body>
<div class="topbar wrap">
  <div class="topbar-inner">
    <a href="/partner/" class="brand">Blast Partners</a>
    {nav}
    <div class="topbar-tail">
      <span class="whoami">{_esc(who)}</span>
      <a href="/partner/logout" class="navlink">Выйти</a>
    </div>
  </div>
</div>
<main class="wrap">
<h1>{_esc(title)}</h1>
{body}
</main>
</body></html>"""


def _login_html(error: str = "") -> str:
    err = f'<div class="flash flash-err">{_esc(error)}</div>' if error else ""
    return f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Вход — Blast Partners</title>
<style>{_STYLE}</style></head><body>
<div class="login wrap">
  <div class="card">
    <span class="brand">Blast Partners</span>
    <p>Кабинет партнёра по трафику</p>
    {err}
    <form method="post" action="/partner/login">
      <input type="text" name="login" placeholder="Логин" required autofocus autocomplete="username">
      <input type="password" name="password" placeholder="Пароль" required autocomplete="current-password">
      <button type="submit">Войти</button>
    </form>
  </div>
</div>
</body></html>"""


_PERIODS = (7, 30, 90)


def _delta_html(current: int, previous: int) -> str:
    """Change against the previous period. Growth from zero has no meaningful
    percentage, so it stays a dash rather than a fake +100%."""
    if previous == 0:
        return '<span class="delta flat">-</span>' if current == 0 else '<span class="delta up">новое</span>'
    pct = (current - previous) / previous * 100
    if abs(pct) < 1:
        return '<span class="delta flat">без изменений</span>'
    cls = "up" if pct > 0 else "down"
    return f'<span class="delta {cls}">{pct:+.0f}%</span>'


def _period_stat(label: str, value: str, current: int, previous: int) -> str:
    return (
        f'<div class="pstat"><div class="k">{label}</div>'
        f'<div class="v">{value}{_delta_html(current, previous)}</div></div>'
    )


def _pager(page: int, total_pages: int, base: str) -> str:
    if total_pages <= 1:
        return ""
    sep = "&" if "?" in base else "?"
    out = []
    if page > 1:
        out.append(f'<a href="{base}{sep}page={page - 1}">Назад</a>')
    out.append(f'<span class="cur">Страница {page} из {total_pages}</span>')
    if page < total_pages:
        out.append(f'<a href="{base}{sep}page={page + 1}">Вперёд</a>')
    return f'<div class="pager">{"".join(out)}</div>'


def _funnel_html(counts: Dict[str, int]) -> str:
    # Always render every step, zeros included: a partner should see the whole
    # path their traffic has to walk, not an empty card.
    top = max(counts.values()) if counts else 0
    top = top or 1
    base = counts.get(_FUNNEL_ORDER[0], 0) or 1
    rows = ""
    for i, event in enumerate(_FUNNEL_ORDER):
        cnt = counts.get(event, 0)
        width = (cnt / top * 100) if top else 0
        conv = (cnt / base * 100) if base else 0
        color = _FUNNEL_COLORS[i] if i < len(_FUNNEL_COLORS) else "#8b6fe6"
        rows += (
            f'<div class="frow">'
            f'<div class="flabel">{_FUNNEL_LABELS.get(event, event)}</div>'
            f'<div class="ftrack"><div class="ffill" style="width:{width:.1f}%;background:{color}"></div></div>'
            f'<div class="fval">{cnt}<span class="pct">{conv:.0f}%</span></div>'
            f'</div>'
        )
    return f'<div class="funnel">{rows}</div>'


def build_router(credits_db: "CreditsDB", state_store: "StateStore", settings: "Settings") -> APIRouter:
    router = APIRouter()

    async def _new_session(partner_id: int) -> str:
        token = secrets.token_urlsafe(32)
        await state_store.redis.set(f"{_SESSION_PREFIX}{token}", str(partner_id), ex=_SESSION_TTL_S)
        return token

    async def _session_partner_id(token: str) -> Optional[int]:
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
        partner_id = await _session_partner_id(request.cookies.get(_COOKIE_NAME, ""))
        if partner_id is None:
            raise HTTPException(status_code=303, headers={"Location": "/partner/login"})
        partner = await credits_db.get_partner(partner_id)
        if not partner or str(partner.get("status", "")) != "active":
            raise HTTPException(status_code=303, headers={"Location": "/partner/login"})
        return partner

    def _who(partner: Dict[str, Any]) -> str:
        return str(partner.get("name") or partner.get("login") or "")

    def _bot_username() -> str:
        return str(getattr(settings, "tg_bot_username", "") or "blast808bot")

    # ── Auth ────────────────────────────────────────────────────────

    @router.get("/partner/login", response_class=HTMLResponse)
    async def login_form(request: Request) -> str:
        if await _session_partner_id(request.cookies.get(_COOKIE_NAME, "")) is not None:
            raise HTTPException(status_code=303, headers={"Location": "/partner/"})
        return _login_html()

    @router.post("/partner/login", response_class=HTMLResponse)
    async def login_submit(login: str = Form(...), password: str = Form(...)):
        from .credits_db import verify_partner_password

        partner = await credits_db.get_partner_by_login(login)
        if not partner or not verify_partner_password(password, str(partner["password_hash"])):
            return HTMLResponse(_login_html("Неверный логин или пароль"), status_code=401)
        if str(partner.get("status", "")) != "active":
            return HTMLResponse(_login_html("Доступ приостановлен, напишите менеджеру"), status_code=403)
        token = await _new_session(int(partner["id"]))
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

    # ── Dashboard ───────────────────────────────────────────────────

    @router.get("/partner/", response_class=HTMLResponse)
    async def dashboard(request: Request, partner: Dict[str, Any] = Depends(_current_partner)) -> str:
        pid = int(partner["id"])
        try:
            days = int(str(request.query_params.get("days", "30")))
        except ValueError:
            days = 30
        if days not in _PERIODS:
            days = 30

        tg_ids = await credits_db.list_partner_user_ids(pid)
        funnel_raw = await credits_db.funnel_reach_counts_for_users(tg_ids) if tg_ids else []
        rating_raw = await credits_db.rating_distribution_for_users(tg_ids) if tg_ids else []
        money = await credits_db.partner_commission_summary(pid)
        trend = await credits_db.partner_revenue_timeseries(pid, days=days)
        now_totals = await credits_db.partner_period_totals(pid, days=days, shift=0)
        prev_totals = await credits_db.partner_period_totals(pid, days=days, shift=1)
        links = (await credits_db.partner_link_stats(pid))[:5]

        funnel_counts = {r["event"]: r["count"] for r in funnel_raw}
        ratings = {r["rating"]: r["count"] for r in rating_raw}
        rating_total = sum(ratings.values())

        donut_data = [ratings.get(k, 0) for k in _RATING_BUCKETS] if rating_total else [1]
        donut_colors = [_RATING_COLORS[k] for k in _RATING_BUCKETS] if rating_total else ["#1b1436"]
        donut_labels = [_RATING_LABELS[k] for k in _RATING_BUCKETS] if rating_total else ["Нет оценок"]

        rating_legend = "".join(
            f'<span class="legend-item"><span class="legend-dot" style="background:{_RATING_COLORS[k]}"></span>'
            f'{_RATING_LABELS[k]}</span>'
            for k in _RATING_BUCKETS
        )

        period_links = "".join(
            f'<a href="/partner/?days={d}" class="{"on" if d == days else ""}">{d} дней</a>'
            for d in _PERIODS
        )
        has_trend = any(t["starts"] or t["purchases"] for t in trend)
        trend_block = (
            '<div class="chart-h"><canvas id="trend"></canvas></div>'
            if has_trend else
            '<div class="empty">Данные появятся после первых переходов по вашей ссылке.'
            ' <a href="/partner/links">Создать ссылку</a></div>'
        )

        link_rows = "".join(
            f"<tr><td>{_esc(l['label'] or l['code'])}</td>"
            f"<td class='num'>{l['starts_count']}</td>"
            f"<td class='num'>{l['paying_users']}</td>"
            f"<td class='num'>{_rub(l['commission_rub'])} &#8381;</td></tr>"
            for l in links
        )

        body = f"""
        <div class="hero">
          <div class="tile">
            <div class="tile-label">Приведено пользователей</div>
            <div class="tile-value">{len(tg_ids)}</div>
          </div>
          <div class="tile">
            <div class="tile-label">Заработано
              <span class="hint" tabindex="0" role="note" aria-label="Как считается заработок">?<span class="hint-pop">{_COMMISSION_HINT}</span></span>
            </div>
            <div class="tile-value grad">{_rub(money['earned_rub'])} &#8381;</div>
          </div>
        </div>

        <div class="two-col">
          <div class="card">
            <div class="card-head"><h2>Воронка</h2><span class="sub">по вашим пользователям</span></div>
            {_funnel_html(funnel_counts)}
          </div>
          <div class="card">
            <div class="card-head"><h2>Оценки видео</h2><div class="legend">{rating_legend}</div></div>
            <div class="donut-wrap">
              <canvas id="donut"></canvas>
              <span class="donut-note">{"Всего оценок: " + str(rating_total) if rating_total else "Оценок пока нет"}</span>
            </div>
          </div>
        </div>

        <div class="card">
          <div class="card-head">
            <h2>Динамика</h2>
            <div class="head-tools">
              <div class="legend">
                <span class="legend-item"><span class="legend-dot" style="background:#8b6fe6"></span>Старты</span>
                <span class="legend-item"><span class="legend-dot" style="background:#34d399"></span>Покупки</span>
              </div>
              <div class="seg">{period_links}</div>
            </div>
          </div>
          <div class="pstats">
            {_period_stat("Старты", str(now_totals["starts"]), now_totals["starts"], prev_totals["starts"])}
            {_period_stat("Покупки", str(now_totals["purchases"]), now_totals["purchases"], prev_totals["purchases"])}
            {_period_stat("Заработок", _rub(now_totals["earned_rub"]) + " &#8381;", now_totals["earned_rub"], prev_totals["earned_rub"])}
          </div>
          {trend_block}
        </div>

        <div class="card">
          <div class="card-head"><h2>Лучшие ссылки</h2><a href="/partner/links" class="sub">Все ссылки</a></div>
          <div class="table-wrap">
          <table>
            <thead><tr><th>Ссылка</th><th class="num">Переходов</th><th class="num">Купили</th><th class="num">Комиссия</th></tr></thead>
            <tbody>{link_rows if link_rows else '<tr><td colspan="4"><div class="empty">Ссылок пока нет. <a href="/partner/links">Создайте первую</a>.</div></td></tr>'}</tbody>
          </table>
          </div>
        </div>

        <script>
        Chart.defaults.font.family = "Point, system-ui, sans-serif";
        Chart.defaults.color = "rgba(246,245,253,.45)";
        new Chart(document.getElementById("donut"), {{
          type: "doughnut",
          data: {{ labels: {json.dumps(donut_labels, ensure_ascii=False)},
                   datasets: [{{ data: {json.dumps(donut_data)}, backgroundColor: {json.dumps(donut_colors)},
                                 borderWidth: 0, cutout: "68%" }}] }},
          options: {{ responsive: true, plugins: {{ legend: {{ display: false }},
                      tooltip: {{ enabled: {"true" if rating_total else "false"} }} }} }}
        }});
        if (document.getElementById("trend")) {{
          new Chart(document.getElementById("trend"), {{
            type: "bar",
            data: {{
              labels: {json.dumps([t["day"][5:] for t in trend])},
              datasets: [
                {{ label: "Старты", data: {json.dumps([t["starts"] for t in trend])},
                   backgroundColor: "#8b6fe6", borderRadius: 3, maxBarThickness: 22 }},
                {{ label: "Покупки", data: {json.dumps([t["purchases"] for t in trend])},
                   backgroundColor: "#34d399", borderRadius: 3, maxBarThickness: 22 }}
              ]
            }},
            options: {{
              responsive: true, maintainAspectRatio: false,
              interaction: {{ mode: "index", intersect: false }},
              plugins: {{ legend: {{ display: false }} }},
              scales: {{
                x: {{ grid: {{ display: false }}, ticks: {{ maxTicksLimit: 10, font: {{ size: 12 }} }} }},
                y: {{ beginAtZero: true, border: {{ display: false }},
                      grid: {{ color: "rgba(139,111,230,.07)" }},
                      ticks: {{ precision: 0, maxTicksLimit: 5, font: {{ size: 12 }} }} }}
              }}
            }}
          }});
        }}
        </script>
        """
        return _chrome("Дашборд", body, active="/partner/", who=_who(partner))

    # ── Links ───────────────────────────────────────────────────────

    @router.get("/partner/links", response_class=HTMLResponse)
    async def links_page(request: Request, partner: Dict[str, Any] = Depends(_current_partner)) -> str:
        pid = int(partner["id"])
        stats = await credits_db.partner_link_stats(pid)
        bot = _bot_username()
        err = str(request.query_params.get("err", "")).strip()

        rows = ""
        for s in stats:
            url = f"https://t.me/{bot}?start={s['code']}"
            conv = (s["paying_users"] / s["starts_count"] * 100) if s["starts_count"] else 0
            rows += (
                f"<tr><td>{_esc(s['label'] or s['code'])}</td>"
                f"<td><code>{_esc(url)}</code> "
                f"<button type=\"button\" class=\"copy\" data-url=\"{_esc(url)}\">Копировать</button></td>"
                f"<td class='num'>{s['starts_count']}</td>"
                f"<td class='num'>{s['paying_users']}</td>"
                f"<td class='num'>{conv:.0f}%</td>"
                f"<td class='num'>{_rub(s['commission_rub'])} &#8381;</td></tr>"
            )

        err_html = f'<div class="flash flash-err">{_esc(err)}</div>' if err else ""
        body = f"""
        {err_html}
        <div class="card">
          <div class="card-head"><h2>Новая ссылка</h2><span class="sub">название придумываете сами</span></div>
          <form method="post" action="/partner/links/new" class="gen">
            <label class="gen-field">
              <span class="gen-prefix">https://t.me/{_esc(bot)}?start={_LINK_PREFIX}</span>
              <input type="text" name="slug" placeholder="instagram_reels" required
                     pattern="[a-zA-Z0-9_]{{2,40}}" title="Латиница, цифры и подчёркивания, от 2 до 40 символов">
            </label>
            <button type="submit">Создать</button>
          </form>
          <div class="note" style="margin-top:14px">
            То, что вы впишете, и станет вашей ссылкой: по ней считается вся статистика.
            Латиница, цифры и подчёркивания, до 40 символов. Пользователь закрепляется за вами
            при первом переходе и остаётся закреплённым навсегда, включая все будущие покупки.
          </div>
        </div>

        <div class="card">
          <div class="card-head"><h2>Мои ссылки</h2><span class="sub">всего {len(stats)}</span></div>
          <div class="table-wrap">
          <table>
            <thead><tr><th>Название</th><th>Ссылка</th><th class="num">Переходов</th><th class="num">Купили</th><th class="num">Конверсия</th><th class="num">Комиссия</th></tr></thead>
            <tbody>{rows if rows else '<tr><td colspan="6"><div class="empty">Ссылок пока нет</div></td></tr>'}</tbody>
          </table>
          </div>
        </div>
        <script>
        document.querySelectorAll(".copy").forEach(function (b) {{
          b.addEventListener("click", function () {{
            navigator.clipboard.writeText(b.dataset.url);
            var t = b.textContent; b.textContent = "Скопировано";
            setTimeout(function () {{ b.textContent = t; }}, 1400);
          }});
        }});
        </script>
        """
        return _chrome("Ссылки", body, active="/partner/links", who=_who(partner))

    @router.post("/partner/links/new")
    async def create_link(partner: Dict[str, Any] = Depends(_current_partner), slug: str = Form(...)):
        pid = int(partner["id"])
        clean = str(slug or "").strip().lower().replace(" ", "_")
        if not _SLUG_RE.match(clean):
            msg = "Название: латиница, цифры и подчёркивания, от 2 до 40 символов"
            return RedirectResponse("/partner/links?err=" + url_quote(msg), status_code=303)
        code = f"{_LINK_PREFIX}{clean}"
        if await credits_db.get_partner_link_by_code(code):
            msg = f"Ссылка {code} уже занята, придумайте другое название"
            return RedirectResponse("/partner/links?err=" + url_quote(msg), status_code=303)
        await credits_db.create_partner_link(pid, code, clean)
        return RedirectResponse("/partner/links", status_code=303)

    # ── Users (read-only) ───────────────────────────────────────────

    @router.get("/partner/users", response_class=HTMLResponse)
    async def users_page(request: Request, partner: Dict[str, Any] = Depends(_current_partner)) -> str:
        pid = int(partner["id"])
        try:
            page = max(1, int(str(request.query_params.get("page", "1"))))
        except ValueError:
            page = 1
        per_page = 50
        total = await credits_db.count_partner_users(pid)
        users = await credits_db.partner_users(pid, limit=per_page, offset=(page - 1) * per_page)
        money = await credits_db.partner_commission_summary(pid)
        buyers = int(money["first_count"])

        rows = "".join(
            f"<tr><td><a href='/partner/users/{u['tg_id']}'>{_esc(u['username'] or u['tg_id'])}</a></td>"
            f"<td class='num'><span class='badge {'badge-ok' if u['credits'] > 0 else 'badge-zero'}'>{u['credits']}</span></td>"
            f"<td>{_esc(u['partner_link_code'])}</td>"
            f"<td>{_esc(u['created_at'])}</td></tr>"
            for u in users
        )

        sample = """
        <div class="card">
          <div class="card-head"><h2>Как выглядит карточка</h2><span class="sub">пример</span></div>
          <div class="ucard sample">
            <div class="ucard-top">
              <div class="avatar blank">?</div>
              <div>
                <div class="ucard-name">username</div>
                <div class="ucard-sub">tg_id, дата регистрации</div>
              </div>
            </div>
            <div class="ucard-grid">
              <div class="ucard-item"><div class="k">Генераций осталось</div><div class="v">0</div></div>
              <div class="ucard-item"><div class="k">Пришёл по ссылке</div><div class="v">p_your_link</div></div>
              <div class="ucard-item"><div class="k">Закреплён</div><div class="v">дата</div></div>
            </div>
          </div>
        </div>
        """ if not users else ""

        conv = (buyers / total * 100) if total else 0
        body = f"""
        <div class="hero">
          <div class="tile">
            <div class="tile-label">Всего пользователей</div>
            <div class="tile-value">{total}</div>
          </div>
          <div class="tile">
            <div class="tile-label">Из них купили</div>
            <div class="tile-value">{buyers}<span style="font-size:.4em;font-weight:500;color:var(--faint);margin-left:10px">{conv:.0f}%</span></div>
          </div>
        </div>
        <div class="card">
          <div class="table-wrap">
          <table>
            <thead><tr><th>Пользователь</th><th class="num">Генераций</th><th>Ссылка</th><th>Регистрация</th></tr></thead>
            <tbody>{rows if rows else '<tr><td colspan="4"><div class="empty">Пользователей пока нет</div></td></tr>'}</tbody>
          </table>
          </div>
          {_pager(page, max(1, (total + per_page - 1) // per_page), '/partner/users')}
        </div>
        {sample}
        """
        return _chrome("Пользователи", body, active="/partner/users", who=_who(partner))

    @router.get("/partner/users/{tg_id}", response_class=HTMLResponse)
    async def user_detail(tg_id: int, partner: Dict[str, Any] = Depends(_current_partner)) -> str:
        pid = int(partner["id"])
        user = await credits_db.get_partner_user(pid, tg_id)
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        activity = await credits_db.get_activity(tg_id=tg_id, limit=30)
        name = user["username"] or str(user["tg_id"])
        initial = (user["username"][:1] or "#").upper()

        rows = "".join(
            f"<tr><td>{_esc(_EVENT_LABELS.get(a.get('event', ''), a.get('event', '')))}</td>"
            f"<td>{_esc(a.get('detail', ''))}</td>"
            f"<td>{_esc(a.get('created_at', ''))}</td></tr>"
            for a in activity
        )

        body = f"""
        <p><a href="/partner/users">Назад к списку</a></p>
        <div class="card">
          <div class="ucard">
            <div class="ucard-top">
              <div class="avatar">{_esc(initial)}</div>
              <div>
                <div class="ucard-name">{_esc(name)}</div>
                <div class="ucard-sub">tg_id {user['tg_id']} · с {_esc(user['created_at'])}</div>
              </div>
            </div>
            <div class="ucard-grid">
              <div class="ucard-item"><div class="k">Генераций осталось</div><div class="v">{user['credits']}</div></div>
              <div class="ucard-item"><div class="k">Пришёл по ссылке</div><div class="v">{_esc(user['partner_link_code'] or '-')}</div></div>
              <div class="ucard-item"><div class="k">Закреплён</div><div class="v">{_esc(user['partner_attributed_at'] or '-')}</div></div>
            </div>
          </div>
        </div>
        <div class="card">
          <div class="card-head"><h2>Активность</h2><span class="sub">последние 30 событий</span></div>
          <div class="table-wrap">
          <table>
            <thead><tr><th>Событие</th><th>Детали</th><th>Когда</th></tr></thead>
            <tbody>{rows if rows else '<tr><td colspan="3"><div class="empty">Активности пока нет</div></td></tr>'}</tbody>
          </table>
          </div>
        </div>
        """
        return _chrome(name, body, active="/partner/users", who=_who(partner))

    # ── Задачи (activity feed) ──────────────────────────────────────

    @router.get("/partner/jobs")
    async def jobs_redirect():
        return RedirectResponse("/partner/activity", status_code=308)

    @router.get("/partner/activity", response_class=HTMLResponse)
    async def activity_page(request: Request, partner: Dict[str, Any] = Depends(_current_partner)) -> str:
        pid = int(partner["id"])
        try:
            page = max(1, int(str(request.query_params.get("page", "1"))))
        except ValueError:
            page = 1
        per_page = 50
        total = await credits_db.count_partner_activity(pid)
        events = await credits_db.partner_activity(pid, limit=per_page, offset=(page - 1) * per_page)
        jobs = await credits_db.partner_jobs_summary(pid)

        done = int(jobs.get("succeeded", 0))
        running = sum(v for k, v in jobs.items() if k not in ("succeeded", "failed", "cancelled"))

        rows = "".join(
            f"<tr><td>{_esc(_EVENT_LABELS.get(e['event'], e['event']))}</td>"
            f"<td><a href='/partner/users/{e['tg_id']}'>{_esc(e['username'] or e['tg_id'])}</a></td>"
            f"<td>{_esc(e['detail'])}</td>"
            f"<td>{_esc(e['created_at'])}</td></tr>"
            for e in events
        )

        body = f"""
        <div class="hero">
          <div class="tile">
            <div class="tile-label">Видео готово</div>
            <div class="tile-value">{done}</div>
          </div>
          <div class="tile">
            <div class="tile-label">Сейчас в работе</div>
            <div class="tile-value">{running}</div>
          </div>
        </div>
        <div class="card">
          <div class="card-head"><h2>Лента событий</h2><span class="sub">всего {total}</span></div>
          <div class="table-wrap">
          <table>
            <thead><tr><th>Событие</th><th>Пользователь</th><th>Детали</th><th>Когда</th></tr></thead>
            <tbody>{rows if rows else '<tr><td colspan="4"><div class="empty">Событий пока нет</div></td></tr>'}</tbody>
          </table>
          </div>
          {_pager(page, max(1, (total + per_page - 1) // per_page), '/partner/activity')}
        </div>
        """
        return _chrome("Задачи", body, active="/partner/activity", who=_who(partner))

    # ── Payouts ─────────────────────────────────────────────────────

    @router.get("/partner/payouts", response_class=HTMLResponse)
    async def payouts_page(partner: Dict[str, Any] = Depends(_current_partner)) -> str:
        pid = int(partner["id"])
        money = await credits_db.partner_commission_summary(pid)
        payouts = await credits_db.list_partner_payouts(pid)

        rows = "".join(
            f"<tr><td>{_esc(p['created_at'])}</td>"
            f"<td>{_esc(p['note'] or '-')}</td>"
            f"<td class='num'>{_rub(p['amount_rub'])} &#8381;</td></tr>"
            for p in payouts
        )

        body = f"""
        <div class="hero">
          <div class="tile">
            <div class="tile-label">К выплате
              <span class="hint" tabindex="0" role="note" aria-label="Как считается заработок">?<span class="hint-pop">{_COMMISSION_HINT}</span></span>
            </div>
            <div class="tile-value grad">{_rub(money['due_rub'])} &#8381;</div>
          </div>
          <div class="tile">
            <div class="tile-label">Уже выплачено</div>
            <div class="tile-value">{_rub(money['paid_rub'])} &#8381;</div>
          </div>
        </div>
        <div class="card">
          <div class="card-head"><h2>История выплат</h2><span class="sub">всего {len(payouts)}</span></div>
          <div class="table-wrap">
          <table>
            <thead><tr><th>Дата</th><th>Комментарий</th><th class="num">Сумма</th></tr></thead>
            <tbody>{rows if rows else '<tr><td colspan="3"><div class="empty">Выплаты формирует менеджер на основе статистики, по любым вопросам — обращайтесь к нему</div></td></tr>'}</tbody>
          </table>
          </div>
        </div>
        """
        return _chrome("Выплаты", body, active="/partner/payouts", who=_who(partner))

    return router
