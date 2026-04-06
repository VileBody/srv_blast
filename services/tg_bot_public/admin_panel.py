"""Lightweight admin web panel — runs as a background asyncio task inside the bot process."""

from __future__ import annotations

import asyncio
import html as html_mod
import json
import logging
import secrets
from urllib.parse import quote as url_quote, quote_plus
from typing import TYPE_CHECKING

import httpx
import uvicorn
from fastapi import FastAPI, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from services.orchestrator.windows_node_pool import normalize_windows_urls, runtime_windows_urls_key

from .render_node_pool import (
    RenderNodePoolError,
    create_render_server_from_clone,
    delete_render_server,
    list_render_servers,
    probe_render_node,
)
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
    "generation_failed": "Генерация с ошибкой",
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
    "admin_force_reset": "Force reset админом",
    "initial_grant": "Стартовые кредиты",
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
  <a href="/admin/jobs">Jobs</a>
  <a href="/admin/render-nodes">Render Nodes</a>
  <a href="/admin/assets/" target="_blank" rel="noopener noreferrer">Assets</a>
  <a href="/admin/llm-workers">LLM Workers</a>
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


def _llm_workers_runtime_warnings(workers: dict[str, dict[str, object]]) -> list[str]:
    enabled_types = 0
    useful_types = 0

    for row in workers.values():
        enabled = bool(row.get("enabled", False))
        weight = int(row.get("weight", 0) or 0)
        max_inflight = int(row.get("max_inflight", 0) or 0)
        if enabled:
            enabled_types += 1
        if enabled and weight > 0 and max_inflight > 0:
            useful_types += 1

    warnings: list[str] = []
    if enabled_types == 0:
        warnings.append("no_enabled_types: все worker types выключены, admission недоступен")
    elif useful_types == 0:
        warnings.append(
            "zero_useful_weight: нет ни одного enabled worker с weight > 0 и max_inflight > 0"
        )
    return warnings


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


def _seconds_to_age(seconds: int) -> str:
    sec = max(0, int(seconds))
    if sec < 60:
        return f"{sec}s"
    mins, rem = divmod(sec, 60)
    if mins < 60:
        return f"{mins}m {rem}s"
    hours, mins = divmod(mins, 60)
    if hours < 24:
        return f"{hours}h {mins}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def _project_chat_id(project_id: str) -> int | None:
    raw = str(project_id or "").strip()
    if not raw.startswith("tg-"):
        return None
    part = raw[3:].split("-", 1)[0].strip()
    if not part.isdigit():
        return None
    try:
        return int(part)
    except Exception:
        return None


def _query_int(
    request: Request,
    name: str,
    *,
    default: int,
    min_value: int | None = None,
    max_value: int | None = None,
) -> int:
    raw = str(request.query_params.get(name, str(default))).strip()
    try:
        value = int(raw or str(default))
    except Exception:
        value = int(default)
    if min_value is not None:
        value = max(int(min_value), value)
    if max_value is not None:
        value = min(int(max_value), value)
    return value


def _payment_status_rank(status: str) -> int:
    st = str(status or "").strip().upper()
    ranks = {
        "NEW": 0,
        "FORM_SHOWED": 1,
        "AUTHORIZED": 2,
        "CONFIRMED": 3,
        "REJECTED": 4,
        "REVERSED": 4,
        "REFUNDED": 4,
        "PARTIAL_REFUNDED": 4,
        "DEADLINE_EXPIRED": 4,
        "CANCELED": 4,
    }
    return int(ranks.get(st, -1))


def _should_apply_payment_status_update(current_status: str, incoming_status: str) -> bool:
    cur = str(current_status or "").strip().upper()
    inc = str(incoming_status or "").strip().upper()
    if not inc:
        return False
    if not cur:
        return True
    if cur == inc:
        return True
    cur_rank = _payment_status_rank(cur)
    inc_rank = _payment_status_rank(inc)
    if cur_rank >= 0 and inc_rank >= 0 and inc_rank < cur_rank:
        return False
    return True


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
        if not secrets.compare_digest(creds.password.encode(), settings.admin_panel_password.encode()):
            raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})
        return creds.username

    async def _orchestrator_get_llm_workers() -> dict:
        base = str(settings.orchestrator_public_url or "").strip().rstrip("/")
        if not base:
            raise RuntimeError("ORCHESTRATOR_PUBLIC_URL is empty")
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(f"{base}/llm-workers")
        if resp.status_code >= 300:
            raise RuntimeError(f"orchestrator GET /llm-workers failed: {resp.status_code} {resp.text}")
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"orchestrator GET /llm-workers returned non-object: {data!r}")
        return data

    async def _orchestrator_put_llm_workers(payload: dict) -> dict:
        base = str(settings.orchestrator_public_url or "").strip().rstrip("/")
        if not base:
            raise RuntimeError("ORCHESTRATOR_PUBLIC_URL is empty")
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.put(f"{base}/llm-workers", json=payload)
        if resp.status_code >= 300:
            raise RuntimeError(f"orchestrator PUT /llm-workers failed: {resp.status_code} {resp.text}")
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"orchestrator PUT /llm-workers returned non-object: {data!r}")
        return data

    async def _orchestrator_get_active_jobs(*, min_age_seconds: int, limit: int) -> dict:
        base = str(settings.orchestrator_public_url or "").strip().rstrip("/")
        if not base:
            raise RuntimeError("ORCHESTRATOR_PUBLIC_URL is empty")
        params = {
            "min_age_seconds": max(0, int(min_age_seconds)),
            "limit": max(1, min(int(limit), 500)),
        }
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.get(f"{base}/jobs/active", params=params)
        if resp.status_code >= 300:
            raise RuntimeError(f"orchestrator GET /jobs/active failed: {resp.status_code} {resp.text}")
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"orchestrator GET /jobs/active returned non-object: {data!r}")
        return data

    async def _orchestrator_kill_job(*, job_id: str, reason: str) -> dict:
        base = str(settings.orchestrator_public_url or "").strip().rstrip("/")
        if not base:
            raise RuntimeError("ORCHESTRATOR_PUBLIC_URL is empty")
        jid = str(job_id or "").strip()
        if not jid:
            raise RuntimeError("job_id is empty")
        payload = {"reason": " ".join(str(reason or "").split()).strip() or "stuck_job_manual_kill"}
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(f"{base}/jobs/{jid}/kill", json=payload)
        if resp.status_code >= 300:
            raise RuntimeError(f"orchestrator POST /jobs/{jid}/kill failed: {resp.status_code} {resp.text}")
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"orchestrator POST /jobs/{jid}/kill returned non-object: {data!r}")
        return data

    async def _orchestrator_get_job(*, job_id: str) -> dict:
        base = str(settings.orchestrator_public_url or "").strip().rstrip("/")
        if not base:
            raise RuntimeError("ORCHESTRATOR_PUBLIC_URL is empty")
        jid = str(job_id or "").strip()
        if not jid:
            raise RuntimeError("job_id is empty")
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(f"{base}/jobs/{jid}")
        if resp.status_code == 404:
            return {}
        if resp.status_code >= 300:
            raise RuntimeError(f"orchestrator GET /jobs/{jid} failed: {resp.status_code} {resp.text}")
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"orchestrator GET /jobs/{jid} returned non-object: {data!r}")
        return data

    async def _orchestrator_get_metrics() -> dict:
        base = str(settings.orchestrator_public_url or "").strip().rstrip("/")
        if not base:
            raise RuntimeError("ORCHESTRATOR_PUBLIC_URL is empty")
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(f"{base}/metrics")
        if resp.status_code >= 300:
            raise RuntimeError(f"orchestrator GET /metrics failed: {resp.status_code} {resp.text}")
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"orchestrator GET /metrics returned non-object: {data!r}")
        return data

    async def _safe_get_metrics() -> dict:
        try:
            return await _orchestrator_get_metrics()
        except Exception:
            return {}

    # ── Dashboard ─────────────────────────────────────────────────────

    @app.get("/admin/", response_class=HTMLResponse)
    async def dashboard(request: Request, _user: str = Depends(_check_auth)) -> str:
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td

        # ── Period selection via query params ──
        now_utc = _dt.now(_tz.utc)
        today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        period_param = str(request.query_params.get("period", "")).strip()
        date_from_param = str(request.query_params.get("date_from", "")).strip()
        date_to_param = str(request.query_params.get("date_to", "")).strip()

        _PERIOD_PRESETS = {
            "1d": ("Сегодня", today_start, now_utc),
            "7d": ("7 дней", now_utc - _td(days=7), now_utc),
            "30d": ("30 дней", now_utc - _td(days=30), now_utc),
            "90d": ("90 дней", now_utc - _td(days=90), now_utc),
            "all": ("Всё время", _dt(2020, 1, 1, tzinfo=_tz.utc), now_utc),
        }

        # Determine effective range
        if date_from_param and date_to_param:
            # Custom range from form
            try:
                period_from = _dt.strptime(date_from_param, "%Y-%m-%d").replace(tzinfo=_tz.utc)
                period_to = _dt.strptime(date_to_param, "%Y-%m-%d").replace(tzinfo=_tz.utc) + _td(days=1)
                active_period = "custom"
            except ValueError:
                period_from, period_to, active_period = now_utc - _td(days=30), now_utc, "30d"
        elif period_param in _PERIOD_PRESETS:
            _, period_from, period_to = _PERIOD_PRESETS[period_param]
            active_period = period_param
        else:
            active_period = "30d"
            _, period_from, period_to = _PERIOD_PRESETS["30d"]

        total, ratings, funnel_raw, stage_counts, users, recent, payments_summary, period_stats_row, metrics_data = await asyncio.gather(
            credits_db.count_users(),
            credits_db.rating_distribution(),
            credits_db.funnel_reach_counts(),
            state_store.list_stage_counts(),
            credits_db.list_users(limit=10),
            credits_db.get_activity(limit=10),
            credits_db.payments_status_summary(),
            credits_db.period_stats_range(period_from, period_to),
            _safe_get_metrics(),
        )

        # ── Rating distribution for doughnut chart ──
        rating_map = {r["rating"]: r["count"] for r in ratings}
        chart_labels = json.dumps([_RATING_LABELS.get(k, k) for k in ["low", "mid_low", "high"]])
        chart_data = json.dumps([rating_map.get(k, 0) for k in ["low", "mid_low", "high"]])
        chart_colors = json.dumps([_RATING_COLORS.get(k, "#999") for k in ["low", "mid_low", "high"]])
        total_ratings = sum(rating_map.values())

        # ── Funnel reach counts with conversion ──
        funnel_map = {r["event"]: r["count"] for r in funnel_raw}
        max_funnel = max(funnel_map.values()) if funnel_map else 1
        first_cnt = funnel_map.get(_FUNNEL_ORDER[0], 0) or 1
        funnel_html = ""
        for i, event in enumerate(_FUNNEL_ORDER):
            cnt = funnel_map.get(event, 0)
            pct = max(15, cnt / max_funnel * 100) if max_funnel > 0 else 15
            conv = cnt / first_cnt * 100
            color = _FUNNEL_COLORS[i] if i < len(_FUNNEL_COLORS) else "#999"
            label = _event_label(event)
            funnel_html += (
                f'<div class="funnel-bar-wrap">'
                f'<div class="funnel-bar" style="width:{pct:.0f}%;background:{color}">'
                f'<span class="flabel">{label}</span>'
                f'<span class="fcount">{cnt} <small>({conv:.0f}%)</small></span>'
                f'</div></div>\n'
            )

        # ── Current stage snapshot from indexed Redis counters ──
        stage_html = ""
        for stage, cnt in sorted(stage_counts.items(), key=lambda x: -x[1]):
            label = _stage_label(stage)
            stage_html += f'<div class="stage-chip"><div class="count">{cnt}</div><div class="label">{label}</div></div>'

        # ── Recent users ──
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
        act_rows = ""
        for a in recent:
            act_rows += (
                f"<tr><td><a href='/admin/users/{a['tg_id']}'>{a['tg_id']}</a></td>"
                f"<td>{_event_label(a['event'])}</td>"
                f"<td>{a['detail']}</td>"
                f"<td>{a['created_at']}</td></tr>"
            )

        # ── Period pills HTML ──
        period_pills_html = ""
        for _pk, (_plbl, _, _) in _PERIOD_PRESETS.items():
            _pill_style = "background:#2c3e50;font-weight:700" if active_period == _pk else "background:#bdc3c7;color:#333"
            period_pills_html += f'<a href="/admin/?period={_pk}" class="btn" style="{_pill_style}">{_plbl}</a> '
        period_custom_badge = f'<span class="badge badge-stage">{html_mod.escape(date_from_param)} — {html_mod.escape(date_to_param)}</span>' if active_period == "custom" else ""
        period_date_from_val = date_from_param or period_from.strftime("%Y-%m-%d")
        period_date_to_val = date_to_param or (period_to - _td(days=1)).strftime("%Y-%m-%d")

        # ── Queue / jobs metrics card ──
        job_counts = metrics_data.get("job_status_counts") or {} if isinstance(metrics_data, dict) else {}
        celery_workers = metrics_data.get("workers") or {} if isinstance(metrics_data, dict) else {}
        llm_inflight = metrics_data.get("llm_inflight_by_worker_type") or {} if isinstance(metrics_data, dict) else {}
        if job_counts:
            q_new = int(job_counts.get("NEW", 0))
            q_queued = int(job_counts.get("QUEUED", 0))
            q_running = int(job_counts.get("RUNNING", 0))
            q_succeeded = int(job_counts.get("SUCCEEDED", 0))
            q_failed = int(job_counts.get("FAILED", 0))

            worker_rows = ""
            for wname, wdata in celery_workers.items():
                if isinstance(wdata, dict):
                    worker_rows += f"<tr><td>{html_mod.escape(wname)}</td><td>{wdata.get('active', 0)}</td><td>{wdata.get('reserved', 0)}</td></tr>"

            llm_chips = ""
            for lt, cnt in llm_inflight.items():
                llm_chips += f'<div class="stage-chip"><div class="count">{cnt}</div><div class="label">{lt}</div></div>'

            metrics_card = f"""
            <div class="card">
            <h2>Очередь и джобы</h2>
            <div class="stage-grid">
              <div class="stage-chip"><div class="count">{q_new}</div><div class="label">NEW</div></div>
              <div class="stage-chip"><div class="count" style="color:#f39c12">{q_queued}</div><div class="label">QUEUED</div></div>
              <div class="stage-chip"><div class="count" style="color:#3498db">{q_running}</div><div class="label">RUNNING</div></div>
              <div class="stage-chip"><div class="count" style="color:#27ae60">{q_succeeded}</div><div class="label">SUCCEEDED</div></div>
              <div class="stage-chip"><div class="count" style="color:#e74c3c">{q_failed}</div><div class="label">FAILED</div></div>
            </div>
            {f'<h3 style="margin-top:1rem">LLM inflight</h3><div class="stage-grid">{llm_chips}</div>' if llm_chips else ''}
            {f'<h3 style="margin-top:1rem">Celery workers</h3><div class="table-wrap"><table><tr><th>Worker</th><th>Active</th><th>Reserved</th></tr>{worker_rows}</table></div>' if worker_rows else ''}
            <p style="margin-top:8px"><a href="/admin/jobs?min_age_seconds=0">Все active jobs &rarr;</a></p>
            </div>
            """
        else:
            metrics_card = ""

        body = f"""
        <div class="card">
        <h2>Всего пользователей: {total}</h2>
        <p>Выручка (CONFIRMED): <strong>{int(payments_summary.get('confirmed_revenue_rub', 0)):,}&nbsp;&#8381;</strong></p>
        <p>Ожидает списания (AUTHORIZED): <strong>{int(payments_summary.get('authorized_revenue_rub', 0)):,}&nbsp;&#8381;</strong></p>
        <p>Видимая сумма (CONFIRMED + AUTHORIZED): <strong>{int(payments_summary.get('visible_revenue_rub', 0)):,}&nbsp;&#8381;</strong></p>
        <div class="chart-row">
          <div class="chart-box">
            <h3>Оценки видео</h3>
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
        <h2>Статистика по периодам</h2>
        <div style="display:flex;gap:4px;flex-wrap:wrap;align-items:center;margin-bottom:10px">
          {period_pills_html}
        </div>
        <form method="get" action="/admin/" style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:10px">
          <label>С: <input type="date" name="date_from" value="{period_date_from_val}"></label>
          <label>По: <input type="date" name="date_to" value="{period_date_to_val}"></label>
          <button type="submit">Показать</button>
          {period_custom_badge}
        </form>
        <div class="table-wrap">
        <table>
          <tr>
            <th>Новые пользователи</th>
            <th>Стартовали</th>
            <th>Генерация старт</th>
            <th>Генерация done</th>
            <th>Генерация fail</th>
            <th>Интент покупки</th>
            <th>Оплат подтвержд.</th>
            <th>Выручка</th>
          </tr>
          <tr>
            <td>{int(period_stats_row.get('users_new', 0))}</td>
            <td>{int(period_stats_row.get('starts_users', 0))}</td>
            <td>{int(period_stats_row.get('generation_started_users', 0))}</td>
            <td>{int(period_stats_row.get('generation_done_users', 0))}</td>
            <td>{int(period_stats_row.get('generation_failed_users', 0))}</td>
            <td>{int(period_stats_row.get('purchase_intent_users', 0))}</td>
            <td>{int(period_stats_row.get('paid_orders', 0))}</td>
            <td>{int(period_stats_row.get('revenue_rub', 0)):,}&nbsp;&#8381;</td>
          </tr>
        </table>
        </div>
        </div>

        {metrics_card}

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
        page = _query_int(request, "page", default=1, min_value=1)
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

        # Get current stages for requested page only (no full state scan).
        stages_map = await state_store.get_stages_for_chat_ids([int(u["tg_id"]) for u in users])

        rows = ""
        for u in users:
            badge = "badge-ok" if u["credits"] > 0 else "badge-zero"
            uname = f"@{u['username']}" if u["username"] else str(u["tg_id"])
            stage = stages_map.get(u["tg_id"], "—")
            stage_lbl = _stage_label(stage) if stage != "—" else "—"
            src = u.get("source", "")
            src_cell = f'<a href="/admin/sources/{url_quote(src, safe="")}" class="badge badge-source">{html_mod.escape(src)}</a>' if src else '<span style="color:#ccc">—</span>'
            rows += (
                f"<tr><td><a href='/admin/users/{u['tg_id']}'>{uname}</a></td>"
                f"<td>{u['tg_id']}</td>"
                f"<td><span class='badge {badge}'>{u['credits']}</span></td>"
                f"<td><span class='badge badge-stage'>{stage_lbl}</span></td>"
                f"<td>{src_cell}</td>"
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
        <table><tr><th>Username</th><th>tg_id</th><th>Credits</th><th>Этап</th><th>Источник</th><th>Created</th><th>Updated</th></tr>
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
                f"<td>{t['reason']}</td><td>{html_mod.escape(str(t.get('actor') or '—'))}</td>"
                f"<td>{html_mod.escape(str(t.get('order_id') or '—'))}</td>"
                f"<td>{t['admin_note']}</td><td>{t['created_at']}</td></tr>"
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
        <p><a href="/admin/users">&laquo; Все пользователи</a></p>
        <div class="card">
        <h2>{html_mod.escape(uname)} (id: {tg_id})</h2>
        <p>Credits: <strong>{user['credits']}</strong> |
           Этап: <span class="badge badge-stage">{stage_lbl}</span> |
           Источник: {source_badge} |
           Created: {user['created_at']} | Updated: {user['updated_at']}</p>
        </div>

        <div class="card">
        <h3>Выдать кредиты</h3>
        <form method="post" action="/admin/users/{tg_id}/credits">
          <input type="number" name="amount" value="0" min="-1000" max="10000">
          <input type="text" name="reason" placeholder="reason" style="width:140px">
          <input type="text" name="order_id" placeholder="order_id (optional)" style="width:210px">
          <input type="text" name="note" placeholder="note (optional)" style="width:220px">
          <button type="submit">Add credits</button>
        </form>
        </div>

        <div class="card">
        <h3>Активировать пакет (внешняя оплата)</h3>
        <p style="color:#666;font-size:0.85em">Начислит кредиты и переведёт пользователя на этап генерации (WAIT_AUDIO).
        Юзер получит уведомление в Telegram.</p>
        <form method="post" action="/admin/users/{tg_id}/activate" onsubmit="return confirm('Активировать пакет для {html_mod.escape(uname, quote=True).replace(chr(39), "&#39;")}'?)">
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
        <table><tr><th>#</th><th>Amount</th><th>Reason</th><th>Actor</th><th>Order</th><th>Note</th><th>Date</th></tr>
        {tx_rows if tx_rows else '<tr><td colspan="7">Нет данных</td></tr>'}</table>
        </div>
        </div>
        """
        return _page(f"User {uname}", body)

    @app.post("/admin/users/{tg_id}/credits")
    async def user_add_credits(
        tg_id: int,
        amount: int = Form(...),
        reason: str = Form("admin_panel"),
        order_id: str = Form(""),
        note: str = Form(""),
        _user: str = Depends(_check_auth),
    ) -> RedirectResponse:
        note_parts = [str(note or "").strip(), f"via panel by {_user}"]
        merged_note = " | ".join(part for part in note_parts if part)
        await credits_db.add_credits(
            tg_id,
            amount,
            reason,
            admin_note=merged_note,
            actor=_user,
            order_id=str(order_id or "").strip(),
        )
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
        page = _query_int(request, "page", default=1, min_value=1)
        per_page = 50
        offset = (page - 1) * per_page
        acts, total = await asyncio.gather(
            credits_db.get_activity(limit=per_page, offset=offset),
            credits_db.count_activity(),
        )
        total_pages = max(1, (total + per_page - 1) // per_page)
        rows = ""
        for a in acts:
            rows += (
                f"<tr><td>{a['id']}</td>"
                f"<td><a href='/admin/users/{a['tg_id']}'>{a['tg_id']}</a></td>"
                f"<td>{_event_label(a['event'])}</td>"
                f"<td>{html_mod.escape(str(a['detail']))}</td>"
                f"<td>{a['created_at']}</td></tr>"
            )
        body = f"""
        <div class="card">
        <p>Total: {total}</p>
        <div class="table-wrap">
        <table><tr><th>#</th><th>tg_id</th><th>Событие</th><th>Детали</th><th>Дата</th></tr>
        {rows}</table>
        </div>
        {_pagination_html(page, total_pages)}
        </div>
        """
        return _page("Activity Log", body)

    # ── Transactions ──────────────────────────────────────────────────

    @app.get("/admin/transactions", response_class=HTMLResponse)
    async def transactions_list(request: Request, _user: str = Depends(_check_auth)) -> str:
        page = _query_int(request, "page", default=1, min_value=1)
        per_page = 50
        offset = (page - 1) * per_page
        txs, total = await asyncio.gather(
            credits_db.get_transactions(limit=per_page, offset=offset),
            credits_db.count_transactions(),
        )
        total_pages = max(1, (total + per_page - 1) // per_page)
        rows = ""
        for t in txs:
            sign = "+" if t["amount"] > 0 else ""
            rows += (
                f"<tr><td>{t['id']}</td><td>{t['tg_id']}</td><td>{sign}{t['amount']}</td>"
                f"<td>{t['reason']}</td>"
                f"<td>{html_mod.escape(str(t.get('actor') or '—'))}</td>"
                f"<td>{html_mod.escape(str(t.get('order_id') or '—'))}</td>"
                f"<td>{html_mod.escape(str(t['admin_note']))}</td><td>{t['created_at']}</td></tr>"
            )
        body = f"""
        <div class="card">
        <p>Total: {total}</p>
        <div class="table-wrap">
        <table><tr><th>#</th><th>tg_id</th><th>Amount</th><th>Reason</th><th>Actor</th><th>Order</th><th>Note</th><th>Date</th></tr>
        {rows}</table>
        </div>
        {_pagination_html(page, total_pages)}
        </div>
        """
        return _page("Transactions", body)

    # ── Payments list ────────────────────────────────────────────────

    @app.get("/admin/payments", response_class=HTMLResponse)
    async def payments_list(request: Request, _user: str = Depends(_check_auth)) -> str:
        page = _query_int(request, "page", default=1, min_value=1)
        per_page = 50
        offset = (page - 1) * per_page
        pays, total = await asyncio.gather(
            credits_db.get_payments(limit=per_page, offset=offset),
            credits_db.count_payments(),
        )
        total_pages = max(1, (total + per_page - 1) // per_page)
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
                f"<td>{p['created_at']}</td></tr>"
            )
        body = f"""
        <div class="card">
        <p>Total: {total}</p>
        <div class="table-wrap">
        <table><tr><th>#</th><th>tg_id</th><th>Order</th><th>Amount</th><th>Package</th><th>Status</th><th>Date</th></tr>
        {rows}</table>
        </div>
        {_pagination_html(page, total_pages)}
        </div>
        """
        return _page("Payments", body)

    # ── UTM summary ─────────────────────────────────────────────────

    @app.get("/admin/utm", response_class=HTMLResponse)
    async def utm_summary(_user: str = Depends(_check_auth)) -> str:
        rows_data, payments_summary, payments_status = await asyncio.gather(
            credits_db.get_utm_summary(limit=200),
            credits_db.confirmed_payments_summary(),
            credits_db.payments_status_summary(),
        )
        utm_paid_orders = sum(int(row.get("paid_orders", 0) or 0) for row in rows_data)
        utm_revenue_rub = sum(int(row.get("revenue_rub", 0) or 0) for row in rows_data)
        total_paid_orders = int(payments_summary.get("orders_count", 0))
        total_revenue_rub = int(payments_summary.get("revenue_rub", 0))
        mismatch = (utm_paid_orders != total_paid_orders) or (utm_revenue_rub != total_revenue_rub)
        rows = ""
        for row in rows_data:
            rows += (
                f"<tr>"
                f"<td>{row['source'] or '(none)'}</td>"
                f"<td>{row['medium'] or '(none)'}</td>"
                f"<td>{row['campaign'] or '(none)'}</td>"
                f"<td>{row['content'] or '(none)'}</td>"
                f"<td>{row['term'] or '(none)'}</td>"
                f"<td>{row['starts_count']}</td>"
                f"<td>{row['paid_orders']}</td>"
                f"<td>{row['revenue_rub']}₽</td>"
                f"</tr>"
            )
        body = f"""
        <div class="card">
        <p>Подтвержденные оплаты (global): <strong>{total_paid_orders}</strong></p>
        <p>Выручка (global): <strong>{total_revenue_rub:,}&nbsp;&#8381;</strong></p>
        <p>Ожидает списания (AUTHORIZED, global): <strong>{int(payments_status.get('authorized_revenue_rub', 0)):,}&nbsp;&#8381;</strong></p>
        <p>Видимая сумма (CONFIRMED + AUTHORIZED): <strong>{int(payments_status.get('visible_revenue_rub', 0)):,}&nbsp;&#8381;</strong></p>
        <p>Сумма по UTM-строкам: <strong>{utm_paid_orders}</strong> оплат / <strong>{utm_revenue_rub:,}&nbsp;&#8381;</strong></p>
        <p style="color:{'#c0392b' if mismatch else '#1e8449'}">{'Есть расхождение между global и UTM суммами' if mismatch else 'Global и UTM суммы совпадают'}</p>
        </div>
        <div class="card">
        <div class="table-wrap">
        <table><tr><th>Source</th><th>Medium</th><th>Campaign</th><th>Content</th><th>Term</th><th>Starts</th><th>Paid</th><th>Revenue</th></tr>
        {rows if rows else '<tr><td colspan="8">Нет данных</td></tr>'}</table>
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
            src_escaped = html_mod.escape(d["source"])
            src_url = url_quote(d["source"], safe="")
            rows += f"<tr><td><a href='/admin/sources/{src_url}'>{src_escaped}</a></td><td><strong>{d['count']}</strong></td></tr>"

        bot_username = settings.tg_bot_username or "YOUR_BOT"

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

    # ── Source detail page ──────────────────────────────────────────

    @app.get("/admin/sources/{source:path}", response_class=HTMLResponse)
    async def source_detail(source: str, _user: str = Depends(_check_auth)) -> str:
        src_escaped = html_mod.escape(source)
        users = await credits_db.users_by_source(source)
        tg_ids = [u["tg_id"] for u in users]
        total_users = len(tg_ids)

        funnel_raw = await credits_db.funnel_reach_counts_for_users(tg_ids)
        funnel_map = {r["event"]: r["count"] for r in funnel_raw}
        max_funnel = max(funnel_map.values()) if funnel_map else 1
        first_cnt = funnel_map.get(_FUNNEL_ORDER[0], 0) or 1
        funnel_html = ""
        for i, event in enumerate(_FUNNEL_ORDER):
            cnt = funnel_map.get(event, 0)
            pct = max(15, cnt / max_funnel * 100) if max_funnel > 0 else 15
            conv = cnt / first_cnt * 100
            color = _FUNNEL_COLORS[i] if i < len(_FUNNEL_COLORS) else "#999"
            label = _event_label(event)
            funnel_html += (
                f'<div class="funnel-bar-wrap">'
                f'<div class="funnel-bar" style="width:{pct:.0f}%;background:{color}">'
                f'<span class="flabel">{label}</span>'
                f'<span class="fcount">{cnt} <small>({conv:.0f}%)</small></span>'
                f'</div></div>\n'
            )

        revenue = await credits_db.revenue_breakdown_for_users(tg_ids)

        user_rows = ""
        for u in users:
            uname = f"@{u['username']}" if u["username"] else str(u["tg_id"])
            badge = "badge-ok" if u["credits"] > 0 else "badge-zero"
            user_rows += (
                f"<tr><td><a href='/admin/users/{u['tg_id']}'>{uname}</a></td>"
                f"<td>{u['tg_id']}</td>"
                f"<td><span class='badge {badge}'>{u['credits']}</span></td>"
                f"<td>{u['created_at']}</td></tr>"
            )

        body = f"""
        <p><a href="/admin/sources">&laquo; Все источники</a></p>
        <div class="card">
        <h2>Источник: <span class="badge badge-source">{src_escaped}</span></h2>
        <p>Пользователей: <strong>{total_users}</strong> &nbsp;|&nbsp;
           Выручка (CONFIRMED): <strong>{int(revenue.get('confirmed_revenue_rub', 0)):,}&nbsp;&#8381;</strong><br>
           Ожидает списания (AUTHORIZED): <strong>{int(revenue.get('authorized_revenue_rub', 0)):,}&nbsp;&#8381;</strong><br>
           Видимая сумма (CONFIRMED + AUTHORIZED): <strong>{int(revenue.get('visible_revenue_rub', 0)):,}&nbsp;&#8381;</strong></p>
        </div>
        <div class="card">
        <h2>Воронка</h2>
        {funnel_html if funnel_html else '<p>Нет данных</p>'}
        </div>
        <div class="card">
        <h2>Пользователи</h2>
        <div class="table-wrap">
        <table><tr><th>Username</th><th>tg_id</th><th>Credits</th><th>Дата регистрации</th></tr>
        {user_rows if user_rows else '<tr><td colspan="4">Нет данных</td></tr>'}</table>
        </div>
        </div>
        """
        return _page(f"Источник: {src_escaped}", body)

    # ── Jobs (stuck/in-flight control) ──────────────────────────────

    @app.get("/admin/jobs", response_class=HTMLResponse)
    async def jobs_page(request: Request, _user: str = Depends(_check_auth)) -> str:
        try:
            min_age_seconds = int(str(request.query_params.get("min_age_seconds", "900")).strip() or "900")
        except Exception:
            min_age_seconds = 900
        try:
            limit = int(str(request.query_params.get("limit", "200")).strip() or "200")
        except Exception:
            limit = 200
        min_age_seconds = max(0, min(min_age_seconds, 604800))
        limit = max(1, min(limit, 500))

        ok_msg = html_mod.escape(str(request.query_params.get("ok", "")).strip())
        err_msg = html_mod.escape(str(request.query_params.get("err", "")).strip())
        data: dict = {}
        if not err_msg:
            try:
                data = await _orchestrator_get_active_jobs(min_age_seconds=min_age_seconds, limit=limit)
            except Exception as e:
                err_msg = html_mod.escape(str(e))

        jobs_obj = data.get("jobs") if isinstance(data, dict) else None
        jobs = jobs_obj if isinstance(jobs_obj, list) else []
        total_active = int(data.get("total_active", 0) or 0) if isinstance(data, dict) else 0

        dozzle_base = str(settings.dozzle_base_url or "").strip().rstrip("/")
        rows = ""
        for row in jobs:
            if not isinstance(row, dict):
                continue
            jid_raw = str(row.get("job_id") or "")
            jid = html_mod.escape(jid_raw)
            status = html_mod.escape(str(row.get("status") or ""))
            stage = html_mod.escape(str(row.get("stage") or ""))
            project_id = html_mod.escape(str(row.get("project_id") or ""))
            worker_type = html_mod.escape(str(row.get("llm_worker_type") or ""))
            age_seconds = int(row.get("age_seconds", 0) or 0)
            updated_at = float(row.get("updated_at", 0.0) or 0.0)
            age_human = _seconds_to_age(age_seconds)
            updated_s = f"{updated_at:.0f}"

            # Dozzle log links
            logs_cell = "—"
            if dozzle_base:
                jid_q = url_quote(jid_raw, safe="")
                logs_cell = (
                    f"<a href='{dozzle_base}/container/worker-build?search={jid_q}' target='_blank' title='Build logs'>B</a>"
                    f" <a href='{dozzle_base}/container/worker-render?search={jid_q}' target='_blank' title='Render logs'>R</a>"
                    f" <a href='{dozzle_base}/container/orchestrator-api?search={jid_q}' target='_blank' title='Orchestrator logs'>O</a>"
                )

            rows += (
                f"<tr>"
                f"<td><a href='/admin/jobs/{jid}'><code>{jid[:12]}…</code></a></td>"
                f"<td>{status}</td>"
                f"<td>{stage or '—'}</td>"
                f"<td>{project_id or '—'}</td>"
                f"<td>{worker_type or '—'}</td>"
                f"<td>{age_human}</td>"
                f"<td>{updated_s}</td>"
                f"<td>{logs_cell}</td>"
                f"<td>"
                f"  <form method='post' action='/admin/jobs/{jid}/kill' "
                f"        onsubmit=\"return confirm('Kill job {jid}?');\">"
                f"    <input type='hidden' name='min_age_seconds' value='{min_age_seconds}'>"
                f"    <input type='hidden' name='limit' value='{limit}'>"
                f"    <input type='text' name='reason' value='stuck_job_manual_kill' style='width:170px'>"
                f"    <button type='submit' class='btn-danger'>Kill</button>"
                f"  </form>"
                f"</td>"
                f"</tr>"
            )

        has_logs = bool(dozzle_base)
        logs_th = "<th>Logs</th>" if has_logs else ""
        colspan = "9" if has_logs else "8"

        body = f"""
        <div class="card">
        <h2>In-flight / stuck jobs</h2>
        {f"<p style='color:#1e8449'><strong>OK:</strong> {ok_msg}</p>" if ok_msg else ""}
        {f"<p style='color:#c0392b'><strong>Ошибка:</strong> {err_msg}</p>" if err_msg else ""}
        <form method="get" action="/admin/jobs" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <label>Min age (sec): <input type="number" name="min_age_seconds" value="{min_age_seconds}" min="0" max="604800"></label>
          <label>Limit: <input type="number" name="limit" value="{limit}" min="1" max="500"></label>
          <button type="submit">Refresh</button>
        </form>
        <p style="margin-top:8px">Active jobs (after filter): <strong>{total_active}</strong></p>
        <div class="table-wrap">
        <table><tr><th>Job</th><th>Status</th><th>Stage</th><th>Project</th><th>Worker</th><th>Age</th><th>Updated</th>{logs_th}<th>Action</th></tr>
        {rows if rows else f'<tr><td colspan="{colspan}">Нет job по текущему фильтру</td></tr>'}</table>
        </div>
        <p style="color:#666;font-size:0.88em">Kill ставит job в FAILED и пытается revoke Celery task. Для проектов вида <code>tg-{{chat_id}}-...</code> дополнительно делается reset пользователя в WAIT_AUDIO.</p>
        </div>
        """
        return _page("Jobs", body)

    @app.post("/admin/jobs/{job_id}/kill")
    async def jobs_kill(
        job_id: str,
        reason: str = Form("stuck_job_manual_kill"),
        min_age_seconds: int = Form(900),
        limit: int = Form(200),
        _user: str = Depends(_check_auth),
    ) -> RedirectResponse:
        min_age = max(0, min(int(min_age_seconds), 604800))
        out_limit = max(1, min(int(limit), 500))
        base_q = f"min_age_seconds={min_age}&limit={out_limit}"

        jid = str(job_id or "").strip()
        if not jid:
            return RedirectResponse(f"/admin/jobs?{base_q}&err={quote_plus('empty job_id')}", status_code=303)

        actor_reason = " ".join(f"{reason} by {_user}".split()).strip()
        try:
            res = await _orchestrator_kill_job(job_id=jid, reason=actor_reason)
            project_id = str(res.get("project_id") or "")
            chat_id = _project_chat_id(project_id)
            if chat_id:
                try:
                    await state_store.reset_to_wait_audio(chat_id)
                    await credits_db.log_event(chat_id, "admin_force_reset", f"job={jid} by {_user}")
                except Exception as e:
                    log.warning("jobs_kill: reset_to_wait_audio failed job=%s chat_id=%s err=%s", jid, chat_id, e)
            revoked_ids = res.get("revoked_task_ids")
            revoked_count = len(revoked_ids) if isinstance(revoked_ids, list) else 0
            ok = f"killed job={jid}; revoked={revoked_count}; project={project_id or '-'}"
            return RedirectResponse(f"/admin/jobs?{base_q}&ok={quote_plus(ok)}", status_code=303)
        except Exception as e:
            return RedirectResponse(f"/admin/jobs?{base_q}&err={quote_plus(str(e))}", status_code=303)

    # ── Job detail page ───────────────────────────────────────────

    @app.get("/admin/jobs/{job_id}", response_class=HTMLResponse)
    async def job_detail(job_id: str, _user: str = Depends(_check_auth)) -> str:
        jid = str(job_id or "").strip()
        if not jid:
            raise HTTPException(404, "job_id is empty")

        try:
            data = await _orchestrator_get_job(job_id=jid)
        except Exception as e:
            return _page("Job Error", f'<div class="card"><p style="color:#c0392b">{html_mod.escape(str(e))}</p></div>')

        if not data:
            raise HTTPException(404, "Job not found")

        status = str(data.get("status") or "")
        stage = str(data.get("stage") or "")
        error = str(data.get("error") or "")
        created_at = float(data.get("created_at", 0) or 0)
        updated_at = float(data.get("updated_at", 0) or 0)
        queued_at = data.get("queued_at")
        started_at = data.get("started_at")
        finished_at = data.get("finished_at")
        request_obj = data.get("request") or {}
        result_obj = data.get("result")
        version = int(data.get("version", 0) or 0)
        idem_key = str(data.get("idempotency_key") or "")

        project_id = str(request_obj.get("project_id") or "")
        worker_type = str(request_obj.get("llm_worker_type") or "")

        import time as _time
        now = _time.time()
        age_seconds = max(0, int(now - updated_at)) if updated_at else 0

        def _ts(ts):
            if not ts:
                return "—"
            from datetime import datetime, timezone
            return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        # Status badge color
        status_color = {"SUCCEEDED": "#27ae60", "FAILED": "#e74c3c", "RUNNING": "#3498db", "QUEUED": "#f39c12", "NEW": "#95a5a6"}.get(status, "#999")

        # Dozzle log links
        dozzle_base = str(settings.dozzle_base_url or "").strip().rstrip("/")
        logs_html = ""
        if dozzle_base:
            jid_escaped = url_quote(jid, safe="")
            logs_html = f"""
            <div class="card">
            <h3>Логи контейнеров</h3>
            <p>
              <a href="{dozzle_base}/container/worker-build?search={jid_escaped}" target="_blank" rel="noopener">worker-build</a> &nbsp;|&nbsp;
              <a href="{dozzle_base}/container/worker-render?search={jid_escaped}" target="_blank" rel="noopener">worker-render</a> &nbsp;|&nbsp;
              <a href="{dozzle_base}/container/orchestrator-api?search={jid_escaped}" target="_blank" rel="noopener">orchestrator-api</a>
            </p>
            </div>
            """

        # Request/result as formatted JSON
        req_json = html_mod.escape(json.dumps(request_obj, indent=2, ensure_ascii=False, default=str)) if request_obj else "—"
        result_json = html_mod.escape(json.dumps(result_obj, indent=2, ensure_ascii=False, default=str)) if result_obj else "—"

        # Link to user if project_id has tg chat_id
        user_link = ""
        chat_id = _project_chat_id(project_id)
        if chat_id:
            user_link = f' &nbsp;(<a href="/admin/users/{chat_id}">user {chat_id}</a>)'

        error_html = (
            '<div class="card"><h3>Ошибка</h3>'
            f'<pre style="white-space:pre-wrap;color:#c0392b">{html_mod.escape(error)}</pre></div>'
        ) if error else ""

        result_html = (
            '<div class="card"><h3>Result</h3>'
            '<pre style="white-space:pre-wrap;max-height:400px;overflow:auto;background:#f8f9fa;padding:12px;border-radius:6px;font-size:0.85em">'
            f'{result_json}</pre></div>'
        ) if result_obj else ""

        jid_esc = html_mod.escape(jid)
        kill_html = (
            '<div class="card"><h3>Действия</h3>'
            f"<form method='post' action='/admin/jobs/{jid_esc}/kill'"
            f""" onsubmit="return confirm('Kill job {jid_esc}?');">"""
            "<input type='hidden' name='min_age_seconds' value='0'>"
            "<input type='hidden' name='limit' value='200'>"
            "<input type='text' name='reason' value='stuck_job_manual_kill' style='width:250px'>"
            " <button type='submit' class='btn-danger'>Kill</button>"
            "</form></div>"
        ) if status in ("NEW", "QUEUED", "RUNNING") else ""

        body = f"""
        <p><a href="/admin/jobs">&laquo; Jobs</a></p>
        <div class="card">
        <h2>Job <code>{jid_esc}</code></h2>
        <table>
          <tr><td style="width:160px"><strong>Status</strong></td><td><span style="color:{status_color};font-weight:700">{html_mod.escape(status)}</span></td></tr>
          <tr><td><strong>Stage</strong></td><td>{html_mod.escape(stage) or '—'}</td></tr>
          <tr><td><strong>Project</strong></td><td>{html_mod.escape(project_id) or '—'}{user_link}</td></tr>
          <tr><td><strong>Worker type</strong></td><td>{html_mod.escape(worker_type) or '—'}</td></tr>
          <tr><td><strong>Idempotency key</strong></td><td><code>{html_mod.escape(idem_key) or '—'}</code></td></tr>
          <tr><td><strong>Version</strong></td><td>{version}</td></tr>
          <tr><td><strong>Age</strong></td><td>{_seconds_to_age(age_seconds)}</td></tr>
          <tr><td><strong>Created</strong></td><td>{_ts(created_at)}</td></tr>
          <tr><td><strong>Queued</strong></td><td>{_ts(queued_at)}</td></tr>
          <tr><td><strong>Started</strong></td><td>{_ts(started_at)}</td></tr>
          <tr><td><strong>Finished</strong></td><td>{_ts(finished_at)}</td></tr>
          <tr><td><strong>Updated</strong></td><td>{_ts(updated_at)}</td></tr>
        </table>
        </div>

        {error_html}

        {logs_html}

        <div class="card">
        <h3>Request</h3>
        <pre style="white-space:pre-wrap;max-height:400px;overflow:auto;background:#f8f9fa;padding:12px;border-radius:6px;font-size:0.85em">{req_json}</pre>
        </div>

        {result_html}

        {kill_html}
        """
        return _page(f"Job {jid[:12]}…", body)

    # ── LLM workers runtime control ────────────────────────────────

    @app.get("/admin/llm-workers", response_class=HTMLResponse)
    async def llm_workers_page(_user: str = Depends(_check_auth)) -> str:
        err = ""
        data: dict = {}
        try:
            data = await _orchestrator_get_llm_workers()
        except Exception as e:
            err = html_mod.escape(str(e))

        workers_obj = data.get("workers") if isinstance(data, dict) else None
        workers = workers_obj if isinstance(workers_obj, dict) else {}
        order = ("sdk", "openrouter", "hybrid")
        runtime_warnings = _llm_workers_runtime_warnings(
            {
                wt: row if isinstance(row, dict) else {}
                for wt, row in workers.items()
            }
        )

        rows = ""
        for wt in order:
            row = workers.get(wt) if isinstance(workers.get(wt), dict) else {}
            enabled = bool(row.get("enabled", False))
            weight = int(row.get("weight", 0) or 0)
            max_inflight = int(row.get("max_inflight", 1) or 1)
            inflight = int(row.get("inflight", 0) or 0)
            slots = int(row.get("available_slots", max(0, max_inflight - inflight)) or 0)
            rows += (
                f"<tr>"
                f"<td><strong>{wt}</strong></td>"
                f"<td>{'on' if enabled else 'off'}</td>"
                f"<td>{weight}</td>"
                f"<td>{max_inflight}</td>"
                f"<td>{inflight}</td>"
                f"<td>{slots}</td>"
                f"</tr>"
            )

        def _enabled_select(name: str, selected: bool) -> str:
            on_sel = " selected" if selected else ""
            off_sel = " selected" if not selected else ""
            return (
                f'<select name="{name}">'
                f'<option value="1"{on_sel}>on</option>'
                f'<option value="0"{off_sel}>off</option>'
                f"</select>"
            )

        form_rows = ""
        for wt in order:
            row = workers.get(wt) if isinstance(workers.get(wt), dict) else {}
            enabled = bool(row.get("enabled", False))
            weight = int(row.get("weight", 1) or 1)
            max_inflight = int(row.get("max_inflight", 4) or 4)
            form_rows += (
                f"<tr>"
                f"<td><strong>{wt}</strong></td>"
                f"<td>{_enabled_select(f'{wt}_enabled', enabled)}</td>"
                f"<td><input type='number' name='{wt}_weight' value='{weight}' min='0' max='1000'></td>"
                f"<td><input type='number' name='{wt}_max_inflight' value='{max_inflight}' min='1' max='1000'></td>"
                f"</tr>"
            )

        body = f"""
        <div class="card">
        <h2>Текущий runtime статус</h2>
        {f"<p style='color:#c0392b'><strong>Ошибка:</strong> {err}</p>" if err else ""}
        {"".join(f"<p style='color:#c0392b'><strong>Warning:</strong> {html_mod.escape(w)}</p>" for w in runtime_warnings)}
        <div class="table-wrap">
        <table><tr><th>Worker</th><th>Enabled</th><th>Weight</th><th>Max inflight</th><th>Inflight</th><th>Free slots</th></tr>
        {rows if rows else '<tr><td colspan="6">Нет данных</td></tr>'}</table>
        </div>
        </div>

        <div class="card">
        <h2>Обновить конфиг</h2>
        <form method="post" action="/admin/llm-workers">
          <div class="table-wrap">
          <table><tr><th>Worker</th><th>Enabled</th><th>Weight</th><th>Max inflight</th></tr>
          {form_rows}
          </table>
          </div>
          <p><button type="submit" class="btn-success">Apply Runtime Config</button></p>
        </form>
        </div>
        """
        return _page("LLM Workers", body)

    @app.post("/admin/llm-workers")
    async def llm_workers_update(request: Request, _user: str = Depends(_check_auth)) -> RedirectResponse:
        form = await request.form()
        workers_payload: dict[str, dict[str, object]] = {}
        for wt in ("sdk", "openrouter", "hybrid"):
            enabled_raw = str(form.get(f"{wt}_enabled", "0")).strip().lower()
            enabled = enabled_raw in {"1", "true", "yes", "on"}
            try:
                weight = int(str(form.get(f"{wt}_weight", "1")).strip() or "1")
            except Exception:
                weight = 1
            try:
                max_inflight = int(str(form.get(f"{wt}_max_inflight", "4")).strip() or "4")
            except Exception:
                max_inflight = 4
            workers_payload[wt] = {
                "enabled": bool(enabled),
                "weight": max(0, int(weight)),
                "max_inflight": max(1, int(max_inflight)),
            }
        payload = {"workers": workers_payload}
        await _orchestrator_put_llm_workers(payload)
        return RedirectResponse("/admin/llm-workers", status_code=303)

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
        status = str(data.get("Status", "")).strip().upper()
        payment_id = str(data.get("PaymentId", ""))

        if not order_id:
            return PlainTextResponse("OK", status_code=200)

        # Dedup check
        if payment_id and await credits_db.is_payment_processed(payment_id, status):
            log.info("tbank notify: duplicate payment_id=%s status=%s", payment_id, status)
            return PlainTextResponse("OK", status_code=200)

        existing_payment = await credits_db.get_payment(order_id)
        current_status = str(existing_payment.get("status", "")).strip().upper() if existing_payment else ""
        should_apply_status = _should_apply_payment_status_update(current_status, status)
        if should_apply_status:
            await credits_db.update_payment_status(order_id, status, payment_id)
            payment = await credits_db.get_payment(order_id)
        else:
            payment = existing_payment
            log.info(
                "tbank notify: ignored status downgrade order=%s current=%s incoming=%s payment_id=%s",
                order_id,
                current_status,
                status,
                payment_id,
            )

        effective_status = str(payment.get("status", status) if payment else status).strip().upper()

        # Get payment info for notifications
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
            status_label = status_labels.get(effective_status, effective_status)
            emoji = {"CONFIRMED": "\u2705", "REJECTED": "\u274c", "REFUNDED": "\U0001f504", "REVERSED": "\U0001f504"}.get(effective_status, "\U0001f4cb")
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
        if should_apply_status and effective_status == "CONFIRMED" and current_status != "CONFIRMED" and payment:
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
                actor="tbank_webhook",
                order_id=order_id,
            )
            await credits_db.log_event(tg_id, "payment_confirmed", f"{pkg} \u2014 {amount_rub}\u20bd")
            try:
                await state_store.reset_to_wait_audio(tg_id)
            except Exception as e:
                log.warning("tbank notify: failed to unlock user state %s: %s", tg_id, e)
            log.info("payment confirmed tg_id=%s pkg=%s credits=+%s", tg_id, pkg, credits_to_add)

            # Notify user as side-effect. Unlock is already committed.
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
                except Exception as e:
                    log.warning("tbank notify: failed to notify user %s: %s", tg_id, e)

            # Отправить доход в finance-bot
            try:
                user_info_fb = await credits_db.get_user(tg_id)
                uname_fb = f"@{user_info_fb['username']}" if user_info_fb and user_info_fb.get("username") else str(tg_id)
                fb_url = settings.finance_bot_url.rstrip("/") + "/webhook/income"
                async with httpx.AsyncClient(timeout=5) as cli:
                    await cli.post(fb_url, json={"amount": amount_rub, "source": "blast", "client": f"{uname_fb} — {pkg}"})
            except Exception as e:
                log.warning("tbank notify: finance_bot income failed: %s", e)

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
