from __future__ import annotations

from pathlib import Path


def test_logs_sql_contains_append_only_triggers_and_dedup_indexes() -> None:
    sql_path = Path(__file__).resolve().parents[1] / "infra" / "logging" / "sql" / "001_logs_schema.sql"
    sql = sql_path.read_text(encoding="utf-8")

    assert "CREATE TRIGGER trg_raw_events_reject_mutation" in sql
    assert "CREATE TRIGGER trg_events_norm_reject_mutation" in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS ux_raw_events_event_ts_fingerprint" in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS ux_events_norm_raw_event_ref" in sql
    assert "CREATE TABLE IF NOT EXISTS logs.ingest_cursor" in sql
    assert "CREATE TABLE IF NOT EXISTS logs.ingest_runs" in sql
    assert "CREATE TABLE IF NOT EXISTS logs.s3_objects" in sql
