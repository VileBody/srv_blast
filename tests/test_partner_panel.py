from __future__ import annotations

from fastapi.testclient import TestClient

from services.tg_bot_public import admin_panel, credits_db as credits_db_mod


class _DummyRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key, value, ex=None, nx=None):
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)
        return 1


class _DummyStateStore:
    redis = _DummyRedis()


_PWD_HASH = credits_db_mod.hash_partner_password("secret123")

_PARTNER = {
    "id": 1, "login": "acme", "name": "Acme Traffic", "password_hash": _PWD_HASH,
    "status": "active", "created_at": None, "updated_at": None,
    "users_count": 1, "links_count": 1,
}


class _DummyCreditsDB:
    def _pool_or_fail(self):
        return object()

    async def get_partner_by_login(self, login):
        return _PARTNER if login == "acme" else None

    async def get_partner(self, partner_id):
        return _PARTNER if int(partner_id) == 1 else None

    async def list_partners(self):
        return [_PARTNER]

    async def list_partner_user_ids(self, partner_id):
        return [111]

    async def funnel_reach_counts_for_users(self, tg_ids):
        return [{"event": "start", "count": 1}]

    async def rating_distribution_for_users(self, tg_ids):
        return []

    async def partner_commission_summary(self, partner_id):
        return {
            "first_revenue_rub": 1000, "repeat_revenue_rub": 0,
            "first_count": 1, "repeat_count": 0,
            "earned_rub": 500, "paid_rub": 0, "due_rub": 500,
        }

    async def partner_period_totals(self, partner_id, *, days, shift=0):
        return {"starts": 3, "purchases": 1, "earned_rub": 500}

    async def partner_revenue_timeseries(self, partner_id, days=30):
        return [{"day": "2026-09-01", "starts": 1, "purchases": 0}]

    async def partner_link_stats(self, partner_id):
        return [{"code": "p_insta_ab12cd", "label": "Instagram", "created_at": "2026-09-01", "starts_count": 1, "paying_users": 1, "commission_rub": 500}]

    async def list_partner_links(self, partner_id):
        return [{"id": 1, "partner_id": 1, "code": "p_insta_ab12cd", "label": "Instagram", "created_at": "2026-09-01", "starts_count": 1}]

    async def get_partner_link_by_code(self, code):
        return None

    async def create_partner_link(self, partner_id, code, label=""):
        return 2

    async def partner_users(self, partner_id, limit=50, offset=0):
        return [{"tg_id": 111, "username": "john", "credits": 3, "created_at": "2026-09-01", "partner_link_code": "p_insta_ab12cd"}]

    async def count_partner_users(self, partner_id):
        return 1

    async def get_partner_user(self, partner_id, tg_id):
        if int(tg_id) == 111:
            return {"tg_id": 111, "username": "john", "credits": 3, "created_at": "2026-09-01", "partner_link_code": "p_insta_ab12cd", "partner_attributed_at": "2026-09-01"}
        return None

    async def get_activity(self, tg_id=0, limit=50, offset=0):
        return [{"event": "start", "detail": "", "created_at": "2026-09-01"}]

    async def partner_jobs_summary(self, partner_id):
        return {"succeeded": 1, "running": 2}

    async def partner_activity(self, partner_id, limit=50, offset=0):
        return [{"id": 1, "tg_id": 111, "username": "john", "event": "start", "detail": "", "created_at": "2026-09-01"}]

    async def count_partner_activity(self, partner_id):
        return 1

    async def count_partner_jobs(self, partner_id, active_only=False):
        return 1

    async def partner_jobs(self, partner_id, active_only=False, limit=30, offset=0):
        return [{"run_id": "r1", "status": "succeeded", "versions_total": 1, "current_stage": "done", "created_at": "2026-09-01", "updated_at": "2026-09-01", "username": "john", "tg_id": 111}]

    async def list_partner_payouts(self, partner_id, limit=100):
        return [{"id": 1, "amount_rub": 100, "note": "test", "created_by": "admin", "created_at": "2026-09-01"}]

    async def create_partner(self, login, password, name=""):
        return 2

    async def add_partner_payout(self, partner_id, amount_rub, note="", created_by=""):
        return 1

    async def set_partner_password(self, partner_id, password):
        return None

    async def set_partner_status(self, partner_id, status):
        return None

    async def audit_log(self, admin_user, action, target="", details=""):
        return None


def _make_client() -> TestClient:
    from types import SimpleNamespace

    settings = SimpleNamespace(
        admin_panel_password="x", admin_panel_port=8080, admin_panel_public_url="",
        admin_panel_enable_donor_restart=False, tg_bot_username="blast808bot",
        tg_bot_api_env="dev", season_redis_prefix="season:",
        orchestrator_public_url="http://x",
    )
    app = admin_panel.build_app(_DummyCreditsDB(), _DummyStateStore(), settings)
    return TestClient(app)


def test_partner_login_requires_valid_credentials():
    client = _make_client()
    r = client.get("/partner/login")
    assert r.status_code == 200
    assert "Blast Partners" in r.text

    r = client.post("/partner/login", data={"login": "acme", "password": "wrong"})
    assert r.status_code == 401

    r = client.post("/partner/login", data={"login": "acme", "password": "secret123"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.cookies.get("partner_sid")


def test_partner_cabinet_pages_render_after_login():
    client = _make_client()
    r = client.post("/partner/login", data={"login": "acme", "password": "secret123"}, follow_redirects=False)
    client.cookies.set("partner_sid", r.cookies.get("partner_sid"))

    for path in (
        "/partner/",
        "/partner/links",
        "/partner/users",
        "/partner/users/111",
        "/partner/activity",
        "/partner/payouts",
    ):
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} -> {resp.status_code}"


def test_partner_cannot_view_another_partners_user():
    client = _make_client()
    r = client.post("/partner/login", data={"login": "acme", "password": "secret123"}, follow_redirects=False)
    client.cookies.set("partner_sid", r.cookies.get("partner_sid"))

    resp = client.get("/partner/users/999")
    assert resp.status_code == 404


def test_partner_logout_revokes_session():
    client = _make_client()
    r = client.post("/partner/login", data={"login": "acme", "password": "secret123"}, follow_redirects=False)
    client.cookies.set("partner_sid", r.cookies.get("partner_sid"))

    client.get("/partner/logout", follow_redirects=False)
    client.cookies.set("partner_sid", r.cookies.get("partner_sid"))
    resp = client.get("/partner/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers.get("location") == "/partner/login"


def test_admin_partners_pages_render():
    client = _make_client()
    client.auth = ("admin", "x")
    r = client.get("/admin/partners")
    assert r.status_code == 200
    r = client.get("/admin/partners/1")
    assert r.status_code == 200


def test_partner_password_hash_roundtrip():
    stored = credits_db_mod.hash_partner_password("hunter2")
    assert credits_db_mod.verify_partner_password("hunter2", stored)
    assert not credits_db_mod.verify_partner_password("wrong", stored)
