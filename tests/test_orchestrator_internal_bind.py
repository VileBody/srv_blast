"""Оркестратор без аутентификации (/jobs/{id}/edit_state, /asr/preview/from-job и др.)
защищён только сетью: порт публикуется на 127.0.0.1, сайт ходит по docker-сети.
Тест держит этот инвариант — смена дефолта на 0.0.0.0 открыла бы ручки наружу."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _ports(compose: str, service: str) -> list[str]:
    block = re.search(rf"^  {re.escape(service)}:\n(.*?)(?=^  \S|^\S|\Z)", compose, re.S | re.M)
    assert block, f"service {service} not found"
    ports = re.search(r"^    ports:\n((?:      - .*\n)+)", block.group(1), re.M)
    assert ports, f"service {service} has no ports"
    return [line.split("- ", 1)[1].strip().strip('"') for line in ports.group(1).splitlines()]


def test_orchestrator_api_port_defaults_to_loopback() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert _ports(compose, "orchestrator-api") == ["${ORCHESTRATOR_API_BIND_HOST:-127.0.0.1}:18000:8000"]
    ha = (ROOT / "docker-compose.orchestrator-ha.yml").read_text(encoding="utf-8")
    assert _ports(ha, "orchestrator-api-2") == ["127.0.0.1:18001:8000"]


def test_env_example_keeps_the_loopback_bind() -> None:
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^ORCHESTRATOR_API_BIND_HOST=127\.0\.0\.1$", env, re.M)


def test_unauthenticated_edit_endpoints_document_the_network_guard() -> None:
    src = (ROOT / "services" / "orchestrator" / "app.py").read_text(encoding="utf-8")
    guard = src.index("tests/test_orchestrator_internal_bind.py")
    assert guard < src.index('"/asr/preview/from-job"') < src.index('"/jobs/{job_id}/edit_state"')
