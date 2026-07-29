#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


# Team files that must be mirrored into public bot when changed.
MIRROR_RULES: dict[str, tuple[str, ...]] = {
    "services/tg_bot_botapi/app.py": ("services/tg_bot_public/app.py",),
    "services/tg_bot_botapi/state_store.py": ("services/tg_bot_public/state_store.py",),
    "services/tg_bot_botapi/orchestrator_client.py": ("services/tg_bot_public/orchestrator_client.py",),
    "services/tg_bot_botapi/audio_prepare.py": ("services/tg_bot_public/audio_prepare.py",),
    "services/tg_bot_botapi/s3_client.py": ("services/tg_bot_public/s3_client.py",),
}

PUBLIC_TEST_PREFIXES: tuple[str, ...] = (
    "tests/test_tg_bot_public_",
    "tests/test_tg_public_",
)

TEAM_ONLY_EXCEPTIONS_PATH = ".ci/team_public_parity_exceptions.json"


def _git(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed with code {proc.returncode}: {proc.stderr.strip()}"
        )
    return proc.stdout


def _changed_files(repo_root: Path, *, base_ref: str, head_ref: str) -> list[str]:
    merge_base = _git(repo_root, "merge-base", base_ref, head_ref).strip()
    if not merge_base:
        raise RuntimeError(f"Unable to resolve merge-base for {base_ref}..{head_ref}")
    out = _git(repo_root, "diff", "--name-only", f"{merge_base}..{head_ref}")
    return [line.strip() for line in out.splitlines() if line.strip()]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate tg_bot_botapi -> tg_bot_public mirror contract."
    )
    parser.add_argument("--base-ref", required=True, help="Git base ref/sha")
    parser.add_argument("--head-ref", required=True, help="Git head ref/sha")
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root path (default: current directory)",
    )
    return parser.parse_args()


def _team_only_exceptions(
    repo_root: Path,
    *,
    changed_set: set[str],
) -> tuple[set[str], list[str]]:
    if TEAM_ONLY_EXCEPTIONS_PATH not in changed_set:
        return set(), []

    path = repo_root / TEAM_ONLY_EXCEPTIONS_PATH
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid {TEAM_ONLY_EXCEPTIONS_PATH}: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("exceptions"), list):
        raise RuntimeError(
            f"{TEAM_ONLY_EXCEPTIONS_PATH} must contain an exceptions list"
        )

    waived: set[str] = set()
    labels: list[str] = []
    for index, item in enumerate(raw["exceptions"]):
        if not isinstance(item, dict):
            raise RuntimeError(f"Exception #{index + 1} must be an object")
        name = str(item.get("name") or "").strip()
        reason = str(item.get("reason") or "").strip()
        team_files = item.get("team_files")
        required_tests = item.get("required_tests")
        if not name or not reason:
            raise RuntimeError(f"Exception #{index + 1} requires name and reason")
        if not isinstance(team_files, list) or not team_files:
            raise RuntimeError(f"Exception {name!r} requires non-empty team_files")
        if not isinstance(required_tests, list) or not required_tests:
            raise RuntimeError(
                f"Exception {name!r} requires non-empty required_tests"
            )

        normalized_team_files = {str(value).strip() for value in team_files}
        unknown = normalized_team_files.difference(MIRROR_RULES)
        if unknown:
            raise RuntimeError(
                f"Exception {name!r} references non-mirrored team files: "
                + ", ".join(sorted(unknown))
            )
        missing_tests = {
            str(value).strip()
            for value in required_tests
            if str(value).strip() not in changed_set
        }
        if missing_tests:
            raise RuntimeError(
                f"Exception {name!r} is missing changed evidence tests: "
                + ", ".join(sorted(missing_tests))
            )

        waived.update(normalized_team_files.intersection(changed_set))
        labels.append(name)
    return waived, labels


def main() -> int:
    args = _parse_args()
    repo_root = Path(args.repo_root).resolve()

    changed = _changed_files(repo_root, base_ref=args.base_ref, head_ref=args.head_ref)
    changed_set = set(changed)

    mirrored_team_changes = [path for path in changed if path in MIRROR_RULES]
    if not mirrored_team_changes:
        print("parity-check: no mirrored team files changed; nothing to validate.")
        return 0

    waived_team_changes, waiver_labels = _team_only_exceptions(
        repo_root,
        changed_set=changed_set,
    )
    mirrored_team_changes = [
        path for path in mirrored_team_changes if path not in waived_team_changes
    ]
    if waived_team_changes:
        print("parity-check: explicit Team-only exceptions:")
        for label in waiver_labels:
            print(f"  - {label}")
        for path in sorted(waived_team_changes):
            print(f"    {path}")
    if not mirrored_team_changes:
        print("parity-check: PASS")
        return 0

    missing_mirrors: list[tuple[str, tuple[str, ...]]] = []
    for team_path in mirrored_team_changes:
        required_public_paths = MIRROR_RULES[team_path]
        if not any(pub_path in changed_set for pub_path in required_public_paths):
            missing_mirrors.append((team_path, required_public_paths))

    public_tests_changed = any(
        path.startswith(PUBLIC_TEST_PREFIXES) for path in changed
    )

    if not missing_mirrors and public_tests_changed:
        print("parity-check: PASS")
        print("mirrored team files:")
        for path in mirrored_team_changes:
            print(f"  - {path}")
        return 0

    print("parity-check: FAIL")
    print("Detected team changes that require explicit public mirror updates.")
    print("Changed mirrored team files:")
    for path in mirrored_team_changes:
        print(f"  - {path}")

    if missing_mirrors:
        print("Missing required public mirrors:")
        for team_path, public_paths in missing_mirrors:
            req = ", ".join(public_paths)
            print(f"  - {team_path} -> expected one of: {req}")

    if not public_tests_changed:
        print("Missing public regression tests update.")
        print("Expected at least one changed test under:")
        for prefix in PUBLIC_TEST_PREFIXES:
            print(f"  - {prefix}*")

    print("Action required: port logic to tg_bot_public and add/adjust public tests in the same PR.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
