from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FANOUT_SCRIPT = REPO_ROOT / "infra" / "runners" / "deploy_remote_fanout.sh"


def _run_fanout(tmp_path: Path, *, fail_nodes: str) -> subprocess.CompletedProcess[str]:
    key0 = tmp_path / "node0"
    key1 = tmp_path / "node1"
    key0.write_text("test", encoding="utf-8")
    key1.write_text("test", encoding="utf-8")
    deploy_stub = tmp_path / "deploy-remote-stub.sh"
    deploy_stub.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'case ",${FAIL_NODES:-}," in\n'
        '  *",${DEPLOY_REMOTE_HOST},"*) exit 1 ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    deploy_stub.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "DEPLOY_REMOTE_SCRIPT_OVERRIDE": str(deploy_stub),
            "DEPLOY_REMOTE_NODE0_HOST": "node0",
            "DEPLOY_REMOTE_NODE0_REPO_DIR": "/repo",
            "DEPLOY_REMOTE_NODE0_SSH_KEY_PATH": str(key0),
            "DEPLOY_REMOTE_NODE1_HOST": "node1",
            "DEPLOY_REMOTE_NODE1_REPO_DIR": "/repo",
            "DEPLOY_REMOTE_NODE1_SSH_KEY_PATH": str(key1),
            "FAIL_NODES": fail_nodes,
            "GITHUB_ACTIONS": "true",
        }
    )
    return subprocess.run(
        ["bash", str(FANOUT_SCRIPT), "main", "prod-path"],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_fanout_succeeds_degraded_when_one_node_deploys(tmp_path: Path) -> None:
    result = _run_fanout(tmp_path, fail_nodes="node0")

    assert result.returncode == 0
    assert "degraded deploy: successful=orchestrator-1 failed=orchestrator-0" in result.stdout
    assert "::warning title=Prod deploy degraded::" in result.stdout


def test_fanout_fails_when_all_nodes_fail(tmp_path: Path) -> None:
    result = _run_fanout(tmp_path, fail_nodes="node0,node1")

    assert result.returncode == 1
    assert "all nodes failed: orchestrator-0 orchestrator-1" in result.stdout
