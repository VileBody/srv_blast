#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ssl
import urllib.error
import urllib.request


def _fetch_text(url: str, timeout_s: float) -> tuple[int, str, str]:
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "User-Agent": "landing-public-check/1.0",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout_s, context=ctx) as resp:
        status = int(getattr(resp, "status", 200))
        final_url = str(getattr(resp, "url", url))
        body = resp.read().decode("utf-8", errors="ignore")
    return status, body, final_url


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-check public landing domains")
    parser.add_argument(
        "--url",
        action="append",
        default=[],
        help="Public URL to verify (repeatable)",
    )
    parser.add_argument(
        "--contains",
        action="append",
        default=[],
        help="Substring that must be present in response body (repeatable)",
    )
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=20.0,
        help="HTTP timeout in seconds (default: 20)",
    )
    args = parser.parse_args()

    urls = [u.strip() for u in args.url if str(u).strip()]
    markers = [m for m in args.contains if m]

    if not urls:
        raise RuntimeError("At least one --url is required")
    if not markers:
        raise RuntimeError("At least one --contains marker is required")

    failed = False
    for url in urls:
        try:
            status, body, final_url = _fetch_text(url, timeout_s=float(args.timeout_s))
        except urllib.error.HTTPError as exc:
            print(f"[landing-public-check] FAIL url={url} http_status={exc.code}")
            failed = True
            continue
        except urllib.error.URLError as exc:
            print(f"[landing-public-check] FAIL url={url} url_error={exc}")
            failed = True
            continue
        except Exception as exc:  # noqa: BLE001
            print(f"[landing-public-check] FAIL url={url} error={exc}")
            failed = True
            continue

        if status != 200:
            print(f"[landing-public-check] FAIL url={url} status={status}")
            failed = True
            continue

        missing = [m for m in markers if m not in body]
        if missing:
            print(
                "[landing-public-check] FAIL url=%s final_url=%s missing_markers=%s"
                % (url, final_url, missing)
            )
            failed = True
            continue

        print(
            "[landing-public-check] OK url=%s final_url=%s markers=%d"
            % (url, final_url, len(markers))
        )

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
