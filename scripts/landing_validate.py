#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Iterable

HTML_REF_RE = re.compile(r"(?:src|href)\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)
CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)([^'\")]+)\1\s*\)", re.IGNORECASE)

EXTERNAL_PREFIXES = (
    "http://",
    "https://",
    "//",
    "mailto:",
    "tel:",
    "data:",
    "javascript:",
)


def _strip_ref(raw: str) -> str:
    return raw.split("#", 1)[0].split("?", 1)[0].strip()


def _is_local_ref(raw: str) -> bool:
    value = raw.strip()
    if not value or value.startswith("#"):
        return False
    return not value.lower().startswith(EXTERNAL_PREFIXES)


def _extract_html_refs(content: str) -> list[str]:
    return [m.group(1).strip() for m in HTML_REF_RE.finditer(content)]


def _extract_css_refs(content: str) -> list[str]:
    refs: list[str] = []
    for match in CSS_URL_RE.finditer(content):
        refs.append(match.group(2).strip())
    return refs


def _resolve_local_ref(root: Path, source_file: Path, ref: str) -> Path:
    clean = _strip_ref(ref)
    if clean in {"", ".", "/"}:
        candidate = root / "index.html"
    elif clean.startswith("/"):
        candidate = root / clean.lstrip("/")
    else:
        candidate = source_file.parent / clean

    normalized = candidate.resolve()
    root_resolved = root.resolve()
    if root_resolved not in normalized.parents and normalized != root_resolved:
        raise RuntimeError(f"path escapes landing root: {ref}")

    # Allow directory ref to resolve to index.html.
    if normalized.is_dir():
        normalized = normalized / "index.html"

    return normalized


def _iter_source_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in {".html", ".css"}:
            yield path


def validate_landing(root: Path, *, allow_local_rar: bool = False) -> list[str]:
    errors: list[str] = []
    if not root.exists() or not root.is_dir():
        return [f"landing root is missing: {root}"]

    index_file = root / "index.html"
    if not index_file.exists():
        errors.append(f"missing required file: {index_file}")

    rar_files = sorted(str(p.relative_to(root)) for p in root.rglob("*.rar"))
    if rar_files and not allow_local_rar:
        errors.append("RAR archives are not allowed in deploy tree: " + ", ".join(rar_files))

    for source_file in _iter_source_files(root):
        try:
            content = source_file.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            errors.append(f"{source_file.relative_to(root)}: not utf-8 text ({exc})")
            continue

        refs = _extract_html_refs(content) if source_file.suffix.lower() == ".html" else _extract_css_refs(content)
        for ref in refs:
            if not _is_local_ref(ref):
                continue
            try:
                target = _resolve_local_ref(root=root, source_file=source_file, ref=ref)
            except RuntimeError as exc:
                errors.append(f"{source_file.relative_to(root)} -> {ref}: {exc}")
                continue
            if not target.exists() or not target.is_file():
                errors.append(
                    f"{source_file.relative_to(root)} -> {ref}: target missing ({target.relative_to(root) if target.exists() else target})"
                )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate landing static files before S3 deploy")
    parser.add_argument("--root", default="landing", help="Landing root directory (default: landing)")
    parser.add_argument(
        "--allow-local-rar",
        action="store_true",
        help="Allow local *.rar files (for local import workflows). CI should not use this flag.",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    errors = validate_landing(root, allow_local_rar=bool(args.allow_local_rar))

    if errors:
        print(f"[landing-validate] FAILED ({len(errors)} issues)")
        for item in errors:
            print(f" - {item}")
        return 1

    print(f"[landing-validate] OK root={root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
