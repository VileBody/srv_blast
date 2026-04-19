from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "logs_pipeline.py"
    spec = importlib.util.spec_from_file_location("logs_pipeline", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_redact_message_masks_sensitive_values_but_keeps_safe_text() -> None:
    mod = _load_module()
    text = (
        "Authorization: Bearer abc123 "
        "token=secretToken password=hunter2 "
        "safe_key=value"
    )

    out = mod.redact_message(text)

    assert "<redacted:" in out
    assert "abc123" not in out
    assert "secretToken" not in out
    assert "hunter2" not in out
    assert "safe_key=value" in out


def test_build_event_fingerprint_is_stable_and_sensitive_to_line_marker() -> None:
    mod = _load_module()
    ts = datetime(2026, 4, 19, 10, 0, 0, tzinfo=timezone.utc)
    labels = {"service": "orchestrator-api", "stream": "stdout"}

    fp1 = mod.build_event_fingerprint(
        source_kind="loki",
        node_name="blast-ops-1",
        node_role="infra-ops",
        labels=labels,
        event_ts=ts,
        line_marker="1:abc",
        message_raw="event=job_started",
    )
    fp2 = mod.build_event_fingerprint(
        source_kind="loki",
        node_name="blast-ops-1",
        node_role="infra-ops",
        labels=labels,
        event_ts=ts,
        line_marker="1:abc",
        message_raw="event=job_started",
    )
    fp3 = mod.build_event_fingerprint(
        source_kind="loki",
        node_name="blast-ops-1",
        node_role="infra-ops",
        labels=labels,
        event_ts=ts,
        line_marker="2:def",
        message_raw="event=job_started",
    )

    assert fp1 == fp2
    assert fp1 != fp3


def test_normalize_event_extracts_core_fields() -> None:
    mod = _load_module()
    ts = datetime(2026, 4, 19, 10, 0, 0, tzinfo=timezone.utc)
    job_id = "a" * 32
    req_id = "12345678-1234-1234-1234-123456789abc"

    raw = mod.RawEvent(
        event_ts=ts,
        source_kind="loki",
        node_role="infra-ops",
        node_name="blast-ops-1",
        service="orchestrator-api",
        container="orchestrator-api",
        stream="stdout",
        severity="info",
        job_id=job_id,
        request_id=req_id,
        message_raw=(
            f"event=job_completed queue=render job_id={job_id} request_id={req_id} "
            "duration_ms=321 cost=0.42 chat_id=777 success"
        ),
        message_redacted=(
            f"event=job_completed queue=render job_id={job_id} request_id={req_id} "
            "duration_ms=321 cost=0.42 chat_id=777 success"
        ),
        labels_json={"service": "orchestrator-api"},
        attrs_json={"collector": "loki"},
        event_fingerprint="fp",
        line_marker="1",
    )

    norm = mod.normalize_event(raw, raw_event_id=42, raw_event_ts=ts)

    assert norm.event_domain == "orchestrator"
    assert norm.event_name == "job_completed"
    assert norm.outcome == "success"
    assert norm.queue_name == "render"
    assert norm.chat_id == 777
    assert norm.duration_ms == 321
    assert norm.cost_value == Decimal("0.42")
