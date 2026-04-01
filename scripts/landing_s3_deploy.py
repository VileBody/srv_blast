#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import posixpath
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config

HTML_REF_RE = re.compile(r"(?:src|href)\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)
EXTERNAL_PREFIXES = (
    "http://",
    "https://",
    "//",
    "mailto:",
    "tel:",
    "data:",
    "javascript:",
)


@dataclass(frozen=True)
class LocalFile:
    rel_key: str
    path: Path
    size: int
    md5: str
    content_type: str
    cache_control: str


@dataclass(frozen=True)
class RemoteObject:
    rel_key: str
    key: str
    size: int
    etag: str


@dataclass
class SyncPlan:
    creates: list[LocalFile]
    updates: list[LocalFile]
    deletes: list[RemoteObject]
    unchanged_count: int


@dataclass
class PromotePlan:
    copies_create: list[tuple[RemoteObject, str]]
    copies_update: list[tuple[RemoteObject, str]]
    deletes: list[RemoteObject]
    unchanged_count: int


def _require_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"Missing required env: {name}")
    return value


def _clean_prefix(value: str) -> str:
    cleaned = str(value or "").strip().strip("/")
    if not cleaned:
        raise RuntimeError("S3 prefix must be non-empty")
    return cleaned


def _join_key(prefix: str, rel_key: str) -> str:
    return f"{_clean_prefix(prefix)}/{rel_key.lstrip('/')}"


def _slug_branch(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    if not slug:
        raise RuntimeError(f"Invalid branch name for preview path: {value!r}")
    return slug


def _normalize_sha(value: str, *, min_len: int = 7, max_len: int = 40) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{%d,%d}" % (min_len, max_len), text):
        raise RuntimeError(f"Invalid git SHA value: {value!r}")
    return text.lower()


def _md5_file(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _content_type_for(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return str(guessed or "application/octet-stream")


def _cache_control_for(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".html":
        return "no-cache"
    if ext in {".css", ".js", ".mjs"}:
        return "public, max-age=2592000"
    if ext in {
        ".png",
        ".jpg",
        ".jpeg",
        ".svg",
        ".webp",
        ".avif",
        ".gif",
        ".ico",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".eot",
    }:
        return "public, max-age=31536000, immutable"
    return "public, max-age=3600"


def _list_remote_objects(s3: Any, *, bucket: str, prefix: str) -> dict[str, RemoteObject]:
    base = _clean_prefix(prefix)
    list_prefix = f"{base}/"

    token: str | None = None
    out: dict[str, RemoteObject] = {}
    while True:
        kwargs: dict[str, Any] = {
            "Bucket": bucket,
            "Prefix": list_prefix,
            "MaxKeys": 1000,
        }
        if token:
            kwargs["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kwargs)

        for item in resp.get("Contents") or []:
            key = str(item.get("Key") or "").strip()
            if not key or key.endswith("/"):
                continue
            if not key.startswith(list_prefix):
                continue
            rel = key[len(list_prefix) :]
            if not rel:
                continue
            out[rel] = RemoteObject(
                rel_key=rel,
                key=key,
                size=int(item.get("Size") or 0),
                etag=str(item.get("ETag") or "").strip('"'),
            )

        if not resp.get("IsTruncated"):
            break
        token = str(resp.get("NextContinuationToken") or "").strip() or None
        if token is None:
            break

    return out


def _build_local_files(root: Path) -> dict[str, LocalFile]:
    if not root.exists() or not root.is_dir():
        raise RuntimeError(f"Landing source dir is missing: {root}")

    out: dict[str, LocalFile] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if path.suffix.lower() == ".rar":
            continue
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        out[rel] = LocalFile(
            rel_key=rel,
            path=path,
            size=int(path.stat().st_size),
            md5=_md5_file(path),
            content_type=_content_type_for(path),
            cache_control=_cache_control_for(path),
        )

    if "index.html" not in out:
        raise RuntimeError(f"Landing source dir must include index.html: {root}")

    return out


def _needs_upload(local: LocalFile, remote: RemoteObject) -> bool:
    if local.size != remote.size:
        return True
    etag = str(remote.etag or "").strip().lower()
    if not etag or "-" in etag:
        return True
    return local.md5.lower() != etag


def _build_sync_plan(local_map: dict[str, LocalFile], remote_map: dict[str, RemoteObject]) -> SyncPlan:
    creates: list[LocalFile] = []
    updates: list[LocalFile] = []
    deletes: list[RemoteObject] = []
    unchanged_count = 0

    for rel in sorted(local_map):
        local = local_map[rel]
        remote = remote_map.get(rel)
        if remote is None:
            creates.append(local)
            continue
        if _needs_upload(local, remote):
            updates.append(local)
        else:
            unchanged_count += 1

    for rel in sorted(remote_map):
        if rel not in local_map:
            deletes.append(remote_map[rel])

    return SyncPlan(
        creates=creates,
        updates=updates,
        deletes=deletes,
        unchanged_count=unchanged_count,
    )


def _print_sync_plan(plan: SyncPlan, *, target_prefix: str) -> None:
    print(
        "[landing-s3] plan target=%s create=%d update=%d delete=%d unchanged=%d"
        % (target_prefix, len(plan.creates), len(plan.updates), len(plan.deletes), plan.unchanged_count)
    )
    for item in plan.creates:
        print(f"  + create {item.rel_key}")
    for item in plan.updates:
        print(f"  ~ update {item.rel_key}")
    for item in plan.deletes:
        print(f"  - delete {item.rel_key}")


def _apply_sync(
    s3: Any,
    *,
    bucket: str,
    target_prefix: str,
    plan: SyncPlan,
) -> None:
    uploaded = 0
    for item in [*plan.creates, *plan.updates]:
        key = _join_key(target_prefix, item.rel_key)
        extra_args = {
            "ContentType": item.content_type,
            "CacheControl": item.cache_control,
        }
        s3.upload_file(str(item.path), bucket, key, ExtraArgs=extra_args)
        uploaded += 1
        print(f"[landing-s3] uploaded {uploaded}/{len(plan.creates) + len(plan.updates)} {item.rel_key}")

    deleted = 0
    for item in plan.deletes:
        s3.delete_object(Bucket=bucket, Key=item.key)
        deleted += 1
        print(f"[landing-s3] deleted {deleted}/{len(plan.deletes)} {item.rel_key}")


def _build_promote_plan(
    *,
    source_map: dict[str, RemoteObject],
    live_map: dict[str, RemoteObject],
) -> PromotePlan:
    copies_create: list[tuple[RemoteObject, str]] = []
    copies_update: list[tuple[RemoteObject, str]] = []
    deletes: list[RemoteObject] = []
    unchanged_count = 0

    for rel in sorted(source_map):
        src = source_map[rel]
        live = live_map.get(rel)
        if live is None:
            copies_create.append((src, rel))
            continue
        if live.size != src.size or (live.etag or "").lower() != (src.etag or "").lower():
            copies_update.append((src, rel))
        else:
            unchanged_count += 1

    for rel in sorted(live_map):
        if rel not in source_map:
            deletes.append(live_map[rel])

    return PromotePlan(
        copies_create=copies_create,
        copies_update=copies_update,
        deletes=deletes,
        unchanged_count=unchanged_count,
    )


def _print_promote_plan(plan: PromotePlan, *, source_prefix: str, live_prefix: str) -> None:
    print(
        "[landing-s3] promote-plan source=%s live=%s create=%d update=%d delete=%d unchanged=%d"
        % (
            source_prefix,
            live_prefix,
            len(plan.copies_create),
            len(plan.copies_update),
            len(plan.deletes),
            plan.unchanged_count,
        )
    )
    for _, rel in plan.copies_create:
        print(f"  + copy {rel}")
    for _, rel in plan.copies_update:
        print(f"  ~ copy(update) {rel}")
    for item in plan.deletes:
        print(f"  - delete {item.rel_key}")


def _apply_promote(
    s3: Any,
    *,
    bucket: str,
    live_prefix: str,
    plan: PromotePlan,
) -> None:
    total_copy = len(plan.copies_create) + len(plan.copies_update)
    copied = 0
    for src, rel in [*plan.copies_create, *plan.copies_update]:
        dst_key = _join_key(live_prefix, rel)
        s3.copy_object(
            Bucket=bucket,
            Key=dst_key,
            CopySource={"Bucket": bucket, "Key": src.key},
            MetadataDirective="COPY",
        )
        copied += 1
        print(f"[landing-s3] copied {copied}/{total_copy} {rel}")

    deleted = 0
    for item in plan.deletes:
        s3.delete_object(Bucket=bucket, Key=item.key)
        deleted += 1
        print(f"[landing-s3] deleted-live {deleted}/{len(plan.deletes)} {item.rel_key}")


def _extract_local_refs_from_html(html_text: str) -> list[str]:
    refs: list[str] = []
    for match in HTML_REF_RE.finditer(html_text):
        ref = match.group(1).strip()
        if not ref or ref.startswith("#"):
            continue
        if ref.lower().startswith(EXTERNAL_PREFIXES):
            continue
        clean = ref.split("#", 1)[0].split("?", 1)[0].strip()
        if clean:
            refs.append(clean)
    return refs


def _ref_to_key(*, base_key: str, target_prefix: str, ref: str) -> str:
    clean = ref.strip()
    if clean.startswith("/"):
        key = _join_key(target_prefix, clean.lstrip("/"))
    else:
        base_dir = posixpath.dirname(base_key)
        key = posixpath.normpath(posixpath.join(base_dir, clean))
    prefix = _clean_prefix(target_prefix) + "/"
    if not key.startswith(prefix):
        raise RuntimeError(f"Resolved key escapes target prefix: {ref} -> {key}")
    return key


def _fetch_via_presigned_http(s3: Any, *, bucket: str, key: str) -> bytes:
    url = s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=300,
    )
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = int(getattr(resp, "status", 200))
            body = resp.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP GET failed for {key}: {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"HTTP GET failed for {key}: {exc}") from exc

    if status != 200:
        raise RuntimeError(f"HTTP GET returned status={status} for {key}")
    return body


def _smoke_check_target(s3: Any, *, bucket: str, target_prefix: str) -> list[str]:
    index_key = _join_key(target_prefix, "index.html")
    index_body = _fetch_via_presigned_http(s3, bucket=bucket, key=index_key)

    html_text = index_body.decode("utf-8", errors="ignore")
    refs = _extract_local_refs_from_html(html_text)
    checked: list[str] = [index_key]

    for ref in refs:
        try:
            key = _ref_to_key(base_key=index_key, target_prefix=target_prefix, ref=ref)
        except RuntimeError:
            continue
        if key in checked or key.endswith(".rar"):
            continue
        _fetch_via_presigned_http(s3, bucket=bucket, key=key)
        checked.append(key)
        if len(checked) >= 3:
            break

    if len(checked) < 3:
        raise RuntimeError(
            "Smoke check requires index.html + at least 2 asset GETs, got %d" % len(checked)
        )

    print("[landing-s3] smoke-ok keys=%s" % json.dumps(checked, ensure_ascii=False))
    return checked


def _make_s3_client() -> Any:
    endpoint = _require_env("S3_ENDPOINT_URL")
    access_key = _require_env("S3_ACCESS_KEY_ID")
    secret_key = _require_env("S3_SECRET_ACCESS_KEY")
    region = _require_env("S3_REGION")

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        config=Config(signature_version="s3v4", retries={"max_attempts": 8, "mode": "standard"}),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy landing static files to S3")
    parser.add_argument("--mode", required=True, choices=["preview", "release", "promote", "rollback"])
    parser.add_argument("--source-dir", default="landing", help="Local landing dir for preview/release modes")
    parser.add_argument("--bucket", required=True, help="S3 target bucket")
    parser.add_argument("--base-prefix", required=True, help="Base prefix, e.g. landing/blast808")
    parser.add_argument("--branch", default="", help="Branch name for preview mode")
    parser.add_argument("--sha", default="", help="Commit SHA for preview/release mode")
    parser.add_argument("--release-sha", default="", help="Release SHA for promote/rollback mode")
    parser.add_argument("--dry-run", action="store_true", help="Show plan only")
    parser.add_argument("--smoke", action="store_true", help="Run presigned HTTP smoke checks")
    args = parser.parse_args()

    bucket = str(args.bucket or "").strip()
    if not bucket:
        raise RuntimeError("Bucket must be non-empty")

    base_prefix = _clean_prefix(args.base_prefix)
    s3 = _make_s3_client()

    summary: dict[str, Any] = {
        "mode": args.mode,
        "bucket": bucket,
        "base_prefix": base_prefix,
        "dry_run": bool(args.dry_run),
        "smoke": bool(args.smoke),
    }

    if args.mode == "preview":
        branch = _slug_branch(args.branch)
        sha = _normalize_sha(args.sha)
        short_sha = sha[:8]
        target_prefix = f"{base_prefix}/previews/{branch}-{short_sha}"
        local_map = _build_local_files(Path(args.source_dir).resolve())
        remote_map = _list_remote_objects(s3, bucket=bucket, prefix=target_prefix)
        plan = _build_sync_plan(local_map=local_map, remote_map=remote_map)
        _print_sync_plan(plan, target_prefix=target_prefix)
        if not args.dry_run:
            _apply_sync(s3, bucket=bucket, target_prefix=target_prefix, plan=plan)
            if args.smoke:
                _smoke_check_target(s3, bucket=bucket, target_prefix=target_prefix)

        summary.update(
            {
                "target_prefix": target_prefix,
                "create": len(plan.creates),
                "update": len(plan.updates),
                "delete": len(plan.deletes),
                "unchanged": plan.unchanged_count,
            }
        )

    elif args.mode == "release":
        sha = _normalize_sha(args.sha)
        target_prefix = f"{base_prefix}/releases/{sha}"
        local_map = _build_local_files(Path(args.source_dir).resolve())
        remote_map = _list_remote_objects(s3, bucket=bucket, prefix=target_prefix)
        plan = _build_sync_plan(local_map=local_map, remote_map=remote_map)
        _print_sync_plan(plan, target_prefix=target_prefix)
        if not args.dry_run:
            _apply_sync(s3, bucket=bucket, target_prefix=target_prefix, plan=plan)
            if args.smoke:
                _smoke_check_target(s3, bucket=bucket, target_prefix=target_prefix)

        summary.update(
            {
                "target_prefix": target_prefix,
                "create": len(plan.creates),
                "update": len(plan.updates),
                "delete": len(plan.deletes),
                "unchanged": plan.unchanged_count,
            }
        )

    else:
        release_sha = _normalize_sha(args.release_sha)
        source_prefix = f"{base_prefix}/releases/{release_sha}"
        live_prefix = f"{base_prefix}/live"
        source_map = _list_remote_objects(s3, bucket=bucket, prefix=source_prefix)
        if not source_map:
            raise RuntimeError(f"Release prefix is empty or missing: s3://{bucket}/{source_prefix}")
        live_map = _list_remote_objects(s3, bucket=bucket, prefix=live_prefix)
        plan = _build_promote_plan(source_map=source_map, live_map=live_map)
        _print_promote_plan(plan, source_prefix=source_prefix, live_prefix=live_prefix)
        if not args.dry_run:
            _apply_promote(
                s3,
                bucket=bucket,
                live_prefix=live_prefix,
                plan=plan,
            )
            if args.smoke:
                _smoke_check_target(s3, bucket=bucket, target_prefix=live_prefix)

        summary.update(
            {
                "source_prefix": source_prefix,
                "live_prefix": live_prefix,
                "copy_create": len(plan.copies_create),
                "copy_update": len(plan.copies_update),
                "delete": len(plan.deletes),
                "unchanged": plan.unchanged_count,
            }
        )

    print("[landing-s3] summary=" + json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
