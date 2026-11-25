"""Realistic stateful in-memory Proxmox simulator for Demo / Preview Mode."""

from __future__ import annotations

import copy
import math
import time
import uuid
from typing import Any

from .client import ProxmoxAPIError

GIB = 1024**3
MIB = 1024**2


class DemoProxmoxClient:
    """Drop-in async replacement for ProxmoxClient when PROXMOX_DEMO_MODE=true."""

    def __init__(self) -> None:
        self._start_ts = int(time.time())
        self.node_name = "pve"
        self._guests: dict[int, dict[str, Any]] = {
            100: {
                "vmid": 100,
                "name": "docker-core",
                "type": "qemu",
                "node": "pve",
                "status": "running",
                "cpus": 4,
                "cpu": 0.24,
                "mem": int(9.8 * GIB),
                "maxmem": 16 * GIB,
                "disk": int(48 * GIB),
                "maxdisk": 120 * GIB,
                "uptime": 14 * 86400 + 7320,
                "netin": 48_920_114_210,
                "netout": 21_440_991_002,
                "tags": "docker;production;homelab",
                "template": 0,
                "config": {
                    "name": "docker-core",
                    "cores": 4,
                    "sockets": 1,
                    "memory": 16384,
                    "ostype": "l26",
                    "scsihw": "virtio-scsi-pci",
                    "scsi0": "local-lvm:vm-100-disk-0,size=120G",
                    "net0": "virtio=BC:24:11:AA:10:01,bridge=vmbr0",
                    "onboot": 1,
                    "agent": "1",
                    "tags": "docker;production;homelab",
                    "description": "Primary Docker host running Portainer, Traefik, Immich, and Forgejo.",
                },
                "ips": ["192.168.1.50"],
            },
            101: {
                "vmid": 101,
                "name": "home-assistant",
                "type": "qemu",
                "node": "pve",
                "status": "running",
                "cpus": 2,
                "cpu": 0.09,
                "mem": int(2.7 * GIB),
                "maxmem": 4 * GIB,
                "disk": int(18 * GIB),
                "maxdisk": 64 * GIB,
                "uptime": 29 * 86400 + 1840,
                "netin": 12_110_443_120,
                "netout": 8_330_112_880,
                "tags": "iot;critical;homelab",
                "template": 0,
                "config": {
                    "name": "home-assistant",
                    "cores": 2,
                    "sockets": 1,
                    "memory": 4096,
                    "ostype": "l26",
                    "bios": "ovmf",
                    "scsi0": "local-lvm:vm-101-disk-0,size=64G",
                    "net0": "virtio=BC:24:11:AA:10:02,bridge=vmbr0",
                    "onboot": 1,
                    "agent": "1",
                    "tags": "iot;critical;homelab",
                    "description": "Home Assistant OS with Zigbee USB passthrough.",
                },
                "ips": ["192.168.1.55"],
            },
            102: {
                "vmid": 102,
                "name": "win11-dev-lab",
                "type": "qemu",
                "node": "pve",
                "status": "stopped",
                "cpus": 4,
                "cpu": 0.0,
                "mem": 0,
                "maxmem": 8 * GIB,
                "disk": int(42 * GIB),
                "maxdisk": 100 * GIB,
                "uptime": 0,
                "netin": 0,
                "netout": 0,
                "tags": "windows;sandbox",
                "template": 0,
                "config": {
                    "name": "win11-dev-lab",
                    "cores": 4,
                    "sockets": 1,
                    "memory": 8192,
                    "ostype": "win11",
                    "bios": "ovmf",
                    "scsi0": "local-lvm:vm-102-disk-0,size=100G",
                    "net0": "virtio=BC:24:11:AA:10:03,bridge=vmbr0",
                    "onboot": 0,
                    "agent": "1",
                    "tags": "windows;sandbox",
                    "description": "On-demand Windows 11 testing workstation.",
                },
                "ips": [],
            },
            9000: {
                "vmid": 9000,
                "name": "ubuntu-2404-cloud-tpl",
                "type": "qemu",
                "node": "pve",
                "status": "stopped",
                "cpus": 2,
                "cpu": 0.0,
                "mem": 0,
                "maxmem": 2 * GIB,
                "disk": int(4 * GIB),
                "maxdisk": 20 * GIB,
                "uptime": 0,
                "netin": 0,
                "netout": 0,
                "tags": "template;ubuntu;cloud-init",
                "template": 1,
                "config": {
                    "name": "ubuntu-2404-cloud-tpl",
                    "cores": 2,
                    "sockets": 1,
                    "memory": 2048,
                    "ostype": "l26",
                    "template": 1,
                    "scsi0": "local-lvm:base-9000-disk-0,size=20G",
                    "net0": "virtio=BC:24:11:AA:90:00,bridge=vmbr0",
                    "onboot": 0,
                    "agent": "1",
                    "tags": "template;ubuntu;cloud-init",
                    "description": "Cloud-init ready Ubuntu 24.04 LTS golden image.",
                },
                "ips": [],
            },
            200: {
                "vmid": 200,
                "name": "adguard-dns",
                "type": "lxc",
                "node": "pve",
                "status": "running",
                "cpus": 1,
                "cpu": 0.03,
                "mem": int(220 * MIB),
                "maxmem": 1 * GIB,
                "disk": int(1.6 * GIB),
                "maxdisk": 8 * GIB,
                "uptime": 41 * 86400 + 3210,
                "netin": 9_441_002_110,
                "netout": 6_810_220_410,
                "tags": "dns;network;critical",
                "template": 0,
                "config": {
                    "hostname": "adguard-dns",
                    "cores": 1,
                    "memory": 1024,
                    "swap": 512,
                    "ostype": "debian",
                    "rootfs": "local-lvm:subvol-200-disk-0,size=8G",
                    "net0": "name=eth0,bridge=vmbr0,ip=192.168.1.3/24,gw=192.168.1.1",
                    "unprivileged": 1,
                    "onboot": 1,
                    "tags": "dns;network;critical",
                    "description": "Network-wide AdGuard Home DNS sinkhole.",
                },
                "ips": ["192.168.1.3"],
            },
            201: {
                "vmid": 201,
                "name": "jellyfin-media",
                "type": "lxc",
                "node": "pve",
                "status": "running",
                "cpus": 4,
                "cpu": 0.16,
                "mem": int(3.1 * GIB),
                "maxmem": 6 * GIB,
                "disk": int(14 * GIB),
                "maxdisk": 32 * GIB,
                "uptime": 9 * 86400 + 440,
                "netin": 88_120_004_900,
                "netout": 241_800_112_300,
                "tags": "media;gpu",
                "template": 0,
                "config": {
                    "hostname": "jellyfin-media",
                    "cores": 4,
                    "memory": 6144,
                    "swap": 1024,
                    "ostype": "ubuntu",
                    "rootfs": "local-lvm:subvol-201-disk-0,size=32G",
                    "net0": "name=eth0,bridge=vmbr0,ip=192.168.1.60/24,gw=192.168.1.1",
                    "unprivileged": 1,
                    "features": "nesting=1",
                    "onboot": 1,
                    "tags": "media;gpu",
                    "description": "Jellyfin media server with Intel QuickSync iGPU.",
                },
                "ips": ["192.168.1.60"],
            },
            202: {
                "vmid": 202,
                "name": "monitoring-stack",
                "type": "lxc",
                "node": "pve",
                "status": "running",
                "cpus": 2,
                "cpu": 0.11,
                "mem": int(2.4 * GIB),
                "maxmem": 4 * GIB,
                "disk": int(9.2 * GIB),
                "maxdisk": 24 * GIB,
                "uptime": 14 * 86400 + 6900,
                "netin": 19_200_331_000,
                "netout": 7_112_980_000,
                "tags": "monitoring;prometheus;grafana",
                "template": 0,
                "config": {
                    "hostname": "monitoring-stack",
                    "cores": 2,
                    "memory": 4096,
                    "swap": 512,
                    "ostype": "debian",
                    "rootfs": "local-lvm:subvol-202-disk-0,size=24G",
                    "net0": "name=eth0,bridge=vmbr0,ip=192.168.1.65/24,gw=192.168.1.1",
                    "unprivileged": 1,
                    "features": "nesting=1",
                    "onboot": 1,
                    "tags": "monitoring;prometheus;grafana",
                    "description": "Prometheus + Grafana + Uptime Kuma observability.",
                },
                "ips": ["192.168.1.65"],
            },
        }

        now = int(time.time())
        self._snapshots: dict[int, list[dict[str, Any]]] = {
            100: [
                {
                    "name": "pre-traefik-v3",
                    "description": "Clean state before Traefik v3 upgrade",
                    "snaptime": now - 5 * 86400,
                    "vmstate": 0,
                    "parent": "",
                },
                {
                    "name": "current",
                    "description": "You are here!",
                    "parent": "pre-traefik-v3",
                },
            ],
            101: [
                {
                    "name": "ha-2026-11-backup",
                    "description": "Before core 2026.11 update",
                    "snaptime": now - 2 * 86400,
                    "vmstate": 1,
                    "parent": "",
                },
                {
                    "name": "current",
                    "description": "You are here!",
                    "parent": "ha-2026-11-backup",
                },
            ],
            200: [{"name": "current", "description": "You are here!"}],
            201: [{"name": "current", "description": "You are here!"}],
            202: [{"name": "current", "description": "You are here!"}],
            102: [{"name": "current", "description": "You are here!"}],
            9000: [{"name": "current", "description": "You are here!"}],
        }

        self._storages: list[dict[str, Any]] = [
            {
                "storage": "local",
                "type": "dir",
                "content": "iso,vztmpl,backup",
                "active": 1,
                "enabled": 1,
                "shared": 0,
                "total": 100 * GIB,
                "used": 34 * GIB,
                "avail": 66 * GIB,
                "used_fraction": 0.34,
            },
            {
                "storage": "local-lvm",
                "type": "lvmthin",
                "content": "images,rootdir",
                "active": 1,
                "enabled": 1,
                "shared": 0,
                "total": 900 * GIB,
                "used": 412 * GIB,
                "avail": 488 * GIB,
                "used_fraction": 0.4578,
            },
            {
                "storage": "zfs-tank",
                "type": "zfspool",
                "content": "images,rootdir",
                "active": 1,
                "enabled": 1,
                "shared": 0,
                "total": 3800 * GIB,
                "used": 3190 * GIB,
                "avail": 610 * GIB,
                "used_fraction": 0.8395,
            },
            {
                "storage": "pbs-backup",
                "type": "pbs",
                "content": "backup",
                "active": 1,
                "enabled": 1,
                "shared": 1,
                "total": 2000 * GIB,
                "used": 640 * GIB,
                "avail": 1360 * GIB,
                "used_fraction": 0.32,
            },
        ]

        self._storage_content: dict[str, list[dict[str, Any]]] = {
            "local": [
                {
                    "volid": "local:iso/ubuntu-24.04.2-live-server-amd64.iso",
                    "content": "iso",
                    "format": "iso",
                    "size": int(2.6 * GIB),
                    "ctime": now - 20 * 86400,
                },
                {
                    "volid": "local:iso/debian-12.8.0-amd64-netinst.iso",
                    "content": "iso",
                    "format": "iso",
                    "size": int(0.65 * GIB),
                    "ctime": now - 35 * 86400,
                },
                {
                    "volid": "local:iso/virtio-win-0.1.266.iso",
                    "content": "iso",
                    "format": "iso",
                    "size": int(0.71 * GIB),
                    "ctime": now - 12 * 86400,
                },
                {
                    "volid": "local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst",
                    "content": "vztmpl",
                    "format": "tzst",
                    "size": int(124 * MIB),
                    "ctime": now - 18 * 86400,
                },
                {
                    "volid": "local:vztmpl/ubuntu-24.04-standard_24.04-2_amd64.tar.zst",
                    "content": "vztmpl",
                    "format": "tzst",
                    "size": int(136 * MIB),
                    "ctime": now - 15 * 86400,
                },
                {
                    "volid": "local:vztmpl/alpine-3.20-default_20240908_amd64.tar.xz",
                    "content": "vztmpl",
                    "format": "txz",
                    "size": int(3.4 * MIB),
                    "ctime": now - 10 * 86400,
                },
            ],
            "pbs-backup": [
                {
                    "volid": "pbs-backup:backup/vm/100/2026-11-24T02:00:01Z",
                    "content": "backup",
                    "format": "pbs-vm",
                    "size": int(31 * GIB),
                    "vmid": 100,
                    "ctime": now - 86400,
                    "notes": "docker-core nightly",
                },
                {
                    "volid": "pbs-backup:backup/vm/101/2026-11-24T02:08:11Z",
                    "content": "backup",
                    "format": "pbs-vm",
                    "size": int(11 * GIB),
                    "vmid": 101,
                    "ctime": now - 86000,
                    "notes": "home-assistant nightly",
                },
                {
                    "volid": "pbs-backup:backup/ct/200/2026-11-24T02:11:05Z",
                    "content": "backup",
                    "format": "pbs-ct",
                    "size": int(1.2 * GIB),
                    "vmid": 200,
                    "ctime": now - 85700,
                    "notes": "adguard-dns nightly",
                },
            ],
        }

        self._tasks: list[dict[str, Any]] = [
            {
                "upid": f"UPID:pve:000A1B2C:0123ABCD:{now - 3600:08X}:vzdump:100:root@pam:",
                "node": "pve",
                "type": "vzdump",
                "id": "100",
                "user": "root@pam",
                "starttime": now - 3600,
                "endtime": now - 3480,
                "status": "OK",
                "log": [
                    "INFO: starting new backup job: vzdump 100 --storage pbs-backup --mode snapshot --compress zstd",
                    "INFO: Starting Backup of VM 100 (qemu)",
                    "INFO: status = running",
                    "INFO: backup mode: snapshot",
                    "INFO: creating Proxmox Backup Server archive 'vm/100/2026-11-24T02:00:01Z'",
                    "INFO: Finished Backup of VM 100 (00:02:00)",
                    "TASK OK",
                ],
            },
            {
                "upid": f"UPID:pve:000A1044:0120AAAA:{now - 7200:08X}:qmsnap:101:mcp@pve!homelab:",
                "node": "pve",
                "type": "qmsnap",
                "id": "101",
                "user": "mcp@pve!homelab",
                "starttime": now - 7200,
                "endtime": now - 7194,
                "status": "OK",
                "log": [
                    "snapshotting 'drive-scsi0' (local-lvm:vm-101-disk-0)",
                    "Saving VM state and RAM using storage 'local-lvm'",
                    "TASK OK",
                ],
            },
            {
                "upid": f"UPID:pve:0009F810:01190011:{now - 14400:08X}:aptupdate::root@pam:",
                "node": "pve",
                "type": "aptupdate",
                "id": "",
                "user": "root@pam",
                "starttime": now - 14400,
                "endtime": now - 14380,
                "status": "OK",
                "log": [
                    "starting apt-get update",
                    "Hit:1 http://deb.debian.org/debian bookworm InRelease",
                    "Reading package lists... Done",
                    "TASK OK",
                ],
            },
        ]

        self._backup_jobs: list[dict[str, Any]] = [
            {
                "id": "backup-nightly-homelab",
                "enabled": 1,
                "schedule": "02:00",
                "storage": "pbs-backup",
                "mode": "snapshot",
                "compress": "zstd",
                "vmid": "100,101,200,202",
                "mailnotification": "failure",
                "comment": "Nightly critical homelab workloads to PBS",
            }
        ]
        self._demo_fs: dict[str, str] = {
            "/etc/hostname": "docker-core\n",
            "/etc/hosts": "127.0.0.1 localhost\n192.168.1.50 docker-core\n",
            "/opt/docker/compose.yml": "services:\n  traefik:\n    image: traefik:v3.2\n    restart: unless-stopped\n  portainer:\n    image: portainer/portainer-ce:latest\n    restart: unless-stopped\n",
        }
        self._exec_outputs: dict[int, dict[str, Any]] = {}
        self._next_pid = 4100

    async def close(self) -> None:
        return None

    def _new_task(self, task_type: str, target_id: str, lines: list[str]) -> str:
        now = int(time.time())
        upid = f"UPID:pve:{uuid.uuid4().hex[:8].upper()}:01A2B3C4:{now:08X}:{task_type}:{target_id}:demo@pve!hub:"
        self._tasks.insert(
            0,
            {
                "upid": upid,
                "node": "pve",
                "type": task_type,
                "id": str(target_id),
                "user": "demo@pve!hub",
                "starttime": now,
                "endtime": now + 1,
                "status": "OK",
                "log": lines + ["TASK OK"],
            },
        )
        return upid

    def _require_guest(self, vmid: int, expected_type: str | None = None) -> dict[str, Any]:
        guest = self._guests.get(int(vmid))
        if not guest or (expected_type and guest["type"] != expected_type):
            raise ProxmoxAPIError(f"Guest {vmid} not found on node 'pve'.", status_code=404)
        return guest

    # ── Cluster & Version ────────────────────────────────────────────────
    async def get_version(self) -> dict[str, Any]:
        return {
            "version": "8.3.2",
            "release": "8.3",
            "repoid": "demo-sim-832",
        }

    async def get_cluster_status(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "node/pve",
                "name": "pve",
                "type": "node",
                "ip": "192.168.1.2",
                "online": 1,
                "local": 1,
                "nodeid": 1,
                "level": "",
            }
        ]

    async def get_cluster_resources(
        self, resource_type: str | None = None
    ) -> list[dict[str, Any]]:
        resources: list[dict[str, Any]] = []
        if resource_type in (None, "node"):
            resources.extend(await self.get_nodes())
        if resource_type in (None, "vm"):
            for g in self._guests.values():
                item = {k: v for k, v in g.items() if k not in ("config", "ips")}
                item["id"] = f"{g['type']}/{g['vmid']}"
                resources.append(item)
        if resource_type in (None, "storage"):
            for s in self._storages:
                resources.append(
                    {
                        "id": f"storage/pve/{s['storage']}",
                        "type": "storage",
                        "node": "pve",
                        "storage": s["storage"],
                        "plugintype": s["type"],
                        "content": s["content"],
                        "disk": s["used"],
                        "maxdisk": s["total"],
                        "status": "available" if s["active"] else "unknown",
                    }
                )
        return resources

    async def get_next_vmid(self) -> int:
        used = {vid for vid in self._guests if vid < 9000}
        candidate = 100
        while candidate in used:
            candidate += 1
        return candidate

    async def get_cluster_backups(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self._backup_jobs)

    async def get_cluster_log(self, max_entries: int = 50) -> list[dict[str, Any]]:
        return [
            {
                "time": t["starttime"],
                "node": t["node"],
                "user": t["user"],
                "tag": t["type"],
                "msg": f"{t['type']} {t['id']} — {t['status']}",
            }
            for t in self._tasks[:max_entries]
        ]

    # ── Nodes & Telemetry ────────────────────────────────────────────────
    async def get_nodes(self) -> list[dict[str, Any]]:
        running_mem = sum(g["mem"] for g in self._guests.values() if g["status"] == "running")
        host_mem = running_mem + int(4.2 * GIB)
        total_mem = 64 * GIB
        running_cpu = sum(
            g["cpu"] * g["cpus"] for g in self._guests.values() if g["status"] == "running"
        )
        cpu_frac = min(0.95, max(0.06, running_cpu / 16.0 + 0.04 * math.sin(time.time() / 20)))
        return [
            {
                "node": "pve",
                "type": "node",
                "status": "online",
                "cpu": round(cpu_frac, 4),
                "maxcpu": 16,
                "mem": host_mem,
                "maxmem": total_mem,
                "disk": int(34 * GIB),
                "maxdisk": int(100 * GIB),
                "uptime": 41 * 86400 + int(time.time() - self._start_ts) + 12450,
                "ssl_fingerprint": "DE:MO:01:92:16:81:02:80:06:AA:BB:CC:DD:EE:FF:00",
            }
        ]

    async def get_node_status(self, node: str) -> dict[str, Any]:
        node_summary = (await self.get_nodes())[0]
        return {
            "cpu": node_summary["cpu"],
            "uptime": node_summary["uptime"],
            "loadavg": ["1.42", "1.28", "1.19"],
            "kversion": "Linux 6.8.12-4-pve #1 SMP PREEMPT_DYNAMIC",
            "pveversion": "pve-manager/8.3.2/3e76eec21c4a14a7",
            "cpuinfo": {
                "cpus": 16,
                "cores": 8,
                "sockets": 1,
                "model": "AMD Ryzen 7 5700G with Radeon Graphics (Demo)",
                "mhz": "3800.000",
            },
            "memory": {
                "total": node_summary["maxmem"],
                "used": node_summary["mem"],
                "free": node_summary["maxmem"] - node_summary["mem"],
            },
            "swap": {
                "total": 8 * GIB,
                "used": int(0.4 * GIB),
                "free": int(7.6 * GIB),
            },
            "rootfs": {
                "total": 100 * GIB,
                "used": int(34 * GIB),
                "avail": int(66 * GIB),
                "free": int(66 * GIB),
            },
            "wait": 0.012,
        }

    async def get_node_rrd(
        self, node: str, timeframe: str = "hour", cf: str = "AVERAGE"
    ) -> list[dict[str, Any]]:
        now = int(time.time())
        step_map = {"hour": 60, "day": 1800, "week": 10800, "month": 43200, "year": 604800}
        step = step_map.get(timeframe, 60)
        points = 48
        out: list[dict[str, Any]] = []
        for i in range(points):
            ts = now - (points - 1 - i) * step
            wave = math.sin(i * 0.35) * 0.06 + math.cos(i * 0.17) * 0.04
            cpu = max(0.05, min(0.92, 0.23 + wave))
            memused = int((22.5 + math.sin(i * 0.2) * 2.1) * GIB)
            out.append(
                {
                    "time": ts,
                    "cpu": round(cpu, 4),
                    "iowait": round(max(0.002, 0.011 + math.sin(i * 0.5) * 0.005), 4),
                    "loadavg": round(1.1 + cpu * 2.4, 2),
                    "memtotal": 64 * GIB,
                    "memused": memused,
                    "netin": int(4_500_000 + abs(math.sin(i * 0.4)) * 9_000_000),
                    "netout": int(2_800_000 + abs(math.cos(i * 0.3)) * 6_500_000),
                }
            )
        return out

    async def get_node_services(self, node: str) -> list[dict[str, Any]]:
        return [
            {"service": "pveproxy", "name": "pveproxy", "state": "running", "desc": "PVE API Proxy Server"},
            {"service": "pvedaemon", "name": "pvedaemon", "state": "running", "desc": "PVE API Daemon"},
            {"service": "pvestatd", "name": "pvestatd", "state": "running", "desc": "PVE Status Daemon"},
            {"service": "pve-cluster", "name": "pve-cluster", "state": "running", "desc": "The Proxmox VE cluster filesystem"},
            {"service": "pvescheduler", "name": "pvescheduler", "state": "running", "desc": "Proxmox VE scheduler"},
            {"service": "chrony", "name": "chrony", "state": "running", "desc": "NTP Time Synchronization"},
            {"service": "smartd", "name": "smartd", "state": "running", "desc": "Self Monitoring and Reporting Technology Daemon"},
        ]

    async def get_node_network(self, node: str) -> list[dict[str, Any]]:
        return [
            {
                "iface": "vmbr0",
                "type": "bridge",
                "active": 1,
                "autostart": 1,
                "address": "192.168.1.2",
                "cidr": "192.168.1.2/24",
                "gateway": "192.168.1.1",
                "bridge_ports": "enp3s0",
                "comments": "Primary LAN Bridge",
            },
            {
                "iface": "enp3s0",
                "type": "eth",
                "active": 1,
                "autostart": 1,
                "comments": "2.5GbE Onboard NIC",
            },
        ]

    async def get_node_storage(self, node: str) -> list[dict[str, Any]]:
        return copy.deepcopy(self._storages)

    async def get_storage_content(
        self, node: str, storage: str, content: str | None = None
    ) -> list[dict[str, Any]]:
        items = copy.deepcopy(self._storage_content.get(storage, []))
        if content:
            items = [i for i in items if i.get("content") == content]
        return items

    async def get_node_disks(self, node: str) -> list[dict[str, Any]]:
        return [
            {
                "devpath": "/dev/nvme0n1",
                "model": "Samsung SSD 990 PRO 1TB",
                "serial": "S73WNJ0W000111",
                "size": 1000 * GIB,
                "type": "nvme",
                "health": "PASSED",
                "wearout": 96,
                "used": "LVM",
            },
            {
                "devpath": "/dev/sda",
                "model": "WD Red Plus 4TB (WD40EFZX)",
                "serial": "WD-WX22D31DEMO1",
                "size": 4000 * GIB,
                "type": "hdd",
                "health": "PASSED",
                "wearout": "N/A",
                "used": "ZFS",
            },
            {
                "devpath": "/dev/sdb",
                "model": "WD Red Plus 4TB (WD40EFZX)",
                "serial": "WD-WX22D31DEMO2",
                "size": 4000 * GIB,
                "type": "hdd",
                "health": "PASSED",
                "wearout": "N/A",
                "used": "ZFS",
            },
        ]

    async def get_node_zfs(self, node: str) -> list[dict[str, Any]]:
        return [
            {
                "name": "zfs-tank",
                "health": "ONLINE",
                "size": 3800 * GIB,
                "alloc": 3190 * GIB,
                "free": 610 * GIB,
                "frag": 14,
                "dedup": 1.0,
            }
        ]

    # ── Tasks & Backups ──────────────────────────────────────────────────
    async def get_node_tasks(
        self,
        node: str,
        limit: int = 40,
        vmid: int | None = None,
        errors_only: bool = False,
    ) -> list[dict[str, Any]]:
        items = self._tasks
        if vmid is not None:
            items = [t for t in items if str(t.get("id")) == str(vmid)]
        if errors_only:
            items = [t for t in items if t.get("status") != "OK"]
        return [
            {k: v for k, v in t.items() if k != "log"}
            for t in items[:limit]
        ]

    async def get_task_status(self, node: str, upid: str) -> dict[str, Any]:
        for t in self._tasks:
            if t["upid"] == upid:
                return {
                    "upid": t["upid"],
                    "node": t["node"],
                    "type": t["type"],
                    "id": t["id"],
                    "user": t["user"],
                    "starttime": t["starttime"],
                    "status": "stopped",
                    "exitstatus": t["status"],
                }
        return {"upid": upid, "status": "stopped", "exitstatus": "OK"}

    async def get_task_log(
        self, node: str, upid: str, start: int = 0, limit: int = 200
    ) -> list[dict[str, Any]]:
        for t in self._tasks:
            if t["upid"] == upid:
                lines = t.get("log", ["TASK OK"])
                return [
                    {"n": idx + 1, "t": line}
                    for idx, line in enumerate(lines[start : start + limit])
                ]
        return [{"n": 1, "t": "TASK OK"}]

    async def run_vzdump(self, node: str, params: dict[str, Any]) -> str:
        vmid = str(params.get("vmid", ""))
        storage = str(params.get("storage", "pbs-backup"))
        mode = str(params.get("mode", "snapshot"))
        compress = str(params.get("compress", "zstd"))
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if storage in self._storage_content:
            self._storage_content[storage].insert(
                0,
                {
                    "volid": f"{storage}:backup/guest/{vmid}/{ts}",
                    "content": "backup",
                    "format": "vma.zst",
                    "size": int(4.5 * GIB),
                    "vmid": int(vmid) if vmid.isdigit() else 0,
                    "ctime": int(time.time()),
                    "notes": f"Manual backup ({mode}, {compress})",
                },
            )
        return self._new_task(
            "vzdump",
            vmid,
            [
                f"INFO: starting backup for guest {vmid} -> {storage} (mode={mode}, compress={compress})",
                f"INFO: created archive {storage}:backup/guest/{vmid}/{ts}",
                "INFO: Backup completed successfully.",
            ],
        )

    # ── QEMU VMs ─────────────────────────────────────────────────────────
    async def list_qemu(self, node: str) -> list[dict[str, Any]]:
        return [
            {k: v for k, v in g.items() if k not in ("config", "ips")}
            for g in self._guests.values()
            if g["type"] == "qemu"
        ]

    async def get_qemu_status(self, node: str, vmid: int) -> dict[str, Any]:
        g = self._require_guest(vmid, "qemu")
        return {k: v for k, v in g.items() if k not in ("config", "ips")}

    async def get_qemu_config(self, node: str, vmid: int) -> dict[str, Any]:
        return copy.deepcopy(self._require_guest(vmid, "qemu")["config"])

    async def update_qemu_config(
        self, node: str, vmid: int, params: dict[str, Any]
    ) -> Any:
        g = self._require_guest(vmid, "qemu")
        g["config"].update(params)
        if "cores" in params:
            g["cpus"] = int(params["cores"])
        if "memory" in params:
            g["maxmem"] = int(params["memory"]) * MIB
        if "tags" in params:
            g["tags"] = str(params["tags"])
        if "name" in params:
            g["name"] = str(params["name"])
        return None

    async def get_qemu_rrd(
        self, node: str, vmid: int, timeframe: str = "hour"
    ) -> list[dict[str, Any]]:
        g = self._require_guest(vmid, "qemu")
        return self._guest_rrd(g, timeframe)

    async def qemu_power_action(self, node: str, vmid: int, action: str) -> str:
        g = self._require_guest(vmid, "qemu")
        return self._apply_power(g, action, "qm")

    async def create_qemu(self, node: str, params: dict[str, Any]) -> str:
        vmid = int(params.get("vmid") or await self.get_next_vmid())
        if vmid in self._guests:
            raise ProxmoxAPIError(f"VMID {vmid} already exists.", status_code=400)
        name = str(params.get("name") or f"vm-{vmid}")
        cores = int(params.get("cores", 2))
        memory = int(params.get("memory", 2048))
        disk_gb = 32
        for k in ("scsi0", "virtio0", "ide0"):
            if k in params and ":" in str(params[k]):
                tail = str(params[k]).split(":")[1].split(",")[0]
                if tail.isdigit():
                    disk_gb = int(tail)
        cfg = dict(params)
        cfg["name"] = name
        cfg["cores"] = cores
        cfg["memory"] = memory
        self._guests[vmid] = {
            "vmid": vmid,
            "name": name,
            "type": "qemu",
            "node": node,
            "status": "stopped",
            "cpus": cores,
            "cpu": 0.0,
            "mem": 0,
            "maxmem": memory * MIB,
            "disk": int(2 * GIB),
            "maxdisk": disk_gb * GIB,
            "uptime": 0,
            "netin": 0,
            "netout": 0,
            "tags": str(params.get("tags", "")),
            "template": 0,
            "config": cfg,
            "ips": [],
        }
        self._snapshots[vmid] = [{"name": "current", "description": "You are here!"}]
        return self._new_task(
            "qmcreate",
            str(vmid),
            [f"Creating QEMU VM {vmid} ({name}) — {cores} vCPU, {memory} MB RAM, {disk_gb} GB disk"],
        )

    async def clone_qemu(
        self, node: str, vmid: int, params: dict[str, Any]
    ) -> str:
        src = self._require_guest(vmid, "qemu")
        newid = int(params["newid"])
        if newid in self._guests:
            raise ProxmoxAPIError(f"VMID {newid} already exists.", status_code=400)
        clone = copy.deepcopy(src)
        clone["vmid"] = newid
        clone["name"] = str(params.get("name") or f"{src['name']}-clone")
        clone["status"] = "stopped"
        clone["template"] = 0
        clone["cpu"] = 0.0
        clone["mem"] = 0
        clone["uptime"] = 0
        clone["config"]["name"] = clone["name"]
        clone["config"].pop("template", None)
        self._guests[newid] = clone
        self._snapshots[newid] = [{"name": "current", "description": "You are here!"}]
        return self._new_task(
            "qmclone",
            str(vmid),
            [f"Cloned VM {vmid} ({src['name']}) -> VM {newid} ({clone['name']})"],
        )

    async def delete_qemu(
        self, node: str, vmid: int, purge: bool = True, destroy_unreferenced: bool = True
    ) -> str:
        g = self._require_guest(vmid, "qemu")
        if g["status"] == "running":
            raise ProxmoxAPIError(
                f"Cannot delete running VM {vmid}. Stop it first.", status_code=400
            )
        del self._guests[vmid]
        self._snapshots.pop(vmid, None)
        return self._new_task("qmdestroy", str(vmid), [f"Destroyed QEMU VM {vmid} ({g['name']})"])

    async def get_qemu_agent_ips(self, node: str, vmid: int) -> dict[str, Any] | None:
        g = self._guests.get(int(vmid))
        if not g or g["status"] != "running" or not g.get("ips"):
            return None
        return {
            "result": [
                {
                    "name": "eth0",
                    "ip-addresses": [
                        {"ip-address": ip, "ip-address-type": "ipv4", "prefix": 24}
                        for ip in g["ips"]
                    ],
                }
            ]
        }

    # ── LXC Containers ───────────────────────────────────────────────────
    async def list_lxc(self, node: str) -> list[dict[str, Any]]:
        return [
            {k: v for k, v in g.items() if k not in ("config", "ips")}
            for g in self._guests.values()
            if g["type"] == "lxc"
        ]

    async def get_lxc_status(self, node: str, vmid: int) -> dict[str, Any]:
        g = self._require_guest(vmid, "lxc")
        return {k: v for k, v in g.items() if k not in ("config", "ips")}

    async def get_lxc_config(self, node: str, vmid: int) -> dict[str, Any]:
        return copy.deepcopy(self._require_guest(vmid, "lxc")["config"])

    async def update_lxc_config(
        self, node: str, vmid: int, params: dict[str, Any]
    ) -> Any:
        g = self._require_guest(vmid, "lxc")
        g["config"].update(params)
        if "cores" in params:
            g["cpus"] = int(params["cores"])
        if "memory" in params:
            g["maxmem"] = int(params["memory"]) * MIB
        if "tags" in params:
            g["tags"] = str(params["tags"])
        if "hostname" in params:
            g["name"] = str(params["hostname"])
        return None

    async def get_lxc_rrd(
        self, node: str, vmid: int, timeframe: str = "hour"
    ) -> list[dict[str, Any]]:
        g = self._require_guest(vmid, "lxc")
        return self._guest_rrd(g, timeframe)

    async def lxc_power_action(self, node: str, vmid: int, action: str) -> str:
        g = self._require_guest(vmid, "lxc")
        return self._apply_power(g, action, "vz")

    async def create_lxc(self, node: str, params: dict[str, Any]) -> str:
        vmid = int(params.get("vmid") or await self.get_next_vmid())
        if vmid in self._guests:
            raise ProxmoxAPIError(f"VMID {vmid} already exists.", status_code=400)
        hostname = str(params.get("hostname") or f"ct-{vmid}")
        cores = int(params.get("cores", 1))
        memory = int(params.get("memory", 1024))
        disk_gb = 8
        rootfs = str(params.get("rootfs", "local-lvm:8"))
        if ":" in rootfs:
            tail = rootfs.split(":")[1].split(",")[0]
            if tail.isdigit():
                disk_gb = int(tail)
        cfg = dict(params)
        cfg.pop("password", None)
        cfg["hostname"] = hostname
        self._guests[vmid] = {
            "vmid": vmid,
            "name": hostname,
            "type": "lxc",
            "node": node,
            "status": "running" if params.get("start") else "stopped",
            "cpus": cores,
            "cpu": 0.02 if params.get("start") else 0.0,
            "mem": int(memory * MIB * 0.18) if params.get("start") else 0,
            "maxmem": memory * MIB,
            "disk": int(0.8 * GIB),
            "maxdisk": disk_gb * GIB,
            "uptime": 5 if params.get("start") else 0,
            "netin": 0,
            "netout": 0,
            "tags": str(params.get("tags", "")),
            "template": 0,
            "config": cfg,
            "ips": [f"192.168.1.{min(240, 100 + (vmid % 120))}"],
        }
        self._snapshots[vmid] = [{"name": "current", "description": "You are here!"}]
        return self._new_task(
            "vzcreate",
            str(vmid),
            [
                f"Extracting template {params.get('ostemplate', 'debian-12')}",
                f"Created LXC container {vmid} ({hostname}) — {cores} core, {memory} MB RAM, {disk_gb} GB rootfs",
            ],
        )

    async def clone_lxc(self, node: str, vmid: int, params: dict[str, Any]) -> str:
        src = self._require_guest(vmid, "lxc")
        newid = int(params["newid"])
        if newid in self._guests:
            raise ProxmoxAPIError(f"VMID {newid} already exists.", status_code=400)
        clone = copy.deepcopy(src)
        clone["vmid"] = newid
        clone["name"] = str(params.get("hostname") or f"{src['name']}-clone")
        clone["status"] = "stopped"
        clone["cpu"] = 0.0
        clone["mem"] = 0
        clone["uptime"] = 0
        clone["config"]["hostname"] = clone["name"]
        self._guests[newid] = clone
        self._snapshots[newid] = [{"name": "current", "description": "You are here!"}]
        return self._new_task(
            "vzclone",
            str(vmid),
            [f"Cloned LXC {vmid} ({src['name']}) -> LXC {newid} ({clone['name']})"],
        )

    async def delete_lxc(self, node: str, vmid: int, purge: bool = True) -> str:
        g = self._require_guest(vmid, "lxc")
        if g["status"] == "running":
            raise ProxmoxAPIError(
                f"Cannot delete running LXC {vmid}. Stop it first.", status_code=400
            )
        del self._guests[vmid]
        self._snapshots.pop(vmid, None)
        return self._new_task("vzdestroy", str(vmid), [f"Destroyed LXC {vmid} ({g['name']})"])

    # ── Snapshots ────────────────────────────────────────────────────────
    async def list_snapshots(
        self, node: str, guest_type: str, vmid: int
    ) -> list[dict[str, Any]]:
        self._require_guest(vmid, guest_type)
        return copy.deepcopy(self._snapshots.get(vmid, []))

    async def create_snapshot(
        self,
        node: str,
        guest_type: str,
        vmid: int,
        snapname: str,
        description: str = "",
        vmstate: bool = False,
    ) -> str:
        self._require_guest(vmid, guest_type)
        snaps = self._snapshots.setdefault(
            vmid, [{"name": "current", "description": "You are here!"}]
        )
        if any(s["name"] == snapname for s in snaps):
            raise ProxmoxAPIError(f"Snapshot '{snapname}' already exists.", status_code=400)
        parent = snaps[-2]["name"] if len(snaps) >= 2 else ""
        new_snap = {
            "name": snapname,
            "description": description,
            "snaptime": int(time.time()),
            "vmstate": 1 if vmstate else 0,
            "parent": parent,
        }
        snaps.insert(max(0, len(snaps) - 1), new_snap)
        snaps[-1]["parent"] = snapname
        prefix = "qm" if guest_type == "qemu" else "vz"
        return self._new_task(
            f"{prefix}snap",
            str(vmid),
            [f"Created snapshot '{snapname}' for {guest_type} {vmid}"],
        )

    async def rollback_snapshot(
        self, node: str, guest_type: str, vmid: int, snapname: str
    ) -> str:
        self._require_guest(vmid, guest_type)
        snaps = self._snapshots.get(vmid, [])
        if not any(s["name"] == snapname for s in snaps if s["name"] != "current"):
            raise ProxmoxAPIError(f"Snapshot '{snapname}' not found.", status_code=404)
        prefix = "qm" if guest_type == "qemu" else "vz"
        return self._new_task(
            f"{prefix}rollback",
            str(vmid),
            [f"Rolled back {guest_type} {vmid} to snapshot '{snapname}'"],
        )

    async def delete_snapshot(
        self, node: str, guest_type: str, vmid: int, snapname: str
    ) -> str:
        self._require_guest(vmid, guest_type)
        snaps = self._snapshots.get(vmid, [])
        before = len(snaps)
        self._snapshots[vmid] = [s for s in snaps if s["name"] != snapname]
        if len(self._snapshots[vmid]) == before:
            raise ProxmoxAPIError(f"Snapshot '{snapname}' not found.", status_code=404)
        prefix = "qm" if guest_type == "qemu" else "vz"
        return self._new_task(
            f"{prefix}delsnapshot",
            str(vmid),
            [f"Deleted snapshot '{snapname}' from {guest_type} {vmid}"],
        )

    # ── Helpers ──────────────────────────────────────────────────────────
    def _apply_power(self, g: dict[str, Any], action: str, prefix: str) -> str:
        if action in ("start", "resume"):
            g["status"] = "running"
            g["cpu"] = 0.08
            g["mem"] = int(g["maxmem"] * 0.32)
            g["uptime"] = 12
        elif action in ("stop", "shutdown"):
            g["status"] = "stopped"
            g["cpu"] = 0.0
            g["mem"] = 0
            g["uptime"] = 0
        elif action == "suspend":
            g["status"] = "paused"
            g["cpu"] = 0.0
        elif action in ("reboot", "reset"):
            g["status"] = "running"
            g["uptime"] = 3
        else:
            raise ProxmoxAPIError(f"Unsupported power action: {action}", status_code=400)
        return self._new_task(
            f"{prefix}{action}",
            str(g["vmid"]),
            [f"Executed '{action}' on {g['type']} {g['vmid']} ({g['name']})"],
        )

    def _guest_rrd(self, g: dict[str, Any], timeframe: str) -> list[dict[str, Any]]:
        now = int(time.time())
        step_map = {"hour": 60, "day": 1800, "week": 10800, "month": 43200, "year": 604800}
        step = step_map.get(timeframe, 60)
        points = 36
        running = g["status"] == "running"
        base_cpu = g["cpu"] if running else 0.0
        base_mem = g["mem"] if running else 0
        out: list[dict[str, Any]] = []
        for i in range(points):
            ts = now - (points - 1 - i) * step
            out.append(
                {
                    "time": ts,
                    "cpu": round(max(0.0, base_cpu + (math.sin(i * 0.45) * 0.04 if running else 0)), 4),
                    "mem": int(max(0, base_mem * (0.94 + 0.08 * math.sin(i * 0.25)))) if running else 0,
                    "maxmem": g["maxmem"],
                    "netin": int(800_000 + abs(math.sin(i * 0.5)) * 2_400_000) if running else 0,
                    "netout": int(450_000 + abs(math.cos(i * 0.4)) * 1_600_000) if running else 0,
                }
            )
        return out

    # ── Storage Downloads, Uploads, Delete & Appliances ──────────────────
    async def download_url_to_storage(
        self,
        node: str,
        storage: str,
        *,
        url: str,
        filename: str,
        content: str = "iso",
        checksum: str | None = None,
        checksum_algorithm: str | None = None,
    ) -> str:
        subdir = "iso" if content == "iso" else "vztmpl"
        volid = f"{storage}:{subdir}/{filename}"
        self._storage_content.setdefault(storage, []).insert(
            0,
            {
                "volid": volid,
                "content": content,
                "format": "iso" if content == "iso" else "tzst",
                "size": int(1.4 * GIB) if content == "iso" else int(120 * MIB),
                "ctime": int(time.time()),
            },
        )
        return self._new_task(
            "download",
            filename,
            [f"Downloading {url}", f"Saved directly to {volid}"],
        )

    async def delete_storage_volume(self, node: str, storage: str, volid: str) -> str:
        items = self._storage_content.get(storage, [])
        before = len(items)
        self._storage_content[storage] = [i for i in items if i.get("volid") != volid]
        if len(self._storage_content[storage]) == before:
            raise ProxmoxAPIError(f"Volume '{volid}' not found in '{storage}'.", status_code=404)
        return self._new_task("imgdel", volid, [f"Deleted volume {volid}"])

    async def upload_to_storage(
        self,
        node: str,
        storage: str,
        *,
        content: str,
        filename: str,
        file_bytes: bytes,
    ) -> str:
        volid = f"{storage}:{content}/{filename}"
        self._storage_content.setdefault(storage, []).insert(
            0,
            {
                "volid": volid,
                "content": content,
                "format": content,
                "size": len(file_bytes),
                "ctime": int(time.time()),
            },
        )
        return self._new_task("upload", filename, [f"Uploaded {filename} ({len(file_bytes)} bytes) to {volid}"])

    async def list_appliances(self, node: str) -> list[dict[str, Any]]:
        return [
            {"template": "debian-12-standard_12.7-1_amd64.tar.zst", "os": "debian-12", "section": "system", "headline": "Debian 12 (bookworm) standard", "version": "12.7-1", "package": "debian-12-standard"},
            {"template": "ubuntu-24.04-standard_24.04-2_amd64.tar.zst", "os": "ubuntu-24.04", "section": "system", "headline": "Ubuntu 24.04 LTS (Noble Numbat) standard", "version": "24.04-2", "package": "ubuntu-24.04-standard"},
            {"template": "alpine-3.21-default_20241217_amd64.tar.xz", "os": "alpine-3.21", "section": "system", "headline": "Alpine Linux 3.21 minimal container", "version": "3.21", "package": "alpine-3.21-default"},
            {"template": "rockylinux-9-default_20240912_amd64.tar.xz", "os": "rockylinux-9", "section": "system", "headline": "Rocky Linux 9 enterprise container", "version": "9", "package": "rockylinux-9-default"},
            {"template": "debian-12-turnkey-nginx-php-fastcgi_18.0-1_amd64.tar.gz", "os": "debian-12", "section": "turnkeylinux", "headline": "TurnKey NGINX PHP FastCGI appliance", "version": "18.0-1", "package": "turnkey-nginx"},
        ]

    async def download_appliance(self, node: str, storage: str, template: str) -> str:
        volid = f"{storage}:vztmpl/{template}"
        self._storage_content.setdefault(storage, []).insert(
            0,
            {"volid": volid, "content": "vztmpl", "format": "tzst", "size": int(128 * MIB), "ctime": int(time.time())},
        )
        return self._new_task("aplinfo", template, [f"Downloaded LXC template {template} to {storage}"])

    # ── Host APT Updates, Syslog, Firewall & Backup Schedules ────────────
    async def get_apt_updates(self, node: str) -> list[dict[str, Any]]:
        return [
            {"Package": "pve-manager", "Title": "Proxmox VE Manager", "OldVersion": "8.3.1", "Version": "8.3.2", "Origin": "Proxmox", "Priority": "optional", "Section": "admin"},
            {"Package": "zfs-zed", "Title": "OpenZFS Event Daemon", "OldVersion": "2.2.6-pve1", "Version": "2.2.7-pve1", "Origin": "Proxmox", "Priority": "optional", "Section": "contrib/admin"},
        ]

    async def refresh_apt_updates(self, node: str) -> str:
        return self._new_task("aptupdate", "", ["Hit:1 http://deb.debian.org/debian bookworm InRelease", "Reading package lists... Done"])

    async def get_apt_versions(self, node: str) -> list[dict[str, Any]]:
        return [
            {"Package": "proxmox-ve", "Version": "8.3.2", "Title": "Proxmox Virtual Environment"},
            {"Package": "pve-kernel-6.8", "Version": "6.8.12-4", "Title": "Proxmox VE Kernel"},
            {"Package": "zfsutils-linux", "Version": "2.2.6-pve1", "Title": "OpenZFS utilities"},
        ]

    async def get_node_syslog(
        self, node: str, limit: int = 120, service: str | None = None
    ) -> list[dict[str, Any]]:
        lines = [
            "Nov 25 02:10:01 pve systemd[1]: Started pvestatd.service - PVE Status Daemon.",
            "Nov 25 02:12:44 pve kernel: vmbr0: port 2(tap100i0) entered forwarding state",
            "Nov 25 02:15:11 pve smartd[812]: Device: /dev/nvme0n1, SMART Health Status: OK",
            "Nov 25 02:20:00 pve pvescheduler[910]: replication & backup scheduler heartbeat OK",
        ]
        if service:
            lines = [l for l in lines if service.lower() in l.lower()]
        return [{"n": i + 1, "t": line} for i, line in enumerate(lines[:limit])]

    async def get_cluster_firewall_options(self) -> dict[str, Any]:
        return {"enable": 1, "policy_in": "DROP", "policy_out": "ACCEPT"}

    async def get_cluster_firewall_rules(self) -> list[dict[str, Any]]:
        return [
            {"pos": 0, "type": "in", "action": "ACCEPT", "proto": "tcp", "dport": "8006", "source": "192.168.1.0/24", "enable": 1, "comment": "Allow LAN PVE WebUI"},
            {"pos": 1, "type": "in", "action": "ACCEPT", "proto": "tcp", "dport": "22", "source": "192.168.1.0/24", "enable": 1, "comment": "Allow LAN SSH"},
        ]

    async def get_node_firewall_rules(self, node: str) -> list[dict[str, Any]]:
        return []

    async def create_cluster_backup_job(self, params: dict[str, Any]) -> Any:
        job = dict(params)
        job["id"] = f"backup-{uuid.uuid4().hex[:6]}"
        job.setdefault("enabled", 1)
        self._backup_jobs.append(job)
        return job

    async def delete_cluster_backup_job(self, job_id: str) -> Any:
        self._backup_jobs = [j for j in self._backup_jobs if j.get("id") != job_id]
        return None

    # ── QEMU Guest Agent File Explorer & Exec ────────────────────────────
    async def qemu_agent_info(self, node: str, vmid: int) -> dict[str, Any]:
        g = self._require_guest(vmid, "qemu")
        if g["status"] != "running":
            raise ProxmoxAPIError("VM is not running.", status_code=400)
        return {"result": {"version": "9.0.0-demo", "supported_commands": [{"name": "guest-exec", "enabled": True}, {"name": "guest-file-read", "enabled": True}]}}

    async def qemu_agent_file_read(self, node: str, vmid: int, file_path: str) -> dict[str, Any]:
        self._require_guest(vmid, "qemu")
        if file_path not in self._demo_fs:
            raise ProxmoxAPIError(f"File '{file_path}' not found in demo guest.", status_code=404)
        return {"content": self._demo_fs[file_path], "truncated": 0}

    async def qemu_agent_file_write(
        self, node: str, vmid: int, file_path: str, content: str
    ) -> Any:
        self._require_guest(vmid, "qemu")
        self._demo_fs[file_path] = content
        return None

    async def qemu_agent_exec(
        self, node: str, vmid: int, command: str | list[str]
    ) -> dict[str, Any]:
        self._require_guest(vmid, "qemu")
        pid = self._next_pid
        self._next_pid += 1
        cmd_str = " ".join(command) if isinstance(command, list) else str(command)
        if "ls " in cmd_str:
            out = "d|etc|4096|2026-11-24\nd|opt|4096|2026-11-24\nf|/etc/hostname|12|2026-11-24\nf|/etc/hosts|48|2026-11-24\nf|/opt/docker/compose.yml|164|2026-11-24\n"
            if "/etc" in cmd_str:
                out = "f|hostname|12|2026-11-24\nf|hosts|48|2026-11-24\n"
            elif "/opt/docker" in cmd_str:
                out = "f|compose.yml|164|2026-11-24\n"
            elif "/opt" in cmd_str:
                out = "d|docker|4096|2026-11-24\n"
            else:
                out = "d|etc|4096|2026-11-24\nd|opt|4096|2026-11-24\nd|var|4096|2026-11-24\n"
        else:
            out = f"[demo guest {vmid}] executed: {cmd_str}\n"
        self._exec_outputs[pid] = {"exited": 1, "exitcode": 0, "out-data": out, "err-data": ""}
        return {"pid": pid}

    async def qemu_agent_exec_status(self, node: str, vmid: int, pid: int) -> dict[str, Any]:
        return self._exec_outputs.get(pid, {"exited": 1, "exitcode": 0, "out-data": "", "err-data": ""})
