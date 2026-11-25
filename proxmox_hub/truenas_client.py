"""Read-only TrueNAS SCALE (25.04+/25.10 Goldeye JSON-RPC 2.0 WebSocket) & CORE (REST v2.0 fallback) monitor."""

from __future__ import annotations

import asyncio
import json
import ssl
import time
from typing import Any
from urllib.parse import urlparse

import httpx
import websockets

from .config import AppSettings

# Strictly read-only TrueNAS middleware methods used by this module.
# No mutating or destructive methods are invoked anywhere in this client.
STORAGE_SERVICE_NAMES = {"cifs", "smb", "nfs", "iscsitarget", "smartd", "ssh", "snmp", "rsync"}


def _normalize_truenas_url(raw: str) -> str:
    host = (raw or "").strip().rstrip("/")
    if not host:
        return ""
    if not host.startswith(("http://", "https://")):
        host = f"https://{host}"
    return host


def _build_ws_url(http_url: str, path: str = "/api/current") -> str:
    parsed = urlparse(http_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    netloc = parsed.netloc or parsed.path
    return f"{scheme}://{netloc}{path}"


def _ssl_context(verify_ssl: bool) -> ssl.SSLContext | None:
    if verify_ssl:
        return ssl.create_default_context()
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _extract_parsed(val: Any, default: Any = 0) -> Any:
    if isinstance(val, dict):
        if "parsed" in val and val["parsed"] is not None:
            return val["parsed"]
        if "value" in val and val["value"] is not None:
            return val["value"]
        return default
    return default if val is None else val


def _walk_topology_disks(topology: dict[str, Any] | None) -> tuple[int, list[dict[str, Any]], str]:
    """Walk ZFS pool topology tree to count disks, find errored disks, and summarize vdevs."""
    if not isinstance(topology, dict):
        return 0, [], ""

    disk_count = 0
    errored: list[dict[str, Any]] = []
    vdev_parts: list[str] = []

    def _visit(node: dict[str, Any]) -> None:
        nonlocal disk_count
        children = node.get("children") or []
        if not children:
            # Leaf disk vdev
            disk_count += 1
            stats = node.get("stats") or {}
            r_err = int(stats.get("read_errors") or 0)
            w_err = int(stats.get("write_errors") or 0)
            c_err = int(stats.get("checksum_errors") or 0)
            status = str(node.get("status") or "ONLINE").upper()
            if status != "ONLINE" or (r_err + w_err + c_err) > 0:
                errored.append(
                    {
                        "name": node.get("disk") or node.get("name") or node.get("path") or "unknown",
                        "status": status,
                        "read_errors": r_err,
                        "write_errors": w_err,
                        "checksum_errors": c_err,
                    }
                )
        else:
            for ch in children:
                if isinstance(ch, dict):
                    _visit(ch)

    for section in ("data", "special", "dedup", "log", "cache", "spare"):
        vdevs = topology.get(section) or []
        if not vdevs:
            continue
        if section == "data":
            types = [str(v.get("type") or "vdev").lower() for v in vdevs if isinstance(v, dict)]
            if types:
                vdev_parts.append(f"{len(types)}× {types[0]}" if len(set(types)) == 1 else ", ".join(types))
        else:
            vdev_parts.append(f"{len(vdevs)} {section}")
        for v in vdevs:
            if isinstance(v, dict):
                _visit(v)

    return disk_count, errored, " · ".join(vdev_parts)


def _flatten_datasets(raw_datasets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flat: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _collect(item: dict[str, Any]) -> None:
        ds_id = str(item.get("id") or item.get("name") or "")
        if ds_id and ds_id not in seen:
            seen.add(ds_id)
            used = int(_extract_parsed(item.get("used"), 0) or 0)
            avail = int(_extract_parsed(item.get("available"), 0) or 0)
            quota = int(_extract_parsed(item.get("quota"), 0) or 0)
            comp_ratio = _extract_parsed(item.get("compressratio"), "")
            comp_algo = _extract_parsed(item.get("compression"), "")
            pool_name = item.get("pool") or (ds_id.split("/")[0] if "/" in ds_id else ds_id)
            flat.append(
                {
                    "id": ds_id,
                    "name": item.get("name") or ds_id,
                    "pool": pool_name,
                    "type": str(item.get("type") or "FILESYSTEM"),
                    "mountpoint": item.get("mountpoint") or "",
                    "used": used,
                    "available": avail,
                    "quota": quota,
                    "compression": str(comp_algo),
                    "compressratio": str(comp_ratio),
                    "readonly": bool(_extract_parsed(item.get("readonly"), False)),
                }
            )
        for child in item.get("children") or []:
            if isinstance(child, dict):
                _collect(child)

    for ds in raw_datasets or []:
        if isinstance(ds, dict):
            _collect(ds)
    return flat


def _parse_netdata_latest(reports: list[dict[str, Any]] | None) -> tuple[float | None, dict[str, int]]:
    """Extract latest non-null CPU usage % and ZFS ARC/Free/Services memory bytes from Netdata reports."""
    cpu_pct: float | None = None
    mem_breakdown: dict[str, int] = {}
    if not isinstance(reports, list):
        return cpu_pct, mem_breakdown

    for rep in reports:
        if not isinstance(rep, dict):
            continue
        name = str(rep.get("name") or "").lower()
        legend = [str(x).lower() for x in (rep.get("legend") or [])]
        rows = rep.get("data") or []
        latest = None
        for row in reversed(rows):
            if isinstance(row, list) and len(row) > 1 and any(v is not None for v in row[1:]):
                latest = row
                break
        if not latest:
            continue

        row_map = {legend[i]: latest[i] for i in range(min(len(legend), len(latest))) if latest[i] is not None}
        if name == "cpu":
            # Netdata cpu chart may report 'cpu' or user/system/iowait/idle columns
            if "cpu" in row_map:
                cpu_pct = round(max(0.0, min(100.0, float(row_map["cpu"]))), 1)
            elif "idle" in row_map:
                cpu_pct = round(max(0.0, min(100.0, 100.0 - float(row_map["idle"]))), 1)
            else:
                busy = sum(
                    float(row_map[k])
                    for k in ("user", "system", "nice", "iowait", "softirq", "irq")
                    if k in row_map
                )
                if busy > 0 or any(k in row_map for k in ("user", "system")):
                    cpu_pct = round(max(0.0, min(100.0, busy)), 1)
        elif name in ("memory", "arcsize"):
            # TrueNAS Netdata memory columns are typically in MiB or bytes; detect unit heuristically
            def _to_bytes(val: float) -> int:
                return int(val * 1024 * 1024) if val < 2_000_000 else int(val)

            if "free" in row_map:
                mem_breakdown["free"] = _to_bytes(float(row_map["free"]))
            if "arc" in row_map:
                mem_breakdown["zfs_arc"] = _to_bytes(float(row_map["arc"]))
            elif "arc_size" in row_map:
                mem_breakdown["zfs_arc"] = _to_bytes(float(row_map["arc_size"]))
            elif "cached" in row_map and "zfs_arc" not in mem_breakdown:
                mem_breakdown["zfs_arc"] = _to_bytes(float(row_map["cached"]))
            if "used" in row_map:
                mem_breakdown["services"] = _to_bytes(float(row_map["used"]))
            elif "apps" in row_map:
                mem_breakdown["services"] = _to_bytes(float(row_map["apps"]))

    return cpu_pct, mem_breakdown


def _normalize_snapshot(
    *,
    base_url: str,
    transport: str,
    sys_info: dict[str, Any],
    raw_pools: list[dict[str, Any]],
    raw_datasets: list[dict[str, Any]],
    raw_disks: list[dict[str, Any]],
    disk_temps: dict[str, Any],
    raw_alerts: list[dict[str, Any]],
    raw_nfs: list[dict[str, Any]],
    raw_smb: list[dict[str, Any]],
    raw_iscsi: list[dict[str, Any]],
    raw_services: list[dict[str, Any]],
    netdata_reports: list[dict[str, Any]] | None = None,
    demo_mode: bool = False,
) -> dict[str, Any]:
    datasets = _flatten_datasets(raw_datasets)
    root_ds_by_pool: dict[str, dict[str, Any]] = {}
    for ds in datasets:
        if "/" not in ds["id"] and ds["id"] == ds["pool"]:
            root_ds_by_pool[ds["pool"]] = ds

    errored_disk_map: dict[str, dict[str, Any]] = {}
    pools: list[dict[str, Any]] = []
    total_bytes = 0
    used_bytes = 0
    free_bytes = 0
    pools_degraded = 0
    total_errored_disks = 0

    for p in raw_pools or []:
        pname = str(p.get("name") or "pool")
        status = str(p.get("status") or "UNKNOWN").upper()
        healthy = bool(p.get("healthy", status == "ONLINE"))
        if status != "ONLINE" or not healthy:
            pools_degraded += 1

        # Prefer root dataset usable space (matches TrueNAS Dashboard UI), fall back to pool raw size
        root_ds = root_ds_by_pool.get(pname)
        if root_ds and (root_ds["used"] + root_ds["available"]) > 0:
            p_used = int(root_ds["used"])
            p_free = int(root_ds["available"])
            p_size = p_used + p_free
        else:
            p_size = int(p.get("size") or 0)
            p_used = int(p.get("allocated") or 0)
            p_free = int(p.get("free") or max(0, p_size - p_used))

        used_pct = round((p_used / p_size) * 100, 1) if p_size > 0 else 0.0
        total_bytes += p_size
        used_bytes += p_used
        free_bytes += p_free

        disks_cnt, errored, vdev_summary = _walk_topology_disks(p.get("topology"))
        total_errored_disks += len(errored)
        for ed in errored:
            errored_disk_map[str(ed["name"])] = ed

        scan = p.get("scan") or {}
        end_time_raw = scan.get("end_time")
        scrub_end = (
            int(end_time_raw.get("$date", 0) / 1000)
            if isinstance(end_time_raw, dict) and "$date" in end_time_raw
            else (int(end_time_raw) if isinstance(end_time_raw, (int, float)) else 0)
        )
        frag_raw = p.get("fragmentation")
        frag_val = None
        if isinstance(frag_raw, (int, float)):
            frag_val = float(frag_raw)
        elif isinstance(frag_raw, str) and frag_raw.rstrip("%").isdigit():
            frag_val = float(frag_raw.rstrip("%"))

        pools.append(
            {
                "id": p.get("id"),
                "name": pname,
                "status": status,
                "status_detail": p.get("status_detail") or "",
                "healthy": healthy,
                "warning": bool(p.get("warning", False)),
                "size": p_size,
                "allocated": p_used,
                "free": p_free,
                "used_pct": used_pct,
                "fragmentation": frag_val,
                "disks_total": disks_cnt,
                "disks_with_errors": len(errored),
                "errored_disks": errored,
                "vdev_summary": vdev_summary,
                "last_scrub": {
                    "function": scan.get("function") or "SCRUB",
                    "state": scan.get("state") or "UNKNOWN",
                    "errors": int(scan.get("errors") or 0),
                    "end_time": scrub_end,
                    "percentage": scan.get("percentage"),
                },
            }
        )

    # Normalize physical disks with temperature & ZFS error correlation
    disks: list[dict[str, Any]] = []
    temp_map = disk_temps if isinstance(disk_temps, dict) else {}
    for d in raw_disks or []:
        dname = str(d.get("name") or d.get("devname") or "")
        err_info = errored_disk_map.get(dname)
        temp_val = temp_map.get(dname)
        if temp_val is None and d.get("temperature") is not None:
            temp_val = d.get("temperature")
        disks.append(
            {
                "name": dname,
                "model": d.get("model") or "Unknown",
                "serial": d.get("serial") or "",
                "size": int(d.get("size") or 0),
                "type": str(d.get("type") or ("HDD" if (d.get("rotationrate") or 0) > 0 else "SSD")).upper(),
                "pool": d.get("pool") or "",
                "bus": d.get("bus") or d.get("subsystem") or "",
                "rotationrate": d.get("rotationrate"),
                "temperature": int(temp_val) if isinstance(temp_val, (int, float)) else None,
                "zfs_status": err_info["status"] if err_info else ("ONLINE" if d.get("pool") else "UNUSED"),
                "read_errors": err_info["read_errors"] if err_info else 0,
                "write_errors": err_info["write_errors"] if err_info else 0,
                "checksum_errors": err_info["checksum_errors"] if err_info else 0,
            }
        )
    disks.sort(key=lambda x: (-(x["read_errors"] + x["write_errors"] + x["checksum_errors"]), x["pool"] or "zzz", x["name"]))

    # Normalize shares (NFS, SMB, iSCSI)
    shares: list[dict[str, Any]] = []
    for s in raw_nfs or []:
        shares.append(
            {
                "protocol": "NFS",
                "name": s.get("path") or s.get("comment") or f"nfs-{s.get('id')}",
                "path": s.get("path") or "",
                "enabled": bool(s.get("enabled", True)),
                "readonly": bool(s.get("ro", False)),
                "comment": s.get("comment") or "",
                "hosts": s.get("hosts") or s.get("networks") or [],
            }
        )
    for s in raw_smb or []:
        shares.append(
            {
                "protocol": "SMB",
                "name": s.get("name") or f"smb-{s.get('id')}",
                "path": s.get("path") or "",
                "enabled": bool(s.get("enabled", True)),
                "readonly": bool(s.get("ro", False)),
                "comment": s.get("comment") or "",
                "hosts": [],
            }
        )
    for s in raw_iscsi or []:
        shares.append(
            {
                "protocol": "iSCSI",
                "name": s.get("name") or f"iscsi-{s.get('id')}",
                "path": s.get("disk") or s.get("path") or "",
                "enabled": bool(s.get("enabled", True)),
                "readonly": bool(s.get("ro", False)),
                "comment": s.get("comment") or "",
                "hosts": [],
            }
        )

    # Normalize storage-relevant services
    services: list[dict[str, Any]] = []
    for sv in raw_services or []:
        sname = str(sv.get("service") or "").lower()
        if sname in STORAGE_SERVICE_NAMES:
            services.append(
                {
                    "service": sname,
                    "state": str(sv.get("state") or "UNKNOWN").upper(),
                    "enable": bool(sv.get("enable", False)),
                }
            )
    services.sort(key=lambda x: (x["state"] != "RUNNING", x["service"]))

    # Normalize active alerts
    sev_order = {"CRITICAL": 0, "ALERT": 0, "EMERGENCY": 0, "ERROR": 1, "WARNING": 2, "NOTICE": 3, "INFO": 4}
    alerts: list[dict[str, Any]] = []
    for a in raw_alerts or []:
        if a.get("dismissed"):
            continue
        lvl = str(a.get("level") or "INFO").upper()
        dt = a.get("datetime")
        ts = (
            int(dt.get("$date", 0) / 1000)
            if isinstance(dt, dict) and "$date" in dt
            else (int(dt) if isinstance(dt, (int, float)) else 0)
        )
        alerts.append(
            {
                "id": str(a.get("uuid") or a.get("id") or ""),
                "level": lvl,
                "klass": a.get("klass") or "",
                "message": a.get("formatted") or a.get("text") or "",
                "timestamp": ts,
            }
        )
    alerts.sort(key=lambda x: (sev_order.get(x["level"], 9), -x["timestamp"]))

    # System & CPU/RAM telemetry
    physmem = int(sys_info.get("physmem") or 0)
    cpu_pct, mem_bd = _parse_netdata_latest(netdata_reports)
    mem_free = int(mem_bd.get("free") or 0)
    mem_arc = int(mem_bd.get("zfs_arc") or 0)
    mem_services = int(mem_bd.get("services") or 0)
    if physmem > 0 and mem_services == 0 and (mem_free or mem_arc):
        mem_services = max(0, physmem - mem_free - mem_arc)
    mem_used = max(0, physmem - mem_free) if (physmem > 0 and mem_free > 0) else (mem_arc + mem_services)
    mem_used_pct = round((mem_used / physmem) * 100, 1) if physmem > 0 and mem_used > 0 else None

    critical_alerts = sum(1 for a in alerts if a["level"] in ("CRITICAL", "ALERT", "EMERGENCY", "ERROR"))
    warning_alerts = sum(1 for a in alerts if a["level"] == "WARNING")
    max_disk_temp = max((d["temperature"] for d in disks if d["temperature"] is not None), default=None)

    return {
        "configured": True,
        "connected": True,
        "demo_mode": demo_mode,
        "read_only": True,
        "transport": transport,
        "url": base_url,
        "checked_at": int(time.time()),
        "system": {
            "hostname": sys_info.get("hostname") or urlparse(base_url).hostname or "TrueNAS",
            "version": sys_info.get("version") or "TrueNAS",
            "uptime": sys_info.get("uptime") or "",
            "uptime_seconds": float(sys_info.get("uptime_seconds") or 0),
            "cpu_model": sys_info.get("model") or "",
            "cores": int(sys_info.get("cores") or 0),
            "physical_cores": int(sys_info.get("physical_cores") or 0),
            "physmem": physmem,
            "ecc_memory": bool(sys_info.get("ecc_memory", False)),
            "loadavg": sys_info.get("loadavg") or [],
            "cpu_usage_pct": cpu_pct,
            "memory": {
                "total": physmem,
                "free": mem_free,
                "zfs_arc": mem_arc,
                "services": mem_services,
                "used_pct": mem_used_pct,
            },
        },
        "summary": {
            "pools_total": len(pools),
            "pools_online": len(pools) - pools_degraded,
            "pools_degraded": pools_degraded,
            "total_bytes": total_bytes,
            "used_bytes": used_bytes,
            "free_bytes": free_bytes,
            "used_pct": round((used_bytes / total_bytes) * 100, 1) if total_bytes > 0 else 0.0,
            "disks_total": len(disks),
            "disks_with_errors": total_errored_disks,
            "max_disk_temp": max_disk_temp,
            "datasets_total": len(datasets),
            "shares_total": len(shares),
            "active_alerts": len(alerts),
            "critical_alerts": critical_alerts,
            "warning_alerts": warning_alerts,
        },
        "pools": pools,
        "datasets": datasets[:120],
        "disks": disks,
        "shares": shares,
        "services": services,
        "alerts": alerts,
        "proxmox_links": [],
    }


def _demo_truenas_payload(base_url: str) -> dict[str, Any]:
    now = int(time.time())
    tib = 1024**4
    gib = 1024**3
    sys_info = {
        "hostname": "truenas-lab (Demo)",
        "version": "TrueNAS-SCALE-25.10.2.1 - Goldeye",
        "uptime": "17 days, 2:05:12",
        "uptime_seconds": 17 * 86400 + 7512,
        "model": "Intel(R) Xeon(R) CPU E3-1240 v5 @ 3.50GHz",
        "cores": 8,
        "physical_cores": 4,
        "physmem": int(7.6 * gib),
        "ecc_memory": True,
        "loadavg": [0.18, 0.24, 0.21],
    }
    raw_pools = [
        {
            "id": 1,
            "name": "media-pool",
            "status": "DEGRADED",
            "healthy": False,
            "warning": True,
            "size": int(63.29 * tib),
            "allocated": int(56.89 * tib),
            "free": int(6.40 * tib),
            "fragmentation": 18,
            "topology": {
                "data": [
                    {
                        "type": "RAIDZ2",
                        "children": [
                            {"disk": "sda", "status": "ONLINE", "stats": {"read_errors": 0, "write_errors": 0, "checksum_errors": 0}},
                            {"disk": "sdb", "status": "ONLINE", "stats": {"read_errors": 0, "write_errors": 0, "checksum_errors": 0}},
                            {"disk": "sdc", "status": "DEGRADED", "stats": {"read_errors": 4, "write_errors": 0, "checksum_errors": 12}},
                            {"disk": "sdd", "status": "ONLINE", "stats": {"read_errors": 0, "write_errors": 0, "checksum_errors": 0}},
                        ],
                    }
                ]
            },
            "scan": {"function": "SCRUB", "state": "FINISHED", "errors": 12, "end_time": now - 86400 * 3},
        },
        {
            "id": 2,
            "name": "data-pool",
            "status": "ONLINE",
            "healthy": True,
            "warning": False,
            "size": int(1.76 * tib),
            "allocated": int(1.31 * tib),
            "free": int(0.45 * tib),
            "fragmentation": 12,
            "topology": {
                "data": [
                    {
                        "type": "MIRROR",
                        "children": [
                            {"disk": "sde", "status": "ONLINE", "stats": {"read_errors": 0, "write_errors": 0, "checksum_errors": 0}},
                            {"disk": "sdf", "status": "ONLINE", "stats": {"read_errors": 0, "write_errors": 0, "checksum_errors": 0}},
                        ],
                    }
                ]
            },
            "scan": {"function": "SCRUB", "state": "FINISHED", "errors": 0, "end_time": now - 86400 * 5},
        },
    ]
    raw_datasets = [
        {"id": "media-pool", "name": "media-pool", "pool": "media-pool", "type": "FILESYSTEM", "mountpoint": "/mnt/media-pool", "used": {"parsed": int(56.89 * tib)}, "available": {"parsed": int(6.40 * tib)}, "compression": {"value": "lz4"}, "compressratio": {"value": "1.03x"}},
        {"id": "media-pool/movies", "name": "media-pool/movies", "pool": "media-pool", "type": "FILESYSTEM", "mountpoint": "/mnt/media-pool/movies", "used": {"parsed": int(34.2 * tib)}, "available": {"parsed": int(6.40 * tib)}, "compression": {"value": "lz4"}, "compressratio": {"value": "1.01x"}},
        {"id": "data-pool", "name": "data-pool", "pool": "data-pool", "type": "FILESYSTEM", "mountpoint": "/mnt/data-pool", "used": {"parsed": int(1.31 * tib)}, "available": {"parsed": int(0.45 * tib)}, "compression": {"value": "zstd"}, "compressratio": {"value": "1.28x"}},
    ]
    raw_disks = [
        {"name": "sda", "model": "WDC WD120EFBX", "serial": "WD-DEMO-01", "size": int(12 * tib), "type": "HDD", "pool": "media-pool", "rotationrate": 7200, "bus": "SATA"},
        {"name": "sdb", "model": "WDC WD120EFBX", "serial": "WD-DEMO-02", "size": int(12 * tib), "type": "HDD", "pool": "media-pool", "rotationrate": 7200, "bus": "SATA"},
        {"name": "sdc", "model": "WDC WD120EFBX", "serial": "WD-DEMO-03", "size": int(12 * tib), "type": "HDD", "pool": "media-pool", "rotationrate": 7200, "bus": "SATA"},
        {"name": "sdd", "model": "WDC WD120EFBX", "serial": "WD-DEMO-04", "size": int(12 * tib), "type": "HDD", "pool": "media-pool", "rotationrate": 7200, "bus": "SATA"},
        {"name": "sde", "model": "Samsung SSD 870 EVO", "serial": "SS-DEMO-01", "size": int(2 * tib), "type": "SSD", "pool": "data-pool", "rotationrate": 0, "bus": "SATA"},
        {"name": "sdf", "model": "Samsung SSD 870 EVO", "serial": "SS-DEMO-02", "size": int(2 * tib), "type": "SSD", "pool": "data-pool", "rotationrate": 0, "bus": "SATA"},
    ]
    disk_temps = {"sda": 34, "sdb": 35, "sdc": 41, "sdd": 33, "sde": 29, "sdf": 28}
    raw_alerts = [
        {
            "uuid": "demo-alert-1",
            "level": "CRITICAL",
            "klass": "VolumeStatus",
            "formatted": "Pool media-pool state is DEGRADED: One or more devices has experienced an unrecoverable error.",
            "dismissed": False,
            "datetime": now - 3600,
        },
        {
            "uuid": "demo-alert-2",
            "level": "WARNING",
            "klass": "ZpoolCapacityWarning",
            "formatted": "Space usage for pool 'media-pool' is 89.8%. Optimal ZFS performance is below 80%.",
            "dismissed": False,
            "datetime": now - 7200,
        },
    ]
    raw_nfs = [
        {"id": 1, "path": "/mnt/media-pool/movies", "enabled": True, "ro": False, "comment": "Plex / Arr Media", "networks": ["192.168.1.0/24"]},
        {"id": 2, "path": "/mnt/data-pool", "enabled": True, "ro": False, "comment": "Proxmox Backups & ISOs", "networks": ["192.168.1.2/32"]},
    ]
    raw_smb = [
        {"id": 1, "name": "media", "path": "/mnt/media-pool", "enabled": True, "ro": False, "comment": "LAN Media Share"},
    ]
    raw_services = [
        {"service": "nfs", "state": "RUNNING", "enable": True},
        {"service": "cifs", "state": "RUNNING", "enable": True},
        {"service": "smartd", "state": "RUNNING", "enable": True},
        {"service": "ssh", "state": "RUNNING", "enable": True},
        {"service": "iscsitarget", "state": "STOPPED", "enable": False},
    ]
    netdata = [
        {"name": "cpu", "legend": ["time", "user", "system", "iowait", "idle"], "data": [[now, 2.4, 1.8, 0.4, 95.4]]},
        {"name": "memory", "legend": ["time", "free", "arc", "used"], "data": [[now, int(2.0 * gib), int(3.4 * gib), int(2.2 * gib)]]},
    ]
    return _normalize_snapshot(
        base_url=base_url or "https://your-truenas-ip",
        transport="demo",
        sys_info=sys_info,
        raw_pools=raw_pools,
        raw_datasets=raw_datasets,
        raw_disks=raw_disks,
        disk_temps=disk_temps,
        raw_alerts=raw_alerts,
        raw_nfs=raw_nfs,
        raw_smb=raw_smb,
        raw_iscsi=[],
        raw_services=raw_services,
        netdata_reports=netdata,
        demo_mode=True,
    )


class _TrueNASJsonRpcSession:
    """Short-lived read-only JSON-RPC 2.0 WebSocket session for TrueNAS SCALE 25.04+/25.10 Goldeye."""

    def __init__(self, base_url: str, api_key: str, verify_ssl: bool = False, timeout: float = 12.0) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self._ws = None
        self._next_id = 1

    async def __aenter__(self) -> "_TrueNASJsonRpcSession":
        ws_url = _build_ws_url(self.base_url, "/api/current")
        ssl_ctx = _ssl_context(self.verify_ssl) if ws_url.startswith("wss://") else None
        self._ws = await asyncio.wait_for(
            websockets.connect(
                ws_url,
                ssl=ssl_ctx,
                open_timeout=self.timeout,
                close_timeout=4.0,
                max_size=16 * 1024 * 1024,
            ),
            timeout=self.timeout,
        )
        ok = await self._authenticate()
        if not ok:
            await self.close()
            raise PermissionError("TrueNAS API key authentication failed. Check TRUENAS_API_KEY.")
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def close(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def call(self, method: str, params: list[Any] | None = None, timeout: float | None = None) -> Any:
        assert self._ws is not None
        req_id = self._next_id
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or []}
        await self._ws.send(json.dumps(payload))

        deadline = time.monotonic() + (timeout or self.timeout)
        while True:
            remaining = max(0.5, deadline - time.monotonic())
            raw = await asyncio.wait_for(self._ws.recv(), timeout=remaining)
            msg = json.loads(raw)
            if msg.get("id") != req_id:
                continue  # ignore unsolicited server notifications/events
            if "error" in msg and msg["error"] is not None:
                err = msg["error"]
                detail = err.get("message") if isinstance(err, dict) else str(err)
                data_reason = ""
                if isinstance(err, dict) and isinstance(err.get("data"), dict):
                    data_reason = err["data"].get("reason") or ""
                raise RuntimeError(f"RPC '{method}' failed: {data_reason or detail}")
            return msg.get("result")

    async def safe_call(self, method: str, params: list[Any] | None = None, default: Any = None) -> Any:
        try:
            return await self.call(method, params)
        except Exception:
            return default

    async def _authenticate(self) -> bool:
        try:
            res = await self.call("auth.login_with_api_key", [self.api_key])
            return bool(res)
        except Exception as exc:
            # Fallback if a newer TrueNAS release requires auth.login_ex
            if "not found" in str(exc).lower() or "method" in str(exc).lower():
                res = await self.call(
                    "auth.login_ex",
                    [{"mechanism": "API_KEY_PLAIN", "api_key": self.api_key}],
                )
                return bool(res and (res is True or (isinstance(res, dict) and res.get("response_type") == "SUCCESS")))
            raise


async def _fetch_via_jsonrpc_ws(base_url: str, api_key: str, verify_ssl: bool) -> dict[str, Any]:
    async with _TrueNASJsonRpcSession(base_url, api_key, verify_ssl=verify_ssl) as rpc:
        sys_info = await rpc.call("system.info")
        pools = await rpc.safe_call("pool.query", [], [])
        datasets = await rpc.safe_call("pool.dataset.query", [[], {"extra": {"flat": True}}], None)
        if datasets is None:
            datasets = await rpc.safe_call("pool.dataset.query", [], [])
        disks = await rpc.safe_call("disk.query", [], [])
        disk_names = [d.get("name") for d in (disks or []) if isinstance(d, dict) and d.get("name")]
        disk_temps = await rpc.safe_call("disk.temperatures", [disk_names], {}) if disk_names else {}
        alerts = await rpc.safe_call("alert.list", [], [])
        nfs = await rpc.safe_call("sharing.nfs.query", [], [])
        smb = await rpc.safe_call("sharing.smb.query", [], [])
        iscsi = await rpc.safe_call("iscsi.extent.query", [], [])
        services = await rpc.safe_call("service.query", [], [])
        now = int(time.time())
        netdata = await rpc.safe_call(
            "reporting.netdata_get_data",
            [[{"name": "cpu"}, {"name": "memory"}], {"start": now - 300, "end": now}],
            [],
        )

    return _normalize_snapshot(
        base_url=base_url,
        transport="jsonrpc-ws",
        sys_info=sys_info or {},
        raw_pools=pools or [],
        raw_datasets=datasets or [],
        raw_disks=disks or [],
        disk_temps=disk_temps or {},
        raw_alerts=alerts or [],
        raw_nfs=nfs or [],
        raw_smb=smb or [],
        raw_iscsi=iscsi or [],
        raw_services=services or [],
        netdata_reports=netdata or [],
        demo_mode=False,
    )


async def _fetch_via_rest_v2(base_url: str, api_key: str, verify_ssl: bool) -> dict[str, Any]:
    """Read-only REST v2.0 fallback for older TrueNAS SCALE (<=24.10) or TrueNAS CORE."""
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    async with httpx.AsyncClient(
        base_url=base_url,
        headers=headers,
        verify=verify_ssl,
        timeout=httpx.Timeout(12.0),
    ) as client:
        sys_r = await client.get("/api/v2.0/system/info")
        if sys_r.status_code == 401:
            raise PermissionError("TrueNAS API key authentication failed (401). Check TRUENAS_API_KEY.")
        sys_r.raise_for_status()
        sys_info = sys_r.json()

        async def _get(path: str, default: Any) -> Any:
            try:
                r = await client.get(path)
                return r.json() if r.status_code == 200 else default
            except Exception:
                return default

        pools, datasets, disks, alerts, nfs, smb, iscsi, services = await asyncio.gather(
            _get("/api/v2.0/pool", []),
            _get("/api/v2.0/pool/dataset", []),
            _get("/api/v2.0/disk", []),
            _get("/api/v2.0/alert/list", []),
            _get("/api/v2.0/sharing/nfs", []),
            _get("/api/v2.0/sharing/smb", []),
            _get("/api/v2.0/iscsi/extent", []),
            _get("/api/v2.0/service", []),
        )

    return _normalize_snapshot(
        base_url=base_url,
        transport="rest-v2",
        sys_info=sys_info or {},
        raw_pools=pools or [],
        raw_datasets=datasets or [],
        raw_disks=disks or [],
        disk_temps={},
        raw_alerts=alerts or [],
        raw_nfs=nfs or [],
        raw_smb=smb or [],
        raw_iscsi=iscsi or [],
        raw_services=services or [],
        netdata_reports=[],
        demo_mode=False,
    )


async def fetch_truenas_telemetry(settings: AppSettings) -> dict[str, Any]:
    """Fetch read-only TrueNAS host, ZFS pool, disk, share, service & alert telemetry."""
    base_url = _normalize_truenas_url(settings.truenas_host)

    if settings.proxmox_demo_mode:
        return _demo_truenas_payload(base_url)

    api_key = settings.truenas_api_key.strip()
    if not base_url or not api_key:
        return {
            "configured": False,
            "connected": False,
            "read_only": True,
            "url": base_url,
            "message": (
                "Optional: enter your bare-metal TrueNAS URL (e.g. https://your-truenas-ip) and a TrueNAS API Key "
                "in System → MCP & Settings for live read-only ZFS pool health, disk errors/temps, shares, and alerts."
            ),
        }

    # Try JSON-RPC 2.0 over WebSocket first (TrueNAS 25.04+ / 25.10 Goldeye standard).
    # If HTTPS WS fails with an HTTP/TLS mismatch or 404, try REST v2.0 fallback for older hosts.
    ws_error: Exception | None = None
    try:
        return await _fetch_via_jsonrpc_ws(base_url, api_key, settings.truenas_verify_ssl)
    except PermissionError as exc:
        return {"configured": True, "connected": False, "read_only": True, "url": base_url, "error": str(exc)}
    except Exception as exc:
        ws_error = exc

    # If user entered https:// but host only serves http:// (or vice versa), try alternate scheme on WS
    alt_url = (
        base_url.replace("https://", "http://", 1)
        if base_url.startswith("https://")
        else base_url.replace("http://", "https://", 1)
    )
    if any(tok in str(ws_error).lower() for tok in ("ssl", "tls", "wrong_version", "handshake", "eof", "redirect", "301", "302")):
        try:
            return await _fetch_via_jsonrpc_ws(alt_url, api_key, settings.truenas_verify_ssl)
        except PermissionError as exc:
            return {"configured": True, "connected": False, "read_only": True, "url": alt_url, "error": str(exc)}
        except Exception:
            pass

    # Fallback to REST v2.0 for TrueNAS CORE / SCALE <=24.10
    try:
        return await _fetch_via_rest_v2(base_url, api_key, settings.truenas_verify_ssl)
    except PermissionError as exc:
        return {"configured": True, "connected": False, "read_only": True, "url": base_url, "error": str(exc)}
    except Exception:
        return {
            "configured": True,
            "connected": False,
            "read_only": True,
            "url": base_url,
            "error": f"Cannot reach TrueNAS at {base_url}: {ws_error}",
        }


async def test_truenas_connection(settings: AppSettings) -> dict[str, Any]:
    """Quick read-only connection check for the Settings UI."""
    data = await fetch_truenas_telemetry(settings)
    if not data.get("configured"):
        return {"ok": False, "error": data.get("message") or "TrueNAS host and API key are required."}
    if not data.get("connected"):
        return {"ok": False, "error": data.get("error") or "Unable to connect to TrueNAS."}
    sys_d = data.get("system") or {}
    summary = data.get("summary") or {}
    return {
        "ok": True,
        "demo_mode": bool(data.get("demo_mode")),
        "transport": data.get("transport"),
        "url": data.get("url"),
        "hostname": sys_d.get("hostname"),
        "version": sys_d.get("version"),
        "pools_total": summary.get("pools_total", 0),
        "pools_degraded": summary.get("pools_degraded", 0),
        "active_alerts": summary.get("active_alerts", 0),
    }
