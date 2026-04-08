from __future__ import annotations

import argparse
import datetime as dt
import json
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable


def normalize_url(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    return value.rstrip("/")


def dedupe_urls(urls: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in urls:
        url = normalize_url(raw)
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def merge_pool_urls(existing: list[str], candidate: str) -> list[str]:
    return dedupe_urls([*existing, candidate])


def _now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def emit_event(event: str, **fields: Any) -> None:
    payload: dict[str, Any] = {"ts": _now_iso(), "event": str(event or "").strip() or "unknown"}
    for key, value in fields.items():
        if value is None:
            continue
        payload[str(key)] = value
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def http_json(
    *,
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    timeout_s: float = 20.0,
) -> dict[str, Any]:
    data: bytes | None = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(url=url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=float(timeout_s)) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            status_code = int(getattr(resp, "status", 200))
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        raise RuntimeError(
            f"http_error method={method} url={url} status={getattr(e, 'code', '?')} body={raw[:800]}"
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"http_transport_error method={method} url={url} err={e!r}") from e

    if status_code >= 400:
        raise RuntimeError(f"http_status_error method={method} url={url} status={status_code} body={body[:800]}")
    try:
        obj = json.loads(body)
    except Exception as e:
        raise RuntimeError(f"http_invalid_json method={method} url={url} body={body[:800]}") from e
    if not isinstance(obj, dict):
        raise RuntimeError(f"http_json_object_expected method={method} url={url} got={type(obj).__name__}")
    return obj


def build_ansible_restart_command(
    *,
    node_host: str,
    node_user: str,
    node_password: str,
    test_node_url: str,
    playbook_path: str,
    dev_root: str,
    start_afterfx: bool,
    kill_afterfx_first: bool,
    health_timeout_sec: int,
    health_poll_sec: int,
) -> list[str]:
    extra_vars = {
        "ansible_user": str(node_user),
        "ansible_password": str(node_password),
        "ansible_connection": "winrm",
        "ansible_port": 5985,
        "ansible_winrm_transport": "ntlm",
        "ansible_winrm_server_cert_validation": "ignore",
        "dev_root": str(dev_root),
        "test_node_url": normalize_url(test_node_url),
        "start_afterfx": bool(start_afterfx),
        "kill_afterfx_first": bool(kill_afterfx_first),
        "health_timeout_sec": int(health_timeout_sec),
        "health_poll_sec": int(health_poll_sec),
    }
    return [
        "ansible-playbook",
        "-i",
        f"{node_host},",
        str(playbook_path),
        "-e",
        json.dumps(extra_vars, ensure_ascii=False),
    ]


def run_command_stream(command: list[str]) -> None:
    emit_event("command_start", command=" ".join(shlex.quote(part) for part in command))
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for raw_line in proc.stdout:
        line = raw_line.rstrip("\n")
        if not line:
            continue
        emit_event("command_output", line=line)
    rc = proc.wait()
    emit_event("command_exit", return_code=rc)
    if rc != 0:
        raise RuntimeError(f"command_failed return_code={rc}")


def wait_for_health(*, node_url: str, timeout_s: float, poll_interval_s: float) -> dict[str, Any]:
    health_url = f"{normalize_url(node_url)}/health"
    deadline = time.time() + float(timeout_s)
    last_error = "unknown"
    while time.time() < deadline:
        try:
            payload = http_json(method="GET", url=health_url, timeout_s=5.0)
            status_raw = str(payload.get("status") or "").strip().lower()
            ok_raw = payload.get("ok")
            if status_raw == "ok" or ok_raw is True:
                emit_event("node_health_ok", node_url=node_url, payload=payload)
                return payload
            last_error = f"unexpected_payload={payload!r}"
        except Exception as e:
            last_error = repr(e)
        emit_event("node_health_wait", node_url=node_url, error=last_error)
        time.sleep(max(0.1, float(poll_interval_s)))
    raise RuntimeError(f"node_health_timeout node_url={node_url} timeout_s={timeout_s} last_error={last_error}")


def fetch_windows_nodes(*, orchestrator_url: str, timeout_s: float = 20.0) -> dict[str, Any]:
    return http_json(
        method="GET",
        url=f"{normalize_url(orchestrator_url)}/windows-nodes",
        timeout_s=timeout_s,
    )


def put_windows_nodes(*, orchestrator_url: str, urls: list[str], timeout_s: float = 20.0) -> dict[str, Any]:
    return http_json(
        method="PUT",
        url=f"{normalize_url(orchestrator_url)}/windows-nodes",
        payload={"urls": dedupe_urls(urls)},
        timeout_s=timeout_s,
    )


def enqueue_canary_job(
    *,
    orchestrator_url: str,
    audio_s3_url: str,
    mode: str = "with_gemini",
    llm_worker_type: str = "",
    timeout_s: float = 20.0,
) -> str:
    selected_mode = str(mode or "with_gemini").strip().lower()
    if selected_mode not in {"with_gemini", "no_gemini"}:
        raise RuntimeError(f"unsupported_canary_mode={mode!r}; expected with_gemini|no_gemini")
    payload: dict[str, Any] = {
        "audio_s3_url": str(audio_s3_url).strip(),
        "mode": selected_mode,
        "idempotency_key": f"win-canary-{int(time.time())}",
    }
    wt = str(llm_worker_type or "").strip()
    if wt:
        payload["llm_worker_type"] = wt
    out = http_json(
        method="POST",
        url=f"{normalize_url(orchestrator_url)}/send_audio_s3",
        payload=payload,
        timeout_s=timeout_s,
    )
    job_id = str(out.get("job_id") or "").strip()
    if not job_id:
        raise RuntimeError(f"missing_job_id_in_enqueue_response payload={out!r}")
    return job_id


def fetch_job_state(*, orchestrator_url: str, job_id: str, timeout_s: float = 20.0) -> dict[str, Any]:
    return http_json(
        method="GET",
        url=f"{normalize_url(orchestrator_url)}/jobs/{job_id}",
        timeout_s=timeout_s,
    )


def wait_for_terminal_job_status(
    *,
    fetch_state: Callable[[], dict[str, Any]],
    timeout_s: float,
    poll_interval_s: float,
) -> dict[str, Any]:
    deadline = time.time() + float(timeout_s)
    while time.time() < deadline:
        state = fetch_state()
        status = str(state.get("status") or "").strip().upper()
        emit_event(
            "canary_poll",
            job_id=state.get("job_id"),
            status=status,
            stage=state.get("stage"),
        )
        if status in {"SUCCEEDED", "FAILED"}:
            return state
        time.sleep(max(0.2, float(poll_interval_s)))
    raise RuntimeError(f"canary_job_timeout timeout_s={timeout_s}")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Restart Windows render node, run canary, and update orchestrator node pool automatically."
    )
    p.add_argument("--node-host", required=True, help="Windows host/IP for WinRM")
    p.add_argument("--node-user", default="Administrator")
    p.add_argument("--node-password", required=True)
    p.add_argument("--test-node-url", required=True, help="Render node base URL, e.g. http://72.56.246.24:8000")
    p.add_argument("--orchestrator-url", default="", help="Orchestrator base URL")
    p.add_argument("--canary-audio-s3-url", default="", help="S3/HTTP URL for a real canary audio")
    p.add_argument("--canary-mode", default="with_gemini", choices=["with_gemini", "no_gemini"])
    p.add_argument("--llm-worker-type", default="", help="Optional worker type pin (sdk/openrouter/hybrid)")
    p.add_argument("--playbook-path", default="infra/windows_ops/restart_render_node.yml")
    p.add_argument("--dev-root", default=r"C:\ae_dev")
    p.add_argument("--health-timeout-sec", type=int, default=180)
    p.add_argument("--health-poll-sec", type=int, default=2)
    p.add_argument("--canary-timeout-sec", type=int, default=1800)
    p.add_argument("--canary-poll-sec", type=float, default=5.0)
    p.add_argument("--skip-restart", action="store_true")
    p.add_argument("--skip-canary", action="store_true")
    p.add_argument("--start-afterfx", action="store_true")
    p.add_argument("--kill-afterfx-first", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    test_node_url = normalize_url(args.test_node_url)
    orchestrator_url = normalize_url(args.orchestrator_url)

    emit_event(
        "rollout_start",
        node_host=args.node_host,
        test_node_url=test_node_url,
        orchestrator_url=orchestrator_url,
        skip_restart=bool(args.skip_restart),
        skip_canary=bool(args.skip_canary),
    )

    if not args.skip_restart:
        command = build_ansible_restart_command(
            node_host=args.node_host,
            node_user=args.node_user,
            node_password=args.node_password,
            test_node_url=test_node_url,
            playbook_path=args.playbook_path,
            dev_root=args.dev_root,
            start_afterfx=bool(args.start_afterfx),
            kill_afterfx_first=bool(args.kill_afterfx_first),
            health_timeout_sec=int(args.health_timeout_sec),
            health_poll_sec=int(args.health_poll_sec),
        )
        run_command_stream(command)

    wait_for_health(
        node_url=test_node_url,
        timeout_s=float(args.health_timeout_sec),
        poll_interval_s=float(args.health_poll_sec),
    )

    if args.skip_canary:
        emit_event("rollout_done", mode="restart_only")
        return 0

    if not orchestrator_url:
        raise RuntimeError("--orchestrator-url is required unless --skip-canary is set")
    if not str(args.canary_audio_s3_url or "").strip():
        raise RuntimeError("--canary-audio-s3-url is required unless --skip-canary is set")

    before = fetch_windows_nodes(orchestrator_url=orchestrator_url)
    before_runtime = dedupe_urls([str(x) for x in before.get("runtime_urls") or []])
    before_effective = dedupe_urls([str(x) for x in before.get("effective_urls") or []])
    emit_event(
        "pool_before",
        source=before.get("source"),
        runtime_urls=before_runtime,
        effective_urls=before_effective,
    )

    try:
        pinned = put_windows_nodes(orchestrator_url=orchestrator_url, urls=[test_node_url])
        emit_event("pool_pinned", effective_urls=pinned.get("effective_urls"))

        job_id = enqueue_canary_job(
            orchestrator_url=orchestrator_url,
            audio_s3_url=args.canary_audio_s3_url,
            mode=args.canary_mode,
            llm_worker_type=args.llm_worker_type,
        )
        emit_event("canary_enqueued", job_id=job_id)

        final_state = wait_for_terminal_job_status(
            fetch_state=lambda: fetch_job_state(orchestrator_url=orchestrator_url, job_id=job_id),
            timeout_s=float(args.canary_timeout_sec),
            poll_interval_s=float(args.canary_poll_sec),
        )
        final_status = str(final_state.get("status") or "").strip().upper()
        emit_event("canary_terminal", job_id=job_id, status=final_status, stage=final_state.get("stage"))
        if final_status != "SUCCEEDED":
            raise RuntimeError(f"canary_failed status={final_status} state={final_state!r}")

        final_pool_urls = merge_pool_urls(before_effective, test_node_url)
        after = put_windows_nodes(orchestrator_url=orchestrator_url, urls=final_pool_urls)
        emit_event("pool_updated", source=after.get("source"), effective_urls=after.get("effective_urls"))
        emit_event("rollout_done", mode="restart_canary_pool_update", canary_status=final_status)
        return 0
    except Exception as e:
        emit_event("rollout_error", error=repr(e), action="restore_pool")
        restored = put_windows_nodes(orchestrator_url=orchestrator_url, urls=before_runtime)
        emit_event(
            "pool_restored",
            source=restored.get("source"),
            runtime_urls=restored.get("runtime_urls"),
            effective_urls=restored.get("effective_urls"),
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
