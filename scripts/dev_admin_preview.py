"""Локальный прототип админки на синтетических данных (без Postgres/Redis).

    python scripts/dev_admin_preview.py            # http://127.0.0.1:18081/admin/  (пароль: dev)

Дашборд рендерится настоящим `admin_panel.build_app`; подменён только источник
данных: `product_metrics_source` собирает правдоподобный поток событий
(воронка, повторные визиты, оплаты) с фиксированным seed, остальные методы
credits_db возвращают пустые/минимальные структуры.
"""
from __future__ import annotations

import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.tg_bot_public import admin_panel, product_metrics as pm  # noqa: E402

NOW = datetime.now(timezone.utc).replace(tzinfo=None)
SEED = int(os.environ.get("PREVIEW_SEED", "7"))


def _synthetic_source(channel: str) -> pm.MetricsSource:
    rng = random.Random(SEED)
    events: list[pm.Event] = []
    payments: list[pm.Payment] = []
    users: list[tuple[str, datetime]] = []
    bot_steps = ["start", "subscription_ok", "audio_uploaded", "generation_started", "generation_done"]
    site_steps = ["app_entry", "signup_completed", "track_uploaded", "generation_started", "generation_completed"]
    drop = [1.0, 0.72, 0.55, 0.48, 0.41]  # доля дошедших до шага
    n_users = 620 if channel != "site" else 240
    for i in range(n_users):
        is_site = channel == "site" or (channel == "all" and rng.random() < 0.3)
        ident = f"web:u{i}" if (is_site and rng.random() < 0.4) else f"tg:{100000 + i}"
        ch = "site" if is_site else "bot"
        steps = site_steps if is_site else bot_steps
        first = NOW - timedelta(days=rng.uniform(0, 120), hours=rng.uniform(0, 12))
        if ident.startswith("tg:"):
            users.append((ident, first))
        reach = rng.random()
        t = first
        for step, share in zip(steps, drop):
            if reach > share:
                break
            t += timedelta(minutes=rng.uniform(1, 90))
            events.append(pm.Event(ident, step, t, ch))
        got_video = reach <= drop[-1]
        # повторные визиты: ~35% возвращаются, часть — несколько раз
        visits = 0
        if rng.random() < 0.35:
            visits = rng.choice([1, 1, 2, 3, 5])
        for _ in range(visits):
            t2 = first + timedelta(days=rng.expovariate(1 / 9.0) + 1)
            if t2 < NOW:
                events.append(pm.Event(ident, steps[0], t2, ch))
                if got_video and rng.random() < 0.6:
                    events.append(pm.Event(ident, "generation_started", t2 + timedelta(minutes=5), ch))
                    events.append(pm.Event(ident, steps[-1] if rng.random() < 0.9 else "generation_failed", t2 + timedelta(minutes=20), ch))
        # оплаты: ~6% из получивших ролик
        if got_video and ident.startswith("tg:") and rng.random() < 0.14:
            pay_at = first + timedelta(days=rng.uniform(0.2, 12))
            if pay_at < NOW:
                amount = rng.choice([990, 1990, 1990, 4990])
                payments.append(pm.Payment(ident, amount, pay_at))
                if rng.random() < 0.4:  # rebill
                    again = pay_at + timedelta(days=30)
                    if again < NOW:
                        payments.append(pm.Payment(ident, amount, again))
        # блокировки: ~9% ушли
        if ident.startswith("tg:") and rng.random() < 0.09:
            events.append(pm.Event(ident, "bot_blocked", first + timedelta(days=rng.uniform(0.1, 20)), "bot"))
    events = [e for e in events if e.at < NOW]
    events.sort(key=lambda e: e.at)
    return pm.MetricsSource(events=events, payments=payments, users=users, spend_rub=0,
                            subscriptions={"active_cnt": 23, "paused_cnt": 4, "recurrent_fail_30d": 2})


class _FakeCreditsDB:
    def __init__(self) -> None:
        self.spend: dict[str, dict] = {}

    async def product_metrics_source(self, channel, date_from, date_to):
        src = _synthetic_source(channel)
        df = date_from.replace(tzinfo=None)
        src.spend_rub = sum(
            r["spend_rub"] for (m, _s), r in self.spend.items()
            if datetime.strptime(m, "%Y-%m") >= df.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        )
        return src

    async def list_marketing_spend(self, limit=24):
        return [dict(month=m, source=src, **r) for (m, src), r in sorted(self.spend.items(), reverse=True)]

    async def set_marketing_spend(self, month, spend_rub, *, source="", note="", actor=""):
        datetime.strptime(month, "%Y-%m")
        self.spend[(month, source)] = {"spend_rub": int(spend_rub), "note": note, "updated_by": actor, "updated_at": NOW.strftime("%Y-%m-%d %H:%M")}

    async def source_economics(self, date_from, date_to):
        from services.tg_bot_public.credits_db import source_economics_row
        rng = random.Random(SEED + 1)
        rows = []
        for src, users in (("instagram_bio", 210), ("tiktok_link", 140), ("youtube_desc", 95), ("p_acme_1f2a", 60), ("(direct)", 115)):
            new = int(users * rng.uniform(0.2, 0.5)); payers = int(users * rng.uniform(0.04, 0.09)); pn = max(1, int(payers * 0.5))
            spend = sum(r["spend_rub"] for (m, s2), r in self.spend.items() if s2 == src)
            rows.append(source_economics_row({"src": src, "users_total": users, "users_new": new, "payers_total": payers,
                                              "payers_new": pn, "revenue_period": pn * 1990 + payers * 700, "revenue_total": payers * 3200, "spend_rub": spend}))
        return rows

    async def rating_distribution(self):
        return [{"rating": "low", "count": 9}, {"rating": "mid_low", "count": 31}, {"rating": "high", "count": 118}]
    async def funnel_reach_counts(self): return []
    async def list_users(self, limit=10, offset=0): return []
    async def get_activity(self, *args, limit=10, offset=0):
        if not args:
            return []
        return [{"id": 3, "tg_id": args[0], "event": "generation_done", "detail": "job 7c1a…", "created_at": "2026-09-15 21:40"},
                {"id": 2, "tg_id": args[0], "event": "audio_uploaded", "detail": "track.mp3", "created_at": "2026-09-15 21:12"},
                {"id": 1, "tg_id": args[0], "event": "start", "detail": "", "created_at": "2026-07-14 18:02"}]
    async def list_broadcasts(self, limit=50, offset=0):
        rng = random.Random(SEED + 2)
        titles = ["Скидка на Глоу до воскресенья", "Новый хук «Прогрев»", "Напоминание: кредиты сгорают", "Опрос после первой генерации", "Партнёрская программа"]
        statuses = ["done", "done", "sending", "scheduled", "draft"]
        out = []
        for i, (t, st) in enumerate(zip(titles, statuses)):
            size = rng.randint(200, 620)
            sent = size if st == "done" else (rng.randint(20, size) if st == "sending" else 0)
            out.append({"id": 41 - i, "title": t, "status": st, "audience_size": size, "sent_count": sent,
                        "failed_count": rng.randint(0, 6) if sent else 0, "schedule_at": "2026-09-1%d 10:00" % (8 - i) if st in ("scheduled", "done") else "",
                        "created_by": "nikita", "created_at": "2026-09-%02d 12:3%d" % (16 - i, i), "started_at": "", "finished_at": ""})
        return out
    async def count_broadcasts(self): return 5
    async def list_active_subscriptions(self):
        rng = random.Random(SEED + 3)
        out = []
        for i in range(12):
            out.append({"id": i + 1, "tg_id": 100000 + i * 7, "username": rng.choice(["mari_beats", "dj_kolya", "", "anna.sings", "prod_vlad"]),
                        "package": rng.choice(["15", "30", "50"]), "amount_rub": rng.choice([990, 1990, 4990]),
                        "status": "active" if i < 10 else "paused", "next_charge_at": "2026-09-%02d" % (17 + i), "_next_charge_raw": NOW + timedelta(days=i + 1),
                        "last_charge_at": "2026-08-%02d" % (17 + i), "last_charge_status": "CONFIRMED" if i % 5 else "REJECTED",
                        "rebill_id": "1234567890", "charge_retries": 0, "created_at": "2026-07-%02d" % (10 + i), "cancelled_at": None})
        return out
    async def find_orphan_recurrent_payments(self): return []
    async def list_clients(self, min_credits=5, limit=50, offset=0, tag="", sort="credits", product=""):
        rng = random.Random(SEED + 4)
        out = []
        for i in range(18):
            out.append({"tg_id": 100000 + i * 3, "username": rng.choice(["mari_beats", "dj_kolya", "", "anna.sings", "prod_vlad", "lil_tim"]),
                        "credits": rng.randint(5, 60), "gens_done": rng.randint(1, 40), "revenue_rub": rng.choice([990, 1990, 2980, 4990, 6980]),
                        "bought_packages": rng.choice([["15"], ["30"], ["15", "30"], ["50"]]), "has_active_subscription": rng.random() < 0.4,
                        "last_activity_at": "2026-09-%02d 1%d:20" % (rng.randint(1, 16), rng.randint(0, 9)), "source": rng.choice(["instagram_bio", "tiktok_link", "", "p_acme_1f2a"]),
                        "created_at": "2026-0%d-1%d" % (rng.randint(5, 9), rng.randint(0, 9)), "updated_at": ""})
        return out
    async def count_clients(self, **kw): return 18
    async def get_user(self, tg_id):
        return {"tg_id": int(tg_id), "username": "anna.sings", "credits": 42, "created_at": "2026-07-14 18:02", "updated_at": "2026-09-15 21:40", "source": "instagram_bio"}
    async def get_user_source(self, tg_id): return "instagram_bio"
    async def user_health_metrics(self, tg_id):
        return {"gens_done": 27, "gens_done_30d": 9, "last_gen_at": "2026-09-15 21:40", "last_activity_at": "2026-09-15 21:40",
                "last_payment_at": "2026-09-01", "paid_orders": 3, "revenue_rub": 6970, "revenue_bot": 5970, "revenue_manual": 1000,
                "_last_activity_raw": NOW - timedelta(days=1), "_last_gen_raw": NOW - timedelta(days=1)}
    async def get_user_tags(self, tg_id): return ["vip", "blogger"]
    async def get_user_notes(self, tg_id):
        return [{"id": 1, "note": "Просила добавить эффект молнии на дропе — ждёт F3.", "created_at": "2026-09-10 12:00", "created_by": "nikita"}]
    async def list_manual_payments(self, tg_id, limit=50):
        return [{"id": 1, "amount_rub": 1000, "note": "инвойс №42", "created_at": "2026-08-20 10:00", "created_by": "kirill"}]
    async def get_user_purchases(self, tg_id):
        return {"purchases": [{"package": "15", "count": 2, "total_rub": 3980, "last_at": "2026-09-01"}, {"package": "30", "count": 1, "total_rub": 1990, "last_at": "2026-08-01"}],
                "active_subscription": {"package": "30", "amount_rub": 1990, "charges_count": 2, "next_charge_at": "2026-10-01", "created_at": "2026-08-01", "status": "active"}}
    async def get_user_tier(self, tg_id): return "P2"
    async def get_transactions(self, tg_id, limit=50):
        return [{"id": 901, "amount": 15, "reason": "payment", "actor": "tbank", "order_id": "ord_8f1", "admin_note": "", "created_at": "2026-09-01 10:05"},
                {"id": 877, "amount": -1, "reason": "generation", "actor": "bot", "order_id": "", "admin_note": "", "created_at": "2026-09-03 19:12"}]
    async def user_payments_history(self, tg_id, limit=50):
        return [{"created_at": "2026-09-01 10:05", "amount_rub": 1990, "package": "15", "is_recurrent": False, "status": "CONFIRMED", "order_id": "ord_8f1a2c"},
                {"created_at": "2026-08-01 10:05", "amount_rub": 1990, "package": "30", "is_recurrent": True, "status": "CONFIRMED", "order_id": "ord_77b0e1"},
                {"created_at": "2026-07-14 18:30", "amount_rub": 990, "package": "5", "is_recurrent": False, "status": "REJECTED", "order_id": "ord_1c0d9a"}]
    async def get_active_subscription(self, tg_id):
        return {"id": 5, "package": "30", "amount_rub": 1990, "next_charge_at": NOW + timedelta(days=15), "charge_retries": 0, "rebill_id": "1234567890"}
    async def recent_lifecycle_fires_for_user(self, tg_id, days=7):
        return [{"created_at": "2026-09-12 09:00", "rule_tier": "P2", "rule_id": 3, "rule_name": "Напомнить про кредиты", "status": "sent", "error": ""}]
    async def users_by_source(self, source):
        rng = random.Random(SEED + 8)
        return [{"tg_id": 100000 + i * 3, "username": rng.choice(["mari_beats", "dj_kolya", "", "anna.sings"]), "credits": rng.randint(0, 30),
                 "created_at": "2026-0%d-%02d" % (rng.randint(6, 9), rng.randint(1, 28)), "updated_at": "", "source": source} for i in range(9)]
    async def funnel_reach_counts_for_users(self, ids):
        return [{"event": e, "count": c} for e, c in (("start", 210), ("subscription_ok", 150), ("audio_uploaded", 118), ("generation_started", 104), ("generation_done", 96), ("payment_confirmed", 14))]
    async def rating_distribution_for_users(self, ids):
        return [{"rating": "low", "count": 3}, {"rating": "mid_low", "count": 12}, {"rating": "high", "count": 41}]
    async def revenue_breakdown_for_users(self, ids): return {"confirmed_revenue_rub": 30510, "authorized_revenue_rub": 1990, "visible_revenue_rub": 32500}
    async def tier_counts(self): return {}
    async def source_distribution(self):
        return [{"source": s_, "count": c} for s_, c in (("instagram_bio", 210), ("tiktok_link", 140), ("youtube_desc", 95), ("(direct)", 115))]
    async def get_tags_for_users(self, ids): return {}
    async def list_all_tags(self): return [{"tag": "vip", "count": 4}, {"tag": "blogger", "count": 9}]
    def _pool_or_fail(self): raise RuntimeError("no db in preview")
    async def clients_summary(self, min_credits=5): return {"clients_count": 0, "credits_on_balance": 0, "revenue_rub_total": 0, "active_7d": 0, "dormant_14d": 0}
    async def payments_status_summary(self): return {}
    async def period_stats_range(self, a, b): return {}
    async def revenue_timeseries(self, *, bucket="month", periods=12):
        rng = random.Random(SEED + 5)
        if bucket == "week":
            return [{"bucket": "W%02d" % (26 + i), "rub": rng.randint(8000, 42000)} for i in range(12)]
        return [{"bucket": str(d), "rub": rng.choice([0, 0, 990, 1990, 2980, 4990, 6980])} for d in range(1, 17)]
    async def subscriptions_summary(self):
        return {"active_cnt": 23, "paused_cnt": 4, "overdue_cnt": 1, "recurrent_fail_30d": 2, "recurrent_ok_30d": 19, "recurrent_revenue_30d": 37810,
                "due_today_cnt": 2, "due_today_rub": 3980, "due_7d_cnt": 7, "due_7d_rub": 13930, "due_this_month_cnt": 18, "due_this_month_rub": 35820}
    async def users_timeseries(self, *, bucket="month"):
        rng = random.Random(SEED + 6)
        if bucket == "week":
            series = [{"bucket": "W%02d" % (26 + i), "inflow": rng.randint(20, 70), "outflow": rng.randint(1, 9)} for i in range(12)]
        else:
            series = [{"bucket": str(d), "inflow": rng.randint(2, 14), "outflow": rng.randint(0, 3)} for d in range(1, 17)]
        return {"series": series, "total_users": 620, "blocked_total": 48, "active_total": 572}
    async def get_partner_by_login(self, login): return None
    async def list_partners(self): return []

    def __getattr__(self, name):
        # Любой неизвестный запрос к БД в прототипе отдаёт «пусто» нужной формы
        # (число / словарь / список) — второстепенные страницы открываются без данных.
        if name.startswith("_"):
            raise AttributeError(name)
        if name.startswith("count") or name.endswith("_count") or name.endswith("_total"):
            value = 0
        elif any(t in name for t in ("summary", "stats", "counts", "breakdown", "snapshot", "get_", "commission")):
            value = {}
        else:
            value = []

        async def _empty(*args, **kwargs):
            return value
        return _empty


class _FakeRedis:
    async def get(self, k): return None
    async def mget(self, *keys): return [None for _ in keys]
    async def hgetall(self, k): return {}
    async def keys(self, *a, **k): return []
    async def scan_iter(self, *a, **k):
        if False:
            yield None
    async def set(self, *a, **k): return True
    async def delete(self, k): return 1


class _FakeStateStore:
    redis = _FakeRedis()

    async def get(self, tg_id): return SimpleNamespace(stage="WAIT_AUDIO")

    async def list_stage_counts(self): return {}
    async def get_stages_for_chat_ids(self, ids): return {}

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        async def _empty(*a, **k):
            return {}
        return _empty


SETTINGS = SimpleNamespace(
    admin_panel_password="dev", admin_panel_port=18081, orchestrator_public_url="http://127.0.0.1:18081", dozzle_base_url="http://127.0.0.1:18081/dozzle",
    manager_chat_id=0, season_redis_prefix="season", tg_delivery_mode="polling",
    tg_bot_username="blast808bot", tg_bot_token="", finance_bot_url="",
)

app = admin_panel.build_app(_FakeCreditsDB(), _FakeStateStore(), SETTINGS)


# Фейковый оркестратор: /jobs/active и /metrics отдаёт этот же процесс.
@app.get("/jobs/active")
async def _fake_jobs_active(min_age_seconds: int = 0, limit: int = 200):
    rng = random.Random(SEED + 7)
    stages = ["stage1a", "stage2_subtitles", "stage2_footage", "build", "render", "render_poll", "dispatch"]
    jobs = []
    for i in range(9):
        age = rng.choice([40, 130, 420, 780, 1260, 2100, 4100])
        if age < min_age_seconds:
            continue
        jobs.append({"job_id": "%08x%08x" % (rng.getrandbits(32), rng.getrandbits(32)), "status": rng.choice(["running", "running", "queued", "polling"]),
                     "stage": rng.choice(stages), "project_id": "tg-%d-%d" % (100000 + i * 5, rng.randint(1, 9)),
                     "llm_worker_type": rng.choice(["gemini", "openrouter", ""]), "age_seconds": age, "updated_at": 1789000000 - age})
    return {"jobs": jobs[:limit], "total_active": 9}


@app.get("/metrics")
async def _fake_metrics():
    return {"job_status_counts": {"RUNNING": 3, "QUEUED": 4, "DONE": 812, "FAILED": 17}, "workers": {}, "llm_inflight_by_worker_type": {"gemini": 2}}

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PREVIEW_PORT", "18081")))
