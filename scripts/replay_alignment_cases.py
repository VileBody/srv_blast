#!/usr/bin/env python3
"""Replay recorded alignment cases against a running alignment service.

Every production window failure worth remembering goes into
``data/alignment_cases.jsonl`` with the verdict it should produce. Replaying the
file after a change to the window search answers the only question that matters
before a deploy: did this fix the case it was written for, and did it break the
cases that already worked?

Usage:

    python scripts/replay_alignment_cases.py \
        --service-url http://localhost:18100 \
        --audio-root /app/work/jobs

Case schema (one JSON object per line):

    case_id            stable name, printed in the report
    audio_path         path readable by the alignment service, OR
    audio_s3           s3:// URL to fetch into --audio-root first (manual)
    target_fragment    the reference text the user sent
    clip_start_abs     window start in seconds, absolute in the track
    clip_end_abs       window end in seconds
    expect             "success" | "failure"
    expect_error_code  required when expect == "failure"
    max_boundary_overflow_sec  optional ceiling for a tolerated overflow
    notes              free text

Exit code is 0 only when every case matches its recorded verdict.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mlcore.alignment.client import (  # noqa: E402
    AlignmentServiceError,
    request_local_alignment,
)
from mlcore.alignment.contracts import (  # noqa: E402
    ALIGNMENT_ALGORITHM_VERSION,
)

DEFAULT_CASES_PATH = REPO_ROOT / "data" / "alignment_cases.jsonl"


class CaseError(RuntimeError):
    """A case that cannot be replayed at all, as opposed to one that failed."""


def load_cases(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise CaseError(f"case file not found: {path}")
    cases: list[dict[str, Any]] = []
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CaseError(f"{path}:{line_number} is not valid JSON: {exc}") from exc
        if not isinstance(case, dict):
            raise CaseError(f"{path}:{line_number} must be a JSON object")
        case.setdefault("case_id", f"line-{line_number}")
        cases.append(case)
    return cases


def _resolve_audio(case: dict[str, Any], *, audio_root: Path) -> Path:
    audio_path = str(case.get("audio_path") or "").strip()
    if audio_path:
        return Path(audio_path)
    audio_s3 = str(case.get("audio_s3") or "").strip()
    if audio_s3:
        raise CaseError(
            f"case {case['case_id']} only has audio_s3={audio_s3!r}; download it "
            f"into {audio_root} and set audio_path"
        )
    raise CaseError(f"case {case['case_id']} has neither audio_path nor audio_s3")


def _check_success(case: dict[str, Any], response: Any) -> str:
    expect = str(case.get("expect") or "success")
    if expect != "success":
        return (
            f"expected {expect} "
            f"({case.get('expect_error_code') or 'any error'}) but alignment "
            "succeeded"
        )
    dynamic_window = dict(response.diagnostics.get("dynamic_window") or {})
    ceiling = case.get("max_boundary_overflow_sec")
    if ceiling is not None:
        overflow = max(
            float(dynamic_window.get("selected_left_window_overflow_sec") or 0.0),
            float(dynamic_window.get("selected_right_window_overflow_sec") or 0.0),
        )
        if overflow > float(ceiling) + 1e-6:
            return (
                f"boundary overflow {overflow:.3f}s exceeds the recorded "
                f"ceiling {float(ceiling):.3f}s"
            )
    return ""


def _check_failure(case: dict[str, Any], exc: AlignmentServiceError) -> str:
    expect = str(case.get("expect") or "success")
    if expect != "failure":
        return f"expected success but alignment failed with {exc.code}: {exc.message}"
    expected_code = str(case.get("expect_error_code") or "").strip()
    if expected_code and exc.code != expected_code:
        return f"expected {expected_code} but got {exc.code}: {exc.message}"
    return ""


def replay(
    cases: list[dict[str, Any]],
    *,
    service_url: str,
    audio_root: Path,
    timeout_s: float,
    only: set[str],
) -> int:
    failures = 0
    skipped = 0
    for case in cases:
        case_id = str(case["case_id"])
        if only and case_id not in only:
            continue
        try:
            audio_path = _resolve_audio(case, audio_root=audio_root)
            fragment = str(case.get("target_fragment") or "").strip()
            if not fragment:
                raise CaseError(f"case {case_id} has an empty target_fragment")
        except CaseError as exc:
            print(f"SKIP {case_id}: {exc}")
            skipped += 1
            continue

        try:
            response = request_local_alignment(
                service_url=service_url,
                timeout_s=timeout_s,
                audio_path=audio_path,
                target_fragment=fragment,
                clip_start_abs=float(case.get("clip_start_abs") or 0.0),
                clip_end_abs=float(case.get("clip_end_abs") or 0.0),
                request_id=f"replay-{case_id}",
            )
        except AlignmentServiceError as exc:
            problem = _check_failure(case, exc)
        else:
            problem = _check_success(case, response)

        if problem:
            failures += 1
            print(f"FAIL {case_id}: {problem}")
        else:
            print(f"PASS {case_id}")

    total = len([case for case in cases if not only or case["case_id"] in only])
    print(
        f"\nalgorithm={ALIGNMENT_ALGORITHM_VERSION}\n"
        f"cases={total} passed={total - failures - skipped} "
        f"failed={failures} skipped={skipped}"
    )
    if skipped and not failures:
        print("note: skipped cases are not evidence of a working algorithm")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service-url", required=True)
    parser.add_argument("--cases", default=str(DEFAULT_CASES_PATH))
    parser.add_argument("--audio-root", default="/app/work/jobs")
    parser.add_argument("--timeout-s", type=float, default=600.0)
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        help="replay a single case_id (repeatable)",
    )
    args = parser.parse_args()

    try:
        cases = load_cases(Path(args.cases))
    except CaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not cases:
        print(f"error: no cases in {args.cases}", file=sys.stderr)
        return 2

    return replay(
        cases,
        service_url=str(args.service_url),
        audio_root=Path(args.audio_root),
        timeout_s=float(args.timeout_s),
        only=set(args.only or []),
    )


if __name__ == "__main__":
    sys.exit(main())
