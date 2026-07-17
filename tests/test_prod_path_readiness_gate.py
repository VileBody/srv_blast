# -*- coding: utf-8 -*-
"""The deploy gate must not switch queues/traffic when readiness FAILs.

This runs the REAL infra/runners/lib_prod_path.sh under bash with a stub `docker`
on PATH that records every invocation. Asserting on that log is the only way to
prove the safety property that matters: on FAIL, `up -d` never happens, so the
previously running containers keep serving and Postgres is never touched.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_LIB = _ROOT / "infra" / "runners" / "lib_prod_path.sh"

# Resolve bash ONCE, by absolute path. Invoking bare "bash" is not reliable on
# Windows: CreateProcess can pick a different interpreter than the one on the
# Python PATH, and that one silently reports $? as 0 — which would make every
# assertion here vacuously pass.
_BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(
    _BASH is None, reason="bash is required to exercise the deploy gate"
)


DOCKER_STUB = r"""#!/usr/bin/env bash
# Records every docker invocation, then fails ONLY the readiness run when asked.
printf '%s\n' "$*" >> "$DOCKER_CALL_LOG"
for arg in "$@"; do
  if [[ "$arg" == "services.orchestrator.picker_readiness" ]]; then
    if [[ "${STUB_READINESS_FAIL:-0}" == "1" ]]; then
      echo '{"ok": false, "pools": {"video": {"mapped_assets": 0}}}'
      exit 1
    fi
    echo '{"ok": true, "pools": {"video": {"mapped_assets": 2400}}}'
    exit 0
  fi
done
exit 0
"""


@pytest.fixture()
def gate_env(tmp_path):
    """Stub docker on PATH + a call log, and the env the lib reads."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "docker"
    # newline="" keeps LF endings — a CRLF shebang makes execve look for "bash\r"
    # and the stub would silently never run.
    stub.write_text(DOCKER_STUB, encoding="utf-8", newline="")
    stub.chmod(0o755)

    log = tmp_path / "docker_calls.log"
    log.touch()

    env = dict(os.environ)
    env["PATH"] = f"{bindir}{os.pathsep}" + env["PATH"]
    env["DOCKER_CALL_LOG"] = log.as_posix()
    return env, log


def _bash(script: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_BASH, "-c", script], cwd=str(_ROOT), env=env, capture_output=True, text=True
    )


def _calls(log: Path) -> list[str]:
    return [l.strip() for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]


_SOURCE = f'. "{_LIB.as_posix()}"'


def _run_rollout(gate_env, *, readiness_fail: bool):
    env, log = gate_env
    env = {**env, "STUB_READINESS_FAIL": "1" if readiness_fail else "0",
           "PROD_PATH_USE_PREBUILT": "true"}
    script = f"""
set -uo pipefail
{_SOURCE}
PROD_PATH_COMPOSE_ARGS=()
PROD_PATH_SERVICES=(orchestrator-api worker-build worker-render worker-render-poll)
prod_path_rollout
echo "ROLLOUT_RC=$?"
"""
    return _bash(script, env), _calls(log)


def test_readiness_fail_blocks_queue_and_traffic_switch(gate_env):
    proc, calls = _run_rollout(gate_env, readiness_fail=True)

    # The rollout reports failure to the caller => red workflow.
    assert "ROLLOUT_RC=1" in proc.stdout, (proc.stdout, proc.stderr)

    # The image was staged ...
    assert any(c.startswith("compose pull") for c in calls), calls
    # ... the candidate ran exactly once ...
    assert len([c for c in calls if "picker_readiness" in c]) == 1, calls
    # ... and NOTHING was ever started. This is the whole point.
    assert not any(c.startswith("compose up") for c in calls), calls

    assert "refusing to attach queues or switch traffic" in proc.stdout, proc.stdout


def test_readiness_candidate_never_attaches_a_queue(gate_env):
    """The candidate must override the celery command and start no dependencies —
    otherwise the 'check' would itself consume a user job."""
    _, calls = _run_rollout(gate_env, readiness_fail=True)
    candidate = next(c for c in calls if "picker_readiness" in c)

    assert "--no-deps" in candidate, candidate
    assert "--entrypoint python" in candidate, candidate
    assert "--rm" in candidate, candidate
    assert "celery" not in candidate, candidate


def test_readiness_pass_switches_queues_after_the_gate(gate_env):
    proc, calls = _run_rollout(gate_env, readiness_fail=False)

    assert "ROLLOUT_RC=0" in proc.stdout, (proc.stdout, proc.stderr)
    up_calls = [c for c in calls if c.startswith("compose up")]
    assert len(up_calls) == 1, calls
    assert "up -d --no-build" in up_calls[0], up_calls

    # Ordering IS the safety property: stage -> prove -> switch.
    pull_i = next(i for i, c in enumerate(calls) if c.startswith("compose pull"))
    gate_i = next(i for i, c in enumerate(calls) if "picker_readiness" in c)
    up_i = next(i for i, c in enumerate(calls) if c.startswith("compose up"))
    assert pull_i < gate_i < up_i, calls


def test_gate_is_fail_closed_by_default(gate_env):
    env, log = gate_env
    env = {**env, "STUB_READINESS_FAIL": "1"}

    proc = _bash(f'set -uo pipefail\n{_SOURCE}\npicker_readiness_gate\necho "GATE_RC=$?"', env)

    assert "GATE_RC=1" in proc.stdout, (proc.stdout, proc.stderr)
    assert any("picker_readiness" in c for c in _calls(log))


def test_gate_opt_out_is_explicit_and_skips_the_candidate(gate_env):
    env, log = gate_env
    env = {**env, "STUB_READINESS_FAIL": "1", "DEPLOY_PICKER_READINESS_ENABLED": "false"}

    proc = _bash(f'set -uo pipefail\n{_SOURCE}\npicker_readiness_gate\necho "GATE_RC=$?"', env)

    assert "GATE_RC=0" in proc.stdout, (proc.stdout, proc.stderr)
    assert "DISABLED" in proc.stdout
    assert not any("picker_readiness" in c for c in _calls(log)), _calls(log)
