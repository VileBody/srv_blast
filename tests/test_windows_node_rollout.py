from __future__ import annotations

import json

import pytest

from services.orchestrator.windows_ops_workflow import (
    build_ansible_restart_command,
    merge_pool_urls,
    wait_for_terminal_job_status,
)


def test_merge_pool_urls_deduplicates_and_preserves_order() -> None:
    merged = merge_pool_urls(
        ["http://85.239.48.31:8000", "http://72.56.246.24:8000"],
        "http://72.56.246.24:8000/",
    )
    assert merged == ["http://85.239.48.31:8000", "http://72.56.246.24:8000"]


def test_build_ansible_restart_command_embeds_json_extra_vars() -> None:
    cmd = build_ansible_restart_command(
        node_host="72.56.246.24",
        node_user="Administrator",
        node_password="pass#1",
        test_node_url="http://72.56.246.24:8000/",
        playbook_path="infra/windows_ops/restart_render_node.yml",
        dev_root=r"C:\ae_dev",
        start_afterfx=True,
        kill_afterfx_first=True,
        health_timeout_sec=180,
        health_poll_sec=2,
    )
    assert cmd[:4] == [
        "ansible-playbook",
        "-i",
        "72.56.246.24,",
        "infra/windows_ops/restart_render_node.yml",
    ]
    assert cmd[4] == "-e"
    extra = json.loads(cmd[5])
    assert extra["ansible_user"] == "Administrator"
    assert extra["ansible_password"] == "pass#1"
    assert extra["test_node_url"] == "http://72.56.246.24:8000"
    assert extra["start_afterfx"] is True
    assert extra["kill_afterfx_first"] is True


def test_wait_for_terminal_job_status_returns_succeeded_state() -> None:
    states = iter(
        [
            {"job_id": "j1", "status": "RUNNING", "stage": "poll"},
            {"job_id": "j1", "status": "SUCCEEDED", "stage": "render"},
        ]
    )
    final_state = wait_for_terminal_job_status(
        fetch_state=lambda: next(states),
        timeout_s=5.0,
        poll_interval_s=0.01,
    )
    assert final_state["status"] == "SUCCEEDED"
    assert final_state["stage"] == "render"


def test_wait_for_terminal_job_status_raises_on_timeout() -> None:
    with pytest.raises(RuntimeError, match="canary_job_timeout"):
        wait_for_terminal_job_status(
            fetch_state=lambda: {"job_id": "j2", "status": "RUNNING", "stage": "poll"},
            timeout_s=0.1,
            poll_interval_s=0.01,
        )
