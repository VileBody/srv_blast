"""Lightweight admin web panel — runs as a background asyncio task inside the bot process."""

from __future__ import annotations

import asyncio
import html as html_mod
import json
import logging
from typing import TYPE_CHECKING

import uvicorn
from fastapi import FastAPI, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
if TYPE_CHECKING:
    from .config import Settings
    from .credits_db import CreditsDB
    from .state_store import RedisChatStateStore as StateStore
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
    "utm_touch": "UTM касание",
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
    "payment_confirmed": "Оплата подтверждена",
    "admin_activate": "Активация админом",
}

_RATING_LABELS = {
    "low": "До 5",
    "mid_low": "5-6",
    "high": "7-10",
}

_RATING_COLORS = {
    "low": "#e74c3c",
    "mid_low": "#f39c12",
    "high": "#27ae60",
}

# Canonical funnel order for visualization
_FUNNEL_ORDER = [
    "start",
    "subscription_ok",
    "audio_uploaded",
    "generation_started",
    "generation_done",
    "rate_video",
    "sales_pitch",
    "view_packages",
    "select_package",
    "purchase_intent",
    "payment_confirmed",
]

# Funnel bar colors (green → red gradient)
_FUNNEL_COLORS = [
    "#27ae60", "#2ecc71", "#3498db", "#2980b9",
    "#8e44ad", "#9b59b6", "#e67e22", "#d35400",
    "#e74c3c", "#c0392b", "#c0392b",
]

# Package definitions
_PACKAGES = {
    "5": "Триал (5 генераций)",
    "15": "Бласт (15 генераций)",
    "30": "Глоу (30 генераций)",
    "50": "Импульс (50 генераций)",
}

# ── HTML templates (inline to keep it self-contained) ────────────────────

_BASE_HEAD = """
<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Blast Admin</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>
  * { box-sizing: border-box; }
  body { font-family: system-ui, sans-serif; max-width: 1100px; margin: 0 auto; padding: 0 1rem 2rem; background: #f4f5f7; color: #333; }
  h1 { margin: 1.5rem 0 1rem; }
  h2 { margin: 1.5rem 0 0.5rem; color: #444; }
  h3 { margin: 1.2rem 0 0.5rem; color: #555; }

  /* Navigation */
  .header { background: #2c3e50; padding: 0.8rem 1.5rem; margin: 0 -1rem; display: flex; align-items: center; flex-wrap: wrap; gap: 0.5rem; }
  .header .brand { color: #fff; font-weight: 700; font-size: 1.2em; margin-right: 1.5rem; text-decoration: none; }
  .header a { color: #ecf0f1; text-decoration: none; padding: 4px 10px; border-radius: 4px; font-size: 0.9em; }
  .header a:hover, .header a.active { background: rgba(255,255,255,0.15); }
  .header .search-form { margin-left: auto; display: flex; gap: 4px; }
  .header .search-form input { padding: 4px 8px; border: none; border-radius: 4px; font-size: 0.85em; width: 180px; }
  .header .search-form button { padding: 4px 10px; border: none; border-radius: 4px; background: #3498db; color: #fff; cursor: pointer; font-size: 0.85em; }

  /* Cards */
  .card { background: #fff; border-radius: 8px; padding: 1.2rem 1.5rem; margin: 1rem 0; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }

  /* Tables */
  .table-wrap { overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; margin: 0.5rem 0; }
  th, td { border: 1px solid #e1e4e8; padding: 8px 12px; text-align: left; font-size: 0.9em; }
  th { background: #f1f3f5; font-weight: 600; }
  tr:hover { background: #f8f9fa; }
  a { color: #0066cc; text-decoration: none; }
  a:hover { text-decoration: underline; }

  /* Forms */
  form { display: inline; }
  input[type=number], input[type=text] { padding: 6px 10px; border: 1px solid #ccc; border-radius: 4px; font-size: 0.9em; }
  select { padding: 6px 10px; border: 1px solid #ccc; border-radius: 4px; font-size: 0.9em; }
  button, .btn { padding: 6px 14px; cursor: pointer; border: none; border-radius: 4px; font-size: 0.9em; background: #3498db; color: #fff; }
  button:hover, .btn:hover { background: #2980b9; }
  .btn-danger { background: #e74c3c; }
  .btn-danger:hover { background: #c0392b; }
  .btn-success { background: #27ae60; }
  .btn-success:hover { background: #1e8449; }

  /* Badges */
  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.85em; }
  .badge-ok { background: #d4edda; color: #155724; }
  .badge-zero { background: #f8d7da; color: #721c24; }
  .badge-stage { background: #cce5ff; color: #004085; }
  .badge-source { background: #e8daef; color: #6c3483; }

  /* Funnel */
  .funnel-bar-wrap { text-align: center; margin: 3px 0; }
  .funnel-bar { display: inline-flex; justify-content: space-between; align-items: center;
                padding: 6px 16px; border-radius: 4px; color: #fff; font-size: 0.9em;
                min-width: 140px; font-weight: 500; }
  .funnel-bar .flabel { text-align: left; }
  .funnel-bar .fcount { font-weight: 700; margin-left: 12px; white-space: nowrap; }

  /* Stage mini-grid */
  .stage-grid { display: flex; flex-wrap: wrap; gap: 6px; margin: 0.5rem 0; }
  .stage-chip { background: #fff; border: 1px solid #ddd; border-radius: 6px; padding: 4px 10px; text-align: center; font-size: 0.85em; }
  .stage-chip .count { font-weight: 700; }
  .stage-chip .label { color: #666; font-size: 0.8em; }

  /* Chart container */
  .chart-row { display: flex; flex-wrap: wrap; gap: 1.5rem; align-items: flex-start; }
  .chart-box { flex: 0 0 280px; }
  .chart-box canvas { max-width: 280px; max-height: 280px; }
  .funnel-box { flex: 1; min-width: 320px; }

  /* Pagination */
  .pagination { display: flex; gap: 4px; align-items: center; margin: 1rem 0; flex-wrap: wrap; }
  .pagination a, .pagination span { padding: 4px 10px; border-radius: 4px; font-size: 0.9em; }
  .pagination a { background: #e9ecef; color: #333; }
  .pagination a:hover { background: #dee2e6; text-decoration: none; }
  .pagination .current { background: #3498db; color: #fff; font-weight: 600; }

  /* Info box */
  .info-box { background: #eaf4fc; border-left: 4px solid #3498db; padding: 1rem 1.2rem; border-radius: 0 6px 6px 0; margin: 1rem 0; font-size: 0.9em; line-height: 1.6; }
  .info-box code { background: #d6eaf8; padding: 2px 6px; border-radius: 3px; font-size: 0.9em; }

  /* Responsive */
  @media (max-width: 768px) {
    body { padding: 0 0.5rem 1rem; }
    .header { padding: 0.5rem; margin: 0 -0.5rem; }
    .header .search-form input { width: 120px; }
    .chart-row { flex-direction: column; }
    .chart-box { flex: auto; width: 100%; }
    th, td { padding: 4px 6px; font-size: 0.8em; }
  }
</style></head><body>
<div class="header">
  <a href="/admin/" class="brand">Blast Admin</a>
  <a href="/admin/">Dashboard</a>
  <a href="/admin/users">Users</a>
  <a href="/admin/activity">Activity</a>
  <a href="/admin/transactions">Transactions</a>
  <a href="/admin/payments">Payments</a>
  <a href="/admin/utm">UTM</a>
  <a href="/admin/sources">Sources</a>
  <a href="/admin/assets/" target="_blank" rel="noopener noreferrer">Assets</a>
  <form class="search-form" action="/admin/users" method="get">
    <input type="text" name="q" placeholder="Username / tg_id...">
    <button type="submit">Search</button>
  </form>
</div>
"""
_BASE_FOOT = "</body></html>"


def _page(title: str, body: str) -> str:
    return f"{_BASE_HEAD}<h1>{title}</h1>{body}{_BASE_FOOT}"


def _stage_label(stage: str) -> str:
    return _STAGE_LABELS.get(stage, stage)


def _event_label(event: str) -> str:
    return _EVENT_LABELS.get(event, event)


def _pagination_html(page: int, total_pages: int, base_url: str = "?") -> str:
    """Generate pagination links."""
    if total_pages <= 1:
        return ""
    parts = ['<div class="pagination">']
    if page > 1:
        parts.append(f'<a href="{base_url}page={page - 1}">&laquo;</a>')
    start = max(1, page - 3)
    end = min(total_pages, page + 3)
    if start > 1:
        parts.append(f'<a href="{base_url}page=1">1</a>')
        if start > 2:
            parts.append('<span>...</span>')
    for p in range(start, end + 1):
        if p == page:
            parts.append(f'<span class="current">{p}</span>')
        else:
            parts.append(f'<a href="{base_url}page={p}">{p}</a>')
    if end < total_pages:
        if end < total_pages - 1:
            parts.append('<span>...</span>')
        parts.append(f'<a href="{base_url}page={total_pages}">{total_pages}</a>')
    if page < total_pages:
        parts.append(f'<a href="{base_url}page={page + 1}">&raquo;</a>')
    parts.append('</div>')
    return "".join(parts)


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

        # ── Rating distribution for doughnut chart ──
        ratings = await credits_db.rating_distribution()
        rating_map = {r["rating"]: r["count"] for r in ratings}
        chart_labels = json.dumps([_RATING_LABELS.get(k, k) for k in ["low", "mid_low", "high"]])
        chart_data = json.dumps([rating_map.get(k, 0) for k in ["low", "mid_low", "high"]])
        chart_colors = json.dumps([_RATING_COLORS.get(k, "#999") for k in ["low", "mid_low", "high"]])
        total_ratings = sum(rating_map.values())

        # ── Funnel reach counts ──
        funnel_raw = await credits_db.funnel_reach_counts()
        funnel_map = {r["event"]: r["count"] for r in funnel_raw}
        max_funnel = max(funnel_map.values()) if funnel_map else 1
        funnel_html = ""
        for i, event in enumerate(_FUNNEL_ORDER):
            cnt = funnel_map.get(event, 0)
            pct = max(15, cnt / max_funnel * 100) if max_funnel > 0 else 15
            color = _FUNNEL_COLORS[i] if i < len(_FUNNEL_COLORS) else "#999"
            label = _event_label(event)
            funnel_html += (
                f'<div class="funnel-bar-wrap">'
                f'<div class="funnel-bar" style="width:{pct:.0f}%;background:{color}">'
                f'<span class="flabel">{label}</span>'
                f'<span class="fcount">{cnt}</span>'
                f'</div></div>\n'
            )

        # ── Current stage snapshot from Redis ──
        all_states = await state_store.list_all_states()
        stage_counts: dict[str, int] = {}
        for s in all_states:
            stage_counts[s.stage] = stage_counts.get(s.stage, 0) + 1
        stage_html = ""
        for stage, cnt in sorted(stage_counts.items(), key=lambda x: -x[1]):
            label = _stage_label(stage)
            stage_html += f'<div class="stage-chip"><div class="count">{cnt}</div><div class="label">{label}</div></div>'

        # ── Recent users ──
        users = await credits_db.list_users(limit=10)
        user_rows = ""
        for u in users:
            badge = "badge-ok" if u["credits"] > 0 else "badge-zero"
            uname = f"@{u['username']}" if u["username"] else str(u["tg_id"])
            user_rows += (
                f"<tr><td><a href='/admin/users/{u['tg_id']}'>{uname}</a></td>"
                f"<td>{u['tg_id']}</td>"
                f"<td><span class='badge {badge}'>{u['credits']}</span></td>"
                f"<td>{u['updated_at']}</td></tr>"
            )

        # ── Recent activity ──
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

        <div class="card">
        <div class="chart-row">
          <div class="chart-box">
            <h2>Оценки видео</h2>
            {"<p>Нет данных</p>" if total_ratings == 0 else f'<canvas id="ratingsChart"></canvas><p style="text-align:center;color:#888;font-size:0.85em">Всего оценок: {total_ratings}</p>'}
          </div>
          <div class="funnel-box">
            <h2>Воронка</h2>
            {funnel_html if funnel_html else '<p>Нет данных</p>'}
          </div>
        </div>
        </div>

        <div class="card">
        <h2>Текущий этап (live)</h2>
        <div class="stage-grid">{stage_html if stage_html else '<p>Нет данных</p>'}</div>
        </div>

        <div class="card">
        <h2>Последние пользователи</h2>
        <div class="table-wrap">
        <table><tr><th>Username</th><th>tg_id</th><th>Credits</th><th>Updated</th></tr>
        {user_rows}</table>
        </div>
        </div>

        <div class="card">
        <h2>Последние действия</h2>
        <div class="table-wrap">
        <table><tr><th>tg_id</th><th>Событие</th><th>Детали</th><th>Дата</th></tr>
        {act_rows}</table>
        </div>
        </div>

        {"" if total_ratings == 0 else '''
        <script>
        new Chart(document.getElementById("ratingsChart"), {
          type: "doughnut",
          data: {
            labels: ''' + chart_labels + ''',
            datasets: [{
              data: ''' + chart_data + ''',
              backgroundColor: ''' + chart_colors + ''',
              borderWidth: 2,
              borderColor: "#fff",
            }]
          },
          options: {
            responsive: true,
            plugins: {
              legend: { position: "bottom", labels: { padding: 16, font: { size: 13 } } },
            }
          }
        });
        </script>
        '''}
        """
        return _page("Blast Admin", body)

    # ── Users list ────────────────────────────────────────────────────

    @app.get("/admin/users", response_class=HTMLResponse)
    async def users_list(request: Request, _user: str = Depends(_check_auth)) -> str:
        q = request.query_params.get("q", "").strip()
        page = int(request.query_params.get("page", "1"))
        per_page = 50
        offset = (page - 1) * per_page

        if q:
            users = await credits_db.search_users(q, limit=per_page)
            total = len(users)
            total_pages = 1
        else:
            users = await credits_db.list_users(limit=per_page, offset=offset)
            total = await credits_db.count_users()
            total_pages = max(1, (total + per_page - 1) // per_page)

        # Get current stages from Redis
        all_states = await state_store.list_all_states()
        stages_map = {s.chat_id: s.stage for s in all_states}

        rows = ""
        for u in users:
            badge = "badge-ok" if u["credits"] > 0 else "badge-zero"
            uname = f"@{u['username']}" if u["username"] else str(u["tg_id"])
            stage = stages_map.get(u["tg_id"], "—")
            stage_lbl = _stage_label(stage) if stage != "—" else "—"
            first_utm = "/".join([x for x in [u.get("first_utm_source", ""), u.get("first_utm_campaign", "")] if x]) or "—"
            last_utm = "/".join([x for x in [u.get("last_utm_source", ""), u.get("last_utm_campaign", "")] if x]) or "—"
            rows += (
                f"<tr><td><a href='/admin/users/{u['tg_id']}'>{uname}</a></td>"
                f"<td>{u['tg_id']}</td>"
                f"<td><span class='badge {badge}'>{u['credits']}</span></td>"
                f"<td><span class='badge badge-stage'>{stage_lbl}</span></td>"
                f"<td>{first_utm}</td>"
                f"<td>{last_utm}</td>"
                f"<td>{u['created_at']}</td>"
                f"<td>{u['updated_at']}</td></tr>"
            )

        q_escaped = html_mod.escape(q)
        search_note = f'<p>Результаты поиска: <strong>{q_escaped}</strong> ({total})</p>' if q else ""
        base_url = f"?q={q_escaped}&" if q else "?"

        body = f"""
        <div class="card">
        {search_note}
        <p>Total: {total}</p>
        <div class="table-wrap">
        <table><tr><th>Username</th><th>tg_id</th><th>Credits</th><th>Этап</th><th>First UTM</th><th>Last UTM</th><th>Created</th><th>Updated</th></tr>
        {rows}</table>
        </div>
        {_pagination_html(page, total_pages, base_url)}
        </div>
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

        # Source
        source = await credits_db.get_user_source(tg_id)
        source_badge = f'<span class="badge badge-source">{html_mod.escape(source)}</span>' if source else '<span style="color:#999">direct</span>'

        # Package options
        pkg_options = "".join(f'<option value="{v}">{lbl}</option>' for v, lbl in _PACKAGES.items())

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
        <div class="card">
        <h2>{uname} (id: {tg_id})</h2>
        <p>Credits: <strong>{user['credits']}</strong> |
           Этап: <span class="badge badge-stage">{stage_lbl}</span> |
           Источник: {source_badge} |
           Created: {user['created_at']} | Updated: {user['updated_at']}</p>
        <p><strong>First UTM:</strong>
           source={user.get('first_utm_source') or '—'},
           medium={user.get('first_utm_medium') or '—'},
           campaign={user.get('first_utm_campaign') or '—'},
           content={user.get('first_utm_content') or '—'},
           term={user.get('first_utm_term') or '—'},
           at={user.get('first_utm_at') or '—'}</p>
        <p><strong>Last UTM:</strong>
           source={user.get('last_utm_source') or '—'},
           medium={user.get('last_utm_medium') or '—'},
           campaign={user.get('last_utm_campaign') or '—'},
           content={user.get('last_utm_content') or '—'},
           term={user.get('last_utm_term') or '—'},
           at={user.get('last_utm_at') or '—'}</p>
        </div>

        <div class="card">
        <h3>Выдать кредиты</h3>
        <form method="post" action="/admin/users/{tg_id}/credits">
          <input type="number" name="amount" value="0" min="-1000" max="10000">
          <input type="text" name="reason" placeholder="reason" style="width:150px">
          <button type="submit">Add credits</button>
        </form>
        </div>

        <div class="card">
        <h3>Активировать пакет (внешняя оплата)</h3>
        <p style="color:#666;font-size:0.85em">Начислит кредиты и переведёт пользователя на этап генерации (WAIT_AUDIO).
        Юзер получит уведомление в Telegram.</p>
        <form method="post" action="/admin/users/{tg_id}/activate" onsubmit="return confirm('Активировать пакет для {uname}?')">
          <select name="package">{pkg_options}</select>
          <button type="submit" class="btn-success">Активировать</button>
        </form>
        </div>

        <div class="card">
        <h3>Действия</h3>
        <div class="table-wrap">
        <table><tr><th>#</th><th>Событие</th><th>Детали</th><th>Дата</th></tr>
        {act_rows if act_rows else '<tr><td colspan="4">Нет данных</td></tr>'}</table>
        </div>
        </div>

        <div class="card">
        <h3>Транзакции</h3>
        <div class="table-wrap">
        <table><tr><th>#</th><th>Amount</th><th>Reason</th><th>Note</th><th>Date</th></tr>
        {tx_rows if tx_rows else '<tr><td colspan="5">Нет данных</td></tr>'}</table>
        </div>
        </div>
        """
        return _page(f"User {uname}", body)

    @app.post("/admin/users/{tg_id}/credits")
    async def user_add_credits(tg_id: int, amount: int = Form(...), reason: str = Form("admin_panel"), _user: str = Depends(_check_auth)) -> RedirectResponse:
        await credits_db.add_credits(tg_id, amount, reason, admin_note=f"via panel by {_user}")
        return RedirectResponse(f"/admin/users/{tg_id}", status_code=303)

    # ── Activate package (external payment) ───────────────────────────

    @app.post("/admin/users/{tg_id}/activate")
    async def user_activate_package(tg_id: int, package: int = Form(...), _user: str = Depends(_check_auth)) -> RedirectResponse:
        pkg_label = _PACKAGES.get(str(package), f"{package} credits")
        # 1. Add credits
        await credits_db.add_credits(
            tg_id, package,
            reason="admin_activate",
            admin_note=f"{pkg_label} — activated by {_user}",
        )
        # 2. Move to WAIT_AUDIO
        await state_store.reset_to_wait_audio(tg_id)
        # 3. Log event
        await credits_db.log_event(tg_id, "admin_activate", f"{pkg_label} by {_user}")
        # 4. Notify user via Telegram
        if bot_ref and bot_ref[0]:
            try:
                bal = await credits_db.get_balance(tg_id)
                await bot_ref[0].send_message(
                    tg_id,
                    f"Пакет активирован!\n"
                    f"Начислено {package} генераций. Доступно: {bal}\n\n"
                    "Отправь трек аудио-файлом, и я соберу клип.",
                )
            except Exception as e:
                log.warning("activate: failed to notify user %s: %s", tg_id, e)
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
        <div class="card">
        <div class="table-wrap">
        <table><tr><th>#</th><th>tg_id</th><th>Событие</th><th>Детали</th><th>Дата</th></tr>
        {rows}</table>
        </div>
        <p><a href="?page={page + 1}">Next page &raquo;</a></p>
        </div>
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
        <div class="card">
        <div class="table-wrap">
        <table><tr><th>#</th><th>tg_id</th><th>Amount</th><th>Reason</th><th>Note</th><th>Date</th></tr>
        {rows}</table>
        </div>
        <p><a href="?page={page + 1}">Next page &raquo;</a></p>
        </div>
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
                f"<td>{p['amount_rub']}&rub;</td>"
                f"<td>{p['package']}</td>"
                f"<td><span class='badge {status_cls}'>{p['status']}</span></td>"
                f"<td>{p.get('utm_source') or '—'}</td>"
                f"<td>{p.get('utm_campaign') or '—'}</td>"
                f"<td>{p['created_at']}</td></tr>"
            )
        body = f"""
        <div class="card">
        <div class="table-wrap">
        <table><tr><th>#</th><th>tg_id</th><th>Order</th><th>Amount</th><th>Package</th><th>Status</th><th>UTM Source</th><th>UTM Campaign</th><th>Date</th></tr>
        {rows}</table>
        </div>
        <p><a href="?page={page + 1}">Next page &raquo;</a></p>
        </div>
        """
        return _page("Payments", body)

    # ── UTM summary ─────────────────────────────────────────────────

    @app.get("/admin/utm", response_class=HTMLResponse)
    async def utm_summary(_user: str = Depends(_check_auth)) -> str:
        rows_data = await credits_db.get_utm_summary(limit=200)
        rows = ""
        for row in rows_data:
            rows += (
                f"<tr>"
                f"<td>{row['source'] or '(none)'}</td>"
                f"<td>{row['medium'] or '(none)'}</td>"
                f"<td>{row['campaign'] or '(none)'}</td>"
                f"<td>{row['starts_count']}</td>"
                f"<td>{row['paid_orders']}</td>"
                f"<td>{row['revenue_rub']}₽</td>"
                f"</tr>"
            )
        body = f"""
        <div class="card">
        <div class="table-wrap">
        <table><tr><th>Source</th><th>Medium</th><th>Campaign</th><th>Starts</th><th>Paid</th><th>Revenue</th></tr>
        {rows if rows else '<tr><td colspan="6">Нет данных</td></tr>'}</table>
        </div>
        </div>
        """
        return _page("UTM Summary", body)

    # ── Sources (UTM tracking) ────────────────────────────────────────

    @app.get("/admin/sources", response_class=HTMLResponse)
    async def sources_page(_user: str = Depends(_check_auth)) -> str:
        dist = await credits_db.source_distribution()
        rows = ""
        for d in dist:
            rows += f"<tr><td>{html_mod.escape(d['source'])}</td><td><strong>{d['count']}</strong></td></tr>"

        bot_username = settings.tg_bot_token.split(":")[0] if settings.tg_bot_token else "YOUR_BOT"

        body = f"""
        <div class="card">
        <h2>Как создавать UTM-ссылки</h2>
        <div class="info-box">
            <p>Для отслеживания источников трафика используйте Telegram deep links с параметром <code>start</code>:</p>
            <p>
              <code>https://t.me/{bot_username}?start=instagram_bio</code><br>
              <code>https://t.me/{bot_username}?start=youtube_desc</code><br>
              <code>https://t.me/{bot_username}?start=vk_post_march</code><br>
              <code>https://t.me/{bot_username}?start=tiktok_link</code>
            </p>
            <p>Параметр после <code>start=</code> автоматически записывается как источник пользователя при первом запуске бота.</p>
            <p><strong>Правила:</strong></p>
            <ul>
              <li>Только латиница, цифры и подчёркивания (ограничение Telegram)</li>
              <li>Максимум 64 символа</li>
              <li>Не начинайте с <code>@</code> — это зарезервировано для рефералов</li>
              <li>Источник сохраняется только при первом запуске бота</li>
            </ul>
        </div>
        </div>

        <div class="card">
        <h2>Распределение по источникам</h2>
        <div class="table-wrap">
        <table><tr><th>Источник</th><th>Пользователей</th></tr>
        {rows if rows else '<tr><td colspan="2">Нет данных</td></tr>'}</table>
        </div>
        </div>
        """
        return _page("Источники трафика", body)

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
            emoji = {"CONFIRMED": "\u2705", "REJECTED": "\u274c", "REFUNDED": "\U0001f504", "REVERSED": "\U0001f504"}.get(status, "\U0001f4cb")
            try:
                user_info = await credits_db.get_user(tg_id)
                uname = f"@{user_info['username']}" if user_info and user_info.get("username") else str(tg_id)
                await bot_ref[0].send_message(
                    settings.manager_chat_id,
                    f"{emoji} Статус оплаты: {status_label}\n\n"
                    f"Пользователь: {uname}\n"
                    f"Пакет: {pkg}\n"
                    f"Сумма: {amount_rub}\u20bd\n"
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
                admin_note=f"pkg={pkg} order={order_id} amount={amount_rub}\u20bd",
            )
            await credits_db.log_event(tg_id, "payment_confirmed", f"{pkg} \u2014 {amount_rub}\u20bd")
            log.info("payment confirmed tg_id=%s pkg=%s credits=+%s", tg_id, pkg, credits_to_add)

            # Notify user + move to generation flow
            if bot_ref and bot_ref[0]:
                try:
                    from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
                    bal = await credits_db.get_balance(tg_id)
                    await bot_ref[0].send_message(
                        tg_id,
                        f"\u2705 Оплата прошла! Пакет \u00ab{pkg}\u00bb активирован.\n"
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
