"""High-level Proxmox Homelab service layer shared by both MCP Server and WebUI."""

from __future__ import annotations

import asyncio
import base64
import shlex
import time
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

from .audit import AuditLogger
from .client import ProxmoxAPIError, ProxmoxClient
from .community_scripts import CommunityScriptsHub, script_raw_url
from .config import AppSettings, load_settings, save_settings
from .demo_provider import DemoProxmoxClient
from .docker_manager import discover_docker_containers, run_docker_container_op
from .hardware_monitor import fetch_idrac_telemetry
from .truenas_client import fetch_truenas_telemetry, test_truenas_connection as probe_truenas_connection
from .insights import (
    detect_service_url,
    extract_agent_ips,
    extract_tag_ips,
    parse_bind_mounts,
    parse_description_summary,
    parse_nic_entries,
    parse_passthroughs,
    parse_storages_used,
    suggest_free_ips,
)
from .iso_catalog import probe_remote_url, search_web_isos
from .uptime_monitor import probe_guest_services
from .wol import check_tcp_reachable, send_magic_packet


class SafetyPolicyError(ProxmoxAPIError):
    """Raised when an operation is blocked by the configured safety mode."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=403)


class ProxmoxService:
    """Orchestrates Proxmox API calls, safety guardrails, task polling, health analysis, and audit logging."""

    def __init__(
        self,
        settings: AppSettings | None = None,
        audit: AuditLogger | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self.audit = audit or AuditLogger()
        self._live_client = ProxmoxClient(self.settings)
        self._demo_client = DemoProxmoxClient()
        self._insights_cache: tuple[float, dict[str, Any]] | None = None
        self._truenas_cache: tuple[float, dict[str, Any]] | None = None
        self.community = CommunityScriptsHub()

    @property
    def client(self) -> ProxmoxClient | DemoProxmoxClient:
        if self.settings.proxmox_demo_mode:
            return self._demo_client
        return self._live_client

    async def reload_settings(self, new_settings: AppSettings, persist: bool = True) -> AppSettings:
        await self._live_client.close()
        self.settings = new_settings
        self._insights_cache = None
        self._truenas_cache = None
        if persist:
            save_settings(new_settings)
        self._live_client = ProxmoxClient(self.settings)
        return self.settings

    async def close(self) -> None:
        await self._live_client.close()

    # ── Guardrails ───────────────────────────────────────────────────────
    def _enforce_safety(
        self,
        *,
        risk: str,
        action: str,
        confirm: bool = False,
    ) -> None:
        mode = self.settings.safety_mode
        if risk == "read":
            return
        if mode == "read_only":
            raise SafetyPolicyError(
                f"Action '{action}' is blocked because SAFETY_MODE is set to 'read_only'. "
                "Switch to 'safe' or 'full' in Settings to allow write operations."
            )
        if risk == "destructive":
            if not self.settings.allow_destructive and mode != "full":
                raise SafetyPolicyError(
                    f"Destructive action '{action}' is blocked by safety guardrails. "
                    "Enable ALLOW_DESTRUCTIVE=true (or SAFETY_MODE=full) in Settings."
                )
            if not confirm:
                raise SafetyPolicyError(
                    f"Destructive action '{action}' requires explicit confirm=true."
                )

    async def _run_audited(
        self,
        *,
        source: str,
        action: str,
        risk: str,
        target: str,
        params: dict[str, Any] | None,
        confirm: bool,
        coro_fn,
    ) -> Any:
        try:
            self._enforce_safety(risk=risk, action=action, confirm=confirm)
            result = await coro_fn()
            if risk != "read":
                self._insights_cache = None
            upid = result if isinstance(result, str) and result.startswith("UPID:") else None
            if risk != "read" or source == "mcp":
                self.audit.record(
                    source=source,
                    action=action,
                    risk=risk,
                    target=target,
                    params=params,
                    status="ok",
                    upid=upid,
                    detail="Success",
                )
            return result
        except Exception as exc:
            self.audit.record(
                source=source,
                action=action,
                risk=risk,
                target=target,
                params=params,
                status="error",
                detail=str(exc),
            )
            raise

    # ── Connection & Overview ────────────────────────────────────────────
    async def test_connection(self) -> dict[str, Any]:
        if self.settings.proxmox_demo_mode:
            version = await self._demo_client.get_version()
            nodes = await self._demo_client.get_nodes()
            return {
                "connected": True,
                "demo_mode": True,
                "host": self.settings.normalized_host,
                "version": version,
                "node_count": len(nodes),
                "nodes": [n.get("node") for n in nodes],
            }
        version = await self._live_client.get_version()
        nodes = await self._live_client.get_nodes()
        return {
            "connected": True,
            "demo_mode": False,
            "host": self.settings.normalized_host,
            "version": version,
            "node_count": len(nodes),
            "nodes": [n.get("node") for n in nodes],
        }

    async def get_default_node(self, node: str | None = None) -> str:
        if node and node.strip():
            return node.strip()
        nodes = await self.client.get_nodes()
        if not nodes:
            raise ProxmoxAPIError("No Proxmox nodes found in cluster.")
        online = [n for n in nodes if n.get("status") == "online"]
        chosen = online[0] if online else nodes[0]
        return str(chosen["node"])

    async def get_cluster_overview(self, source: str = "webui") -> dict[str, Any]:
        async def _op() -> dict[str, Any]:
            version, nodes, resources = await asyncio.gather(
                self.client.get_version(),
                self.client.get_nodes(),
                self.client.get_cluster_resources(),
            )
            vms = [r for r in resources if r.get("type") == "qemu"]
            lxcs = [r for r in resources if r.get("type") == "lxc"]
            storages = [r for r in resources if r.get("type") == "storage"]

            total_cpu = sum(n.get("maxcpu", 0) for n in nodes)
            used_cpu_cores = sum((n.get("cpu", 0) or 0) * (n.get("maxcpu", 0) or 0) for n in nodes)
            total_mem = sum(n.get("maxmem", 0) for n in nodes)
            used_mem = sum(n.get("mem", 0) for n in nodes)
            total_storage = sum(s.get("maxdisk", 0) for s in storages)
            used_storage = sum(s.get("disk", 0) for s in storages)

            return {
                "demo_mode": self.settings.proxmox_demo_mode,
                "host": self.settings.normalized_host,
                "safety_mode": self.settings.safety_mode,
                "version": version,
                "nodes": nodes,
                "summary": {
                    "nodes_total": len(nodes),
                    "nodes_online": sum(1 for n in nodes if n.get("status") == "online"),
                    "vms_total": len([v for v in vms if not v.get("template")]),
                    "vms_running": sum(1 for v in vms if v.get("status") == "running"),
                    "templates_total": sum(1 for v in vms if v.get("template")),
                    "lxcs_total": len(lxcs),
                    "lxcs_running": sum(1 for c in lxcs if c.get("status") == "running"),
                    "cpu_cores_total": total_cpu,
                    "cpu_percent": round((used_cpu_cores / total_cpu) * 100, 1) if total_cpu else 0.0,
                    "mem_used_bytes": used_mem,
                    "mem_total_bytes": total_mem,
                    "mem_percent": round((used_mem / total_mem) * 100, 1) if total_mem else 0.0,
                    "storage_used_bytes": used_storage,
                    "storage_total_bytes": total_storage,
                    "storage_percent": round((used_storage / total_storage) * 100, 1)
                    if total_storage
                    else 0.0,
                },
            }

        return await self._run_audited(
            source=source,
            action="get_cluster_overview",
            risk="read",
            target="cluster",
            params=None,
            confirm=False,
            coro_fn=_op,
        )

    async def get_node_details(self, node: str | None = None, source: str = "webui") -> dict[str, Any]:
        target_node = await self.get_default_node(node)

        async def _op() -> dict[str, Any]:
            status, services, network, storages = await asyncio.gather(
                self.client.get_node_status(target_node),
                self._safe_call(self.client.get_node_services(target_node), []),
                self._safe_call(self.client.get_node_network(target_node), []),
                self._safe_call(self.client.get_node_storage(target_node), []),
            )
            return {
                "node": target_node,
                "status": status,
                "services": services,
                "network": network,
                "storages": storages,
            }

        return await self._run_audited(
            source=source,
            action="get_node_details",
            risk="read",
            target=f"node/{target_node}",
            params={"node": target_node},
            confirm=False,
            coro_fn=_op,
        )

    async def get_node_rrd(
        self, node: str | None = None, timeframe: str = "hour", source: str = "webui"
    ) -> dict[str, Any]:
        target_node = await self.get_default_node(node)
        if timeframe not in ("hour", "day", "week", "month", "year"):
            timeframe = "hour"

        async def _op() -> dict[str, Any]:
            points = await self.client.get_node_rrd(target_node, timeframe=timeframe)
            return {"node": target_node, "timeframe": timeframe, "points": points}

        return await self._run_audited(
            source=source,
            action="get_node_rrd",
            risk="read",
            target=f"node/{target_node}",
            params={"timeframe": timeframe},
            confirm=False,
            coro_fn=_op,
        )

    # ── Guests (VMs & LXCs) ──────────────────────────────────────────────
    async def list_guests(
        self,
        node: str | None = None,
        guest_type: str | None = None,
        status: str | None = None,
        tag: str | None = None,
        source: str = "webui",
    ) -> list[dict[str, Any]]:
        async def _op() -> list[dict[str, Any]]:
            resources = await self.client.get_cluster_resources(resource_type="vm")
            guests = []
            for r in resources:
                gtype = r.get("type")
                if guest_type and gtype != guest_type:
                    continue
                if node and r.get("node") != node:
                    continue
                if status and r.get("status") != status:
                    continue
                raw_tags = str(r.get("tags") or "")
                tag_list = [t.strip() for t in raw_tags.replace(",", ";").split(";") if t.strip()]
                if tag and tag.lower() not in [t.lower() for t in tag_list]:
                    continue
                r_copy = dict(r)
                r_copy["tag_list"] = tag_list
                r_copy["cpu_percent"] = round((r.get("cpu") or 0) * 100, 1)
                maxmem = r.get("maxmem") or 0
                mem = r.get("mem") or 0
                r_copy["mem_percent"] = round((mem / maxmem) * 100, 1) if maxmem else 0.0
                guests.append(r_copy)
            guests.sort(key=lambda x: int(x.get("vmid", 0)))
            return guests

        return await self._run_audited(
            source=source,
            action="list_guests",
            risk="read",
            target=node or "cluster",
            params={"guest_type": guest_type, "status": status, "tag": tag},
            confirm=False,
            coro_fn=_op,
        )

    async def resolve_guest(
        self, vmid: int, node: str | None = None, guest_type: str | None = None
    ) -> tuple[str, str]:
        """Auto-detect (node, guest_type) for a given VMID so users/LLMs don't have to guess."""
        if node and guest_type in ("qemu", "lxc"):
            return node, guest_type
        resources = await self.client.get_cluster_resources(resource_type="vm")
        for r in resources:
            if int(r.get("vmid", -1)) == int(vmid):
                return str(r["node"]), str(r["type"])
        raise ProxmoxAPIError(f"Guest with VMID {vmid} not found in cluster.", status_code=404)

    async def get_guest_detail(
        self,
        vmid: int,
        node: str | None = None,
        guest_type: str | None = None,
        timeframe: str = "hour",
        source: str = "webui",
    ) -> dict[str, Any]:
        resolved_node, resolved_type = await self.resolve_guest(vmid, node, guest_type)

        async def _op() -> dict[str, Any]:
            if resolved_type == "qemu":
                status_coro = self.client.get_qemu_status(resolved_node, vmid)
                config_coro = self.client.get_qemu_config(resolved_node, vmid)
                rrd_coro = self.client.get_qemu_rrd(resolved_node, vmid, timeframe)
                ips_coro = self._safe_call(self.client.get_qemu_agent_ips(resolved_node, vmid), None)
            else:
                status_coro = self.client.get_lxc_status(resolved_node, vmid)
                config_coro = self.client.get_lxc_config(resolved_node, vmid)
                rrd_coro = self.client.get_lxc_rrd(resolved_node, vmid, timeframe)
                ips_coro = asyncio.sleep(0, result=None)

            status, config, rrd, snapshots, agent_net = await asyncio.gather(
                status_coro,
                config_coro,
                self._safe_call(rrd_coro, []),
                self._safe_call(self.client.list_snapshots(resolved_node, resolved_type, vmid), []),
                ips_coro,
            )

            nics = parse_nic_entries(config if isinstance(config, dict) else {}, resolved_type)
            agent_lan, agent_other = extract_agent_ips(agent_net)
            nic_ips = [n["ip"] for n in nics if n.get("ip")]
            raw_tags = str((config or {}).get("tags") or (status or {}).get("tags") or "")
            tag_list = [t.strip() for t in raw_tags.replace(",", ";").split(";") if t.strip()]
            tag_ips = extract_tag_ips(tag_list)

            ips: list[str] = []
            for ip in (*nic_ips, *agent_lan, *tag_ips):
                if ip and ip not in ips:
                    ips.append(ip)

            primary_ip = ips[0] if ips else None
            guest_name = str((status or {}).get("name") or (config or {}).get("name") or (config or {}).get("hostname") or f"guest-{vmid}")
            saved_bm = self.audit.get_bookmarks().get(int(vmid))
            auto_bm = detect_service_url(guest_name, primary_ip)
            desc_info = parse_description_summary((config or {}).get("description"))

            return {
                "vmid": vmid,
                "node": resolved_node,
                "type": resolved_type,
                "status": status,
                "config": config,
                "rrd": rrd,
                "snapshots": snapshots,
                "ips": ips,
                "secondary_ips": agent_other,
                "nics": nics,
                "bind_mounts": parse_bind_mounts(config if isinstance(config, dict) else {}),
                "storages_used": parse_storages_used(config if isinstance(config, dict) else {}),
                "passthroughs": parse_passthroughs(config if isinstance(config, dict) else {}),
                "description_info": desc_info,
                "bookmark": saved_bm or auto_bm,
                "custom_bookmark": saved_bm,
                "console_url": self.build_console_url(resolved_node, resolved_type, vmid, guest_name),
            }

        return await self._run_audited(
            source=source,
            action="get_guest_detail",
            risk="read",
            target=f"{resolved_type}/{vmid}@{resolved_node}",
            params={"timeframe": timeframe},
            confirm=False,
            coro_fn=_op,
        )

    async def guest_power(
        self,
        vmid: int,
        action: str,
        node: str | None = None,
        guest_type: str | None = None,
        wait: bool = False,
        source: str = "webui",
    ) -> dict[str, Any]:
        action = action.lower().strip()
        allowed = {"start", "stop", "shutdown", "reboot", "suspend", "resume", "reset"}
        if action not in allowed:
            raise ProxmoxAPIError(
                f"Invalid power action '{action}'. Allowed: {', '.join(sorted(allowed))}",
                status_code=400,
            )
        resolved_node, resolved_type = await self.resolve_guest(vmid, node, guest_type)
        if resolved_type == "lxc" and action in ("suspend", "resume", "reset"):
            raise ProxmoxAPIError(
                f"LXC containers do not support '{action}'. Use start/stop/shutdown/reboot.",
                status_code=400,
            )

        async def _op() -> str:
            if resolved_type == "qemu":
                return await self.client.qemu_power_action(resolved_node, vmid, action)
            return await self.client.lxc_power_action(resolved_node, vmid, action)

        upid = await self._run_audited(
            source=source,
            action=f"guest_{action}",
            risk="write",
            target=f"{resolved_type}/{vmid}@{resolved_node}",
            params={"action": action},
            confirm=False,
            coro_fn=_op,
        )
        task_result = await self.wait_for_task(resolved_node, upid) if wait and upid else None
        return {
            "vmid": vmid,
            "node": resolved_node,
            "type": resolved_type,
            "action": action,
            "upid": upid,
            "task": task_result,
        }

    async def update_guest_config(
        self,
        vmid: int,
        *,
        cores: int | None = None,
        memory_mb: int | None = None,
        onboot: bool | None = None,
        tags: str | None = None,
        description: str | None = None,
        name: str | None = None,
        node: str | None = None,
        guest_type: str | None = None,
        source: str = "webui",
    ) -> dict[str, Any]:
        resolved_node, resolved_type = await self.resolve_guest(vmid, node, guest_type)
        params: dict[str, Any] = {}
        if cores is not None:
            params["cores"] = int(cores)
        if memory_mb is not None:
            params["memory"] = int(memory_mb)
        if onboot is not None:
            params["onboot"] = 1 if onboot else 0
        if tags is not None:
            params["tags"] = tags
        if description is not None:
            params["description"] = description
        if name is not None:
            params["name" if resolved_type == "qemu" else "hostname"] = name

        if not params:
            raise ProxmoxAPIError("No configuration fields provided to update.", status_code=400)

        async def _op() -> Any:
            if resolved_type == "qemu":
                return await self.client.update_qemu_config(resolved_node, vmid, params)
            return await self.client.update_lxc_config(resolved_node, vmid, params)

        await self._run_audited(
            source=source,
            action="update_guest_config",
            risk="write",
            target=f"{resolved_type}/{vmid}@{resolved_node}",
            params=params,
            confirm=False,
            coro_fn=_op,
        )
        return {
            "vmid": vmid,
            "node": resolved_node,
            "type": resolved_type,
            "updated": params,
        }

    # ── Provisioning (Create & Clone) ────────────────────────────────────
    async def get_provisioning_options(self, node: str | None = None) -> dict[str, Any]:
        target_node = await self.get_default_node(node)
        next_vmid, storages, network, resources = await asyncio.gather(
            self.client.get_next_vmid(),
            self._safe_call(self.client.get_node_storage(target_node), []),
            self._safe_call(self.client.get_node_network(target_node), []),
            self._safe_call(self.client.get_cluster_resources(resource_type="vm"), []),
        )

        isos: list[dict[str, Any]] = []
        vztmpls: list[dict[str, Any]] = []
        disk_storages: list[dict[str, Any]] = []
        backup_storages: list[dict[str, Any]] = []

        for st in storages:
            if not st.get("active", 1) or not st.get("enabled", 1):
                continue
            content_types = {c.strip() for c in str(st.get("content", "")).split(",") if c.strip()}
            if content_types & {"images", "rootdir"}:
                disk_storages.append(st)
            if "backup" in content_types:
                backup_storages.append(st)
            if "iso" in content_types:
                items = await self._safe_call(
                    self.client.get_storage_content(target_node, st["storage"], "iso"), []
                )
                isos.extend(items)
            if "vztmpl" in content_types:
                items = await self._safe_call(
                    self.client.get_storage_content(target_node, st["storage"], "vztmpl"), []
                )
                vztmpls.extend(items)

        bridges = [n["iface"] for n in network if n.get("type") == "bridge"] or ["vmbr0"]
        templates = [r for r in resources if r.get("template") and r.get("node") == target_node]

        return {
            "node": target_node,
            "next_vmid": next_vmid,
            "disk_storages": disk_storages,
            "backup_storages": backup_storages,
            "isos": isos,
            "vztmpls": vztmpls,
            "bridges": bridges,
            "vm_templates": templates,
        }

    async def create_vm(
        self,
        *,
        name: str,
        node: str | None = None,
        vmid: int | None = None,
        cores: int = 2,
        sockets: int = 1,
        memory_mb: int = 2048,
        disk_storage: str = "local-lvm",
        disk_size_gb: int = 32,
        iso: str | None = None,
        bridge: str = "vmbr0",
        vlan_tag: int | None = None,
        ostype: str = "l26",
        bios: str = "seabios",
        cpu_type: str = "host",
        qemu_agent: bool = True,
        onboot: bool = False,
        start_after_create: bool = False,
        tags: str = "",
        description: str = "",
        wait: bool = False,
        source: str = "webui",
    ) -> dict[str, Any]:
        target_node = await self.get_default_node(node)
        assigned_vmid = int(vmid) if vmid else await self.client.get_next_vmid()

        net0 = f"virtio,bridge={bridge}"
        if vlan_tag:
            net0 += f",tag={vlan_tag}"

        params: dict[str, Any] = {
            "vmid": assigned_vmid,
            "name": name,
            "cores": int(cores),
            "sockets": int(sockets),
            "memory": int(memory_mb),
            "ostype": ostype,
            "cpu": cpu_type,
            "bios": bios,
            "scsihw": "virtio-scsi-pci",
            "scsi0": f"{disk_storage}:{int(disk_size_gb)},discard=on,ssd=1",
            "net0": net0,
            "agent": "1" if qemu_agent else "0",
            "onboot": 1 if onboot else 0,
            "boot": "order=scsi0;ide2;net0" if not iso else "order=ide2;scsi0;net0",
        }
        if iso:
            params["ide2"] = f"{iso},media=cdrom"
        if tags:
            params["tags"] = tags
        if description:
            params["description"] = description
        if start_after_create:
            params["start"] = 1

        upid = await self._run_audited(
            source=source,
            action="create_vm",
            risk="write",
            target=f"qemu/{assigned_vmid}@{target_node}",
            params=params,
            confirm=False,
            coro_fn=lambda: self.client.create_qemu(target_node, params),
        )
        task_result = await self.wait_for_task(target_node, upid) if wait and upid else None
        return {
            "vmid": assigned_vmid,
            "name": name,
            "node": target_node,
            "type": "qemu",
            "upid": upid,
            "task": task_result,
        }

    async def create_lxc(
        self,
        *,
        hostname: str,
        ostemplate: str,
        node: str | None = None,
        vmid: int | None = None,
        cores: int = 1,
        memory_mb: int = 1024,
        swap_mb: int = 512,
        disk_storage: str = "local-lvm",
        disk_size_gb: int = 8,
        bridge: str = "vmbr0",
        ip_config: str = "dhcp",
        gateway: str | None = None,
        password: str | None = None,
        ssh_public_keys: str | None = None,
        unprivileged: bool = True,
        nesting: bool = True,
        onboot: bool = True,
        start_after_create: bool = True,
        tags: str = "",
        description: str = "",
        wait: bool = False,
        source: str = "webui",
    ) -> dict[str, Any]:
        target_node = await self.get_default_node(node)
        assigned_vmid = int(vmid) if vmid else await self.client.get_next_vmid()

        net0 = f"name=eth0,bridge={bridge},ip={ip_config}"
        if gateway and ip_config != "dhcp":
            net0 += f",gw={gateway}"

        params: dict[str, Any] = {
            "vmid": assigned_vmid,
            "hostname": hostname,
            "ostemplate": ostemplate,
            "cores": int(cores),
            "memory": int(memory_mb),
            "swap": int(swap_mb),
            "rootfs": f"{disk_storage}:{int(disk_size_gb)}",
            "net0": net0,
            "unprivileged": 1 if unprivileged else 0,
            "onboot": 1 if onboot else 0,
            "start": 1 if start_after_create else 0,
        }
        if nesting:
            params["features"] = "nesting=1"
        if password:
            params["password"] = password
        if ssh_public_keys:
            params["ssh-public-keys"] = ssh_public_keys
        if tags:
            params["tags"] = tags
        if description:
            params["description"] = description

        upid = await self._run_audited(
            source=source,
            action="create_lxc",
            risk="write",
            target=f"lxc/{assigned_vmid}@{target_node}",
            params=params,
            confirm=False,
            coro_fn=lambda: self.client.create_lxc(target_node, params),
        )
        task_result = await self.wait_for_task(target_node, upid) if wait and upid else None
        return {
            "vmid": assigned_vmid,
            "hostname": hostname,
            "node": target_node,
            "type": "lxc",
            "upid": upid,
            "task": task_result,
        }

    async def clone_guest(
        self,
        *,
        source_vmid: int,
        new_name: str,
        new_vmid: int | None = None,
        full_clone: bool = True,
        target_storage: str | None = None,
        node: str | None = None,
        guest_type: str | None = None,
        wait: bool = False,
        source: str = "webui",
    ) -> dict[str, Any]:
        resolved_node, resolved_type = await self.resolve_guest(source_vmid, node, guest_type)
        assigned_id = int(new_vmid) if new_vmid else await self.client.get_next_vmid()
        params: dict[str, Any] = {
            "newid": assigned_id,
            "full": 1 if full_clone else 0,
        }
        if resolved_type == "qemu":
            params["name"] = new_name
        else:
            params["hostname"] = new_name
        if target_storage:
            params["storage"] = target_storage

        async def _op() -> str:
            if resolved_type == "qemu":
                return await self.client.clone_qemu(resolved_node, source_vmid, params)
            return await self.client.clone_lxc(resolved_node, source_vmid, params)

        upid = await self._run_audited(
            source=source,
            action="clone_guest",
            risk="write",
            target=f"{resolved_type}/{source_vmid}->{assigned_id}@{resolved_node}",
            params=params,
            confirm=False,
            coro_fn=_op,
        )
        task_result = await self.wait_for_task(resolved_node, upid) if wait and upid else None
        return {
            "source_vmid": source_vmid,
            "new_vmid": assigned_id,
            "new_name": new_name,
            "node": resolved_node,
            "type": resolved_type,
            "upid": upid,
            "task": task_result,
        }

    async def delete_guest(
        self,
        vmid: int,
        *,
        confirm: bool = False,
        purge: bool = True,
        node: str | None = None,
        guest_type: str | None = None,
        source: str = "webui",
    ) -> dict[str, Any]:
        resolved_node, resolved_type = await self.resolve_guest(vmid, node, guest_type)

        async def _op() -> str:
            if resolved_type == "qemu":
                return await self.client.delete_qemu(resolved_node, vmid, purge=purge)
            return await self.client.delete_lxc(resolved_node, vmid, purge=purge)

        upid = await self._run_audited(
            source=source,
            action="delete_guest",
            risk="destructive",
            target=f"{resolved_type}/{vmid}@{resolved_node}",
            params={"purge": purge, "confirm": confirm},
            confirm=confirm,
            coro_fn=_op,
        )
        return {
            "vmid": vmid,
            "node": resolved_node,
            "type": resolved_type,
            "upid": upid,
        }

    # ── Snapshots & Backups ──────────────────────────────────────────────
    async def list_snapshots(
        self, vmid: int, node: str | None = None, guest_type: str | None = None, source: str = "webui"
    ) -> list[dict[str, Any]]:
        resolved_node, resolved_type = await self.resolve_guest(vmid, node, guest_type)
        return await self._run_audited(
            source=source,
            action="list_snapshots",
            risk="read",
            target=f"{resolved_type}/{vmid}@{resolved_node}",
            params=None,
            confirm=False,
            coro_fn=lambda: self.client.list_snapshots(resolved_node, resolved_type, vmid),
        )

    async def create_snapshot(
        self,
        vmid: int,
        snapname: str,
        description: str = "",
        vmstate: bool = False,
        node: str | None = None,
        guest_type: str | None = None,
        wait: bool = False,
        source: str = "webui",
    ) -> dict[str, Any]:
        resolved_node, resolved_type = await self.resolve_guest(vmid, node, guest_type)
        upid = await self._run_audited(
            source=source,
            action="create_snapshot",
            risk="write",
            target=f"{resolved_type}/{vmid}@{resolved_node}",
            params={"snapname": snapname, "description": description, "vmstate": vmstate},
            confirm=False,
            coro_fn=lambda: self.client.create_snapshot(
                resolved_node, resolved_type, vmid, snapname, description, vmstate
            ),
        )
        task_result = await self.wait_for_task(resolved_node, upid) if wait and upid else None
        return {"vmid": vmid, "snapname": snapname, "upid": upid, "task": task_result}

    async def rollback_snapshot(
        self,
        vmid: int,
        snapname: str,
        confirm: bool = False,
        node: str | None = None,
        guest_type: str | None = None,
        wait: bool = False,
        source: str = "webui",
    ) -> dict[str, Any]:
        resolved_node, resolved_type = await self.resolve_guest(vmid, node, guest_type)
        upid = await self._run_audited(
            source=source,
            action="rollback_snapshot",
            risk="destructive",
            target=f"{resolved_type}/{vmid}@{resolved_node}",
            params={"snapname": snapname, "confirm": confirm},
            confirm=confirm,
            coro_fn=lambda: self.client.rollback_snapshot(
                resolved_node, resolved_type, vmid, snapname
            ),
        )
        task_result = await self.wait_for_task(resolved_node, upid) if wait and upid else None
        return {"vmid": vmid, "snapname": snapname, "upid": upid, "task": task_result}

    async def delete_snapshot(
        self,
        vmid: int,
        snapname: str,
        confirm: bool = False,
        node: str | None = None,
        guest_type: str | None = None,
        source: str = "webui",
    ) -> dict[str, Any]:
        resolved_node, resolved_type = await self.resolve_guest(vmid, node, guest_type)
        upid = await self._run_audited(
            source=source,
            action="delete_snapshot",
            risk="destructive",
            target=f"{resolved_type}/{vmid}@{resolved_node}",
            params={"snapname": snapname, "confirm": confirm},
            confirm=confirm,
            coro_fn=lambda: self.client.delete_snapshot(
                resolved_node, resolved_type, vmid, snapname
            ),
        )
        return {"vmid": vmid, "snapname": snapname, "upid": upid}

    async def trigger_backup(
        self,
        vmid: int,
        storage: str | None = None,
        mode: str = "snapshot",
        compress: str = "zstd",
        notes: str = "",
        node: str | None = None,
        wait: bool = False,
        source: str = "webui",
    ) -> dict[str, Any]:
        resolved_node, _ = await self.resolve_guest(vmid, node)
        params: dict[str, Any] = {
            "vmid": str(vmid),
            "mode": mode,
            "compress": compress,
        }
        if storage:
            params["storage"] = storage
        if notes:
            params["notes-template"] = notes

        upid = await self._run_audited(
            source=source,
            action="trigger_backup",
            risk="write",
            target=f"vzdump/{vmid}@{resolved_node}",
            params=params,
            confirm=False,
            coro_fn=lambda: self.client.run_vzdump(resolved_node, params),
        )
        task_result = await self.wait_for_task(resolved_node, upid) if wait and upid else None
        return {"vmid": vmid, "node": resolved_node, "upid": upid, "task": task_result}

    # ── Storage, Disks, Tasks ────────────────────────────────────────────
    async def get_storage_and_disks(
        self, node: str | None = None, source: str = "webui"
    ) -> dict[str, Any]:
        target_node = await self.get_default_node(node)

        async def _op() -> dict[str, Any]:
            storages, disks, zfs, backup_jobs = await asyncio.gather(
                self._safe_call(self.client.get_node_storage(target_node), []),
                self._safe_call(self.client.get_node_disks(target_node), []),
                self._safe_call(self.client.get_node_zfs(target_node), []),
                self._safe_call(self.client.get_cluster_backups(), []),
            )
            backups: list[dict[str, Any]] = []
            isos: list[dict[str, Any]] = []
            templates: list[dict[str, Any]] = []

            for st in storages:
                if not st.get("active", 1):
                    continue
                ctypes = {c.strip() for c in str(st.get("content", "")).split(",")}
                if "backup" in ctypes:
                    b = await self._safe_call(
                        self.client.get_storage_content(target_node, st["storage"], "backup"), []
                    )
                    backups.extend(b)
                if "iso" in ctypes:
                    i = await self._safe_call(
                        self.client.get_storage_content(target_node, st["storage"], "iso"), []
                    )
                    isos.extend(i)
                if "vztmpl" in ctypes:
                    t = await self._safe_call(
                        self.client.get_storage_content(target_node, st["storage"], "vztmpl"), []
                    )
                    templates.extend(t)

            backups.sort(key=lambda x: x.get("ctime", 0), reverse=True)
            return {
                "node": target_node,
                "storages": storages,
                "disks": disks,
                "zfs_pools": zfs,
                "backup_jobs": backup_jobs,
                "backups": backups[:40],
                "isos": isos,
                "templates": templates,
            }

        return await self._run_audited(
            source=source,
            action="get_storage_and_disks",
            risk="read",
            target=f"storage@{target_node}",
            params=None,
            confirm=False,
            coro_fn=_op,
        )

    async def list_tasks(
        self,
        node: str | None = None,
        limit: int = 35,
        vmid: int | None = None,
        errors_only: bool = False,
        source: str = "webui",
    ) -> dict[str, Any]:
        target_node = await self.get_default_node(node)
        tasks = await self._run_audited(
            source=source,
            action="list_tasks",
            risk="read",
            target=f"tasks@{target_node}",
            params={"limit": limit, "vmid": vmid, "errors_only": errors_only},
            confirm=False,
            coro_fn=lambda: self.client.get_node_tasks(
                target_node, limit=limit, vmid=vmid, errors_only=errors_only
            ),
        )
        return {"node": target_node, "tasks": tasks}

    async def get_task_detail(
        self, upid: str, node: str | None = None, source: str = "webui"
    ) -> dict[str, Any]:
        target_node = node or (upid.split(":")[1] if upid.startswith("UPID:") and len(upid.split(":")) > 1 else await self.get_default_node())
        status, log_lines = await asyncio.gather(
            self.client.get_task_status(target_node, upid),
            self.client.get_task_log(target_node, upid),
        )
        return {
            "node": target_node,
            "upid": upid,
            "status": status,
            "log": [line.get("t", "") for line in log_lines],
        }

    async def wait_for_task(
        self, node: str, upid: str, timeout_sec: float = 60.0, poll_interval: float = 1.0
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            st = await self.client.get_task_status(node, upid)
            if st.get("status") == "stopped":
                return st
            await asyncio.sleep(poll_interval)
        return {"upid": upid, "status": "running", "note": "Timed out waiting for completion"}

    # ── Homelab Health Advisor ───────────────────────────────────────────
    async def analyze_homelab_health(self, source: str = "webui") -> dict[str, Any]:
        async def _op() -> dict[str, Any]:
            nodes = await self.client.get_nodes()
            resources = await self.client.get_cluster_resources()
            guests = [r for r in resources if r.get("type") in ("qemu", "lxc") and not r.get("template")]
            storages = [r for r in resources if r.get("type") == "storage"]

            findings: list[dict[str, Any]] = []
            score = 100

            def add_finding(
                severity: str,
                category: str,
                title: str,
                detail: str,
                recommendation: str,
                penalty: int,
                **extra: Any,
            ) -> None:
                nonlocal score
                score = max(0, score - penalty)
                item = {
                    "severity": severity,
                    "category": category,
                    "title": title,
                    "detail": detail,
                    "recommendation": recommendation,
                }
                item.update(extra)
                findings.append(item)

            # 1. Node health, pressure & RootFS
            for n in nodes:
                nname = n.get("node", "unknown")
                if n.get("status") != "online":
                    add_finding(
                        "critical",
                        "node",
                        f"Node {nname} is offline",
                        f"Status reported as '{n.get('status')}'.",
                        "Inspect host network/power connectivity and pve-cluster service.",
                        35,
                    )
                    continue
                cpu_pct = (n.get("cpu") or 0) * 100
                mem_pct = ((n.get("mem") or 0) / (n.get("maxmem") or 1)) * 100
                if cpu_pct >= 85:
                    add_finding(
                        "warning",
                        "compute",
                        f"High CPU load on {nname} ({cpu_pct:.1f}%)",
                        "Sustained host CPU saturation can cause guest latency spikes.",
                        "Inspect top CPU guests or rebalance vCPU allocations.",
                        12,
                    )
                if mem_pct >= 85:
                    add_finding(
                        "warning",
                        "memory",
                        f"High RAM pressure on {nname} ({mem_pct:.1f}%)",
                        "Host memory is approaching capacity and risks OOM kills.",
                        "Reduce memory limits on idle VMs/LXCs or enable ballooning.",
                        15,
                    )

            # 2. Storage capacity warnings
            for st in storages:
                maxdisk = st.get("maxdisk") or 0
                used = st.get("disk") or 0
                if maxdisk <= 0:
                    continue
                pct = (used / maxdisk) * 100
                st_name = st.get("storage")
                if pct >= 90:
                    add_finding(
                        "critical",
                        "storage",
                        f"Storage '{st_name}' critically full ({pct:.1f}%)",
                        f"Pool on {st.get('node')} has less than 10% free space.",
                        "Prune old backups, logs, ISOs, or expand storage immediately.",
                        22,
                        action_kind="storage_files",
                        action_storage=st_name,
                    )
                elif pct >= 80:
                    add_finding(
                        "warning",
                        "storage",
                        f"Storage '{st_name}' above 80% ({pct:.1f}%)",
                        f"Pool '{st_name}' on {st.get('node')} is at {pct:.1f}% utilization.",
                        "Keep ZFS pools under 80% for optimal write performance; audit old ISOs, templates, and backups.",
                        10,
                        action_kind="storage_files",
                        action_storage=st_name,
                    )

            # 3. Node-level disks (SMART + remaining life), ZFS fragmentation, failed tasks, and backups
            primary_node = await self.get_default_node()
            disks, zfs_pools, failed_tasks, backup_jobs = await asyncio.gather(
                self._safe_call(self.client.get_node_disks(primary_node), []),
                self._safe_call(self.client.get_node_zfs(primary_node), []),
                self._safe_call(self.client.get_node_tasks(primary_node, limit=25, errors_only=True), []),
                self._safe_call(self.client.get_cluster_backups(), []),
            )

            for d in disks:
                health = str(d.get("health", "")).upper()
                if health and health not in ("PASSED", "OK", "UNKNOWN"):
                    add_finding(
                        "critical",
                        "disk",
                        f"SMART warning on {d.get('devpath')} ({d.get('model')})",
                        f"SMART health reported as '{health}'.",
                        "Back up workloads on this disk and prepare a replacement drive.",
                        25,
                    )
                # Proxmox VE reports SSD `wearout` as % LIFE REMAINING (100 = new, 0 = worn out)
                wear_remaining = d.get("wearout")
                if isinstance(wear_remaining, (int, float)):
                    if wear_remaining <= 10:
                        add_finding(
                            "critical",
                            "disk",
                            f"Critical SSD wear on {d.get('devpath')} ({wear_remaining:.0f}% life left)",
                            f"Drive {d.get('model')} has only {wear_remaining:.0f}% rated endurance remaining.",
                            "Replace this SSD immediately and verify ZFS/backup redundancy.",
                            18,
                        )
                    elif wear_remaining <= 20:
                        add_finding(
                            "warning",
                            "disk",
                            f"Low SSD life remaining on {d.get('devpath')} ({wear_remaining:.0f}% left)",
                            f"Drive {d.get('model')} has {wear_remaining:.0f}% rated endurance remaining.",
                            "Plan proactive SSD replacement before endurance exhaustion.",
                            8,
                        )

            for zp in zfs_pools:
                zhealth = str(zp.get("health", "")).upper()
                if zhealth and zhealth != "ONLINE":
                    add_finding(
                        "critical",
                        "zfs",
                        f"ZFS pool '{zp.get('name')}' state is {zhealth}",
                        "Pool redundancy or drive health is degraded.",
                        "Run `zpool status` and inspect disk SMART status immediately.",
                        25,
                    )
                frag = zp.get("frag")
                if isinstance(frag, (int, float)) and frag >= 50:
                    add_finding(
                        "info",
                        "zfs",
                        f"ZFS pool '{zp.get('name')}' fragmentation is {frag:.0f}%",
                        f"High fragmentation on '{zp.get('name')}' can increase I/O latency under heavy write/torrent workloads.",
                        "Monitor pool free space (keep >20% free) or rebalance high-churn disks across pools.",
                        4,
                    )

            if failed_tasks:
                recent_fail = failed_tasks[0]
                add_finding(
                    "warning",
                    "tasks",
                    f"{len(failed_tasks)} recent failed task(s) on {primary_node}",
                    f"Latest failure: {recent_fail.get('type')} (ID {recent_fail.get('id') or 'host'}) — {recent_fail.get('status')}",
                    "Review task console logs in the Tasks tab to resolve recurring failures.",
                    8,
                    action_upid=recent_fail.get("upid"),
                    action_node=recent_fail.get("node") or primary_node,
                )

            # 4. Backup coverage check
            if not backup_jobs:
                add_finding(
                    "warning",
                    "backup",
                    "No scheduled cluster backup jobs detected",
                    "No PVE-level backup schedules found under /cluster/backup (verify if backups are scheduled externally or manually).",
                    "Configure a scheduled vzdump / Proxmox Backup Server job for critical VMs and LXCs.",
                    10,
                    action_kind="schedule_backups",
                )
            else:
                covered_vmids: set[str] = set()
                has_all = False
                for job in backup_jobs:
                    if not job.get("enabled", 1):
                        continue
                    if job.get("all"):
                        has_all = True
                    vmid_str = str(job.get("vmid", ""))
                    for item in vmid_str.replace(" ", ",").split(","):
                        if item.strip():
                            covered_vmids.add(item.strip())
                if not has_all:
                    uncovered = [
                        f"{g.get('vmid')} ({g.get('name')})"
                        for g in guests
                        if g.get("status") == "running" and str(g.get("vmid")) not in covered_vmids
                    ]
                    if uncovered:
                        add_finding(
                            "info",
                            "backup",
                            f"{len(uncovered)} running guest(s) excluded from backup schedule",
                            f"Not in scheduled backup list: {', '.join(uncovered[:6])}",
                            "Add critical running guests to a scheduled backup job or verify they are intentionally stateless.",
                            4,
                        )

            # 5. Bare-Metal TrueNAS Storage Health (when configured)
            truenas_pools_checked = 0
            if self.settings.truenas_configured:
                tn = await self._fetch_truenas_cached(force_refresh=False)
                if not tn.get("connected"):
                    add_finding(
                        "warning",
                        "truenas",
                        "TrueNAS host unreachable",
                        str(tn.get("error") or f"Unable to connect to TrueNAS at {self.settings.truenas_host}."),
                        "Verify TrueNAS network connectivity and API key in System → MCP & Settings.",
                        12,
                        action_kind="truenas",
                    )
                else:
                    tn_host = (tn.get("system") or {}).get("hostname") or "TrueNAS"
                    tn_pools = tn.get("pools") or []
                    truenas_pools_checked = len(tn_pools)
                    for tp in tn_pools:
                        tp_name = tp.get("name", "pool")
                        tp_status = str(tp.get("status") or "UNKNOWN").upper()
                        err_cnt = int(tp.get("disks_with_errors") or 0)
                        disks_tot = int(tp.get("disks_total") or 0)
                        if tp_status != "ONLINE" or not tp.get("healthy", True):
                            err_note = f" ({err_cnt} of {disks_tot} disks with errors)" if err_cnt else ""
                            add_finding(
                                "critical",
                                "truenas",
                                f"TrueNAS pool '{tp_name}' is {tp_status}{err_note}",
                                f"Host {tn_host} reported pool '{tp_name}' in {tp_status} state.",
                                "Inspect errored drives, SMART stats, cables/HBA, and ZFS scrub status on TrueNAS immediately.",
                                25,
                                action_kind="truenas",
                            )
                        elif err_cnt > 0:
                            add_finding(
                                "warning",
                                "truenas",
                                f"TrueNAS pool '{tp_name}' has {err_cnt} disk(s) with ZFS errors",
                                f"Pool '{tp_name}' on {tn_host} is ONLINE but {err_cnt} of {disks_tot} disk(s) show read/write/checksum errors.",
                                "Inspect disk SMART health and run a ZFS scrub on TrueNAS.",
                                12,
                                action_kind="truenas",
                            )

                        tp_pct = float(tp.get("used_pct") or 0.0)
                        if tp_pct >= 90:
                            add_finding(
                                "critical",
                                "truenas",
                                f"TrueNAS pool '{tp_name}' critically full ({tp_pct:.1f}%)",
                                f"Pool '{tp_name}' on {tn_host} has less than 10% free space.",
                                "Prune old media/snapshots or expand the ZFS pool to avoid severe fragmentation and write stalls.",
                                18,
                                action_kind="truenas",
                            )
                        elif tp_pct >= 80:
                            add_finding(
                                "warning",
                                "truenas",
                                f"TrueNAS pool '{tp_name}' above 80% ({tp_pct:.1f}%)",
                                f"Pool '{tp_name}' on {tn_host} is at {tp_pct:.1f}% capacity.",
                                "Keep ZFS pools under 80% utilization for healthy copy-on-write performance.",
                                8,
                                action_kind="truenas",
                            )

                    hot_disks = [
                        d for d in (tn.get("disks") or [])
                        if isinstance(d.get("temperature"), (int, float)) and d["temperature"] >= 48
                    ]
                    if hot_disks:
                        hottest = max(hot_disks, key=lambda x: x["temperature"])
                        sev = "critical" if hottest["temperature"] >= 55 else "warning"
                        add_finding(
                            sev,
                            "truenas",
                            f"TrueNAS hot drive(s): {hottest['name']} at {hottest['temperature']}°C",
                            f"{len(hot_disks)} drive(s) on {tn_host} are at or above 48°C.",
                            "Check chassis airflow, fan curves, and drive bay cooling.",
                            14 if sev == "critical" else 6,
                            action_kind="truenas",
                        )

                    tn_summary = tn.get("summary") or {}
                    crit_al = int(tn_summary.get("critical_alerts") or 0)
                    if crit_al > 0:
                        first_al = next(
                            (a for a in (tn.get("alerts") or []) if a.get("level") in ("CRITICAL", "ALERT", "EMERGENCY", "ERROR")),
                            {},
                        )
                        add_finding(
                            "critical",
                            "truenas",
                            f"TrueNAS has {crit_al} active critical alert(s)",
                            str(first_al.get("message") or "Critical alert reported by TrueNAS.")[:220],
                            "Open Storage → TrueNAS Host to inspect all active TrueNAS alerts.",
                            15,
                            action_kind="truenas",
                        )

            status_label = "healthy" if score >= 85 else ("degraded" if score >= 60 else "critical")
            return {
                "score": score,
                "status": status_label,
                "checked_at": int(time.time()),
                "findings": findings,
                "stats": {
                    "nodes_checked": len(nodes),
                    "guests_checked": len(guests),
                    "storages_checked": len(storages),
                    "disks_checked": len(disks),
                    "zfs_pools_checked": len(zfs_pools),
                    "truenas_pools_checked": truenas_pools_checked,
                    "backup_jobs": len(backup_jobs),
                },
            }

        return await self._run_audited(
            source=source,
            action="analyze_homelab_health",
            risk="read",
            target="homelab",
            params=None,
            confirm=False,
            coro_fn=_op,
        )

    # ── Console URL, Bookmarks, Fleet Insights & Bulk Ops ────────────────
    def build_console_url(
        self, node: str, guest_type: str, vmid: int, name: str = ""
    ) -> str:
        console_kind = "kvm" if guest_type == "qemu" else "lxc"
        params = urlencode(
            {
                "console": console_kind,
                "novnc" if guest_type == "qemu" else "xtermjs": "1",
                "vmid": str(vmid),
                "vmname": name or str(vmid),
                "node": node,
                "resize": "off",
                "cmd": "",
            }
        )
        return f"{self.settings.normalized_host}/?{params}"

    def set_guest_bookmark(self, vmid: int, url: str, label: str = "") -> dict[str, Any] | None:
        self._insights_cache = None
        return self.audit.set_bookmark(vmid, url, label)

    async def get_fleet_insights(self, source: str = "webui", force: bool = False) -> dict[str, Any]:
        """Rich homelab fleet sweep: IPAM/network matrix, PBS backup coverage, capacity/right-sizing, and storage/mount mapping."""
        if (
            not force
            and self._insights_cache is not None
            and (time.monotonic() - self._insights_cache[0]) < 15.0
        ):
            return self._insights_cache[1]

        async def _op() -> dict[str, Any]:
            nodes, resources = await asyncio.gather(
                self.client.get_nodes(),
                self.client.get_cluster_resources(),
            )
            primary_node = await self.get_default_node()
            raw_guests = [r for r in resources if r.get("type") in ("qemu", "lxc")]
            raw_guests.sort(key=lambda x: int(x.get("vmid", 0)))

            network, storages, zfs_pools = await asyncio.gather(
                self._safe_call(self.client.get_node_network(primary_node), []),
                self._safe_call(self.client.get_node_storage(primary_node), []),
                self._safe_call(self.client.get_node_zfs(primary_node), []),
            )

            # Gather all backup archives across backup-enabled storages
            all_backups: list[dict[str, Any]] = []
            for st in storages:
                if not st.get("active", 1):
                    continue
                ctypes = {c.strip() for c in str(st.get("content", "")).split(",")}
                if "backup" in ctypes:
                    b_list = await self._safe_call(
                        self.client.get_storage_content(primary_node, st["storage"], "backup"), []
                    )
                    for b in b_list:
                        b_copy = dict(b)
                        b_copy["_storage"] = st["storage"]
                        all_backups.append(b_copy)

            backups_by_vmid: dict[int, list[dict[str, Any]]] = {}
            for b in all_backups:
                b_vmid = b.get("vmid")
                if b_vmid is None:
                    # Try parsing volid like pbs:backup/vm/100/...
                    parts = str(b.get("volid", "")).split("/")
                    for p in parts:
                        if p.isdigit():
                            b_vmid = int(p)
                            break
                if b_vmid is not None:
                    backups_by_vmid.setdefault(int(b_vmid), []).append(b)
            for bl in backups_by_vmid.values():
                bl.sort(key=lambda x: x.get("ctime", 0), reverse=True)

            bookmarks = self.audit.get_bookmarks()
            uptime_map = self.audit.get_uptime_summary()
            sem = asyncio.Semaphore(10)

            async def enrich_one(g: dict[str, Any]) -> dict[str, Any]:
                vmid = int(g["vmid"])
                gnode = str(g["node"])
                gtype = str(g["type"])
                async with sem:
                    if gtype == "qemu":
                        cfg = await self._safe_call(self.client.get_qemu_config(gnode, vmid), {})
                        agent_enabled = bool(cfg.get("agent")) and not str(cfg.get("agent")).startswith("0")
                        if g.get("status") == "running" and agent_enabled:
                            agent_net = await self._safe_call(
                                asyncio.wait_for(self.client.get_qemu_agent_ips(gnode, vmid), timeout=2.5),
                                None,
                            )
                        else:
                            agent_net = None
                    else:
                        cfg = await self._safe_call(self.client.get_lxc_config(gnode, vmid), {})
                        agent_net = None

                cfg = cfg if isinstance(cfg, dict) else {}
                raw_tags = str(g.get("tags") or cfg.get("tags") or "")
                tag_list = [t.strip() for t in raw_tags.replace(",", ";").split(";") if t.strip()]
                nics = parse_nic_entries(cfg, gtype)
                agent_lan, agent_other = extract_agent_ips(agent_net)
                nic_ips = [n["ip"] for n in nics if n.get("ip")]
                tag_ips = extract_tag_ips(tag_list)

                # Attach agent/tag IPs to NICs without static IPs when possible
                if not nic_ips and (agent_lan or tag_ips) and nics:
                    inferred = agent_lan or tag_ips
                    for idx, ip_val in enumerate(inferred):
                        if idx < len(nics) and not nics[idx]["ip"]:
                            nics[idx]["ip"] = ip_val
                            nics[idx]["inferred"] = True

                all_ips: list[str] = []
                for ip_val in (*nic_ips, *agent_lan, *tag_ips):
                    if ip_val and ip_val not in all_ips:
                        all_ips.append(ip_val)
                primary_ip = all_ips[0] if all_ips else None

                guest_name = str(g.get("name") or cfg.get("name") or cfg.get("hostname") or f"guest-{vmid}")
                saved_bm = bookmarks.get(vmid)
                auto_bm = detect_service_url(guest_name, primary_ip)
                active_bm = saved_bm or auto_bm

                b_list = backups_by_vmid.get(vmid, [])
                now_ts = int(time.time())
                last_b_ts = int(b_list[0].get("ctime", 0)) if b_list else 0
                age_hours = round((now_ts - last_b_ts) / 3600, 1) if last_b_ts else None
                if not last_b_ts:
                    b_status = "none"
                elif age_hours is not None and age_hours <= 48:
                    b_status = "fresh"
                elif age_hours is not None and age_hours <= 168:
                    b_status = "stale"
                else:
                    b_status = "old"

                maxmem = int(g.get("maxmem") or 0)
                mem = int(g.get("mem") or 0)
                maxdisk = int(g.get("maxdisk") or 0)
                disk = int(g.get("disk") or 0)
                desc_info = parse_description_summary(cfg.get("description"))

                return {
                    **g,
                    "name": guest_name,
                    "tag_list": tag_list,
                    "cpu_percent": round((g.get("cpu") or 0) * 100, 1),
                    "mem_percent": round((mem / maxmem) * 100, 1) if maxmem else 0.0,
                    "disk_percent": round((disk / maxdisk) * 100, 1) if maxdisk else 0.0,
                    "primary_ip": primary_ip,
                    "all_ips": all_ips,
                    "secondary_ips": agent_other,
                    "nics": nics,
                    "bridges": sorted({n["bridge"] for n in nics if n.get("bridge")}),
                    "storages_used": parse_storages_used(cfg),
                    "bind_mounts": parse_bind_mounts(cfg),
                    "passthroughs": parse_passthroughs(cfg),
                    "onboot": bool(cfg.get("onboot", 0)),
                    "description_info": desc_info,
                    "last_backup_ts": last_b_ts or None,
                    "backup_age_hours": age_hours,
                    "backup_count": len(b_list),
                    "backup_storage": b_list[0].get("_storage") if b_list else None,
                    "backup_status": b_status,
                    "service_url": active_bm["url"] if active_bm else None,
                    "service_label": active_bm.get("label", "") if active_bm else "",
                    "service_is_custom": bool(saved_bm),
                    "service_up": uptime_map[vmid]["up"] if vmid in uptime_map else None,
                    "service_latency_ms": uptime_map[vmid]["latency_ms"] if vmid in uptime_map else None,
                    "service_uptime_pct": uptime_map[vmid]["uptime_pct"] if vmid in uptime_map else None,
                    "console_url": self.build_console_url(gnode, gtype, vmid, guest_name),
                }

            enriched = list(await asyncio.gather(*(enrich_one(g) for g in raw_guests)))

            # ── 1. Network & IPAM Matrix ─────────────────────────────────
            bridges_meta = {
                n["iface"]: {
                    "iface": n["iface"],
                    "cidr": n.get("cidr") or "",
                    "address": n.get("address") or "",
                    "gateway": n.get("gateway") or "",
                    "ports": n.get("bridge_ports") or "",
                    "vlan_aware": bool(n.get("bridge_vlan_aware")),
                    "comments": str(n.get("comments") or "").strip(),
                    "guest_count": 0,
                    "entries": [],
                }
                for n in network
                if n.get("type") == "bridge"
            }

            ip_entries: list[dict[str, Any]] = []
            ip_seen: dict[str, list[dict[str, Any]]] = {}
            used_by_bridge: dict[str, set[str]] = {}

            for g in enriched:
                seen_bridges_for_guest: set[str] = set()
                for nic in g["nics"]:
                    br = nic.get("bridge") or "unassigned"
                    if br not in bridges_meta:
                        bridges_meta[br] = {
                            "iface": br,
                            "cidr": "",
                            "address": "",
                            "gateway": "",
                            "ports": "",
                            "vlan_aware": False,
                            "comments": "",
                            "guest_count": 0,
                            "entries": [],
                        }
                    if br not in seen_bridges_for_guest:
                        bridges_meta[br]["guest_count"] += 1
                        seen_bridges_for_guest.add(br)

                    ip_val = nic.get("ip") or ""
                    entry = {
                        "vmid": g["vmid"],
                        "name": g["name"],
                        "type": g["type"],
                        "status": g["status"],
                        "nic": nic["id"],
                        "bridge": br,
                        "vlan": nic.get("vlan") or "",
                        "ip": ip_val,
                        "cidr": nic.get("cidr") or "",
                        "dhcp": bool(nic.get("dhcp")),
                        "mac": nic.get("mac") or "",
                        "service_url": g.get("service_url"),
                    }
                    ip_entries.append(entry)
                    bridges_meta[br]["entries"].append(entry)
                    if ip_val:
                        ip_seen.setdefault(ip_val, []).append(entry)
                        used_by_bridge.setdefault(br, set()).add(ip_val)

            conflicts = [
                {"ip": ip, "guests": [f"{e['vmid']} ({e['name']})" for e in ents]}
                for ip, ents in ip_seen.items()
                if len(ents) > 1
            ]

            bridge_list = []
            for br_name in sorted(bridges_meta.keys()):
                meta = bridges_meta[br_name]
                used_set = set(used_by_bridge.get(br_name, set()))
                if meta["address"]:
                    used_set.add(meta["address"])
                if meta["gateway"]:
                    used_set.add(meta["gateway"])
                meta["free_ip_suggestions"] = suggest_free_ips(meta["cidr"], used_set, limit=6)
                bridge_list.append(meta)

            # ── 2. Backup Coverage Matrix ────────────────────────────────
            non_tpl = [g for g in enriched if not g.get("template")]
            backup_summary = {
                "total_guests": len(non_tpl),
                "fresh": sum(1 for g in non_tpl if g["backup_status"] == "fresh"),
                "stale": sum(1 for g in non_tpl if g["backup_status"] == "stale"),
                "old": sum(1 for g in non_tpl if g["backup_status"] == "old"),
                "none": sum(1 for g in non_tpl if g["backup_status"] == "none"),
                "total_archives": len(all_backups),
            }
            backup_rows = [
                {
                    "vmid": g["vmid"],
                    "name": g["name"],
                    "type": g["type"],
                    "status": g["status"],
                    "backup_status": g["backup_status"],
                    "last_backup_ts": g["last_backup_ts"],
                    "backup_age_hours": g["backup_age_hours"],
                    "backup_count": g["backup_count"],
                    "backup_storage": g["backup_storage"],
                }
                for g in non_tpl
            ]

            # ── 3. Capacity & Right-Sizing Advisor ───────────────────────
            host_cores = sum(int(n.get("maxcpu") or 0) for n in nodes)
            host_mem = sum(int(n.get("maxmem") or 0) for n in nodes)
            host_mem_used = sum(int(n.get("mem") or 0) for n in nodes)
            running = [g for g in non_tpl if g.get("status") == "running"]

            alloc_cores_running = sum(int(g.get("maxcpu") or 0) for g in running)
            alloc_cores_all = sum(int(g.get("maxcpu") or 0) for g in non_tpl)
            alloc_mem_running = sum(int(g.get("maxmem") or 0) for g in running)
            alloc_mem_all = sum(int(g.get("maxmem") or 0) for g in non_tpl)

            rightsizing: list[dict[str, Any]] = []
            gib = 1024**3
            for g in running:
                maxmem_gb = (g.get("maxmem") or 0) / gib
                mem_gb = (g.get("mem") or 0) / gib
                mem_pct = g.get("mem_percent") or 0
                cores = int(g.get("maxcpu") or 0)
                cpu_pct = g.get("cpu_percent") or 0
                reasons = []
                if maxmem_gb >= 8 and mem_pct < 18:
                    suggested_gb = max(2, int(round(max(mem_gb * 2.5, 2))))
                    reasons.append(f"RAM {mem_gb:.1f}/{maxmem_gb:.0f} GiB ({mem_pct:.1f}%) — could trim to ~{suggested_gb} GiB")
                if cores >= 8 and cpu_pct < 5:
                    reasons.append(f"CPU {cpu_pct:.1f}% across {cores} vCPUs — low utilization")
                if reasons:
                    rightsizing.append(
                        {
                            "vmid": g["vmid"],
                            "name": g["name"],
                            "type": g["type"],
                            "cores": cores,
                            "cpu_percent": cpu_pct,
                            "mem_bytes": g.get("mem") or 0,
                            "maxmem_bytes": g.get("maxmem") or 0,
                            "mem_percent": mem_pct,
                            "suggestions": reasons,
                        }
                    )
            rightsizing.sort(key=lambda x: x["maxmem_bytes"], reverse=True)

            top_cpu = sorted(running, key=lambda x: x.get("cpu_percent", 0), reverse=True)[:8]
            top_mem = sorted(running, key=lambda x: x.get("mem", 0), reverse=True)[:8]
            top_io = sorted(
                running,
                key=lambda x: int(x.get("diskread") or 0) + int(x.get("diskwrite") or 0),
                reverse=True,
            )[:8]
            top_net = sorted(
                running,
                key=lambda x: int(x.get("netin") or 0) + int(x.get("netout") or 0),
                reverse=True,
            )[:8]

            # ── 4. Storage-to-Guest & Bind Mount Mapping ─────────────────
            storage_guests: dict[str, list[dict[str, Any]]] = {
                st["storage"]: [] for st in storages if st.get("storage")
            }
            shared_mounts: list[dict[str, Any]] = []
            for g in non_tpl:
                for st_name in g["storages_used"]:
                    storage_guests.setdefault(st_name, []).append(
                        {
                            "vmid": g["vmid"],
                            "name": g["name"],
                            "type": g["type"],
                            "status": g["status"],
                            "maxdisk": g.get("maxdisk") or 0,
                        }
                    )
                for mp in g["bind_mounts"]:
                    shared_mounts.append(
                        {
                            "vmid": g["vmid"],
                            "name": g["name"],
                            "status": g["status"],
                            **mp,
                        }
                    )

            return {
                "guests": enriched,
                "network_ipam": {
                    "bridges": bridge_list,
                    "entries": ip_entries,
                    "conflicts": conflicts,
                },
                "backup_coverage": {
                    "summary": backup_summary,
                    "rows": backup_rows,
                },
                "capacity": {
                    "host_cores": host_cores,
                    "alloc_cores_running": alloc_cores_running,
                    "alloc_cores_all": alloc_cores_all,
                    "vcpu_ratio_running": round(alloc_cores_running / host_cores, 2) if host_cores else 0.0,
                    "host_mem_bytes": host_mem,
                    "host_mem_used_bytes": host_mem_used,
                    "alloc_mem_running_bytes": alloc_mem_running,
                    "alloc_mem_all_bytes": alloc_mem_all,
                    "ram_alloc_pct_running": round((alloc_mem_running / host_mem) * 100, 1) if host_mem else 0.0,
                    "rightsizing": rightsizing,
                    "top_cpu": top_cpu,
                    "top_mem": top_mem,
                    "top_io": top_io,
                    "top_net": top_net,
                },
                "storage_map": {
                    "by_storage": storage_guests,
                    "zfs_pools": zfs_pools,
                    "bind_mounts": shared_mounts,
                },
            }

        result = await self._run_audited(
            source=source,
            action="get_fleet_insights",
            risk="read",
            target="fleet",
            params=None,
            confirm=False,
            coro_fn=_op,
        )
        self._insights_cache = (time.monotonic(), result)
        return result

    async def bulk_guest_action(
        self,
        vmids: list[int],
        action: str,
        *,
        snapname: str | None = None,
        storage: str | None = None,
        source: str = "webui",
    ) -> dict[str, Any]:
        action = action.lower().strip()
        if action not in ("start", "shutdown", "reboot", "stop", "snapshot", "backup"):
            raise ProxmoxAPIError(f"Unsupported bulk action: {action}", status_code=400)
        self._enforce_safety(risk="write", action=f"bulk_{action}", confirm=False)

        results: list[dict[str, Any]] = []
        for vmid in vmids:
            try:
                if action in ("start", "shutdown", "reboot", "stop"):
                    r = await self.guest_power(vmid, action, source=source)
                elif action == "snapshot":
                    sname = snapname or f"bulk-{time.strftime('%Y%m%d-%H%M')}"
                    r = await self.create_snapshot(vmid, sname, description="Bulk snapshot", source=source)
                else:
                    r = await self.trigger_backup(vmid, storage=storage, source=source)
                results.append({"vmid": vmid, "ok": True, "result": r})
            except Exception as exc:
                results.append({"vmid": vmid, "ok": False, "error": str(exc)})
        return {
            "action": action,
            "requested": len(vmids),
            "succeeded": sum(1 for r in results if r["ok"]),
            "failed": sum(1 for r in results if not r["ok"]),
            "results": results,
        }

    @staticmethod
    async def _safe_call(coro, default):
        try:
            return await coro
        except Exception:
            return default

    # ── ISO & LXC Appliance Hub ──────────────────────────────────────────
    async def search_isos(self, query: str = "", include_web: bool = True) -> dict[str, Any]:
        return await search_web_isos(query=query, include_web=include_web)

    async def probe_iso_url(self, url: str) -> dict[str, Any]:
        return await probe_remote_url(url)

    async def download_iso_from_url(
        self,
        *,
        storage: str,
        url: str,
        filename: str,
        content: str = "iso",
        checksum: str | None = None,
        checksum_algorithm: str | None = None,
        node: str | None = None,
        source: str = "webui",
    ) -> dict[str, Any]:
        target_node = await self.get_default_node(node)
        upid = await self._run_audited(
            source=source,
            action="download_iso_from_url",
            risk="write",
            target=f"{storage}:{content}/{filename}@{target_node}",
            params={"storage": storage, "url": url, "filename": filename, "content": content},
            confirm=False,
            coro_fn=lambda: self.client.download_url_to_storage(
                target_node,
                storage,
                url=url,
                filename=filename,
                content=content,
                checksum=checksum,
                checksum_algorithm=checksum_algorithm,
            ),
        )
        return {"node": target_node, "storage": storage, "filename": filename, "upid": upid}

    async def list_lxc_appliances(
        self,
        node: str | None = None,
        search: str = "",
        section: str = "",
        source: str = "webui",
    ) -> dict[str, Any]:
        target_node = await self.get_default_node(node)
        items = await self._run_audited(
            source=source,
            action="list_lxc_appliances",
            risk="read",
            target=f"aplinfo@{target_node}",
            params={"search": search, "section": section},
            confirm=False,
            coro_fn=lambda: self.client.list_appliances(target_node),
        )
        q = search.strip().lower()
        sec = section.strip().lower()
        filtered = []
        sections: set[str] = set()
        for it in items:
            s_val = str(it.get("section") or "system")
            sections.add(s_val)
            if sec and s_val.lower() != sec:
                continue
            if q:
                hay = f"{it.get('template', '')} {it.get('os', '')} {it.get('headline', '')} {it.get('package', '')}".lower()
                if q not in hay:
                    continue
            filtered.append(it)
        filtered.sort(key=lambda x: (0 if x.get("section") == "system" else 1, str(x.get("template", ""))))
        return {
            "node": target_node,
            "total": len(items),
            "sections": sorted(sections),
            "appliances": filtered[:150],
        }

    async def download_lxc_appliance(
        self,
        *,
        storage: str,
        template: str,
        node: str | None = None,
        source: str = "webui",
    ) -> dict[str, Any]:
        target_node = await self.get_default_node(node)
        upid = await self._run_audited(
            source=source,
            action="download_lxc_template",
            risk="write",
            target=f"{storage}:vztmpl/{template}@{target_node}",
            params={"storage": storage, "template": template},
            confirm=False,
            coro_fn=lambda: self.client.download_appliance(target_node, storage, template),
        )
        return {"node": target_node, "storage": storage, "template": template, "upid": upid}

    # ── Dual-Mode File Explorer (Storage Volumes + QEMU Guest Agent) ─────
    async def browse_storage_volumes(
        self,
        storage: str | None = None,
        content: str | None = None,
        node: str | None = None,
        source: str = "webui",
    ) -> dict[str, Any]:
        target_node = await self.get_default_node(node)

        async def _op() -> dict[str, Any]:
            storages = await self._safe_call(self.client.get_node_storage(target_node), [])
            active = [s for s in storages if s.get("active", 1)]
            target_storages = [s for s in active if not storage or s.get("storage") == storage]
            volumes: list[dict[str, Any]] = []
            for st in target_storages:
                sname = st["storage"]
                items = await self._safe_call(
                    self.client.get_storage_content(target_node, sname, content or None), []
                )
                for item in items:
                    v = dict(item)
                    v["storage"] = sname
                    v["storage_type"] = st.get("type", "")
                    volumes.append(v)
            volumes.sort(key=lambda x: (int(x.get("size") or 0)), reverse=True)
            return {
                "node": target_node,
                "storages": active,
                "volumes": volumes,
                "total_bytes": sum(int(v.get("size") or 0) for v in volumes),
            }

        return await self._run_audited(
            source=source,
            action="browse_storage_files",
            risk="read",
            target=f"{storage or 'all'}@{target_node}",
            params={"storage": storage, "content": content},
            confirm=False,
            coro_fn=_op,
        )

    async def delete_storage_volume(
        self,
        storage: str,
        volid: str,
        *,
        confirm: bool = False,
        node: str | None = None,
        source: str = "webui",
    ) -> dict[str, Any]:
        target_node = await self.get_default_node(node)
        upid = await self._run_audited(
            source=source,
            action="delete_storage_volume",
            risk="destructive",
            target=f"{volid}@{target_node}",
            params={"storage": storage, "volid": volid, "confirm": confirm},
            confirm=confirm,
            coro_fn=lambda: self.client.delete_storage_volume(target_node, storage, volid),
        )
        return {"node": target_node, "storage": storage, "volid": volid, "upid": upid}

    async def upload_storage_file(
        self,
        *,
        storage: str,
        content: str,
        filename: str,
        file_bytes: bytes,
        node: str | None = None,
        source: str = "webui",
    ) -> dict[str, Any]:
        target_node = await self.get_default_node(node)
        upid = await self._run_audited(
            source=source,
            action="upload_storage_file",
            risk="write",
            target=f"{storage}:{content}/{filename}@{target_node}",
            params={"storage": storage, "content": content, "filename": filename, "size": len(file_bytes)},
            confirm=False,
            coro_fn=lambda: self.client.upload_to_storage(
                target_node, storage, content=content, filename=filename, file_bytes=file_bytes
            ),
        )
        return {"node": target_node, "storage": storage, "filename": filename, "upid": upid}

    async def list_agent_capable_vms(self) -> list[dict[str, Any]]:
        resources = await self.client.get_cluster_resources(resource_type="vm")
        running_vms = [
            r for r in resources if r.get("type") == "qemu" and r.get("status") == "running" and not r.get("template")
        ]
        results: list[dict[str, Any]] = []
        for vm in running_vms:
            vmid = int(vm["vmid"])
            vnode = str(vm["node"])
            info = await self._safe_call(
                asyncio.wait_for(self.client.qemu_agent_info(vnode, vmid), timeout=2.0),
                None,
            )
            if isinstance(info, dict) and info.get("result"):
                results.append(
                    {
                        "vmid": vmid,
                        "name": vm.get("name") or f"vm-{vmid}",
                        "node": vnode,
                        "agent_version": info["result"].get("version", ""),
                    }
                )
        results.sort(key=lambda x: x["vmid"])
        return results

    @staticmethod
    def _decode_agent_field(val: Any) -> str:
        if val is None:
            return ""
        s = str(val)
        return s

    async def _poll_agent_exec(
        self, node: str, vmid: int, pid: int, timeout_sec: float = 15.0
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            st = await self.client.qemu_agent_exec_status(node, vmid, pid)
            if st.get("exited"):
                return {
                    "exitcode": int(st.get("exitcode", 0)),
                    "stdout": self._decode_agent_field(st.get("out-data", "")),
                    "stderr": self._decode_agent_field(st.get("err-data", "")),
                }
            await asyncio.sleep(0.25)
        return {"exitcode": -1, "stdout": "", "stderr": "Command timed out waiting for guest agent"}

    async def guest_agent_list_dir(
        self,
        vmid: int,
        path: str = "/",
        node: str | None = None,
        source: str = "webui",
    ) -> dict[str, Any]:
        resolved_node, resolved_type = await self.resolve_guest(vmid, node)
        if resolved_type != "qemu":
            raise ProxmoxAPIError("Guest File Explorer uses QEMU Guest Agent and only supports running QEMU VMs.", status_code=400)

        clean_path = path.strip() or "/"
        if not clean_path.startswith("/"):
            clean_path = "/" + clean_path

        async def _op() -> dict[str, Any]:
            quoted = shlex.quote(clean_path)
            # Portable POSIX listing emitting type|name|size|mtime
            script = (
                f"cd {quoted} 2>&1 || exit 2; "
                "ls -1ApL 2>/dev/null | head -n 200 | while IFS= read -r f; do "
                "  name=\"${f%/}\"; "
                "  if [ -d \"$name\" ]; then t=d; else t=f; fi; "
                "  sz=$(stat -c '%s' \"$name\" 2>/dev/null || echo 0); "
                "  mt=$(stat -c '%y' \"$name\" 2>/dev/null | cut -d'.' -f1); "
                "  printf '%s|%s|%s|%s\\n' \"$t\" \"$name\" \"$sz\" \"$mt\"; "
                "done"
            )
            exec_res = await self.client.qemu_agent_exec(resolved_node, vmid, ["sh", "-c", script])
            pid = int(exec_res["pid"])
            out = await self._poll_agent_exec(resolved_node, vmid, pid)
            if out["exitcode"] != 0 and not out["stdout"].strip():
                raise ProxmoxAPIError(out["stderr"] or out["stdout"] or f"Cannot list '{clean_path}'")
            entries: list[dict[str, Any]] = []
            for line in out["stdout"].splitlines():
                parts = line.split("|", 3)
                if len(parts) < 2 or not parts[1]:
                    continue
                kind, name = parts[0], parts[1]
                sz = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
                mt = parts[3] if len(parts) > 3 else ""
                full_p = f"{clean_path.rstrip('/')}/{name.lstrip('/')}" if not name.startswith("/") else name
                entries.append(
                    {
                        "name": name.split("/")[-1],
                        "path": full_p,
                        "is_dir": kind == "d",
                        "size": sz,
                        "mtime": mt,
                    }
                )
            entries.sort(key=lambda e: (0 if e["is_dir"] else 1, e["name"].lower()))
            return {"vmid": vmid, "node": resolved_node, "path": clean_path, "entries": entries}

        return await self._run_audited(
            source=source,
            action="guest_agent_list_dir",
            risk="read",
            target=f"qemu/{vmid}:{clean_path}",
            params={"path": clean_path},
            confirm=False,
            coro_fn=_op,
        )

    async def guest_agent_read_file(
        self,
        vmid: int,
        file_path: str,
        node: str | None = None,
        source: str = "webui",
    ) -> dict[str, Any]:
        resolved_node, resolved_type = await self.resolve_guest(vmid, node)
        if resolved_type != "qemu":
            raise ProxmoxAPIError("Only running QEMU VMs with Guest Agent support file-read.", status_code=400)

        async def _op() -> dict[str, Any]:
            res = await self.client.qemu_agent_file_read(resolved_node, vmid, file_path)
            content = res.get("content", "")
            if not content and res.get("content-base64"):
                content = base64.b64decode(res["content-base64"]).decode("utf-8", errors="replace")
            return {
                "vmid": vmid,
                "node": resolved_node,
                "path": file_path,
                "content": content,
                "truncated": bool(res.get("truncated", 0)),
            }

        return await self._run_audited(
            source=source,
            action="guest_agent_read_file",
            risk="read",
            target=f"qemu/{vmid}:{file_path}",
            params={"file": file_path},
            confirm=False,
            coro_fn=_op,
        )

    async def guest_agent_write_file(
        self,
        vmid: int,
        file_path: str,
        content: str,
        node: str | None = None,
        source: str = "webui",
    ) -> dict[str, Any]:
        resolved_node, resolved_type = await self.resolve_guest(vmid, node)
        if resolved_type != "qemu":
            raise ProxmoxAPIError("Only running QEMU VMs with Guest Agent support file-write.", status_code=400)
        await self._run_audited(
            source=source,
            action="guest_agent_write_file",
            risk="write",
            target=f"qemu/{vmid}:{file_path}",
            params={"file": file_path, "bytes": len(content.encode("utf-8"))},
            confirm=False,
            coro_fn=lambda: self.client.qemu_agent_file_write(resolved_node, vmid, file_path, content),
        )
        return {"vmid": vmid, "node": resolved_node, "path": file_path, "saved": True}

    async def guest_agent_exec_command(
        self,
        vmid: int,
        command: str,
        node: str | None = None,
        source: str = "webui",
    ) -> dict[str, Any]:
        resolved_node, resolved_type = await self.resolve_guest(vmid, node)
        if resolved_type != "qemu":
            raise ProxmoxAPIError("Only running QEMU VMs with Guest Agent support exec.", status_code=400)

        async def _op() -> dict[str, Any]:
            res = await self.client.qemu_agent_exec(resolved_node, vmid, ["sh", "-c", command])
            pid = int(res["pid"])
            out = await self._poll_agent_exec(resolved_node, vmid, pid)
            return {"vmid": vmid, "node": resolved_node, "command": command, "pid": pid, **out}

        return await self._run_audited(
            source=source,
            action="guest_agent_exec",
            risk="write",
            target=f"qemu/{vmid}",
            params={"command": command},
            confirm=False,
            coro_fn=_op,
        )

    # ── Host Updates, Syslog, Firewall & Backup Scheduler ────────────────
    async def get_host_maintenance(
        self,
        node: str | None = None,
        syslog_limit: int = 120,
        syslog_service: str | None = None,
        source: str = "webui",
    ) -> dict[str, Any]:
        target_node = await self.get_default_node(node)

        async def _op() -> dict[str, Any]:
            apt_updates, apt_versions, syslog, fw_opts, cluster_fw, node_fw = await asyncio.gather(
                self._safe_call(self.client.get_apt_updates(target_node), []),
                self._safe_call(self.client.get_apt_versions(target_node), []),
                self._safe_call(self.client.get_node_syslog(target_node, limit=syslog_limit, service=syslog_service), []),
                self._safe_call(self.client.get_cluster_firewall_options(), {}),
                self._safe_call(self.client.get_cluster_firewall_rules(), []),
                self._safe_call(self.client.get_node_firewall_rules(target_node), []),
            )
            return {
                "node": target_node,
                "apt_updates": apt_updates,
                "apt_versions": apt_versions[:40],
                "syslog": [line.get("t", "") for line in syslog],
                "firewall": {
                    "options": fw_opts,
                    "cluster_rules": cluster_fw,
                    "node_rules": node_fw,
                },
            }

        return await self._run_audited(
            source=source,
            action="get_host_updates_and_logs",
            risk="read",
            target=f"host@{target_node}",
            params={"syslog_limit": syslog_limit, "service": syslog_service},
            confirm=False,
            coro_fn=_op,
        )

    async def refresh_host_apt(self, node: str | None = None, source: str = "webui") -> dict[str, Any]:
        target_node = await self.get_default_node(node)
        upid = await self._run_audited(
            source=source,
            action="refresh_host_apt",
            risk="write",
            target=f"apt@{target_node}",
            params=None,
            confirm=False,
            coro_fn=lambda: self.client.refresh_apt_updates(target_node),
        )
        return {"node": target_node, "upid": upid}

    async def create_backup_schedule(
        self,
        *,
        storage: str,
        schedule: str,
        vmid: str | None = None,
        all_guests: bool = False,
        mode: str = "snapshot",
        compress: str = "zstd",
        comment: str = "",
        enabled: bool = True,
        source: str = "webui",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "storage": storage,
            "schedule": schedule,
            "mode": mode,
            "compress": compress,
            "enabled": 1 if enabled else 0,
        }
        if all_guests:
            params["all"] = 1
        elif vmid:
            params["vmid"] = vmid
        else:
            raise ProxmoxAPIError("Specify VMIDs or set all_guests=true.", status_code=400)
        if comment:
            params["comment"] = comment

        res = await self._run_audited(
            source=source,
            action="create_backup_schedule",
            risk="write",
            target=f"backup-job:{storage}",
            params=params,
            confirm=False,
            coro_fn=lambda: self.client.create_cluster_backup_job(params),
        )
        return {"created": True, "job": res, "params": params}

    async def delete_backup_schedule(
        self, job_id: str, *, confirm: bool = False, source: str = "webui"
    ) -> dict[str, Any]:
        await self._run_audited(
            source=source,
            action="delete_backup_schedule",
            risk="destructive",
            target=f"backup-job:{job_id}",
            params={"job_id": job_id, "confirm": confirm},
            confirm=confirm,
            coro_fn=lambda: self.client.delete_cluster_backup_job(job_id),
        )
        return {"deleted": True, "job_id": job_id}

    # ── Smart Alert Webhooks (Discord / Slack / ntfy / Generic) ──────────
    def get_alert_config(self) -> dict[str, Any]:
        return self.audit.get_alert_config()

    def save_alert_config(self, cfg: dict[str, Any]) -> dict[str, Any]:
        return self.audit.save_alert_config(cfg)

    async def send_webhook_alert(
        self,
        *,
        title: str,
        message: str,
        severity: str = "info",
        webhook_url: str | None = None,
        provider: str | None = None,
    ) -> dict[str, Any]:
        cfg = self.audit.get_alert_config()
        url = (webhook_url or cfg.get("webhook_url") or "").strip()
        prov = (provider or cfg.get("provider") or "discord").lower()
        if not url:
            raise ProxmoxAPIError("No alert webhook URL configured yet.", status_code=400)

        color_map = {"critical": 0xF43F5E, "warning": 0xF59E0B, "info": 0x22D3EE, "ok": 0x10B981}
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            if prov == "discord":
                resp = await client.post(
                    url,
                    json={
                        "embeds": [
                            {
                                "title": f"Proxmox Homelab Hub · {title}",
                                "description": message[:3900],
                                "color": color_map.get(severity, 0x22D3EE),
                            }
                        ]
                    },
                )
            elif prov == "slack":
                resp = await client.post(url, json={"text": f"*{title}*\n{message}"})
            elif prov == "ntfy":
                prio = "urgent" if severity == "critical" else ("high" if severity == "warning" else "default")
                resp = await client.post(
                    url,
                    content=message.encode("utf-8"),
                    headers={"Title": title, "Priority": prio},
                )
            else:
                resp = await client.post(
                    url,
                    json={
                        "source": "proxmox-homelab-hub",
                        "host": self.settings.normalized_host,
                        "severity": severity,
                        "title": title,
                        "message": message,
                        "timestamp": int(time.time()),
                    },
                )
        if resp.status_code >= 400:
            raise ProxmoxAPIError(f"Webhook returned HTTP {resp.status_code}: {resp.text[:200]}", status_code=502)
        self.audit.save_alert_config({"last_sent_at": int(time.time())})
        return {"sent": True, "provider": prov, "status_code": resp.status_code}

    async def evaluate_and_notify(
        self, *, force_send: bool = False, source: str = "webui"
    ) -> dict[str, Any]:
        cfg = self.audit.get_alert_config()
        health = await self.analyze_homelab_health(source=source)
        overview = await self.get_cluster_overview(source=source)
        summary = overview["summary"]

        triggers: list[str] = []
        if health["score"] < int(cfg["min_health_score"]):
            triggers.append(f"Health score dropped to {health['score']}/100 (threshold < {cfg['min_health_score']})")
        if summary["cpu_percent"] >= float(cfg["max_cpu_pct"]):
            triggers.append(f"Host CPU at {summary['cpu_percent']}% (threshold ≥ {cfg['max_cpu_pct']}%)")
        if summary["mem_percent"] >= float(cfg["max_ram_pct"]):
            triggers.append(f"Host RAM at {summary['mem_percent']}% (threshold ≥ {cfg['max_ram_pct']}%)")

        for f in health["findings"]:
            if f["category"] in ("storage", "truenas") and f["severity"] in ("critical", "warning"):
                triggers.append(f["title"])
            if cfg["notify_failed_tasks"] and f["category"] == "tasks":
                triggers.append(f["title"])

        sent = False
        send_error: str | None = None
        if (triggers and cfg["enabled"] and cfg["webhook_url"]) or (force_send and cfg["webhook_url"]):
            sev = "critical" if health["status"] == "critical" else ("warning" if triggers else "ok")
            body = "\n".join(f"• {t}" for t in triggers) if triggers else "All homelab thresholds are healthy."
            try:
                await self.send_webhook_alert(
                    title=f"Health {health['score']}/100 ({len(triggers)} alert(s))",
                    message=body,
                    severity=sev,
                )
                sent = True
            except Exception as exc:
                send_error = str(exc)

        return {
            "health_score": health["score"],
            "triggers": triggers,
            "webhook_sent": sent,
            "send_error": send_error,
        }

    # ── Community-Scripts.org Catalog, Advanced Setup & SSH Deploy ───────
    def build_host_shell_url(self, node: str) -> str:
        params = urlencode({"console": "shell", "xtermjs": "1", "node": node})
        return f"{self.settings.normalized_host}/?{params}"

    async def list_community_scripts(
        self,
        *,
        query: str = "",
        category: str = "",
        script_type: str = "",
        sort: str = "latest",
        include_dev: bool = False,
        refresh: bool = False,
        limit: int = 80,
    ) -> dict[str, Any]:
        cat = await self.community.get_catalog(force=refresh)
        all_scripts = cat["scripts"]

        latest_pool = [s for s in all_scripts if not s.get("is_dev") and s.get("script_created")]
        latest_pool.sort(key=lambda x: (x.get("script_created") or "", x.get("name") or ""), reverse=True)
        latest = latest_pool[:16]

        q = query.strip().lower()
        c_low = category.strip().lower()
        t_low = script_type.strip().lower()

        filtered = []
        for s in all_scripts:
            if not include_dev and s.get("is_dev"):
                continue
            if t_low and s.get("type", "").lower() != t_low:
                continue
            if c_low and not any(c_low == cn.lower() or c_low == cid.lower() for cn, cid in zip(s.get("categories", []), s.get("category_ids", []))):
                # Also allow substring match on category name
                if not any(c_low in cn.lower() for cn in s.get("categories", [])):
                    continue
            if q:
                hay = f"{s['slug']} {s['name']} {s['description']} {' '.join(s.get('categories', []))}".lower()
                if q not in hay:
                    continue
            filtered.append(s)

        if sort == "name":
            filtered.sort(key=lambda x: x["name"].lower())
        else:
            filtered.sort(key=lambda x: (x.get("script_created") or "", x["name"].lower()), reverse=True)

        return {
            "source": cat["source"],
            "fetched_at": cat["fetched_at"],
            "total_catalog": len([s for s in all_scripts if include_dev or not s.get("is_dev")]),
            "matched": len(filtered),
            "categories": cat["categories"],
            "latest": latest,
            "scripts": filtered[:limit],
        }

    async def get_community_script_setup(
        self, slug: str, script_type: str = "lxc", node: str | None = None
    ) -> dict[str, Any]:
        """Fetch live upstream script defaults + fast Proxmox host context (<300ms)."""
        host_ctx: dict[str, Any] = {
            "node": "",
            "host_shell_url": "",
            "next_vmid": 100,
            "container_storages": [],
            "template_storages": [],
            "bridges": ["vmbr0"],
            "apt_cacher_ip": "",
        }

        async def _fast_host_ctx() -> dict[str, Any]:
            try:
                target_node = await self.get_default_node(node)
                next_vmid, st_list, net_list, vm_res = await asyncio.gather(
                    self.client.get_next_vmid(),
                    self._safe_call(self.client.get_node_storage(target_node), []),
                    self._safe_call(self.client.get_node_network(target_node), []),
                    self._safe_call(self.client.get_cluster_resources(resource_type="vm"), []),
                )
                active_st = [s for s in st_list if s.get("active", 1) and s.get("enabled", 1)]
                c_storages = [
                    s for s in active_st
                    if {c.strip() for c in str(s.get("content", "")).split(",")} & {"rootdir", "images"}
                ]
                t_storages = [
                    s for s in active_st
                    if "vztmpl" in {c.strip() for c in str(s.get("content", "")).split(",")}
                ]
                bridges = [n["iface"] for n in net_list if n.get("type") == "bridge"] or ["vmbr0"]

                # Fast check: only inspect apt-cacher-ng LXC if present (1 single call instead of 33!)
                apt_ip = ""
                for r in vm_res:
                    if "apt-cacher" in str(r.get("name", "")).lower() and r.get("type") == "lxc":
                        cfg = await self._safe_call(
                            self.client.get_lxc_config(str(r["node"]), int(r["vmid"])), {}
                        )
                        nics = parse_nic_entries(cfg if isinstance(cfg, dict) else {}, "lxc")
                        if nics and nics[0].get("ip"):
                            apt_ip = str(nics[0]["ip"])
                        break

                return {
                    "node": target_node,
                    "host_shell_url": self.build_host_shell_url(target_node),
                    "next_vmid": next_vmid,
                    "container_storages": c_storages,
                    "template_storages": t_storages,
                    "bridges": bridges,
                    "apt_cacher_ip": apt_ip,
                }
            except Exception:
                return host_ctx

        details, resolved_ctx = await asyncio.gather(
            self.community.inspect_script_defaults(slug, script_type=script_type),
            _fast_host_ctx(),
        )
        return {**details, "host_context": resolved_ctx}

    def preview_community_script_command(
        self, slug: str, script_type: str, script_url: str | None, options: dict[str, Any]
    ) -> dict[str, Any]:
        url = (script_url or "").strip() or script_raw_url(slug, script_type)
        cmd = self.community.build_advanced_command(url, options, redact_secrets=False)
        redacted = self.community.build_advanced_command(url, options, redact_secrets=True)
        return {"slug": slug, "script_url": url, "command": cmd, "display_command": redacted}

    async def deploy_community_script(
        self,
        *,
        slug: str,
        script_type: str = "lxc",
        script_url: str | None = None,
        options: dict[str, Any] | None = None,
        source: str = "webui",
    ) -> dict[str, Any]:
        opts = options or {}
        url = (script_url or "").strip() or script_raw_url(slug, script_type)
        cmd = self.community.build_advanced_command(url, opts, redact_secrets=False)
        redacted = self.community.build_advanced_command(url, opts, redact_secrets=True)

        async def _op() -> dict[str, Any]:
            return self.community.start_ssh_deploy(
                slug=slug,
                script_url=url,
                command=cmd,
                display_command=redacted,
                host_url=self.settings.normalized_host,
                user=self.settings.proxmox_ssh_user,
                port=self.settings.proxmox_ssh_port,
                key_path=self.settings.proxmox_ssh_key_path,
                demo_mode=self.settings.proxmox_demo_mode,
            )

        return await self._run_audited(
            source=source,
            action="deploy_community_script",
            risk="write",
            target=f"community-script/{slug}",
            params={k: v for k, v in opts.items() if k != "password"},
            confirm=False,
            coro_fn=_op,
        )

    async def test_community_ssh(self) -> dict[str, Any]:
        return await self.community.test_ssh(
            host_url=self.settings.normalized_host,
            user=self.settings.proxmox_ssh_user,
            port=self.settings.proxmox_ssh_port,
            key_path=self.settings.proxmox_ssh_key_path,
            demo_mode=self.settings.proxmox_demo_mode,
        )

    # ── Service Uptime Monitor (Mini Uptime Kuma) ────────────────────────
    async def run_uptime_probe(self, source: str = "webui") -> dict[str, Any]:
        ins = await self.get_fleet_insights(source=source)
        prev = self.audit.get_uptime_summary()
        records = await probe_guest_services(ins.get("guests", []), demo_mode=self.settings.proxmox_demo_mode)
        self.audit.record_uptime_batch(records)
        self._insights_cache = None

        # Alert on newly-down services if webhook enabled
        newly_down = [
            r for r in records
            if not r["up"] and prev.get(int(r["vmid"]), {}).get("up") is True
        ]
        if newly_down:
            cfg = self.audit.get_alert_config()
            if cfg.get("enabled") and cfg.get("webhook_url"):
                lines = "\n".join(f"• {r['name']} ({r['url']}) — {r['error'] or 'Down'}" for r in newly_down)
                try:
                    await self.send_webhook_alert(
                        title=f"{len(newly_down)} Homelab Service(s) Down",
                        message=lines,
                        severity="critical",
                    )
                except Exception:
                    pass

        return await self.get_uptime_dashboard(run_if_empty=False)

    async def get_uptime_dashboard(self, run_if_empty: bool = True) -> dict[str, Any]:
        summary = self.audit.get_uptime_summary()
        if not summary and run_if_empty:
            return await self.run_uptime_probe(source="webui")
        services = sorted(summary.values(), key=lambda x: (0 if not x["up"] else 1, x["name"].lower()))
        up_cnt = sum(1 for s in services if s["up"])
        down_cnt = len(services) - up_cnt
        avg_ms = round(sum(s["latency_ms"] for s in services) / len(services), 1) if services else 0.0
        return {
            "summary": {
                "total": len(services),
                "up": up_cnt,
                "down": down_cnt,
                "avg_latency_ms": avg_ms,
            },
            "services": services,
        }

    # ── Unified Docker Container Manager ─────────────────────────────────
    async def list_docker_containers(self, source: str = "webui") -> dict[str, Any]:
        return await self._run_audited(
            source=source,
            action="list_docker_containers",
            risk="read",
            target="docker-fleet",
            params=None,
            confirm=False,
            coro_fn=lambda: discover_docker_containers(self),
        )

    async def docker_container_action(
        self,
        *,
        host_vmid: int,
        container_name: str,
        action: str,
        tail: int = 80,
        source: str = "webui",
    ) -> dict[str, Any]:
        risk = "read" if action == "logs" else "write"
        return await self._run_audited(
            source=source,
            action=f"docker_{action}",
            risk=risk,
            target=f"{host_vmid}/{container_name}",
            params={"action": action},
            confirm=False,
            coro_fn=lambda: run_docker_container_op(
                self, host_vmid=host_vmid, container_name=container_name, action=action, tail=tail
            ),
        )

    # ── Dell PowerEdge iDRAC Hardware & Power Telemetry ──────────────────
    async def get_hardware_telemetry(self, source: str = "webui") -> dict[str, Any]:
        return await self._run_audited(
            source=source,
            action="get_hardware_power_telemetry",
            risk="read",
            target=self.settings.idrac_host or "idrac",
            params=None,
            confirm=False,
            coro_fn=lambda: fetch_idrac_telemetry(self.settings),
        )

    # ── Bare-Metal TrueNAS Host Monitoring (Strictly Read-Only) ──────────
    async def _correlate_truenas_proxmox_links(self, tn_data: dict[str, Any]) -> list[dict[str, Any]]:
        """Cross-reference TrueNAS IP/shares against Proxmox Datacenter NFS/SMB/iSCSI storages."""
        if not tn_data.get("connected"):
            return []
        tn_url = str(tn_data.get("url") or "")
        tn_hostname = (urlparse(tn_url).hostname or "").lower()
        sys_hostname = str((tn_data.get("system") or {}).get("hostname") or "").lower()
        if not tn_hostname and not sys_hostname:
            return []

        pve_storages: list[dict[str, Any]] = []
        if hasattr(self.client, "get"):
            pve_storages = await self._safe_call(self.client.get("/storage"), [])
        elif self.settings.proxmox_demo_mode:
            # Show example cross-link in Demo Mode
            return [
                {
                    "proxmox_storage": "truenas-nfs",
                    "type": "nfs",
                    "server": tn_hostname or "192.168.1.x",
                    "export": "/mnt/data-pool",
                    "content": "backup,iso,vztmpl",
                    "matched_share": "/mnt/data-pool",
                }
            ]

        shares = tn_data.get("shares") or []
        links: list[dict[str, Any]] = []
        for st in pve_storages or []:
            if not isinstance(st, dict):
                continue
            st_type = str(st.get("type") or "").lower()
            if st_type not in ("nfs", "cifs", "iscsi", "iscsidirect"):
                continue
            server = str(st.get("server") or st.get("portal") or "").split(":")[0].strip().lower()
            if not server or server not in (tn_hostname, sys_hostname):
                continue
            export = str(st.get("export") or st.get("share") or st.get("target") or "")
            matched = next(
                (
                    sh["path"] or sh["name"]
                    for sh in shares
                    if export and (export == sh.get("path") or export == sh.get("name"))
                ),
                "",
            )
            links.append(
                {
                    "proxmox_storage": st.get("storage") or "",
                    "type": st_type,
                    "server": server,
                    "export": export,
                    "content": st.get("content") or "",
                    "matched_share": matched,
                }
            )
        return links

    async def _fetch_truenas_cached(self, force_refresh: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if not force_refresh and self._truenas_cache and (now - self._truenas_cache[0]) < 8.0:
            return self._truenas_cache[1]
        data = await fetch_truenas_telemetry(self.settings)
        if data.get("connected"):
            data["proxmox_links"] = await self._correlate_truenas_proxmox_links(data)
        self._truenas_cache = (now, data)
        return data

    async def get_truenas_telemetry(
        self, *, force_refresh: bool = False, source: str = "webui"
    ) -> dict[str, Any]:
        return await self._run_audited(
            source=source,
            action="get_truenas_overview",
            risk="read",
            target=self.settings.truenas_host or "truenas",
            params=None,
            confirm=False,
            coro_fn=lambda: self._fetch_truenas_cached(force_refresh=force_refresh),
        )

    async def test_truenas_connection(self) -> dict[str, Any]:
        self._truenas_cache = None
        return await probe_truenas_connection(self.settings)

    # ── Wake-on-LAN Portal ───────────────────────────────────────────────
    async def list_wol_devices(self, check_online: bool = True) -> list[dict[str, Any]]:
        devices = self.audit.list_wol_devices()
        if check_online and devices and not self.settings.proxmox_demo_mode:
            states = await asyncio.gather(*(check_tcp_reachable(d.get("ip", "")) for d in devices))
            for d, online in zip(devices, states):
                d["online"] = online
        else:
            for d in devices:
                d["online"] = None
        return devices

    def save_wol_device(
        self, name: str, mac: str, ip: str = "", broadcast: str = "255.255.255.255", notes: str = ""
    ) -> dict[str, Any]:
        from .wol import normalize_mac
        normalize_mac(mac)  # validate before persisting
        return self.audit.upsert_wol_device(name, mac, ip, broadcast, notes)

    def delete_wol_device(self, device_id: int) -> None:
        self.audit.delete_wol_device(device_id)

    async def send_wol(
        self,
        *,
        mac: str | None = None,
        device_id: int | None = None,
        broadcast: str = "255.255.255.255",
        source: str = "webui",
    ) -> dict[str, Any]:
        target_mac = mac or ""
        target_bc = broadcast
        target_name = mac or f"device#{device_id}"
        if device_id is not None:
            for d in self.audit.list_wol_devices():
                if int(d["id"]) == int(device_id):
                    target_mac = d["mac"]
                    target_bc = d.get("broadcast") or broadcast
                    target_name = d["name"]
                    break
        if not target_mac:
            raise ProxmoxAPIError("MAC address or valid device_id is required.", status_code=400)

        async def _op() -> dict[str, Any]:
            res = send_magic_packet(target_mac, broadcast=target_bc)
            if device_id is not None:
                self.audit.mark_wol_woken(device_id)
            return {"device": target_name, **res}

        return await self._run_audited(
            source=source,
            action="send_wake_on_lan",
            risk="write",
            target=f"wol/{target_name}",
            params={"mac": target_mac, "broadcast": target_bc},
            confirm=False,
            coro_fn=_op,
        )

    # ── Fleet-Wide Snapshot Checkpoints ──────────────────────────────────
    async def list_all_snapshots(self, source: str = "webui") -> dict[str, Any]:
        async def _op() -> dict[str, Any]:
            guests = await self.list_guests(source="webui")
            non_tpl = [g for g in guests if not g.get("template")]
            sem = asyncio.Semaphore(10)

            async def _fetch(g: dict[str, Any]) -> list[dict[str, Any]]:
                async with sem:
                    snaps = await self._safe_call(
                        self.client.list_snapshots(g["node"], g["type"], int(g["vmid"])), []
                    )
                out = []
                for s in snaps:
                    if s.get("name") == "current":
                        continue
                    out.append(
                        {
                            "vmid": int(g["vmid"]),
                            "guest_name": g.get("name") or str(g["vmid"]),
                            "type": g["type"],
                            "node": g["node"],
                            "tags": g.get("tag_list") or [],
                            "snapname": s.get("name"),
                            "description": s.get("description") or "",
                            "snaptime": s.get("snaptime") or 0,
                            "vmstate": bool(s.get("vmstate", 0)),
                        }
                    )
                return out

            batches = await asyncio.gather(*(_fetch(g) for g in non_tpl))
            all_snaps = [item for sub in batches for item in sub]
            all_snaps.sort(key=lambda x: x.get("snaptime", 0), reverse=True)
            tag_counts: dict[str, int] = {}
            for g in non_tpl:
                for t in g.get("tag_list") or []:
                    if t != "community-script" and not t[0].isdigit():
                        tag_counts[t] = tag_counts.get(t, 0) + 1
            return {
                "total_snapshots": len(all_snaps),
                "guests_with_snapshots": len({s["vmid"] for s in all_snaps}),
                "total_guests": len(non_tpl),
                "tag_groups": sorted(
                    [{"tag": k, "count": v} for k, v in tag_counts.items()],
                    key=lambda x: x["count"],
                    reverse=True,
                ),
                "snapshots": all_snaps,
            }

        return await self._run_audited(
            source=source,
            action="list_all_snapshots",
            risk="read",
            target="snapshots",
            params=None,
            confirm=False,
            coro_fn=_op,
        )

    async def create_group_checkpoint(
        self,
        *,
        tag: str,
        snapname: str | None = None,
        description: str = "",
        source: str = "webui",
    ) -> dict[str, Any]:
        guests = await self.list_guests(tag=tag, source=source)
        vmids = [int(g["vmid"]) for g in guests if not g.get("template")]
        if not vmids:
            raise ProxmoxAPIError(f"No guests found matching tag '{tag}'.", status_code=404)
        sname = snapname or f"chk-{tag.lower().replace(' ', '-')[:14]}-{time.strftime('%Y%m%d-%H%M')}"
        res = await self.bulk_guest_action(vmids, "snapshot", snapname=sname, source=source)
        return {"tag": tag, "snapname": sname, "description": description, **res}
