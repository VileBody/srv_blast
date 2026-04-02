from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx


TWC_API_BASE = "https://api.timeweb.cloud/api/v1"


class RenderNodePoolError(RuntimeError):
    pass


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _extract_ipv4(server_obj: dict[str, Any]) -> str:
    networks = server_obj.get("networks")
    if not isinstance(networks, list):
        return ""
    fallback: str = ""
    for net in networks:
        if not isinstance(net, dict):
            continue
        ips = net.get("ips")
        if not isinstance(ips, list):
            continue
        for ip_obj in ips:
            if not isinstance(ip_obj, dict):
                continue
            if str(ip_obj.get("type") or "") != "ipv4":
                continue
            ip = str(ip_obj.get("ip") or "").strip()
            if not ip:
                continue
            is_main = ip_obj.get("is_main")
            if is_main is True:
                return ip
            if not fallback:
                fallback = ip
    return fallback


def _extract_status(server_obj: dict[str, Any]) -> str:
    return str(server_obj.get("status") or "").strip().lower()


def _now_utc_tag() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


async def _json_request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    token: str,
    payload: dict[str, Any] | None = None,
    expected_codes: tuple[int, ...] = (200,),
) -> dict[str, Any]:
    url = f"{TWC_API_BASE}{path}"
    resp = await client.request(
        method.upper(),
        url,
        headers=_auth_headers(token),
        json=payload,
    )
    if resp.status_code not in expected_codes:
        raise RenderNodePoolError(
            f"{method.upper()} {path} failed: {resp.status_code} {resp.text}"
        )
    if resp.status_code == 204 or not resp.text.strip():
        return {}
    try:
        data = resp.json()
    except Exception:
        raise RenderNodePoolError(
            f"{method.upper()} {path} returned non-JSON body: {resp.text[:500]}"
        ) from None
    if not isinstance(data, dict):
        raise RenderNodePoolError(
            f"{method.upper()} {path} returned non-object JSON: {data!r}"
        )
    return data


async def probe_render_node(base_url: str, *, timeout_s: float = 8.0) -> dict[str, int]:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return {"root": 0, "render": 0, "jobs": 0}

    async with httpx.AsyncClient(timeout=timeout_s) as client:
        root = 0
        render = 0
        jobs = 0
        try:
            r = await client.get(f"{base}/")
            root = int(r.status_code)
        except Exception:
            root = 0
        try:
            r = await client.post(f"{base}/render", json={})
            render = int(r.status_code)
        except Exception:
            render = 0
        try:
            r = await client.post(f"{base}/jobs", json={})
            jobs = int(r.status_code)
        except Exception:
            jobs = 0
    return {"root": root, "render": render, "jobs": jobs}


def _probe_is_contract_reachable(codes: dict[str, int]) -> bool:
    render = int(codes.get("render", 0) or 0)
    jobs = int(codes.get("jobs", 0) or 0)
    return (render not in {0, 404}) or (jobs not in {0, 404})


async def list_render_servers(
    token: str,
    *,
    name_prefix: str = "",
    include_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    include_ids = include_ids or set()
    prefix = str(name_prefix or "").strip().lower()

    async with httpx.AsyncClient(timeout=30.0) as client:
        data = await _json_request(
            client,
            "GET",
            "/servers",
            token=token,
            expected_codes=(200,),
        )
    raw = data.get("servers")
    if not isinstance(raw, list):
        return []

    out: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        server_id = int(row.get("id") or 0)
        name = str(row.get("name") or "")
        name_l = name.lower()
        os_obj = row.get("os")
        os_name = ""
        if isinstance(os_obj, dict):
            os_name = str(os_obj.get("name") or "").lower()
        if os_name and os_name != "windows":
            continue
        if include_ids and server_id in include_ids:
            pass
        elif prefix and not name_l.startswith(prefix):
            continue
        elif not prefix and not include_ids:
            continue

        out.append(
            {
                "id": server_id,
                "name": name,
                "status": str(row.get("status") or ""),
                "ipv4": _extract_ipv4(row),
                "created_at": str(row.get("created_at") or ""),
                "updated_at": str(row.get("updated_at") or ""),
            }
        )

    out.sort(key=lambda x: (x.get("name") or "", x.get("id") or 0))
    return out


async def create_render_server_from_clone(
    *,
    token: str,
    source_server_id: int,
    firewall_group_id: str = "",
    name_prefix: str = "blast-render-node",
    wait_on_timeout_s: int = 1800,
    wait_api_timeout_s: int = 900,
    poll_interval_s: float = 10.0,
) -> dict[str, Any]:
    src_id = int(source_server_id)
    if src_id <= 0:
        raise RenderNodePoolError("source_server_id must be > 0")

    firewall_group_id = str(firewall_group_id or "").strip()
    prefix = str(name_prefix or "blast-render-node").strip()
    target_name = f"{prefix}-{_now_utc_tag()}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        clone_resp = await _json_request(
            client,
            "POST",
            f"/servers/{src_id}/clone",
            token=token,
            payload={},
            expected_codes=(201,),
        )
        server_obj = clone_resp.get("server")
        if not isinstance(server_obj, dict):
            raise RenderNodePoolError("clone response does not include server object")
        server_id = int(server_obj.get("id") or 0)
        if server_id <= 0:
            raise RenderNodePoolError("clone response has invalid server id")

        await _json_request(
            client,
            "PATCH",
            f"/servers/{server_id}",
            token=token,
            payload={"name": target_name},
            expected_codes=(200, 204),
        )

        current = await _json_request(
            client,
            "GET",
            f"/servers/{server_id}",
            token=token,
            expected_codes=(200,),
        )
        current_server = current.get("server")
        if not isinstance(current_server, dict):
            raise RenderNodePoolError(f"server {server_id}: invalid details payload")
        if not _extract_ipv4(current_server):
            await _json_request(
                client,
                "POST",
                f"/servers/{server_id}/ips",
                token=token,
                payload={"type": "ipv4"},
                expected_codes=(201,),
            )

        await _json_request(
            client,
            "POST",
            f"/servers/{server_id}/start",
            token=token,
            expected_codes=(204,),
        )

        status = ""
        ipv4 = ""
        remaining = float(max(30, int(wait_on_timeout_s)))
        while remaining > 0:
            details = await _json_request(
                client,
                "GET",
                f"/servers/{server_id}",
                token=token,
                expected_codes=(200,),
            )
            srv = details.get("server")
            if not isinstance(srv, dict):
                raise RenderNodePoolError(f"server {server_id}: invalid details payload")
            status = _extract_status(srv)
            ipv4 = _extract_ipv4(srv)
            if status == "on" and ipv4:
                break
            await asyncio.sleep(float(poll_interval_s))
            remaining -= float(poll_interval_s)
        if status != "on" or not ipv4:
            raise RenderNodePoolError(
                f"server {server_id} did not reach status=on with ipv4 within {wait_on_timeout_s}s"
            )

        if firewall_group_id:
            await _json_request(
                client,
                "POST",
                f"/firewall/groups/{firewall_group_id}/resources/{server_id}",
                token=token,
                expected_codes=(201, 409),
            )

    base_url = f"http://{ipv4}:8000"
    probe_codes = {"root": 0, "render": 0, "jobs": 0}
    remaining_probe = float(max(30, int(wait_api_timeout_s)))
    while remaining_probe > 0:
        probe_codes = await probe_render_node(base_url, timeout_s=8.0)
        if _probe_is_contract_reachable(probe_codes):
            break
        await asyncio.sleep(float(poll_interval_s))
        remaining_probe -= float(poll_interval_s)
    if not _probe_is_contract_reachable(probe_codes):
        raise RenderNodePoolError(
            f"render API did not become reachable on {base_url} within {wait_api_timeout_s}s "
            f"(codes={probe_codes})"
        )

    return {
        "server_id": server_id,
        "server_name": target_name,
        "ipv4": ipv4,
        "windows_url": base_url,
        "probe": probe_codes,
    }


async def delete_render_server(
    *,
    token: str,
    server_id: int,
    firewall_group_id: str = "",
) -> dict[str, Any]:
    sid = int(server_id)
    if sid <= 0:
        raise RenderNodePoolError("server_id must be > 0")
    firewall_group_id = str(firewall_group_id or "").strip()

    async with httpx.AsyncClient(timeout=30.0) as client:
        if firewall_group_id:
            await _json_request(
                client,
                "DELETE",
                f"/firewall/groups/{firewall_group_id}/resources/{sid}",
                token=token,
                expected_codes=(200, 204, 404),
            )

        await _json_request(
            client,
            "DELETE",
            f"/servers/{sid}",
            token=token,
            expected_codes=(204, 404),
        )

    return {"server_id": sid, "deleted": True}
