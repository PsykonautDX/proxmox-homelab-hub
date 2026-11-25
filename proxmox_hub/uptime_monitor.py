"""Lightweight async HTTP/TCP service uptime prober (Mini Uptime Kuma)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx


async def probe_single_service(
    client: httpx.AsyncClient, vmid: int, name: str, url: str
) -> dict[str, Any]:
    t0 = time.monotonic()
    now = int(time.time())
    try:
        resp = await client.get(url)
        ms = round((time.monotonic() - t0) * 1000, 1)
        # Treat <500 as UP (401/403/302 login gates mean the service IS alive!)
        is_up = resp.status_code < 500
        return {
            "vmid": vmid,
            "name": name,
            "url": url,
            "up": is_up,
            "status_code": resp.status_code,
            "latency_ms": ms,
            "error": "" if is_up else f"HTTP {resp.status_code}",
            "checked_at": now,
        }
    except Exception as exc:
        ms = round((time.monotonic() - t0) * 1000, 1)
        return {
            "vmid": vmid,
            "name": name,
            "url": url,
            "up": False,
            "status_code": None,
            "latency_ms": ms,
            "error": str(exc)[:180],
            "checked_at": now,
        }


async def probe_guest_services(
    guests: list[dict[str, Any]], demo_mode: bool = False
) -> list[dict[str, Any]]:
    targets = [
        (int(g["vmid"]), str(g.get("service_label") or g.get("name")), str(g["service_url"]))
        for g in guests
        if g.get("status") == "running" and g.get("service_url")
    ]
    if not targets:
        return []

    if demo_mode:
        now = int(time.time())
        return [
            {
                "vmid": vmid,
                "name": name,
                "url": url,
                "up": True,
                "status_code": 200,
                "latency_ms": round(6.5 + (vmid % 17) * 1.3, 1),
                "error": "",
                "checked_at": now,
            }
            for vmid, name, url in targets
        ]

    sem = asyncio.Semaphore(12)
    async with httpx.AsyncClient(
        verify=False,
        follow_redirects=True,
        timeout=httpx.Timeout(3.5),
        headers={"User-Agent": "ProxmoxHomelabHub-Uptime/1.0"},
    ) as client:
        async def _guarded(vmid: int, name: str, url: str) -> dict[str, Any]:
            async with sem:
                return await probe_single_service(client, vmid, name, url)

        return list(await asyncio.gather(*(_guarded(v, n, u) for v, n, u in targets)))
