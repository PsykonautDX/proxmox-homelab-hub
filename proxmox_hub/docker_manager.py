"""Unified Docker Container Discovery & Control across Proxmox VMs (QEMU Guest Agent) and LXCs (SSH pct exec)."""

from __future__ import annotations

import asyncio
import json
import shlex
from typing import Any

from .community_scripts import CommunityScriptsHub


DEMO_DOCKER_CONTAINERS: list[dict[str, Any]] = [
    {"host_vmid": 100, "host_name": "docker-core", "host_type": "qemu", "id": "a1b2c3d4e5f6", "name": "traefik", "image": "traefik:v3.2", "state": "running", "status": "Up 14 days (healthy)", "ports": "0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp"},
    {"host_vmid": 100, "host_name": "docker-core", "host_type": "qemu", "id": "b2c3d4e5f6a7", "name": "portainer", "image": "portainer/portainer-ce:latest", "state": "running", "status": "Up 14 days", "ports": "0.0.0.0:9443->9443/tcp"},
    {"host_vmid": 100, "host_name": "docker-core", "host_type": "qemu", "id": "c3d4e5f6a7b8", "name": "immich-server", "image": "ghcr.io/immich-app/immich-server:release", "state": "running", "status": "Up 9 days (healthy)", "ports": "0.0.0.0:2283->2283/tcp"},
    {"host_vmid": 100, "host_name": "docker-core", "host_type": "qemu", "id": "d4e5f6a7b8c9", "name": "uptime-kuma", "image": "louislam/uptime-kuma:1", "state": "running", "status": "Up 14 days (healthy)", "ports": "0.0.0.0:3001->3001/tcp"},
    {"host_vmid": 100, "host_name": "docker-core", "host_type": "qemu", "id": "e5f6a7b8c9d0", "name": "watchtower", "image": "containrrr/watchtower:latest", "state": "exited", "status": "Exited (0) 3 days ago", "ports": ""},
]


def _parse_docker_json_lines(
    raw: str, host_vmid: int, host_name: str, host_type: str
) -> list[dict[str, Any]]:
    containers: list[dict[str, Any]] = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        containers.append(
            {
                "host_vmid": host_vmid,
                "host_name": host_name,
                "host_type": host_type,
                "id": str(obj.get("ID") or "")[:12],
                "name": str(obj.get("Names") or obj.get("Name") or ""),
                "image": str(obj.get("Image") or ""),
                "state": str(obj.get("State") or "").lower(),
                "status": str(obj.get("Status") or ""),
                "ports": str(obj.get("Ports") or ""),
                "created": str(obj.get("RunningFor") or obj.get("CreatedAt") or ""),
            }
        )
    return containers


async def _ssh_exec_host(
    host_url: str, user: str, port: int, key_path: str, cmd: str, timeout: float = 8.0
) -> tuple[int, str]:
    args = CommunityScriptsHub._ssh_base_args(host_url, user, port, key_path) + [cmd]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, out.decode("utf-8", errors="replace") or err.decode("utf-8", errors="replace")
    except Exception as exc:
        return -1, str(exc)


async def discover_docker_containers(service) -> dict[str, Any]:
    """Scan Docker containers across QEMU VMs (via Guest Agent) and Docker LXCs (via optional SSH pct exec)."""
    if service.settings.proxmox_demo_mode:
        return {
            "hosts": [{"vmid": 100, "name": "docker-core", "type": "qemu", "method": "demo", "ok": True, "count": len(DEMO_DOCKER_CONTAINERS)}],
            "containers": list(DEMO_DOCKER_CONTAINERS),
            "summary": {
                "hosts": 1,
                "total": len(DEMO_DOCKER_CONTAINERS),
                "running": sum(1 for c in DEMO_DOCKER_CONTAINERS if c["state"] == "running"),
                "stopped": sum(1 for c in DEMO_DOCKER_CONTAINERS if c["state"] != "running"),
            },
        }

    ins = await service.get_fleet_insights(source="webui")
    guests = [g for g in ins.get("guests", []) if g.get("status") == "running" and not g.get("template")]

    docker_vms = [
        g for g in guests
        if g["type"] == "qemu"
        and (
            "docker" in str(g.get("name", "")).lower()
            or "docker" in str(g.get("tags", "")).lower()
            or any(ip.startswith("172.1") for ip in g.get("secondary_ips") or [])
        )
    ]
    docker_lxcs = [
        g for g in guests
        if g["type"] == "lxc"
        and ("docker" in str(g.get("name", "")).lower() or "docker" in str(g.get("tags", "")).lower())
    ]

    hosts_info: list[dict[str, Any]] = []
    containers: list[dict[str, Any]] = []

    # 1. Query QEMU Docker VMs via QEMU Guest Agent (zero SSH!)
    async def scan_vm(g: dict[str, Any]) -> None:
        vmid = int(g["vmid"])
        name = str(g["name"])
        try:
            res = await service.client.qemu_agent_exec(
                g["node"], vmid, ["sh", "-c", "docker ps -a --format '{{json .}}' 2>/dev/null"]
            )
            out = await service._poll_agent_exec(g["node"], vmid, int(res["pid"]), timeout_sec=8.0)
            if out["exitcode"] == 0:
                found = _parse_docker_json_lines(out["stdout"], vmid, name, "qemu")
                containers.extend(found)
                hosts_info.append({"vmid": vmid, "name": name, "type": "qemu", "method": "qemu-agent", "ok": True, "count": len(found)})
            else:
                hosts_info.append({"vmid": vmid, "name": name, "type": "qemu", "method": "qemu-agent", "ok": False, "count": 0, "note": out["stderr"] or "docker unavailable"})
        except Exception as exc:
            hosts_info.append({"vmid": vmid, "name": name, "type": "qemu", "method": "qemu-agent", "ok": False, "count": 0, "note": str(exc)[:120]})

    # 2. Query LXC Docker hosts via SSH pct exec (optional — gracefully reports if SSH not configured)
    async def scan_lxc(g: dict[str, Any]) -> None:
        vmid = int(g["vmid"])
        name = str(g["name"])
        rc, out = await _ssh_exec_host(
            service.settings.normalized_host,
            service.settings.proxmox_ssh_user,
            service.settings.proxmox_ssh_port,
            service.settings.proxmox_ssh_key_path,
            f"pct exec {vmid} -- docker ps -a --format '{{{{json .}}}}'",
        )
        if rc == 0:
            found = _parse_docker_json_lines(out, vmid, name, "lxc")
            containers.extend(found)
            hosts_info.append({"vmid": vmid, "name": name, "type": "lxc", "method": "ssh-pct", "ok": True, "count": len(found)})
        else:
            hosts_info.append({
                "vmid": vmid,
                "name": name,
                "type": "lxc",
                "method": "ssh-pct",
                "ok": False,
                "count": 0,
                "note": "Configure passwordless SSH key to Proxmox in System → Settings to inspect LXC Docker hosts",
            })

    await asyncio.gather(*(scan_vm(g) for g in docker_vms), *(scan_lxc(g) for g in docker_lxcs))

    containers.sort(key=lambda c: (0 if c["state"] == "running" else 1, c["host_vmid"], c["name"]))
    return {
        "hosts": hosts_info,
        "containers": containers,
        "summary": {
            "hosts": len(hosts_info),
            "total": len(containers),
            "running": sum(1 for c in containers if c["state"] == "running"),
            "stopped": sum(1 for c in containers if c["state"] != "running"),
        },
    }


async def run_docker_container_op(
    service,
    *,
    host_vmid: int,
    container_name: str,
    action: str,
    tail: int = 80,
) -> dict[str, Any]:
    """Run start/stop/restart/logs on a Docker container inside a QEMU VM or LXC."""
    if action not in ("start", "stop", "restart", "logs"):
        raise ValueError(f"Unsupported Docker action: {action}")

    if service.settings.proxmox_demo_mode:
        if action == "logs":
            return {"host_vmid": host_vmid, "container": container_name, "action": "logs", "output": f"[demo] 2026-11-25T03:30:00Z {container_name} listening healthy\n[demo] Ready to serve requests."}
        for c in DEMO_DOCKER_CONTAINERS:
            if c["name"] == container_name:
                c["state"] = "exited" if action == "stop" else "running"
                c["status"] = "Exited (0) just now" if action == "stop" else "Up 2 seconds"
        return {"host_vmid": host_vmid, "container": container_name, "action": action, "output": f"{container_name} {action}ed (demo)"}

    resolved_node, resolved_type = await service.resolve_guest(host_vmid)
    cname = shlex.quote(container_name)
    docker_cmd = f"docker logs --tail {int(tail)} {cname} 2>&1" if action == "logs" else f"docker {action} {cname} 2>&1"

    if resolved_type == "qemu":
        res = await service.client.qemu_agent_exec(resolved_node, host_vmid, ["sh", "-c", docker_cmd])
        out = await service._poll_agent_exec(resolved_node, host_vmid, int(res["pid"]), timeout_sec=15.0)
        return {
            "host_vmid": host_vmid,
            "container": container_name,
            "action": action,
            "exitcode": out["exitcode"],
            "output": (out["stdout"] + ("\n" + out["stderr"] if out["stderr"] else "")).strip(),
        }

    rc, text = await _ssh_exec_host(
        service.settings.normalized_host,
        service.settings.proxmox_ssh_user,
        service.settings.proxmox_ssh_port,
        service.settings.proxmox_ssh_key_path,
        f"pct exec {host_vmid} -- sh -c {shlex.quote(docker_cmd)}",
        timeout=15.0,
    )
    return {"host_vmid": host_vmid, "container": container_name, "action": action, "exitcode": rc, "output": text.strip()}
