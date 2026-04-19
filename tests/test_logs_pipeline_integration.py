from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path


class _FakeS3:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def put_object(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(dict(kwargs))
        return {"ok": True}


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "logs_pipeline.py"
    spec = importlib.util.spec_from_file_location("logs_pipeline", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_upload_raw_events_to_s3_writes_ndjson_and_manifest() -> None:
    mod = _load_module()
    cfg = mod.PipelineConfig(
        enabled=True,
        node_name="blast-ops-1",
        node_role="infra-ops",
        db_dsn="postgresql://x",
        s3_bucket="bucket",
        s3_prefix="logs-backup",
        s3_endpoint_url="https://s3.example",
        s3_access_key_id="k",
        s3_secret_access_key="s",
        s3_region="ru-1",
        loki_enabled=True,
        loki_url="http://loki:3100",
        loki_query="{}",
        docker_enabled=False,
        retention_days=180,
        raw_retention_days=30,
        norm_retention_days=180,
        backfill_days=30,
        chunk_size=2,
        loki_limit=5000,
        max_lag_min=90,
    )

    ts = datetime(2026, 4, 19, 10, 0, 0, tzinfo=timezone.utc)
    events = [
        mod.RawEvent(
            event_ts=ts,
            source_kind="loki",
            node_role="infra-ops",
            node_name="blast-ops-1",
            service="orchestrator-api",
            container="orchestrator-api",
            stream="stdout",
            severity="info",
            job_id="",
            request_id="",
            message_raw="event=job_started",
            message_redacted="event=job_started",
            labels_json={"service": "orchestrator-api"},
            attrs_json={"collector": "loki"},
            event_fingerprint="fp-1",
            line_marker="1",
        ),
        mod.RawEvent(
            event_ts=ts,
            source_kind="loki",
            node_role="infra-ops",
            node_name="blast-ops-1",
            service="worker-build",
            container="worker-build",
            stream="stderr",
            severity="error",
            job_id="",
            request_id="",
            message_raw="event=job_failed",
            message_redacted="event=job_failed",
            labels_json={"service": "worker-build"},
            attrs_json={"collector": "loki"},
            event_fingerprint="fp-2",
            line_marker="2",
        ),
    ]

    fake_s3 = _FakeS3()
    enriched, objects = mod.upload_raw_events_to_s3(
        cfg=cfg,
        s3_client=fake_s3,
        source_kind="loki",
        events=events,
        window_from=ts,
        window_to=ts.replace(hour=11),
    )

    assert len(enriched) == 2
    assert all(item.s3_bucket == "bucket" for item in enriched)
    assert all(item.s3_key for item in enriched)
    assert [item.s3_line_no for item in enriched] == [1, 2]

    assert len(objects) == 1
    assert objects[0].row_count == 2
    assert objects[0].object_key.startswith("logs-backup/raw/source=loki/node=blast-ops-1/")

    assert len(fake_s3.calls) == 2
    uploaded_keys = [str(call["Key"]) for call in fake_s3.calls]
    assert any("/raw/source=loki/" in key for key in uploaded_keys)
    assert any("/manifests/source=loki/" in key for key in uploaded_keys)
