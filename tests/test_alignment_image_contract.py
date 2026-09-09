from __future__ import annotations

import json
from pathlib import Path

from mlcore.alignment.contracts import ALIGNMENT_ALGORITHM_VERSION


ROOT = Path(__file__).resolve().parents[1]


def test_alignment_image_pins_offline_espeak_and_smoke_checks_ipa() -> None:
    dockerfile = (ROOT / "Dockerfile.alignment").read_text(encoding="utf-8")

    assert "FROM python:3.11-slim-bookworm" in dockerfile
    assert (
        "ARG ALIGNMENT_ESPEAK_PACKAGE_VERSION=1.51+dfsg-10+deb12u2"
        in dockerfile
    )
    assert "espeak-ng=${ALIGNMENT_ESPEAK_PACKAGE_VERSION}" in dockerfile
    assert 'RUN test -n "$(espeak-ng -q --ipa=3 -b 1 -v en-us pretty)"' in dockerfile
    assert "ALIGNMENT_PRONUNCIATION_MODE=espeak_en_to_ru" in dockerfile
    assert "ALIGNMENT_ESPEAK_EXPECTED_VERSION=1.51" in dockerfile


def test_runtime_compose_alignment_identity_matches_python_contract() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert (
        f"ALIGNMENT_ALGORITHM_VERSION: {ALIGNMENT_ALGORITHM_VERSION}"
        in compose
    )


def test_pronunciation_overrides_are_versioned_cyrillic_words() -> None:
    payload = json.loads(
        (ROOT / "config" / "alignment_pronunciations.json").read_text(
            encoding="utf-8"
        )
    )

    assert payload["schema_version"] == 1
    assert payload["english_to_russian"]["alyx"] == "аликс"
    assert payload["english_to_russian"]["iphone"] == "айфон"
    assert payload["english_to_russian"]["samson"] == "самсон"

