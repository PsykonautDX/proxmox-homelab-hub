"""Dell PowerEdge iDRAC Redfish API Hardware, Power (Watts/Cost), Thermal & Fan Telemetry."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .config import AppSettings


async def fetch_idrac_telemetry(settings: AppSettings) -> dict[str, Any]:
    rate = float(settings.electricity_rate_kwh or 0.26)
    sym = settings.currency_symbol or "£"

    if settings.proxmox_demo_mode:
        watts = 168.0
        monthly_kwh = round((watts * 24 * 30) / 1000, 1)
        monthly_cost = round(monthly_kwh * rate, 2)
        return {
            "configured": True,
            "connected": True,
            "demo_mode": True,
            "model": "PowerEdge R640 (Demo)",
            "service_tag": "R640DEMO",
            "bios_version": "2.21.2",
            "health": "OK",
            "power": {
                "watts_now": watts,
                "watts_avg": 162.0,
                "watts_min": 134.0,
                "watts_max": 248.0,
                "monthly_kwh": monthly_kwh,
                "monthly_cost": monthly_cost,
                "currency": sym,
                "rate_kwh": rate,
            },
            "psus": [
                {"name": "PS1 Status", "watts": 86, "capacity_watts": 750, "health": "OK"},
                {"name": "PS2 Status", "watts": 82, "capacity_watts": 750, "health": "OK"},
            ],
            "temperatures": [
                {"name": "Inlet Temp", "celsius": 23, "upper_warn": 42, "health": "OK"},
                {"name": "Exhaust Temp", "celsius": 38, "upper_warn": 70, "health": "OK"},
                {"name": "CPU1 Temp", "celsius": 47, "upper_warn": 85, "health": "OK"},
                {"name": "CPU2 Temp", "celsius": 45, "upper_warn": 85, "health": "OK"},
            ],
            "fans": [
                {"name": "Fan1A", "rpm": 4920, "health": "OK"},
                {"name": "Fan2A", "rpm": 4800, "health": "OK"},
                {"name": "Fan3A", "rpm": 5040, "health": "OK"},
                {"name": "Fan4A", "rpm": 4920, "health": "OK"},
            ],
        }

    host = settings.idrac_host.strip().rstrip("/")
    if not host or not settings.idrac_password.strip():
        return {
            "configured": False,
            "connected": False,
            "message": "Optional: enter your Dell iDRAC IP & credentials in System → Settings to see live Watts, monthly energy cost, CPU/Inlet temps, Fan RPMs & PSU status.",
            "currency": sym,
            "rate_kwh": rate,
        }

    if not host.startswith(("http://", "https://")):
        host = f"https://{host}"

    auth = (settings.idrac_user.strip() or "root", settings.idrac_password.strip())
    try:
        async with httpx.AsyncClient(
            base_url=host,
            auth=auth,
            verify=False,
            timeout=httpx.Timeout(10.0),
            headers={"Accept": "application/json"},
        ) as client:
            sys_r, pwr_r, thm_r = await asyncio.gather(
                client.get("/redfish/v1/Systems/System.Embedded.1"),
                client.get("/redfish/v1/Chassis/System.Embedded.1/Power"),
                client.get("/redfish/v1/Chassis/System.Embedded.1/Thermal"),
            )
            if sys_r.status_code == 401:
                return {"configured": True, "connected": False, "error": "iDRAC authentication failed (401). Check IDRAC_USER / IDRAC_PASSWORD."}

            sys_d = sys_r.json() if sys_r.status_code == 200 else {}
            pwr_d = pwr_r.json() if pwr_r.status_code == 200 else {}
            thm_d = thm_r.json() if thm_r.status_code == 200 else {}

            pc = (pwr_d.get("PowerControl") or [{}])[0]
            metrics = pc.get("PowerMetrics") or {}
            watts_now = float(pc.get("PowerConsumedWatts") or 0)
            monthly_kwh = round((watts_now * 24 * 30) / 1000, 1)
            monthly_cost = round(monthly_kwh * rate, 2)

            psus = [
                {
                    "name": p.get("Name") or "PSU",
                    "watts": p.get("LastPowerOutputWatts") or p.get("PowerOutputWatts"),
                    "capacity_watts": p.get("PowerCapacityWatts"),
                    "health": (p.get("Status") or {}).get("Health") or "Unknown",
                }
                for p in (pwr_d.get("PowerSupplies") or [])
            ]
            temps = [
                {
                    "name": t.get("Name") or "Sensor",
                    "celsius": t.get("ReadingCelsius"),
                    "upper_warn": t.get("UpperThresholdNonCritical"),
                    "health": (t.get("Status") or {}).get("Health") or "OK",
                }
                for t in (thm_d.get("Temperatures") or [])
                if t.get("ReadingCelsius") is not None
            ]
            fans = [
                {
                    "name": f.get("FanName") or f.get("Name") or "Fan",
                    "rpm": f.get("Reading"),
                    "health": (f.get("Status") or {}).get("Health") or "OK",
                }
                for f in (thm_d.get("Fans") or [])
                if f.get("Reading") is not None
            ]

            return {
                "configured": True,
                "connected": True,
                "demo_mode": False,
                "model": sys_d.get("Model") or "Dell PowerEdge",
                "service_tag": sys_d.get("SKU") or sys_d.get("SerialNumber") or "",
                "bios_version": sys_d.get("BiosVersion") or "",
                "health": (sys_d.get("Status") or {}).get("Health") or "OK",
                "power": {
                    "watts_now": watts_now,
                    "watts_avg": metrics.get("AverageConsumedWatts"),
                    "watts_min": metrics.get("MinConsumedWatts"),
                    "watts_max": metrics.get("MaxConsumedWatts"),
                    "monthly_kwh": monthly_kwh,
                    "monthly_cost": monthly_cost,
                    "currency": sym,
                    "rate_kwh": rate,
                },
                "psus": psus,
                "temperatures": temps,
                "fans": fans,
            }
    except Exception as exc:
        return {"configured": True, "connected": False, "error": f"Cannot reach iDRAC at {host}: {exc}"}
