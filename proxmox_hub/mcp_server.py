"""Proxmox Homelab MCP Server (stdio + Streamable HTTP) built on official mcp SDK v2."""

from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver import MCPServer

from .service import ProxmoxService

MCP_TOOL_CATALOG: list[dict[str, str]] = [
    {"name": "get_cluster_overview", "category": "Monitoring", "risk": "read", "summary": "Cluster nodes, version, aggregate CPU/RAM/storage, and guest counts."},
    {"name": "get_node_details", "category": "Monitoring", "risk": "read", "summary": "Deep host telemetry: kernel, load average, CPU model, services, network bridges, storages."},
    {"name": "get_node_metrics", "category": "Monitoring", "risk": "read", "summary": "Node RRD performance history (hour/day/week/month/year) with average and peak stats."},
    {"name": "list_guests", "category": "Guests", "risk": "read", "summary": "List QEMU VMs and LXC containers with optional filters for node, type, status, or tag."},
    {"name": "get_guest_details", "category": "Guests", "risk": "read", "summary": "Inspect a VM or LXC: hardware config, live metrics, IPs, snapshots, and RRD summary."},
    {"name": "list_storage_and_disks", "category": "Storage", "risk": "read", "summary": "Inspect storage pools, physical disks + SMART health, ZFS pools, ISOs, templates, and backups."},
    {"name": "get_provisioning_options", "category": "Provisioning", "risk": "read", "summary": "Discover next free VMID, active storages, ISOs, LXC templates, VM templates, and bridges."},
    {"name": "list_tasks", "category": "Operations", "risk": "read", "summary": "List recent Proxmox background tasks with optional VMID or errors-only filtering."},
    {"name": "get_task_log", "category": "Operations", "risk": "read", "summary": "Fetch live/historical status and console log lines for a Proxmox UPID task."},
    {"name": "analyze_homelab_health", "category": "Health", "risk": "read", "summary": "Automated homelab health score checking CPU/RAM pressure, storage >80%, SMART, ZFS, tasks, and backups."},
    {"name": "get_network_ipam", "category": "Network", "risk": "read", "summary": "Full Network & IPAM matrix across bridges (vmbr0/vmbr1/vmbr2), NICs, MACs, IPs, conflicts, and free IP suggestions."},
    {"name": "get_backup_coverage", "category": "Backups", "risk": "read", "summary": "Cross-reference every VM/LXC against PBS/vzdump archives to inspect backup age and unbacked guests."},
    {"name": "get_capacity_analysis", "category": "Capacity", "risk": "read", "summary": "Allocated vs actual vCPU/RAM oversubscription, right-sizing suggestions, top I/O talkers, and storage/mount mapping."},
    {"name": "get_audit_log", "category": "Operations", "risk": "read", "summary": "View recent audit events recorded across MCP agents and WebUI actions."},
    {"name": "guest_power_action", "category": "Lifecycle", "risk": "write", "summary": "Start, shutdown, stop, reboot, suspend, resume, or reset a VM or LXC container."},
    {"name": "update_guest_config", "category": "Lifecycle", "risk": "write", "summary": "Update guest CPU cores, RAM, onboot flag, tags, name, or description."},
    {"name": "create_vm", "category": "Provisioning", "risk": "write", "summary": "Create a new QEMU VM with VirtIO-SCSI, QEMU Guest Agent, optional ISO, and auto-VMID."},
    {"name": "create_lxc", "category": "Provisioning", "risk": "write", "summary": "Create a new LXC container from an OS template with DHCP/static IP, nesting, and SSH keys."},
    {"name": "clone_guest", "category": "Provisioning", "risk": "write", "summary": "Clone an existing VM/template or LXC container to a new VMID."},
    {"name": "list_snapshots", "category": "Snapshots", "risk": "read", "summary": "List existing snapshots for a VM or LXC container."},
    {"name": "create_snapshot", "category": "Snapshots", "risk": "write", "summary": "Create a point-in-time snapshot of a VM or LXC (optionally including RAM state)."},
    {"name": "rollback_snapshot", "category": "Snapshots", "risk": "destructive", "summary": "Roll back a VM or LXC to a previous snapshot (requires confirm=true & destructive policy)."},
    {"name": "delete_snapshot", "category": "Snapshots", "risk": "destructive", "summary": "Delete a snapshot from a VM or LXC (requires confirm=true & destructive policy)."},
    {"name": "trigger_backup", "category": "Backups", "risk": "write", "summary": "Trigger an on-demand VZDump backup for a VM or LXC to a backup storage target."},
    {"name": "delete_guest", "category": "Lifecycle", "risk": "destructive", "summary": "Permanently destroy a stopped VM or LXC (requires confirm=true & ALLOW_DESTRUCTIVE=true)."},
    {"name": "search_isos", "category": "ISO Hub", "risk": "read", "summary": "Search the curated homelab ISO catalog and live web results for downloadable OS ISO images."},
    {"name": "download_iso_from_url", "category": "ISO Hub", "risk": "write", "summary": "Tell Proxmox to download an ISO or image directly from a remote URL onto a storage pool."},
    {"name": "list_lxc_appliances", "category": "ISO Hub", "risk": "read", "summary": "Search official Proxmox LXC appliance templates (Debian, Ubuntu, Alpine, TurnKey Linux)."},
    {"name": "download_lxc_template", "category": "ISO Hub", "risk": "write", "summary": "Download an official LXC template from Proxmox's appliance catalog onto a storage pool."},
    {"name": "browse_storage_files", "category": "File Explorer", "risk": "read", "summary": "Explore files, ISOs, disk images, templates, snippets, and backups across Proxmox storage pools."},
    {"name": "guest_agent_read_file", "category": "File Explorer", "risk": "read", "summary": "Read a file inside a running QEMU VM via QEMU Guest Agent (no SSH required)."},
    {"name": "guest_agent_exec", "category": "File Explorer", "risk": "write", "summary": "Execute a shell command inside a running QEMU VM via QEMU Guest Agent and return stdout/stderr."},
    {"name": "get_host_updates_and_logs", "category": "Maintenance", "risk": "read", "summary": "Inspect pending Proxmox/Debian APT updates, package versions, host syslog, and firewall rules."},
    {"name": "evaluate_and_send_alert", "category": "Alerts", "risk": "read", "summary": "Evaluate homelab health thresholds and optionally dispatch a Discord/Slack/ntfy webhook alert."},
    {"name": "search_community_scripts", "category": "Community Scripts", "risk": "read", "summary": "Search the live 800+ community-scripts.org Proxmox catalog or browse the latest newly added scripts."},
    {"name": "get_community_script_defaults", "category": "Community Scripts", "risk": "read", "summary": "Inspect a Community Script's live upstream defaults (CPU/RAM/disk/OS/tags) and host context for Advanced LXC setup."},
    {"name": "deploy_community_script", "category": "Community Scripts", "risk": "write", "summary": "Deploy a Community Script on Proxmox via SSH with custom Advanced LXC settings (unattended mode)."},
    {"name": "get_service_uptime", "category": "Monitoring", "risk": "read", "summary": "Probe and inspect live HTTP/TCP service uptime, response latency, and 24h availability across homelab web apps."},
    {"name": "list_docker_containers", "category": "Docker", "risk": "read", "summary": "List all Docker containers running inside Proxmox QEMU VMs (via Guest Agent) and LXC Docker hosts."},
    {"name": "docker_container_action", "category": "Docker", "risk": "write", "summary": "Start, stop, restart, or read recent logs of a Docker container on a Proxmox guest."},
    {"name": "get_hardware_power_telemetry", "category": "Hardware", "risk": "read", "summary": "Fetch Dell PowerEdge iDRAC Redfish telemetry: live Watts draw, monthly electricity cost, CPU/Inlet temps, Fan RPMs & PSUs."},
    {"name": "send_wake_on_lan", "category": "Network", "risk": "write", "summary": "Send a UDP Wake-on-LAN Magic Packet to power on a physical device on your LAN."},
    {"name": "list_all_snapshots", "category": "Snapshots", "risk": "read", "summary": "Audit all snapshots across every VM and LXC in the cluster plus available tag groups for checkpoints."},
    {"name": "create_group_checkpoint", "category": "Snapshots", "risk": "write", "summary": "Snapshot every VM/LXC matching a tag (e.g. 'arr', 'ai', 'docker') with a unified pre-maintenance checkpoint."},
    {"name": "get_truenas_overview", "category": "Storage", "risk": "read", "summary": "Read-only bare-metal TrueNAS telemetry: system/ECC RAM/ARC, ZFS pools & disk errors, temperatures, datasets, NFS/SMB/iSCSI shares, services, and active alerts."},
]


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


def _summarize_rrd(points: list[dict[str, Any]]) -> dict[str, Any]:
    if not points:
        return {"sample_count": 0}
    cpus = [p["cpu"] * 100 for p in points if p.get("cpu") is not None]
    mem_pcts = []
    for p in points:
        used = p.get("memused") if p.get("memused") is not None else p.get("mem")
        total = p.get("memtotal") if p.get("memtotal") is not None else p.get("maxmem")
        if used is not None and total:
            mem_pcts.append((used / total) * 100)
    return {
        "sample_count": len(points),
        "cpu_avg_pct": round(sum(cpus) / len(cpus), 2) if cpus else 0.0,
        "cpu_peak_pct": round(max(cpus), 2) if cpus else 0.0,
        "mem_avg_pct": round(sum(mem_pcts) / len(mem_pcts), 2) if mem_pcts else 0.0,
        "mem_peak_pct": round(max(mem_pcts), 2) if mem_pcts else 0.0,
        "latest_point": points[-1],
    }


def create_mcp_server(service: ProxmoxService) -> MCPServer:
    """Create and configure the Proxmox Homelab MCPServer instance."""

    mcp = MCPServer(
        name="proxmox-homelab-hub",
        title="Proxmox Homelab Hub",
        version="1.0.0",
        instructions=(
            "You are connected to a Proxmox VE homelab management server. "
            "Always inspect cluster capacity and provisioning options before creating VMs or LXCs. "
            "For risky changes, recommend or create a snapshot first. "
            "Destructive actions require explicit user confirmation (confirm=True)."
        ),
    )

    # ── Monitoring & Inspection Tools ────────────────────────────────────
    @mcp.tool()
    async def get_cluster_overview() -> str:
        """Get Proxmox cluster overview: version, nodes, aggregate CPU/RAM/storage, and running VM/LXC counts."""
        return _json(await service.get_cluster_overview(source="mcp"))

    @mcp.tool()
    async def get_node_details(node: str | None = None) -> str:
        """Get detailed status for a Proxmox node including CPU model, kernel, memory, swap, services, network bridges, and storages."""
        return _json(await service.get_node_details(node=node, source="mcp"))

    @mcp.tool()
    async def get_node_metrics(node: str | None = None, timeframe: str = "hour") -> str:
        """Get node RRD performance metrics over timeframe ('hour', 'day', 'week', 'month', 'year') with avg/peak summary."""
        data = await service.get_node_rrd(node=node, timeframe=timeframe, source="mcp")
        summary = _summarize_rrd(data.get("points", []))
        return _json(
            {
                "node": data["node"],
                "timeframe": data["timeframe"],
                "summary": summary,
                "recent_points": data["points"][-12:],
            }
        )

    @mcp.tool()
    async def list_guests(
        node: str | None = None,
        guest_type: str | None = None,
        status: str | None = None,
        tag: str | None = None,
    ) -> str:
        """List all QEMU VMs and LXC containers. Optional filters: node, guest_type ('qemu'|'lxc'), status ('running'|'stopped'), tag."""
        return _json(
            await service.list_guests(
                node=node, guest_type=guest_type, status=status, tag=tag, source="mcp"
            )
        )

    @mcp.tool()
    async def get_guest_details(
        vmid: int,
        node: str | None = None,
        timeframe: str = "hour",
    ) -> str:
        """Get full details of a VM or LXC container: status, hardware config, detected IPs, snapshots, and RRD summary."""
        detail = await service.get_guest_detail(
            vmid=vmid, node=node, timeframe=timeframe, source="mcp"
        )
        detail["rrd_summary"] = _summarize_rrd(detail.pop("rrd", []))
        return _json(detail)

    @mcp.tool()
    async def list_storage_and_disks(node: str | None = None) -> str:
        """Inspect storage pools, physical disks with SMART health, ZFS pools, ISOs, LXC templates, backup jobs, and recent backups."""
        return _json(await service.get_storage_and_disks(node=node, source="mcp"))

    @mcp.tool()
    async def get_provisioning_options(node: str | None = None) -> str:
        """Discover next free VMID, disk storages, backup storages, available ISOs, LXC OS templates, VM templates, and network bridges."""
        return _json(await service.get_provisioning_options(node=node))

    @mcp.tool()
    async def list_tasks(
        node: str | None = None,
        limit: int = 25,
        vmid: int | None = None,
        errors_only: bool = False,
    ) -> str:
        """List recent Proxmox background tasks. Filter optionally by node, vmid, or errors_only=True."""
        return _json(
            await service.list_tasks(
                node=node, limit=limit, vmid=vmid, errors_only=errors_only, source="mcp"
            )
        )

    @mcp.tool()
    async def get_task_log(upid: str, node: str | None = None) -> str:
        """Inspect the execution status and full console log lines of a Proxmox UPID task."""
        return _json(await service.get_task_detail(upid=upid, node=node, source="mcp"))

    @mcp.tool()
    async def analyze_homelab_health() -> str:
        """Run automated homelab health diagnostics: checks node load, RAM pressure, storage pools >80%, disk SMART life remaining, ZFS fragmentation, failed tasks, and backup coverage."""
        return _json(await service.analyze_homelab_health(source="mcp"))

    @mcp.tool()
    async def get_network_ipam() -> str:
        """Get the complete homelab Network & IPAM matrix: all bridges, guest NICs (net0/net1), VLANs, MACs, static/agent IPs, IP conflict detection, and free IP suggestions."""
        data = await service.get_fleet_insights(source="mcp")
        return _json(data["network_ipam"])

    @mcp.tool()
    async def get_backup_coverage() -> str:
        """Check backup coverage across all VMs and LXCs against PBS and backup storages, including last archive age and unbacked guests."""
        data = await service.get_fleet_insights(source="mcp")
        return _json(data["backup_coverage"])

    @mcp.tool()
    async def get_capacity_analysis() -> str:
        """Analyze host vs allocated vCPU/RAM oversubscription, right-sizing opportunities, top CPU/RAM/Disk-IO/Net-IO consumers, and storage-to-guest / bind-mount mappings."""
        data = await service.get_fleet_insights(source="mcp")
        return _json({"capacity": data["capacity"], "storage_map": data["storage_map"]})

    @mcp.tool()
    async def get_audit_log(limit: int = 30, source_filter: str | None = None) -> str:
        """View recent audit log entries from both MCP tool calls and WebUI actions."""
        return _json(service.audit.list_recent(limit=limit, source=source_filter))

    # ── Lifecycle & Config Tools ─────────────────────────────────────────
    @mcp.tool()
    async def guest_power_action(
        vmid: int,
        action: str,
        node: str | None = None,
        wait_for_completion: bool = True,
    ) -> str:
        """Perform a power action on a VM or LXC: 'start', 'shutdown', 'stop', 'reboot', 'suspend', 'resume', or 'reset'."""
        return _json(
            await service.guest_power(
                vmid=vmid,
                action=action,
                node=node,
                wait=wait_for_completion,
                source="mcp",
            )
        )

    @mcp.tool()
    async def update_guest_config(
        vmid: int,
        cores: int | None = None,
        memory_mb: int | None = None,
        onboot: bool | None = None,
        tags: str | None = None,
        description: str | None = None,
        name: str | None = None,
        node: str | None = None,
    ) -> str:
        """Modify a VM or LXC's CPU cores, memory (MB), start-on-boot flag, semicolon-separated tags, description, or name."""
        return _json(
            await service.update_guest_config(
                vmid=vmid,
                cores=cores,
                memory_mb=memory_mb,
                onboot=onboot,
                tags=tags,
                description=description,
                name=name,
                node=node,
                source="mcp",
            )
        )

    # ── Provisioning Tools ───────────────────────────────────────────────
    @mcp.tool()
    async def create_vm(
        name: str,
        cores: int = 2,
        memory_mb: int = 2048,
        disk_size_gb: int = 32,
        disk_storage: str = "local-lvm",
        iso: str | None = None,
        bridge: str = "vmbr0",
        vlan_tag: int | None = None,
        ostype: str = "l26",
        bios: str = "seabios",
        onboot: bool = False,
        start_after_create: bool = False,
        tags: str = "",
        description: str = "",
        vmid: int | None = None,
        node: str | None = None,
        wait_for_completion: bool = True,
    ) -> str:
        """Create a new QEMU Virtual Machine. Call get_provisioning_options first to discover valid storages, ISOs, and bridges."""
        return _json(
            await service.create_vm(
                name=name,
                node=node,
                vmid=vmid,
                cores=cores,
                memory_mb=memory_mb,
                disk_storage=disk_storage,
                disk_size_gb=disk_size_gb,
                iso=iso,
                bridge=bridge,
                vlan_tag=vlan_tag,
                ostype=ostype,
                bios=bios,
                onboot=onboot,
                start_after_create=start_after_create,
                tags=tags,
                description=description,
                wait=wait_for_completion,
                source="mcp",
            )
        )

    @mcp.tool()
    async def create_lxc(
        hostname: str,
        ostemplate: str,
        cores: int = 1,
        memory_mb: int = 1024,
        swap_mb: int = 512,
        disk_size_gb: int = 8,
        disk_storage: str = "local-lvm",
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
        vmid: int | None = None,
        node: str | None = None,
        wait_for_completion: bool = True,
    ) -> str:
        """Create a new LXC container from an OS template (e.g. 'local:vztmpl/debian-12-standard_...tar.zst'). Call get_provisioning_options to list templates."""
        return _json(
            await service.create_lxc(
                hostname=hostname,
                ostemplate=ostemplate,
                node=node,
                vmid=vmid,
                cores=cores,
                memory_mb=memory_mb,
                swap_mb=swap_mb,
                disk_storage=disk_storage,
                disk_size_gb=disk_size_gb,
                bridge=bridge,
                ip_config=ip_config,
                gateway=gateway,
                password=password,
                ssh_public_keys=ssh_public_keys,
                unprivileged=unprivileged,
                nesting=nesting,
                onboot=onboot,
                start_after_create=start_after_create,
                tags=tags,
                description=description,
                wait=wait_for_completion,
                source="mcp",
            )
        )

    @mcp.tool()
    async def clone_guest(
        source_vmid: int,
        new_name: str,
        new_vmid: int | None = None,
        full_clone: bool = True,
        target_storage: str | None = None,
        node: str | None = None,
        wait_for_completion: bool = True,
    ) -> str:
        """Clone an existing QEMU VM, VM template, or LXC container to a new guest."""
        return _json(
            await service.clone_guest(
                source_vmid=source_vmid,
                new_name=new_name,
                new_vmid=new_vmid,
                full_clone=full_clone,
                target_storage=target_storage,
                node=node,
                wait=wait_for_completion,
                source="mcp",
            )
        )

    # ── Snapshots & Backups ──────────────────────────────────────────────
    @mcp.tool()
    async def list_snapshots(vmid: int, node: str | None = None) -> str:
        """List all snapshots for a VM or LXC container."""
        return _json(await service.list_snapshots(vmid=vmid, node=node, source="mcp"))

    @mcp.tool()
    async def create_snapshot(
        vmid: int,
        snapname: str,
        description: str = "",
        include_ram: bool = False,
        node: str | None = None,
        wait_for_completion: bool = True,
    ) -> str:
        """Create a snapshot of a VM or LXC before making changes. snapname must start with a letter and contain only letters, digits, '-' or '_'."""
        return _json(
            await service.create_snapshot(
                vmid=vmid,
                snapname=snapname,
                description=description,
                vmstate=include_ram,
                node=node,
                wait=wait_for_completion,
                source="mcp",
            )
        )

    @mcp.tool()
    async def rollback_snapshot(
        vmid: int,
        snapname: str,
        confirm: bool = False,
        node: str | None = None,
        wait_for_completion: bool = True,
    ) -> str:
        """Roll back a VM or LXC to a named snapshot. DESTRUCTIVE: discards state since snapshot; requires confirm=True and destructive permissions."""
        return _json(
            await service.rollback_snapshot(
                vmid=vmid,
                snapname=snapname,
                confirm=confirm,
                node=node,
                wait=wait_for_completion,
                source="mcp",
            )
        )

    @mcp.tool()
    async def delete_snapshot(
        vmid: int,
        snapname: str,
        confirm: bool = False,
        node: str | None = None,
    ) -> str:
        """Delete a snapshot from a VM or LXC. DESTRUCTIVE: requires confirm=True and destructive permissions."""
        return _json(
            await service.delete_snapshot(
                vmid=vmid,
                snapname=snapname,
                confirm=confirm,
                node=node,
                source="mcp",
            )
        )

    @mcp.tool()
    async def trigger_backup(
        vmid: int,
        storage: str | None = None,
        mode: str = "snapshot",
        compress: str = "zstd",
        notes: str = "",
        node: str | None = None,
        wait_for_completion: bool = False,
    ) -> str:
        """Trigger an on-demand VZDump backup for a VM or LXC. mode: 'snapshot'|'suspend'|'stop'; compress: 'zstd'|'gzip'|'0'."""
        return _json(
            await service.trigger_backup(
                vmid=vmid,
                storage=storage,
                mode=mode,
                compress=compress,
                notes=notes,
                node=node,
                wait=wait_for_completion,
                source="mcp",
            )
        )

    @mcp.tool()
    async def delete_guest(
        vmid: int,
        confirm: bool = False,
        purge: bool = True,
        node: str | None = None,
    ) -> str:
        """Permanently destroy a stopped VM or LXC and its disks. DESTRUCTIVE: requires confirm=True AND ALLOW_DESTRUCTIVE=true (or SAFETY_MODE=full)."""
        return _json(
            await service.delete_guest(
                vmid=vmid,
                confirm=confirm,
                purge=purge,
                node=node,
                source="mcp",
            )
        )

    # ── ISO Hub, File Explorer, Maintenance & Alerts ─────────────────────
    @mcp.tool()
    async def search_isos(query: str = "", include_web: bool = True) -> str:
        """Search both the verified homelab ISO library and live web results for OS ISO download URLs."""
        return _json(await service.search_isos(query=query, include_web=include_web))

    @mcp.tool()
    async def download_iso_from_url(
        storage: str,
        url: str,
        filename: str,
        content: str = "iso",
        node: str | None = None,
    ) -> str:
        """Instruct Proxmox to download an ISO or template directly from a URL onto target storage (e.g. 'ISO' or 'local')."""
        return _json(
            await service.download_iso_from_url(
                storage=storage, url=url, filename=filename, content=content, node=node, source="mcp"
            )
        )

    @mcp.tool()
    async def list_lxc_appliances(search: str = "", section: str = "", node: str | None = None) -> str:
        """Browse/search official Proxmox LXC templates available via aplinfo (Debian, Ubuntu, Alpine, TurnKey)."""
        return _json(await service.list_lxc_appliances(node=node, search=search, section=section, source="mcp"))

    @mcp.tool()
    async def download_lxc_template(storage: str, template: str, node: str | None = None) -> str:
        """Download an official LXC template from Proxmox aplinfo to a target storage pool."""
        return _json(await service.download_lxc_appliance(storage=storage, template=template, node=node, source="mcp"))

    @mcp.tool()
    async def browse_storage_files(
        storage: str | None = None, content: str | None = None, node: str | None = None
    ) -> str:
        """Explore volumes, ISOs, templates, backups, and disk images across Proxmox storages."""
        return _json(await service.browse_storage_volumes(storage=storage, content=content, node=node, source="mcp"))

    @mcp.tool()
    async def guest_agent_read_file(vmid: int, file_path: str, node: str | None = None) -> str:
        """Read a text/config file inside a running QEMU VM using QEMU Guest Agent (no SSH needed)."""
        return _json(await service.guest_agent_read_file(vmid=vmid, file_path=file_path, node=node, source="mcp"))

    @mcp.tool()
    async def guest_agent_exec(vmid: int, command: str, node: str | None = None) -> str:
        """Execute a shell command inside a running QEMU VM via QEMU Guest Agent and return exit code, stdout, and stderr."""
        return _json(await service.guest_agent_exec_command(vmid=vmid, command=command, node=node, source="mcp"))

    @mcp.tool()
    async def get_host_updates_and_logs(
        syslog_limit: int = 80, syslog_service: str | None = None, node: str | None = None
    ) -> str:
        """Inspect pending Proxmox host APT updates, installed versions, host syslog, and cluster/node firewall rules."""
        return _json(
            await service.get_host_maintenance(
                node=node, syslog_limit=syslog_limit, syslog_service=syslog_service, source="mcp"
            )
        )

    @mcp.tool()
    async def evaluate_and_send_alert(force_send: bool = False) -> str:
        """Evaluate homelab health thresholds and send a Discord/Slack/ntfy webhook alert if triggered (or force_send=True)."""
        return _json(await service.evaluate_and_notify(force_send=force_send, source="mcp"))

    @mcp.tool()
    async def search_community_scripts(
        query: str = "",
        category: str = "",
        script_type: str = "",
        sort: str = "latest",
        limit: int = 25,
    ) -> str:
        """Search the live community-scripts.org catalog (800+ Proxmox helper scripts). Use sort='latest' to see newest scripts."""
        res = await service.list_community_scripts(
            query=query, category=category, script_type=script_type, sort=sort, limit=limit
        )
        return _json(
            {
                "total_catalog": res["total_catalog"],
                "matched": res["matched"],
                "latest_5": [
                    {"slug": s["slug"], "name": s["name"], "type": s["type"], "created": s["script_created"]}
                    for s in res["latest"][:5]
                ],
                "scripts": res["scripts"],
            }
        )

    @mcp.tool()
    async def get_community_script_defaults(slug: str, script_type: str = "lxc") -> str:
        """Fetch live upstream defaults (var_cpu, var_ram, var_disk, var_os, var_tags, etc.) and live Proxmox host storages/bridges for a Community Script."""
        return _json(await service.get_community_script_setup(slug=slug, script_type=script_type))

    @mcp.tool()
    async def deploy_community_script(
        slug: str,
        script_type: str = "lxc",
        ctid: int | None = None,
        hostname: str | None = None,
        cpu: int | None = None,
        ram: int | None = None,
        disk: int | None = None,
        container_storage: str | None = None,
        template_storage: str | None = None,
        bridge: str | None = None,
        net: str = "dhcp",
        gateway: str | None = None,
        vlan: int | None = None,
        unprivileged: bool = True,
        tags: str | None = None,
        unattended: bool = True,
    ) -> str:
        """Deploy a Community Script directly to the Proxmox host via SSH using Advanced LXC variables (requires Safe/Full mode and SSH key auth)."""
        opts = {
            "unattended": unattended,
            "ctid": ctid,
            "hostname": hostname,
            "cpu": cpu,
            "ram": ram,
            "disk": disk,
            "container_storage": container_storage,
            "template_storage": template_storage,
            "bridge": bridge,
            "net": net,
            "gateway": gateway,
            "vlan": vlan,
            "unprivileged": unprivileged,
            "tags": tags,
        }
        return _json(
            await service.deploy_community_script(
                slug=slug, script_type=script_type, options=opts, source="mcp"
            )
        )

    @mcp.tool()
    async def get_service_uptime(run_fresh_probe: bool = True) -> str:
        """Check live HTTP/TCP service health, latency (ms), and 24h uptime % for all auto-detected & bookmarked homelab web apps."""
        if run_fresh_probe:
            return _json(await service.run_uptime_probe(source="mcp"))
        return _json(await service.get_uptime_dashboard(run_if_empty=True))

    @mcp.tool()
    async def list_docker_containers() -> str:
        """Discover and list Docker containers across Proxmox QEMU Docker VMs (via Guest Agent) and Docker LXCs."""
        return _json(await service.list_docker_containers(source="mcp"))

    @mcp.tool()
    async def docker_container_action(
        host_vmid: int, container_name: str, action: str = "logs", tail: int = 80
    ) -> str:
        """Perform 'start', 'stop', 'restart', or 'logs' on a Docker container hosted on a Proxmox VM or LXC."""
        return _json(
            await service.docker_container_action(
                host_vmid=host_vmid, container_name=container_name, action=action, tail=tail, source="mcp"
            )
        )

    @mcp.tool()
    async def get_hardware_power_telemetry() -> str:
        """Query Dell iDRAC Redfish API for live power draw (Watts), estimated monthly cost, CPU/Inlet temperatures, Fan RPMs, and PSU status."""
        return _json(await service.get_hardware_telemetry(source="mcp"))

    @mcp.tool()
    async def send_wake_on_lan(mac: str, broadcast: str = "255.255.255.255") -> str:
        """Send a Wake-on-LAN Magic Packet to wake a physical LAN machine by MAC address."""
        return _json(await service.send_wol(mac=mac, broadcast=broadcast, source="mcp"))

    @mcp.tool()
    async def list_all_snapshots() -> str:
        """Audit all snapshots across all VMs and LXCs in the homelab and see tag groups eligible for group checkpoints."""
        return _json(await service.list_all_snapshots(source="mcp"))

    @mcp.tool()
    async def create_group_checkpoint(tag: str, snapname: str | None = None) -> str:
        """Create a pre-maintenance snapshot checkpoint across all VMs/LXCs sharing a tag (e.g. 'arr', 'ai', 'docker')."""
        return _json(await service.create_group_checkpoint(tag=tag, snapname=snapname, source="mcp"))

    @mcp.tool()
    async def get_truenas_overview(force_refresh: bool = False) -> str:
        """Read-only TrueNAS SCALE/CORE monitoring: hostname, version, ECC memory & ZFS ARC, ZFS pool health & disk errors, drive temps, datasets, NFS/SMB/iSCSI shares, services, active alerts, and matched Proxmox storages."""
        return _json(await service.get_truenas_telemetry(force_refresh=force_refresh, source="mcp"))

    # ── MCP Resources ────────────────────────────────────────────────────
    @mcp.resource("proxmox://cluster/overview")
    async def resource_cluster_overview() -> str:
        """Live snapshot of the Proxmox cluster overview."""
        return _json(await service.get_cluster_overview(source="mcp"))

    @mcp.resource("proxmox://health/report")
    async def resource_health_report() -> str:
        """Live automated homelab health analysis report."""
        return _json(await service.analyze_homelab_health(source="mcp"))

    @mcp.resource("proxmox://audit/recent")
    async def resource_audit_recent() -> str:
        """Recent 30 MCP and WebUI audit events."""
        return _json(service.audit.list_recent(limit=30))

    # ── MCP Prompts ──────────────────────────────────────────────────────
    @mcp.prompt()
    def homelab_daily_checkup() -> str:
        """Run a thorough daily health & capacity checkup of the Proxmox homelab."""
        return (
            "Please perform a daily homelab checkup on my Proxmox server:\n"
            "1. Call `get_cluster_overview` and `analyze_homelab_health`.\n"
            "2. Call `list_tasks` with `errors_only=True` to spot any recent failures.\n"
            "3. Call `list_storage_and_disks` to verify ZFS/storage capacity and SMART drive status.\n"
            "4. Summarize overall health score, resource headroom, any warnings, and prioritized next actions."
        )

    @mcp.prompt()
    def plan_new_workload(workload_description: str) -> str:
        """Plan and right-size a new VM or LXC container for a homelab service."""
        return (
            f"I want to deploy the following homelab workload on Proxmox: {workload_description}\n"
            "1. Call `get_cluster_overview` and `get_provisioning_options` to check available CPU/RAM, storage pools, ISOs, templates, and bridges.\n"
            "2. Recommend whether an LXC container or a QEMU VM is better suited, with exact vCPU, RAM, disk size, storage pool, and tags.\n"
            "3. Ask me to confirm the parameters before calling `create_vm` or `create_lxc`."
        )

    @mcp.prompt()
    def safe_maintenance_workflow(vmid: int, planned_change: str) -> str:
        """Safely prepare a VM or LXC before an upgrade or risky configuration change."""
        return (
            f"I am planning maintenance on guest VMID {vmid}: {planned_change}\n"
            "1. Inspect the guest with `get_guest_details` and `list_snapshots`.\n"
            "2. Create a clearly named pre-maintenance snapshot (or trigger a backup if storage is tight).\n"
            "3. Confirm the snapshot succeeded and outline the rollback steps if anything goes wrong."
        )

    return mcp
