#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Sequence, TypeVar

import boto3
import httpx
from botocore.config import Config

try:
    import asyncpg
except Exception:  # pragma: no cover - local/dev fallback for non-DB unit tests
    asyncpg = None  # type: ignore[assignment]

MIGRATION_SQL_PATH = Path(__file__).resolve().parents[1] / "infra" / "logging" / "sql" / "001_logs_schema.sql"

SOURCE_KIND_LOKI = "loki"
SOURCE_KIND_DOCKER = "docker"

JOB_ID_RE = re.compile(r"(?i)(?:job_id=|job[ _-]?id[:= ]+)([a-f0-9]{32})")
REQUEST_ID_RE = re.compile(r"(?i)(?:request_id=|request[ _-]?id[:= ]+)([a-z0-9-]{8,64})")
EVENT_NAME_RE = re.compile(r"(?i)\bevent(?:_name)?\s*[:= ]+([a-z0-9_.-]+)")
QUEUE_RE = re.compile(r"(?i)\bqueue\s*[:= ]+([a-z0-9_.-]+)")
CHAT_ID_RE = re.compile(r"(?i)\b(?:chat_id|tg_id|user_id)\s*[:= ]+(-?\d+)")
DURATION_RE = re.compile(r"(?i)\b(?:duration_ms|elapsed_ms|latency_ms)\s*[:= ]+(\d+)")
COST_RE = re.compile(r"(?i)\b(?:cost|cost_usd|price|amount)\s*[:= ]+([0-9]+(?:\.[0-9]+)?)")
SEVERITY_RE = re.compile(r"(?i)\b(?:level|severity)\s*[:= ]+(debug|info|warn|warning|error|critical|fatal)\b")
AUTH_HEADER_RE = re.compile(r"(?i)(authorization\s*[:=]\s*)(bearer\s+)?([^\s,;]+)")
URL_SECRET_RE = re.compile(
    r"(?i)([?&](?:token|access_token|refresh_token|password|passwd|secret|api[_-]?key|sig|signature)=)([^&\s]+)"
)
KV_SECRET_RE = re.compile(
    r"(?i)\b(token|access_token|refresh_token|password|passwd|secret|api[_-]?key|authorization|cookie|set-cookie)\b\s*[:=]\s*([^\s,;]+)"
)

TARGET_CONTAINER_PREFIXES: tuple[str, ...] = (
    "orchestrator-api",
    "worker-build",
    "worker-render",
    "tg-bot",
    "tg-bot-public",
    "asset-ui",
    "finance-bot",
    "obs-",
    "promtail-edge",
    "dozzle",
)


@dataclass(frozen=True)
class PipelineConfig:
    enabled: bool
    node_name: str
    node_role: str
    db_dsn: str
    s3_bucket: str
    s3_prefix: str
    s3_endpoint_url: str
    s3_access_key_id: str
    s3_secret_access_key: str
    s3_region: str
    loki_enabled: bool
    loki_url: str
    loki_query: str
    docker_enabled: bool
    retention_days: int
    raw_retention_days: int
    norm_retention_days: int
    backfill_days: int
    chunk_size: int
    loki_limit: int
    max_lag_min: int


@dataclass(frozen=True)
class RawEvent:
    event_ts: datetime
    source_kind: str
    node_role: str
    node_name: str
    service: str
    container: str
    stream: str
    severity: str
    job_id: str
    request_id: str
    message_raw: str
    message_redacted: str
    labels_json: dict[str, Any]
    attrs_json: dict[str, Any]
    event_fingerprint: str
    line_marker: str
    s3_bucket: str | None = None
    s3_key: str | None = None
    s3_line_no: int | None = None


@dataclass(frozen=True)
class NormEvent:
    event_ts: datetime
    raw_event_ts: datetime
    raw_event_id: int
    schema_version: int
    event_domain: str
    event_name: str
    outcome: str
    job_id: str
    request_id: str
    queue_name: str
    chat_id: int | None
    duration_ms: int | None
    cost_value: Decimal | None
    message_redacted: str
    attrs_json: dict[str, Any]


@dataclass(frozen=True)
class S3ObjectRecord:
    source_kind: str
    node_name: str
    node_role: str
    bucket: str
    object_key: str
    row_count: int
    sha256: str
    window_from_ts: datetime
    window_to_ts: datetime
    manifest_json: dict[str, Any]


TItem = TypeVar("TItem")


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _hour_floor(ts: datetime) -> datetime:
    return ts.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def _to_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _parse_bool(raw: str, *, key: str) -> bool:
    val = str(raw or "").strip().lower()
    if val in {"1", "true", "yes", "on"}:
        return True
    if val in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"Invalid boolean in {key}: {raw!r}")


def _env_required(key: str) -> str:
    value = str(os.environ.get(key) or "").strip()
    if not value:
        raise RuntimeError(f"Missing required env: {key}")
    return value


def _env_int(key: str, default: int) -> int:
    raw = str(os.environ.get(key) or "").strip()
    if not raw:
        return int(default)
    try:
        value = int(raw)
    except Exception as exc:  # pragma: no cover - defensive
        raise RuntimeError(f"Invalid integer in {key}: {raw!r}") from exc
    return value


def _env_bool(key: str, default: bool) -> bool:
    raw = str(os.environ.get(key) or "").strip()
    if not raw:
        return bool(default)
    return _parse_bool(raw, key=key)


def _clean_prefix(raw: str) -> str:
    prefix = str(raw or "").strip().strip("/")
    if not prefix:
        raise RuntimeError("LOG_BACKUP_S3_PREFIX must be non-empty")
    return prefix


def _load_config(*, require_enabled: bool, require_s3: bool, require_collectors: bool) -> PipelineConfig:
    enabled = _env_bool("LOG_BACKUP_ENABLED", False)
    if require_enabled and not enabled:
        raise RuntimeError("LOG_BACKUP_ENABLED must be true for this command")

    node_name = _env_required("LOG_BACKUP_NODE_NAME")
    node_role = _env_required("LOG_BACKUP_NODE_ROLE")
    db_dsn = _env_required("LOG_BACKUP_DB_DSN")

    s3_bucket = ""
    s3_prefix = ""
    s3_endpoint_url = ""
    s3_access_key_id = ""
    s3_secret_access_key = ""
    s3_region = ""
    if require_s3:
        s3_bucket = _env_required("LOG_BACKUP_S3_BUCKET")
        s3_prefix = _clean_prefix(str(os.environ.get("LOG_BACKUP_S3_PREFIX") or "logs-backup"))
        s3_endpoint_url = _env_required("S3_ENDPOINT_URL")
        s3_access_key_id = _env_required("S3_ACCESS_KEY_ID")
        s3_secret_access_key = _env_required("S3_SECRET_ACCESS_KEY")
        s3_region = str(os.environ.get("S3_REGION") or "ru-1").strip() or "ru-1"

    loki_enabled = _env_bool("LOG_BACKUP_LOKI_ENABLED", False)
    loki_url = str(os.environ.get("LOG_BACKUP_LOKI_URL") or "").strip()
    loki_query = str(os.environ.get("LOG_BACKUP_LOKI_QUERY") or "{}").strip() or "{}"
    if loki_enabled and not loki_url:
        raise RuntimeError("LOG_BACKUP_LOKI_URL is required when LOG_BACKUP_LOKI_ENABLED=true")

    docker_enabled = _env_bool("LOG_BACKUP_DOCKER_ENABLED", False)
    if require_collectors and not (loki_enabled or docker_enabled):
        raise RuntimeError("At least one collector must be enabled: LOG_BACKUP_LOKI_ENABLED or LOG_BACKUP_DOCKER_ENABLED")

    retention_days = _env_int("LOG_BACKUP_RETENTION_DAYS", 180)
    raw_retention_days = _env_int("LOG_BACKUP_RAW_RETENTION_DAYS", 30)
    norm_retention_days = _env_int("LOG_BACKUP_NORM_RETENTION_DAYS", retention_days)
    backfill_days = _env_int("LOG_BACKUP_BACKFILL_DAYS", 30)
    chunk_size = _env_int("LOG_BACKUP_S3_CHUNK_SIZE", 5000)
    loki_limit = _env_int("LOG_BACKUP_LOKI_LIMIT", 5000)
    max_lag_min = _env_int("LOG_BACKUP_MAX_LAG_MIN", 90)

    if retention_days <= 0 or raw_retention_days <= 0 or norm_retention_days <= 0:
        raise RuntimeError("Retention days must be positive")
    if backfill_days <= 0:
        raise RuntimeError("LOG_BACKUP_BACKFILL_DAYS must be positive")
    if chunk_size <= 0:
        raise RuntimeError("LOG_BACKUP_S3_CHUNK_SIZE must be positive")
    if loki_limit <= 0:
        raise RuntimeError("LOG_BACKUP_LOKI_LIMIT must be positive")
    if max_lag_min <= 0:
        raise RuntimeError("LOG_BACKUP_MAX_LAG_MIN must be positive")

    return PipelineConfig(
        enabled=enabled,
        node_name=node_name,
        node_role=node_role,
        db_dsn=db_dsn,
        s3_bucket=s3_bucket,
        s3_prefix=s3_prefix,
        s3_endpoint_url=s3_endpoint_url,
        s3_access_key_id=s3_access_key_id,
        s3_secret_access_key=s3_secret_access_key,
        s3_region=s3_region,
        loki_enabled=loki_enabled,
        loki_url=loki_url,
        loki_query=loki_query,
        docker_enabled=docker_enabled,
        retention_days=retention_days,
        raw_retention_days=raw_retention_days,
        norm_retention_days=norm_retention_days,
        backfill_days=backfill_days,
        chunk_size=chunk_size,
        loki_limit=loki_limit,
        max_lag_min=max_lag_min,
    )


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _deterministic_mask(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"<redacted:{digest}>"


def redact_message(text: str) -> str:
    out = str(text or "")

    def _auth_repl(m: re.Match[str]) -> str:
        prefix = m.group(1)
        bearer = m.group(2) or ""
        token = m.group(3) or ""
        return f"{prefix}{bearer}{_deterministic_mask(token)}"

    def _url_repl(m: re.Match[str]) -> str:
        return f"{m.group(1)}{_deterministic_mask(m.group(2) or '')}"

    def _kv_repl(m: re.Match[str]) -> str:
        key = m.group(1)
        value = m.group(2) or ""
        return f"{key}={_deterministic_mask(value)}"

    out = AUTH_HEADER_RE.sub(_auth_repl, out)
    out = URL_SECRET_RE.sub(_url_repl, out)
    out = KV_SECRET_RE.sub(_kv_repl, out)
    return out


def _extract_first(regex: re.Pattern[str], text: str) -> str:
    m = regex.search(str(text or ""))
    if not m:
        return ""
    return str(m.group(1) or "").strip()


def _parse_severity(message: str) -> str:
    msg = str(message or "")
    m = SEVERITY_RE.search(msg)
    if m:
        val = str(m.group(1) or "").lower()
        if val == "warning":
            return "warn"
        return val
    lower = msg.lower()
    if " exception" in lower or " traceback" in lower or " error" in lower:
        return "error"
    if " warn" in lower:
        return "warn"
    if " info" in lower:
        return "info"
    return ""


def build_event_fingerprint(
    *,
    source_kind: str,
    node_name: str,
    node_role: str,
    labels: dict[str, Any],
    event_ts: datetime,
    line_marker: str,
    message_raw: str,
) -> str:
    payload = {
        "source_kind": str(source_kind),
        "node_name": str(node_name),
        "node_role": str(node_role),
        "labels": labels,
        "event_ts": _to_utc(event_ts).isoformat(),
        "line_marker": str(line_marker),
        "message_raw": str(message_raw),
    }
    raw = _json_dumps(payload)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def infer_event_domain(service: str, container: str, message: str) -> str:
    svc = str(service or "").strip().lower()
    ctn = str(container or "").strip().lower()
    msg = str(message or "").strip().lower()

    if svc == "orchestrator-api" or "orchestrator" in ctn:
        return "orchestrator"
    if svc.startswith("worker-") or "celery" in msg or "queue=" in msg:
        return "workers_queue"
    if svc.startswith("tg-bot") or ctn.startswith("tg-bot"):
        return "tg_bots"
    return "infra_ops"


def infer_event_name(message: str) -> str:
    msg = str(message or "")
    extracted = _extract_first(EVENT_NAME_RE, msg)
    if extracted:
        return extracted

    token = ""
    for part in msg.strip().split():
        cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "", part)
        if not cleaned:
            continue
        if cleaned.lower() in {"info", "warn", "error", "debug"}:
            continue
        token = cleaned
        break
    if token:
        return token.lower()
    return "log_line"


def infer_outcome(message: str) -> str:
    low = str(message or "").lower()
    if any(x in low for x in ("failed", "error", "exception", "traceback", "timeout")):
        return "failed"
    if any(x in low for x in ("success", "completed", "done", "ok")):
        return "success"
    if any(x in low for x in ("start", "queued", "running", "retry")):
        return "running"
    return ""


def _normalize_orchestrator(base: NormEvent, raw_event: RawEvent) -> NormEvent:
    _ = raw_event
    return base


def _normalize_workers_queue(base: NormEvent, raw_event: RawEvent) -> NormEvent:
    attrs = dict(base.attrs_json)
    attrs["worker_type"] = raw_event.service or raw_event.container
    return replace(base, attrs_json=attrs)


def _normalize_tg_bots(base: NormEvent, raw_event: RawEvent) -> NormEvent:
    attrs = dict(base.attrs_json)
    attrs["bot_service"] = raw_event.service or raw_event.container
    return replace(base, attrs_json=attrs)


def _normalize_infra_ops(base: NormEvent, raw_event: RawEvent) -> NormEvent:
    _ = raw_event
    return base


DOMAIN_NORMALIZERS = {
    "orchestrator": _normalize_orchestrator,
    "workers_queue": _normalize_workers_queue,
    "tg_bots": _normalize_tg_bots,
    "infra_ops": _normalize_infra_ops,
}


def normalize_event(raw_event: RawEvent, *, raw_event_id: int, raw_event_ts: datetime) -> NormEvent:
    message = raw_event.message_redacted
    domain = infer_event_domain(raw_event.service, raw_event.container, message)
    event_name = infer_event_name(message)
    outcome = infer_outcome(message)

    queue_name = _extract_first(QUEUE_RE, message)
    chat_id_raw = _extract_first(CHAT_ID_RE, message)
    duration_ms_raw = _extract_first(DURATION_RE, message)
    cost_raw = _extract_first(COST_RE, message)

    chat_id = int(chat_id_raw) if chat_id_raw else None
    duration_ms = int(duration_ms_raw) if duration_ms_raw else None
    cost_value = Decimal(cost_raw) if cost_raw else None

    base = NormEvent(
        event_ts=_to_utc(raw_event.event_ts),
        raw_event_ts=_to_utc(raw_event_ts),
        raw_event_id=int(raw_event_id),
        schema_version=1,
        event_domain=domain,
        event_name=event_name,
        outcome=outcome,
        job_id=raw_event.job_id,
        request_id=raw_event.request_id,
        queue_name=queue_name,
        chat_id=chat_id,
        duration_ms=duration_ms,
        cost_value=cost_value,
        message_redacted=message,
        attrs_json={
            "source_kind": raw_event.source_kind,
            "service": raw_event.service,
            "container": raw_event.container,
            "stream": raw_event.stream,
            "severity": raw_event.severity,
        },
    )
    normalizer = DOMAIN_NORMALIZERS.get(domain)
    if normalizer is None:
        return base
    return normalizer(base, raw_event)


def _parse_loki_ns(ns_value: str) -> datetime:
    ns = int(str(ns_value).strip())
    sec = ns // 1_000_000_000
    nsec = ns % 1_000_000_000
    dt = datetime.fromtimestamp(sec, tz=timezone.utc)
    return dt.replace(microsecond=nsec // 1000)


def _parse_line_with_timestamp(line: str) -> tuple[datetime | None, str]:
    raw = str(line or "").rstrip("\n")
    if not raw:
        return None, ""
    parts = raw.split(" ", 1)
    if len(parts) != 2:
        return None, raw
    ts_raw, msg = parts
    ts_norm = ts_raw.replace("Z", "+00:00")
    if "." in ts_norm:
        head, tail = ts_norm.split(".", 1)
        suffix = ""
        if "+" in tail:
            frac, suffix = tail.split("+", 1)
            suffix = "+" + suffix
        elif "-" in tail:
            frac, suffix = tail.split("-", 1)
            suffix = "-" + suffix
        else:
            frac = tail
        frac = re.sub(r"[^0-9]", "", frac)[:6]
        ts_norm = f"{head}.{frac}{suffix}" if frac else f"{head}{suffix}"
    try:
        ts = datetime.fromisoformat(ts_norm)
    except Exception:
        return None, raw
    return _to_utc(ts), msg


async def collect_loki_events(cfg: PipelineConfig, *, window_from: datetime, window_to: datetime) -> list[RawEvent]:
    start_ns = int(_to_utc(window_from).timestamp() * 1_000_000_000)
    end_ns = int(_to_utc(window_to).timestamp() * 1_000_000_000)
    cursor_ns = start_ns
    events: list[RawEvent] = []
    stream_line_seq: dict[str, int] = {}

    timeout = httpx.Timeout(30.0, read=60.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        while cursor_ns < end_ns:
            params = {
                "query": cfg.loki_query,
                "start": str(cursor_ns),
                "end": str(end_ns),
                "limit": str(cfg.loki_limit),
                "direction": "forward",
            }
            resp = await client.get(cfg.loki_url.rstrip("/") + "/loki/api/v1/query_range", params=params)
            resp.raise_for_status()
            payload = resp.json()
            data = payload.get("data") or {}
            result = data.get("result") or []

            batch_count = 0
            max_ns_seen = cursor_ns
            for stream_item in result:
                labels = dict(stream_item.get("stream") or {})
                label_fingerprint = hashlib.sha1(_json_dumps(labels).encode("utf-8")).hexdigest()[:16]
                values = list(stream_item.get("values") or [])
                for value in values:
                    if not isinstance(value, (list, tuple)) or len(value) < 2:
                        continue
                    ts_ns_raw = str(value[0])
                    message_raw = str(value[1] or "")
                    ts = _parse_loki_ns(ts_ns_raw)
                    ts_ns = int(ts_ns_raw)
                    if ts_ns < start_ns or ts_ns >= end_ns:
                        continue
                    batch_count += 1
                    max_ns_seen = max(max_ns_seen, ts_ns)

                    stream_key = f"{labels.get('container', '')}:{labels.get('stream', '')}:{label_fingerprint}"
                    stream_line_seq[stream_key] = stream_line_seq.get(stream_key, 0) + 1
                    line_marker = f"{ts_ns}:{label_fingerprint}:{stream_line_seq[stream_key]}"

                    redacted = redact_message(message_raw)
                    service = str(labels.get("service") or "").strip()
                    container = str(labels.get("container") or "").strip().lstrip("/")
                    stream = str(labels.get("stream") or "").strip()
                    severity = _parse_severity(message_raw)
                    job_id = _extract_first(JOB_ID_RE, message_raw)
                    request_id = _extract_first(REQUEST_ID_RE, message_raw)

                    raw_event = RawEvent(
                        event_ts=ts,
                        source_kind=SOURCE_KIND_LOKI,
                        node_role=cfg.node_role,
                        node_name=cfg.node_name,
                        service=service,
                        container=container,
                        stream=stream,
                        severity=severity,
                        job_id=job_id,
                        request_id=request_id,
                        message_raw=message_raw,
                        message_redacted=redacted,
                        labels_json=labels,
                        attrs_json={"collector": "loki"},
                        event_fingerprint=build_event_fingerprint(
                            source_kind=SOURCE_KIND_LOKI,
                            node_name=cfg.node_name,
                            node_role=cfg.node_role,
                            labels=labels,
                            event_ts=ts,
                            line_marker=line_marker,
                            message_raw=message_raw,
                        ),
                        line_marker=line_marker,
                    )
                    events.append(raw_event)

            if batch_count == 0:
                break
            cursor_ns = max_ns_seen + 1

    events.sort(key=lambda x: (x.event_ts, x.event_fingerprint))
    return events


def _is_target_container(name: str) -> bool:
    container_name = str(name or "").strip().lstrip("/")
    if not container_name:
        return False
    for prefix in TARGET_CONTAINER_PREFIXES:
        if container_name == prefix or container_name.startswith(prefix):
            return True
    return False


def collect_docker_events(cfg: PipelineConfig, *, window_from: datetime, window_to: datetime) -> list[RawEvent]:
    try:
        import docker  # type: ignore
    except Exception as exc:  # pragma: no cover - runtime env only
        raise RuntimeError("docker python package is required for LOG_BACKUP_DOCKER_ENABLED=true") from exc

    since = int(_to_utc(window_from).timestamp())
    until = int(_to_utc(window_to).timestamp())

    events: list[RawEvent] = []
    client = docker.from_env()
    try:
        containers = client.containers.list(all=False)
    except Exception as exc:  # pragma: no cover - runtime env only
        raise RuntimeError(f"Failed to list docker containers: {exc}") from exc

    for c in containers:
        name = str(getattr(c, "name", "") or "")
        if not _is_target_container(name):
            continue
        attrs = dict(getattr(c, "attrs", {}) or {})
        labels = dict(attrs.get("Config", {}).get("Labels", {}) or {})
        service = str(labels.get("com.docker.compose.service") or name).strip()
        container_name = name.strip().lstrip("/")
        container_id = str(attrs.get("Id") or "")[:12]

        for stream_name, use_stdout, use_stderr in (("stdout", True, False), ("stderr", False, True)):
            try:
                blob = c.logs(
                    stdout=use_stdout,
                    stderr=use_stderr,
                    timestamps=True,
                    since=since,
                    until=until,
                    tail="all",
                )
            except Exception as exc:  # pragma: no cover - runtime env only
                raise RuntimeError(f"Failed to read docker logs for {container_name}/{stream_name}: {exc}") from exc

            text = blob.decode("utf-8", errors="replace") if isinstance(blob, (bytes, bytearray)) else str(blob or "")
            if not text.strip():
                continue

            seq = 0
            for line in text.splitlines():
                ts, msg = _parse_line_with_timestamp(line)
                if ts is None:
                    ts = _to_utc(window_from)
                    msg = str(line or "")
                if ts < _to_utc(window_from) or ts >= _to_utc(window_to):
                    continue
                seq += 1
                line_marker = f"{container_id}:{stream_name}:{seq}"
                redacted = redact_message(msg)

                raw_event = RawEvent(
                    event_ts=ts,
                    source_kind=SOURCE_KIND_DOCKER,
                    node_role=cfg.node_role,
                    node_name=cfg.node_name,
                    service=service,
                    container=container_name,
                    stream=stream_name,
                    severity=_parse_severity(msg),
                    job_id=_extract_first(JOB_ID_RE, msg),
                    request_id=_extract_first(REQUEST_ID_RE, msg),
                    message_raw=msg,
                    message_redacted=redacted,
                    labels_json=labels,
                    attrs_json={"collector": "docker", "container_id": container_id},
                    event_fingerprint=build_event_fingerprint(
                        source_kind=SOURCE_KIND_DOCKER,
                        node_name=cfg.node_name,
                        node_role=cfg.node_role,
                        labels={
                            "service": service,
                            "container": container_name,
                            "stream": stream_name,
                            "container_id": container_id,
                        },
                        event_ts=ts,
                        line_marker=line_marker,
                        message_raw=msg,
                    ),
                    line_marker=line_marker,
                )
                events.append(raw_event)

    events.sort(key=lambda x: (x.event_ts, x.event_fingerprint))
    return events


def _build_s3_client(cfg: PipelineConfig) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=cfg.s3_endpoint_url,
        aws_access_key_id=cfg.s3_access_key_id,
        aws_secret_access_key=cfg.s3_secret_access_key,
        region_name=cfg.s3_region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}, retries={"max_attempts": 5}),
    )


def _chunked(items: Sequence[TItem], size: int) -> Iterable[Sequence[TItem]]:
    for idx in range(0, len(items), size):
        yield items[idx : idx + size]


def _raw_event_to_export_dict(event: RawEvent) -> dict[str, Any]:
    return {
        "event_ts": _to_utc(event.event_ts).isoformat(),
        "source_kind": event.source_kind,
        "node_role": event.node_role,
        "node_name": event.node_name,
        "service": event.service,
        "container": event.container,
        "stream": event.stream,
        "severity": event.severity,
        "job_id": event.job_id,
        "request_id": event.request_id,
        "line_marker": event.line_marker,
        "message_raw": event.message_raw,
        "message_redacted": event.message_redacted,
        "labels": event.labels_json,
        "attrs": event.attrs_json,
        "event_fingerprint": event.event_fingerprint,
    }


def _window_bucket_parts(window_from: datetime) -> tuple[str, str]:
    ts = _to_utc(window_from)
    return ts.strftime("%Y-%m-%d"), ts.strftime("%H")


def upload_raw_events_to_s3(
    *,
    cfg: PipelineConfig,
    s3_client: Any,
    source_kind: str,
    events: Sequence[RawEvent],
    window_from: datetime,
    window_to: datetime,
) -> tuple[list[RawEvent], list[S3ObjectRecord]]:
    if not events:
        return [], []

    dt_part, hour_part = _window_bucket_parts(window_from)
    prefix = cfg.s3_prefix

    enriched_events: list[RawEvent] = []
    objects: list[S3ObjectRecord] = []

    for chunk_idx, chunk in enumerate(_chunked(list(events), cfg.chunk_size)):
        object_key = (
            f"{prefix}/raw/source={source_kind}/node={cfg.node_name}/dt={dt_part}/hour={hour_part}/"
            f"chunk={chunk_idx:05d}.ndjson.gz"
        )

        lines: list[str] = []
        for line_no, event in enumerate(chunk, start=1):
            event_dict = _raw_event_to_export_dict(event)
            lines.append(_json_dumps(event_dict))
            enriched_events.append(
                replace(
                    event,
                    s3_bucket=cfg.s3_bucket,
                    s3_key=object_key,
                    s3_line_no=line_no,
                )
            )

        payload_raw = ("\n".join(lines) + "\n").encode("utf-8")
        payload_gz = gzip.compress(payload_raw, compresslevel=6)
        checksum = hashlib.sha256(payload_raw).hexdigest()

        s3_client.put_object(
            Bucket=cfg.s3_bucket,
            Key=object_key,
            Body=payload_gz,
            ContentType="application/gzip",
        )

        manifest = {
            "source_kind": source_kind,
            "node_name": cfg.node_name,
            "node_role": cfg.node_role,
            "window_from": _to_utc(window_from).isoformat(),
            "window_to": _to_utc(window_to).isoformat(),
            "bucket": cfg.s3_bucket,
            "object_key": object_key,
            "row_count": len(chunk),
            "sha256": checksum,
            "created_at": _utc_now().isoformat(),
        }
        manifest_key = (
            f"{prefix}/manifests/source={source_kind}/node={cfg.node_name}/dt={dt_part}/hour={hour_part}/"
            f"chunk={chunk_idx:05d}.json"
        )
        s3_client.put_object(
            Bucket=cfg.s3_bucket,
            Key=manifest_key,
            Body=(_json_dumps(manifest) + "\n").encode("utf-8"),
            ContentType="application/json",
        )

        objects.append(
            S3ObjectRecord(
                source_kind=source_kind,
                node_name=cfg.node_name,
                node_role=cfg.node_role,
                bucket=cfg.s3_bucket,
                object_key=object_key,
                row_count=len(chunk),
                sha256=checksum,
                window_from_ts=_to_utc(window_from),
                window_to_ts=_to_utc(window_to),
                manifest_json=manifest,
            )
        )

    return enriched_events, objects


async def _connect_db(cfg: PipelineConfig) -> asyncpg.Connection:
    if asyncpg is None:  # pragma: no cover - guarded for environments without DB deps
        raise RuntimeError("asyncpg is required for DB operations. Install dependencies from requirements.txt")
    conn = await asyncpg.connect(dsn=cfg.db_dsn)
    await conn.execute("SET TIME ZONE 'UTC'")
    return conn


async def _apply_schema(conn: asyncpg.Connection) -> None:
    sql = MIGRATION_SQL_PATH.read_text(encoding="utf-8")
    await conn.execute(sql)


def _month_start(ts: datetime) -> datetime:
    x = _to_utc(ts)
    return x.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _next_month(ts: datetime) -> datetime:
    x = _month_start(ts)
    if x.month == 12:
        return x.replace(year=x.year + 1, month=1)
    return x.replace(month=x.month + 1)


def _iter_month_starts(start: datetime, end: datetime) -> Iterable[datetime]:
    cur = _month_start(start)
    limit = _month_start(end)
    while cur <= limit:
        yield cur
        cur = _next_month(cur)


async def _ensure_partition(conn: asyncpg.Connection, parent: str, month_start: datetime) -> None:
    parent = str(parent)
    if parent not in {"logs.raw_events", "logs.events_norm"}:
        raise RuntimeError(f"Unsupported partitioned table: {parent}")
    table_suffix = parent.split(".", 1)[1]
    part_name = f"{table_suffix}_{month_start.year:04d}_{month_start.month:02d}"
    month_end = _next_month(month_start)

    sql = (
        f"CREATE TABLE IF NOT EXISTS logs.{part_name} PARTITION OF {parent} "
        f"FOR VALUES FROM ('{month_start.strftime('%Y-%m-%d %H:%M:%S%z')}') "
        f"TO ('{month_end.strftime('%Y-%m-%d %H:%M:%S%z')}')"
    )
    await conn.execute(sql)


async def _ensure_partitions_for_window(conn: asyncpg.Connection, *, window_from: datetime, window_to: datetime) -> None:
    for month_start in _iter_month_starts(window_from, window_to):
        await _ensure_partition(conn, "logs.raw_events", month_start)
        await _ensure_partition(conn, "logs.events_norm", month_start)


async def _insert_raw_events_and_norm(conn: asyncpg.Connection, events: Sequence[RawEvent]) -> tuple[int, int]:
    raw_inserted = 0
    norm_inserted = 0

    for event in events:
        row = await conn.fetchrow(
            """
            INSERT INTO logs.raw_events (
                event_ts, source_kind, node_role, node_name, service, container, stream,
                severity, job_id, request_id, message_raw, message_redacted, labels_json,
                attrs_json, event_fingerprint, s3_bucket, s3_key, s3_line_no, line_marker
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7,
                $8, $9, $10, $11, $12, $13::jsonb,
                $14::jsonb, $15, $16, $17, $18, $19
            )
            ON CONFLICT (event_ts, event_fingerprint) DO NOTHING
            RETURNING id, event_ts
            """,
            _to_utc(event.event_ts),
            event.source_kind,
            event.node_role,
            event.node_name,
            event.service,
            event.container,
            event.stream,
            event.severity,
            event.job_id,
            event.request_id,
            event.message_raw,
            event.message_redacted,
            _json_dumps(event.labels_json),
            _json_dumps(event.attrs_json),
            event.event_fingerprint,
            event.s3_bucket,
            event.s3_key,
            event.s3_line_no,
            event.line_marker,
        )

        if row is None:
            continue

        raw_inserted += 1
        raw_id = int(row["id"])
        raw_ts = _to_utc(row["event_ts"])
        normalized = normalize_event(event, raw_event_id=raw_id, raw_event_ts=raw_ts)

        result = await conn.execute(
            """
            INSERT INTO logs.events_norm (
                event_ts, raw_event_ts, raw_event_id, schema_version, event_domain,
                event_name, outcome, job_id, request_id, queue_name, chat_id,
                duration_ms, cost_value, message_redacted, attrs_json
            )
            VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, $8, $9, $10, $11,
                $12, $13, $14, $15::jsonb
            )
            ON CONFLICT (event_ts, raw_event_ts, raw_event_id) DO NOTHING
            """,
            normalized.event_ts,
            normalized.raw_event_ts,
            normalized.raw_event_id,
            normalized.schema_version,
            normalized.event_domain,
            normalized.event_name,
            normalized.outcome,
            normalized.job_id,
            normalized.request_id,
            normalized.queue_name,
            normalized.chat_id,
            normalized.duration_ms,
            normalized.cost_value,
            normalized.message_redacted,
            _json_dumps(normalized.attrs_json),
        )
        if result.startswith("INSERT 0 1"):
            norm_inserted += 1

    return raw_inserted, norm_inserted


async def _insert_s3_objects(conn: asyncpg.Connection, objects: Sequence[S3ObjectRecord]) -> int:
    inserted = 0
    for obj in objects:
        res = await conn.execute(
            """
            INSERT INTO logs.s3_objects (
                source_kind, node_name, node_role, bucket, object_key,
                row_count, sha256, window_from_ts, window_to_ts, manifest_json
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb)
            ON CONFLICT (bucket, object_key) DO NOTHING
            """,
            obj.source_kind,
            obj.node_name,
            obj.node_role,
            obj.bucket,
            obj.object_key,
            obj.row_count,
            obj.sha256,
            _to_utc(obj.window_from_ts),
            _to_utc(obj.window_to_ts),
            _json_dumps(obj.manifest_json),
        )
        if res.startswith("INSERT 0 1"):
            inserted += 1
    return inserted


async def _upsert_cursor(
    conn: asyncpg.Connection,
    *,
    source_kind: str,
    cfg: PipelineConfig,
    cursor_key: str,
    window_from: datetime,
    window_to: datetime,
    last_event_ts: datetime | None,
    last_line_marker: str,
) -> None:
    await conn.execute(
        """
        INSERT INTO logs.ingest_cursor (
            source_kind, node_name, node_role, cursor_key,
            window_from_ts, window_to_ts, last_event_ts, last_line_marker, updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
        ON CONFLICT (source_kind, node_name, node_role, cursor_key)
        DO UPDATE
        SET window_from_ts = EXCLUDED.window_from_ts,
            window_to_ts = EXCLUDED.window_to_ts,
            last_event_ts = EXCLUDED.last_event_ts,
            last_line_marker = EXCLUDED.last_line_marker,
            updated_at = NOW()
        """,
        source_kind,
        cfg.node_name,
        cfg.node_role,
        cursor_key,
        _to_utc(window_from),
        _to_utc(window_to),
        _to_utc(last_event_ts) if last_event_ts else None,
        last_line_marker,
    )


async def _insert_run_started(
    conn: asyncpg.Connection,
    *,
    run_id: str,
    run_kind: str,
    cfg: PipelineConfig,
    window_from: datetime,
    window_to: datetime,
) -> None:
    await conn.execute(
        """
        INSERT INTO logs.ingest_runs (
            run_id, run_kind, node_name, node_role, window_from_ts, window_to_ts, status
        )
        VALUES ($1::uuid, $2, $3, $4, $5, $6, 'running')
        """,
        run_id,
        run_kind,
        cfg.node_name,
        cfg.node_role,
        _to_utc(window_from),
        _to_utc(window_to),
    )


async def _finish_run(
    conn: asyncpg.Connection,
    *,
    run_id: str,
    status: str,
    raw_count: int,
    norm_count: int,
    s3_count: int,
    error_text: str,
) -> None:
    await conn.execute(
        """
        UPDATE logs.ingest_runs
        SET status = $2,
            finished_at = NOW(),
            raw_events_cnt = $3,
            norm_events_cnt = $4,
            s3_objects_cnt = $5,
            error_text = $6
        WHERE run_id = $1::uuid
        """,
        run_id,
        status,
        int(raw_count),
        int(norm_count),
        int(s3_count),
        str(error_text or ""),
    )


def _collect_last_markers(events: Sequence[RawEvent]) -> tuple[datetime | None, str]:
    if not events:
        return None, ""
    last = max(events, key=lambda x: (x.event_ts, x.line_marker))
    return _to_utc(last.event_ts), str(last.line_marker)


async def _run_window(
    cfg: PipelineConfig,
    *,
    window_from: datetime,
    window_to: datetime,
    run_kind: str,
) -> dict[str, int]:
    conn = await _connect_db(cfg)
    run_id = str(uuid.uuid4())
    raw_count = 0
    norm_count = 0
    s3_count = 0
    try:
        await _apply_schema(conn)
        await _ensure_partitions_for_window(conn, window_from=window_from, window_to=window_to)
        await _insert_run_started(
            conn,
            run_id=run_id,
            run_kind=run_kind,
            cfg=cfg,
            window_from=window_from,
            window_to=window_to,
        )

        collected: dict[str, list[RawEvent]] = {}
        if cfg.loki_enabled:
            collected[SOURCE_KIND_LOKI] = await collect_loki_events(cfg, window_from=window_from, window_to=window_to)
        if cfg.docker_enabled:
            collected[SOURCE_KIND_DOCKER] = collect_docker_events(cfg, window_from=window_from, window_to=window_to)

        s3 = _build_s3_client(cfg)
        all_enriched: list[RawEvent] = []
        all_s3_objects: list[S3ObjectRecord] = []

        for source_kind, events in collected.items():
            enriched, objects = upload_raw_events_to_s3(
                cfg=cfg,
                s3_client=s3,
                source_kind=source_kind,
                events=events,
                window_from=window_from,
                window_to=window_to,
            )
            all_enriched.extend(enriched)
            all_s3_objects.extend(objects)

        all_enriched.sort(key=lambda x: (x.event_ts, x.event_fingerprint))

        async with conn.transaction():
            raw_count, norm_count = await _insert_raw_events_and_norm(conn, all_enriched)
            s3_count = await _insert_s3_objects(conn, all_s3_objects)

            for source_kind, events in collected.items():
                last_event_ts, last_marker = _collect_last_markers(events)
                await _upsert_cursor(
                    conn,
                    source_kind=source_kind,
                    cfg=cfg,
                    cursor_key=source_kind,
                    window_from=window_from,
                    window_to=window_to,
                    last_event_ts=last_event_ts,
                    last_line_marker=last_marker,
                )

        await _finish_run(
            conn,
            run_id=run_id,
            status="ok",
            raw_count=raw_count,
            norm_count=norm_count,
            s3_count=s3_count,
            error_text="",
        )
    except Exception as exc:
        try:
            await _finish_run(
                conn,
                run_id=run_id,
                status="failed",
                raw_count=raw_count,
                norm_count=norm_count,
                s3_count=s3_count,
                error_text=str(exc),
            )
        finally:
            await conn.close()
        raise
    else:
        await conn.close()

    return {
        "raw_events": raw_count,
        "norm_events": norm_count,
        "s3_objects": s3_count,
    }


def _parse_window_start(value: str) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise RuntimeError("window start is empty")
    text = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except Exception as exc:
        raise RuntimeError(f"Invalid ISO datetime for --window-start: {value!r}") from exc
    if dt.tzinfo is None:
        raise RuntimeError("--window-start must include timezone")
    return _hour_floor(dt)


async def cmd_migrate(_: argparse.Namespace) -> None:
    cfg = _load_config(require_enabled=False, require_s3=False, require_collectors=False)
    conn = await _connect_db(cfg)
    try:
        await _apply_schema(conn)
        now = _utc_now()
        await _ensure_partition(conn, "logs.raw_events", _month_start(now))
        await _ensure_partition(conn, "logs.events_norm", _month_start(now))
        await _ensure_partition(conn, "logs.raw_events", _next_month(now))
        await _ensure_partition(conn, "logs.events_norm", _next_month(now))
    finally:
        await conn.close()

    print("[logs_pipeline] migrate: ok")


async def cmd_run_hourly(args: argparse.Namespace) -> None:
    cfg = _load_config(require_enabled=True, require_s3=True, require_collectors=True)
    if args.window_start:
        window_from = _parse_window_start(args.window_start)
    else:
        window_from = _hour_floor(_utc_now()) - timedelta(hours=1)
    window_to = window_from + timedelta(hours=1)

    stats = await _run_window(cfg, window_from=window_from, window_to=window_to, run_kind="hourly")
    print(
        "[logs_pipeline] run-hourly: ok "
        f"window_from={window_from.isoformat()} window_to={window_to.isoformat()} "
        f"raw_events={stats['raw_events']} norm_events={stats['norm_events']} s3_objects={stats['s3_objects']}"
    )


async def cmd_backfill(args: argparse.Namespace) -> None:
    cfg = _load_config(require_enabled=True, require_s3=True, require_collectors=True)
    days = int(args.days or cfg.backfill_days)
    if days <= 0:
        raise RuntimeError("--days must be positive")

    end = _hour_floor(_utc_now())
    start = end - timedelta(days=days)
    cursor = start

    total_raw = 0
    total_norm = 0
    total_s3 = 0
    windows = 0

    while cursor < end:
        nxt = cursor + timedelta(hours=1)
        stats = await _run_window(cfg, window_from=cursor, window_to=nxt, run_kind="backfill")
        total_raw += stats["raw_events"]
        total_norm += stats["norm_events"]
        total_s3 += stats["s3_objects"]
        windows += 1
        cursor = nxt

    print(
        "[logs_pipeline] backfill: ok "
        f"days={days} windows={windows} raw_events={total_raw} norm_events={total_norm} s3_objects={total_s3}"
    )


async def _drop_old_partitions(conn: asyncpg.Connection, *, parent_rel: str, cutoff_ts: datetime) -> int:
    rows = await conn.fetch(
        """
        SELECT c.relname
        FROM pg_inherits i
        JOIN pg_class c ON c.oid = i.inhrelid
        JOIN pg_class p ON p.oid = i.inhparent
        JOIN pg_namespace pn ON pn.oid = p.relnamespace
        JOIN pg_namespace cn ON cn.oid = c.relnamespace
        WHERE pn.nspname = 'logs' AND cn.nspname = 'logs' AND p.relname = $1
        ORDER BY c.relname
        """,
        parent_rel,
    )
    dropped = 0

    pattern = re.compile(rf"^{re.escape(parent_rel)}_(\d{{4}})_(\d{{2}})$")
    cutoff = _to_utc(cutoff_ts)

    for row in rows:
        relname = str(row["relname"])
        m = pattern.match(relname)
        if not m:
            continue
        year = int(m.group(1))
        month = int(m.group(2))
        start = datetime(year=year, month=month, day=1, tzinfo=timezone.utc)
        end = _next_month(start)
        if end <= cutoff:
            await conn.execute(f"DROP TABLE IF EXISTS logs.{relname}")
            dropped += 1

    return dropped


async def cmd_prune(_: argparse.Namespace) -> None:
    cfg = _load_config(require_enabled=True, require_s3=False, require_collectors=False)
    conn = await _connect_db(cfg)
    try:
        await _apply_schema(conn)
        now = _utc_now()
        raw_cutoff = now - timedelta(days=cfg.raw_retention_days)
        norm_cutoff = now - timedelta(days=cfg.norm_retention_days)

        async with conn.transaction():
            raw_dropped = await _drop_old_partitions(conn, parent_rel="raw_events", cutoff_ts=raw_cutoff)
            norm_dropped = await _drop_old_partitions(conn, parent_rel="events_norm", cutoff_ts=norm_cutoff)
            await conn.execute(
                "DELETE FROM logs.ingest_runs WHERE started_at < NOW() - make_interval(days => $1::int)",
                cfg.norm_retention_days,
            )
            await conn.execute(
                "DELETE FROM logs.s3_objects WHERE created_at < NOW() - make_interval(days => $1::int)",
                cfg.retention_days,
            )
    finally:
        await conn.close()

    print(f"[logs_pipeline] prune: ok raw_partitions_dropped={raw_dropped} norm_partitions_dropped={norm_dropped}")


async def cmd_healthcheck(args: argparse.Namespace) -> None:
    cfg = _load_config(require_enabled=True, require_s3=False, require_collectors=False)
    max_lag_min = int(args.max_lag_min or cfg.max_lag_min)
    if max_lag_min <= 0:
        raise RuntimeError("--max-lag-min must be positive")

    conn = await _connect_db(cfg)
    try:
        await _apply_schema(conn)
        rows = await conn.fetch(
            """
            SELECT source_kind, window_to_ts
            FROM logs.ingest_cursor
            WHERE node_name = $1 AND node_role = $2
            """,
            cfg.node_name,
            cfg.node_role,
        )
    finally:
        await conn.close()

    required_sources: list[str] = []
    if _env_bool("LOG_BACKUP_LOKI_ENABLED", False):
        required_sources.append(SOURCE_KIND_LOKI)
    if _env_bool("LOG_BACKUP_DOCKER_ENABLED", False):
        required_sources.append(SOURCE_KIND_DOCKER)
    if not required_sources:
        raise RuntimeError("Healthcheck requires at least one enabled source")

    source_to_window: dict[str, datetime] = {}
    for row in rows:
        source_to_window[str(row["source_kind"])] = _to_utc(row["window_to_ts"])

    now = _utc_now()
    lag_report: list[str] = []

    for source in required_sources:
        if source not in source_to_window:
            raise RuntimeError(f"Missing ingest cursor for source={source} node={cfg.node_name}/{cfg.node_role}")
        lag_min = (now - source_to_window[source]).total_seconds() / 60.0
        lag_report.append(f"{source}:{lag_min:.1f}m")
        if lag_min > max_lag_min:
            raise RuntimeError(
                f"Lag too high for source={source}: {lag_min:.1f}m (threshold={max_lag_min}m)"
            )

    print("[logs_pipeline] healthcheck: ok " + " ".join(lag_report))


def _ensure_s3_prefix_and_lifecycle(cfg: PipelineConfig, *, s3_client: Any) -> None:
    keep_key = f"{cfg.s3_prefix}/.keep"
    s3_client.put_object(Bucket=cfg.s3_bucket, Key=keep_key, Body=b"\n")

    rules = [
        {
            "ID": "blast-logs-raw-expire",
            "Status": "Enabled",
            "Filter": {"Prefix": f"{cfg.s3_prefix}/raw/"},
            "Expiration": {"Days": cfg.retention_days},
        },
        {
            "ID": "blast-logs-manifests-expire",
            "Status": "Enabled",
            "Filter": {"Prefix": f"{cfg.s3_prefix}/manifests/"},
            "Expiration": {"Days": cfg.retention_days},
        },
    ]

    s3_client.put_bucket_lifecycle_configuration(
        Bucket=cfg.s3_bucket,
        LifecycleConfiguration={"Rules": rules},
    )

    got = s3_client.get_bucket_lifecycle_configuration(Bucket=cfg.s3_bucket)
    got_rules = list((got or {}).get("Rules") or [])
    got_ids = {str(r.get("ID") or "") for r in got_rules}
    for required_id in ("blast-logs-raw-expire", "blast-logs-manifests-expire"):
        if required_id not in got_ids:
            raise RuntimeError(f"Lifecycle rule not applied: {required_id}")


async def cmd_bootstrap_lifecycle(_: argparse.Namespace) -> None:
    cfg = _load_config(require_enabled=True, require_s3=True, require_collectors=False)
    s3 = _build_s3_client(cfg)
    _ensure_s3_prefix_and_lifecycle(cfg, s3_client=s3)
    print(
        "[logs_pipeline] bootstrap-s3-lifecycle: ok "
        f"bucket={cfg.s3_bucket} prefix={cfg.s3_prefix} retention_days={cfg.retention_days}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Blast logs pipeline: postgres normalization + S3 raw backups")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="Apply logs schema migration and ensure current/next month partitions")

    run_hourly = sub.add_parser("run-hourly", help="Ingest previous hour (or explicit --window-start)")
    run_hourly.add_argument("--window-start", default="", help="UTC hour start in ISO format, e.g. 2026-04-19T10:00:00+00:00")

    backfill = sub.add_parser("backfill", help="Run hourly ingestion for historical window")
    backfill.add_argument("--days", type=int, default=0, help="Backfill depth in days (default LOG_BACKUP_BACKFILL_DAYS)")

    sub.add_parser("prune", help="Drop old partitions according to retention")

    healthcheck = sub.add_parser("healthcheck", help="Check ingest lag for enabled sources")
    healthcheck.add_argument("--max-lag-min", type=int, default=0, help="Maximum allowed lag in minutes")

    sub.add_parser("bootstrap-s3-lifecycle", help="Create logs prefix and enforce S3 lifecycle rule")

    return parser


async def _main_async(args: argparse.Namespace) -> None:
    if args.command == "migrate":
        await cmd_migrate(args)
        return
    if args.command == "run-hourly":
        await cmd_run_hourly(args)
        return
    if args.command == "backfill":
        await cmd_backfill(args)
        return
    if args.command == "prune":
        await cmd_prune(args)
        return
    if args.command == "healthcheck":
        await cmd_healthcheck(args)
        return
    if args.command == "bootstrap-s3-lifecycle":
        await cmd_bootstrap_lifecycle(args)
        return
    raise RuntimeError(f"Unknown command: {args.command}")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
