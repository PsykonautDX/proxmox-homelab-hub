"""Async Proxmox VE REST API client."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from .config import AppSettings


class ProxmoxAPIError(Exception):
    """Raised when the Proxmox VE API returns an error or cannot be reached."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details


class ProxmoxNotConfiguredError(ProxmoxAPIError):
    """Raised when Proxmox API credentials are missing and demo mode is off."""

    def __init__(self) -> None:
        super().__init__(
            "Proxmox API token is not configured yet. Set PROXMOX_TOKEN_ID and "
            "PROXMOX_TOKEN_SECRET in .env or via the WebUI Settings page, or enable Demo Mode.",
            status_code=401,
        )


class ProxmoxClient:
    """Thin async wrapper around the Proxmox VE /api2/json REST API."""

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self._client: httpx.AsyncClient | None = None

    def _get_http(self) -> httpx.AsyncClient:
        if not self.settings.is_configured:
            raise ProxmoxNotConfiguredError()
        if self._client is None or self._client.is_closed:
            token_header = (
                f"PVEAPIToken={self.settings.proxmox_token_id.strip()}="
                f"{self.settings.proxmox_token_secret.strip()}"
            )
            self._client = httpx.AsyncClient(
                base_url=f"{self.settings.normalized_host}/api2/json",
                headers={
                    "Authorization": token_header,
                    "Accept": "application/json",
                },
                verify=self.settings.proxmox_verify_ssl,
                timeout=httpx.Timeout(self.settings.proxmox_timeout),
                limits=httpx.Limits(keepalive_expiry=4.0, max_connections=20),
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any:
        client = self._get_http()
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        clean_data = {k: v for k, v in (data or {}).items() if v is not None}

        last_exc: Exception | None = None
        attempts = 2 if method.upper() == "GET" else 1
        resp = None
        for attempt in range(attempts):
            try:
                resp = await client.request(
                    method=method.upper(),
                    url=path,
                    params=clean_params or None,
                    data=clean_data or None,
                )
                break
            except httpx.RemoteProtocolError as exc:
                last_exc = exc
                if attempt + 1 < attempts:
                    continue
                raise ProxmoxAPIError(f"HTTP error talking to Proxmox: {exc}") from exc
            except httpx.ConnectTimeout as exc:
                raise ProxmoxAPIError(
                    f"Connection timed out reaching {self.settings.normalized_host}. "
                    "Verify Proxmox is reachable on your LAN.",
                    status_code=504,
                ) from exc
            except httpx.ConnectError as exc:
                raise ProxmoxAPIError(
                    f"Cannot connect to Proxmox at {self.settings.normalized_host}: {exc}",
                    status_code=502,
                ) from exc
            except httpx.HTTPError as exc:
                raise ProxmoxAPIError(f"HTTP error talking to Proxmox: {exc}") from exc
        if resp is None:
            raise ProxmoxAPIError(f"HTTP error talking to Proxmox: {last_exc}")

        if resp.status_code == 401:
            raise ProxmoxAPIError(
                "Authentication failed (401). Check your PROXMOX_TOKEN_ID "
                "(format: user@realm!tokenname) and PROXMOX_TOKEN_SECRET.",
                status_code=401,
            )
        if resp.status_code == 403:
            raise ProxmoxAPIError(
                f"Permission denied (403) for {method} {path}. Verify the API token's "
                "Proxmox ACL / role permissions (and Privilege Separation setting).",
                status_code=403,
            )

        payload: Any = None
        try:
            payload = resp.json()
        except Exception:
            pass

        if resp.status_code >= 400:
            err_msg = resp.reason_phrase or f"HTTP {resp.status_code}"
            details = None
            if isinstance(payload, dict):
                if payload.get("errors"):
                    details = payload["errors"]
                    err_parts = [f"{k}: {v}" for k, v in payload["errors"].items()]
                    err_msg = f"{err_msg} — {'; '.join(err_parts)}"
                elif payload.get("message"):
                    err_msg = str(payload["message"])
            raise ProxmoxAPIError(err_msg, status_code=resp.status_code, details=details)

        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload

    # ── Cluster & Version ────────────────────────────────────────────────
    async def get_version(self) -> dict[str, Any]:
        return await self.request("GET", "/version")

    async def get_cluster_status(self) -> list[dict[str, Any]]:
        return await self.request("GET", "/cluster/status")

    async def get_cluster_resources(
        self, resource_type: str | None = None
    ) -> list[dict[str, Any]]:
        return await self.request(
            "GET", "/cluster/resources", params={"type": resource_type}
        )

    async def get_next_vmid(self) -> int:
        val = await self.request("GET", "/cluster/nextid")
        return int(val)

    async def get_cluster_backups(self) -> list[dict[str, Any]]:
        return await self.request("GET", "/cluster/backup")

    async def get_cluster_log(self, max_entries: int = 50) -> list[dict[str, Any]]:
        return await self.request("GET", "/cluster/log", params={"max": max_entries})

    # ── Nodes & Telemetry ────────────────────────────────────────────────
    async def get_nodes(self) -> list[dict[str, Any]]:
        return await self.request("GET", "/nodes")

    async def get_node_status(self, node: str) -> dict[str, Any]:
        return await self.request("GET", f"/nodes/{quote(node)}/status")

    async def get_node_rrd(
        self, node: str, timeframe: str = "hour", cf: str = "AVERAGE"
    ) -> list[dict[str, Any]]:
        return await self.request(
            "GET",
            f"/nodes/{quote(node)}/rrddata",
            params={"timeframe": timeframe, "cf": cf},
        )

    async def get_node_services(self, node: str) -> list[dict[str, Any]]:
        return await self.request("GET", f"/nodes/{quote(node)}/services")

    async def get_node_network(self, node: str) -> list[dict[str, Any]]:
        return await self.request("GET", f"/nodes/{quote(node)}/network")

    async def get_node_storage(self, node: str) -> list[dict[str, Any]]:
        return await self.request("GET", f"/nodes/{quote(node)}/storage")

    async def get_storage_content(
        self, node: str, storage: str, content: str | None = None
    ) -> list[dict[str, Any]]:
        return await self.request(
            "GET",
            f"/nodes/{quote(node)}/storage/{quote(storage)}/content",
            params={"content": content},
        )

    async def get_node_disks(self, node: str) -> list[dict[str, Any]]:
        return await self.request("GET", f"/nodes/{quote(node)}/disks/list")

    async def get_node_zfs(self, node: str) -> list[dict[str, Any]]:
        return await self.request("GET", f"/nodes/{quote(node)}/disks/zfs")

    # ── Tasks & Backups ──────────────────────────────────────────────────
    async def get_node_tasks(
        self,
        node: str,
        limit: int = 40,
        vmid: int | None = None,
        errors_only: bool = False,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if vmid is not None:
            params["vmid"] = vmid
        if errors_only:
            params["errors"] = 1
        return await self.request("GET", f"/nodes/{quote(node)}/tasks", params=params)

    async def get_task_status(self, node: str, upid: str) -> dict[str, Any]:
        return await self.request(
            "GET", f"/nodes/{quote(node)}/tasks/{quote(upid, safe='')}/status"
        )

    async def get_task_log(
        self, node: str, upid: str, start: int = 0, limit: int = 200
    ) -> list[dict[str, Any]]:
        return await self.request(
            "GET",
            f"/nodes/{quote(node)}/tasks/{quote(upid, safe='')}/log",
            params={"start": start, "limit": limit},
        )

    async def run_vzdump(self, node: str, params: dict[str, Any]) -> str:
        return await self.request("POST", f"/nodes/{quote(node)}/vzdump", data=params)

    # ── QEMU VMs ─────────────────────────────────────────────────────────
    async def list_qemu(self, node: str) -> list[dict[str, Any]]:
        return await self.request("GET", f"/nodes/{quote(node)}/qemu")

    async def get_qemu_status(self, node: str, vmid: int) -> dict[str, Any]:
        return await self.request(
            "GET", f"/nodes/{quote(node)}/qemu/{vmid}/status/current"
        )

    async def get_qemu_config(self, node: str, vmid: int) -> dict[str, Any]:
        return await self.request("GET", f"/nodes/{quote(node)}/qemu/{vmid}/config")

    async def update_qemu_config(
        self, node: str, vmid: int, params: dict[str, Any]
    ) -> Any:
        return await self.request(
            "PUT", f"/nodes/{quote(node)}/qemu/{vmid}/config", data=params
        )

    async def get_qemu_rrd(
        self, node: str, vmid: int, timeframe: str = "hour"
    ) -> list[dict[str, Any]]:
        return await self.request(
            "GET",
            f"/nodes/{quote(node)}/qemu/{vmid}/rrddata",
            params={"timeframe": timeframe},
        )

    async def qemu_power_action(self, node: str, vmid: int, action: str) -> str:
        return await self.request(
            "POST", f"/nodes/{quote(node)}/qemu/{vmid}/status/{quote(action)}"
        )

    async def create_qemu(self, node: str, params: dict[str, Any]) -> str:
        return await self.request("POST", f"/nodes/{quote(node)}/qemu", data=params)

    async def clone_qemu(
        self, node: str, vmid: int, params: dict[str, Any]
    ) -> str:
        return await self.request(
            "POST", f"/nodes/{quote(node)}/qemu/{vmid}/clone", data=params
        )

    async def delete_qemu(
        self, node: str, vmid: int, purge: bool = True, destroy_unreferenced: bool = True
    ) -> str:
        return await self.request(
            "DELETE",
            f"/nodes/{quote(node)}/qemu/{vmid}",
            params={
                "purge": 1 if purge else 0,
                "destroy-unreferenced-disks": 1 if destroy_unreferenced else 0,
            },
        )

    async def get_qemu_agent_ips(self, node: str, vmid: int) -> dict[str, Any] | None:
        try:
            return await self.request(
                "GET", f"/nodes/{quote(node)}/qemu/{vmid}/agent/network-get-interfaces"
            )
        except ProxmoxAPIError:
            return None

    # ── LXC Containers ───────────────────────────────────────────────────
    async def list_lxc(self, node: str) -> list[dict[str, Any]]:
        return await self.request("GET", f"/nodes/{quote(node)}/lxc")

    async def get_lxc_status(self, node: str, vmid: int) -> dict[str, Any]:
        return await self.request(
            "GET", f"/nodes/{quote(node)}/lxc/{vmid}/status/current"
        )

    async def get_lxc_config(self, node: str, vmid: int) -> dict[str, Any]:
        return await self.request("GET", f"/nodes/{quote(node)}/lxc/{vmid}/config")

    async def update_lxc_config(
        self, node: str, vmid: int, params: dict[str, Any]
    ) -> Any:
        return await self.request(
            "PUT", f"/nodes/{quote(node)}/lxc/{vmid}/config", data=params
        )

    async def get_lxc_rrd(
        self, node: str, vmid: int, timeframe: str = "hour"
    ) -> list[dict[str, Any]]:
        return await self.request(
            "GET",
            f"/nodes/{quote(node)}/lxc/{vmid}/rrddata",
            params={"timeframe": timeframe},
        )

    async def lxc_power_action(self, node: str, vmid: int, action: str) -> str:
        return await self.request(
            "POST", f"/nodes/{quote(node)}/lxc/{vmid}/status/{quote(action)}"
        )

    async def create_lxc(self, node: str, params: dict[str, Any]) -> str:
        return await self.request("POST", f"/nodes/{quote(node)}/lxc", data=params)

    async def clone_lxc(self, node: str, vmid: int, params: dict[str, Any]) -> str:
        return await self.request(
            "POST", f"/nodes/{quote(node)}/lxc/{vmid}/clone", data=params
        )

    async def delete_lxc(self, node: str, vmid: int, purge: bool = True) -> str:
        return await self.request(
            "DELETE",
            f"/nodes/{quote(node)}/lxc/{vmid}",
            params={"purge": 1 if purge else 0},
        )

    # ── Snapshots (QEMU & LXC) ───────────────────────────────────────────
    async def list_snapshots(
        self, node: str, guest_type: str, vmid: int
    ) -> list[dict[str, Any]]:
        return await self.request(
            "GET", f"/nodes/{quote(node)}/{guest_type}/{vmid}/snapshot"
        )

    async def create_snapshot(
        self,
        node: str,
        guest_type: str,
        vmid: int,
        snapname: str,
        description: str = "",
        vmstate: bool = False,
    ) -> str:
        data: dict[str, Any] = {"snapname": snapname, "description": description}
        if guest_type == "qemu" and vmstate:
            data["vmstate"] = 1
        return await self.request(
            "POST", f"/nodes/{quote(node)}/{guest_type}/{vmid}/snapshot", data=data
        )

    async def rollback_snapshot(
        self, node: str, guest_type: str, vmid: int, snapname: str
    ) -> str:
        return await self.request(
            "POST",
            f"/nodes/{quote(node)}/{guest_type}/{vmid}/snapshot/{quote(snapname)}/rollback",
        )

    async def delete_snapshot(
        self, node: str, guest_type: str, vmid: int, snapname: str
    ) -> str:
        return await self.request(
            "DELETE",
            f"/nodes/{quote(node)}/{guest_type}/{vmid}/snapshot/{quote(snapname)}",
        )

    # ── Storage Downloads, Uploads, Delete & Appliances (ISO/LXC Hub) ────
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
        data: dict[str, Any] = {
            "url": url,
            "filename": filename,
            "content": content,
        }
        if checksum:
            data["checksum"] = checksum
        if checksum_algorithm:
            data["checksum-algorithm"] = checksum_algorithm
        return await self.request(
            "POST",
            f"/nodes/{quote(node)}/storage/{quote(storage)}/download-url",
            data=data,
        )

    async def delete_storage_volume(self, node: str, storage: str, volid: str) -> str:
        return await self.request(
            "DELETE",
            f"/nodes/{quote(node)}/storage/{quote(storage)}/content/{quote(volid, safe='')}",
        )

    async def upload_to_storage(
        self,
        node: str,
        storage: str,
        *,
        content: str,
        filename: str,
        file_bytes: bytes,
    ) -> str:
        client = self._get_http()
        resp = await client.post(
            f"/nodes/{quote(node)}/storage/{quote(storage)}/upload",
            data={"content": content},
            files={"filename": (filename, file_bytes)},
            timeout=httpx.Timeout(300.0),
        )
        if resp.status_code >= 400:
            raise ProxmoxAPIError(f"Upload failed ({resp.status_code}): {resp.text[:300]}", status_code=resp.status_code)
        payload = resp.json()
        return str(payload.get("data", ""))

    async def list_appliances(self, node: str) -> list[dict[str, Any]]:
        return await self.request("GET", f"/nodes/{quote(node)}/aplinfo")

    async def download_appliance(self, node: str, storage: str, template: str) -> str:
        return await self.request(
            "POST",
            f"/nodes/{quote(node)}/aplinfo",
            data={"storage": storage, "template": template},
        )

    # ── Host APT Updates, Syslog, Firewall & Backup Schedules ────────────
    async def get_apt_updates(self, node: str) -> list[dict[str, Any]]:
        return await self.request("GET", f"/nodes/{quote(node)}/apt/update")

    async def refresh_apt_updates(self, node: str) -> str:
        return await self.request("POST", f"/nodes/{quote(node)}/apt/update")

    async def get_apt_versions(self, node: str) -> list[dict[str, Any]]:
        return await self.request("GET", f"/nodes/{quote(node)}/apt/versions")

    async def get_node_syslog(
        self, node: str, limit: int = 120, service: str | None = None
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if service:
            params["service"] = service
        return await self.request("GET", f"/nodes/{quote(node)}/syslog", params=params)

    async def get_cluster_firewall_options(self) -> dict[str, Any]:
        return await self.request("GET", "/cluster/firewall/options")

    async def get_cluster_firewall_rules(self) -> list[dict[str, Any]]:
        return await self.request("GET", "/cluster/firewall/rules")

    async def get_node_firewall_rules(self, node: str) -> list[dict[str, Any]]:
        return await self.request("GET", f"/nodes/{quote(node)}/firewall/rules")

    async def create_cluster_backup_job(self, params: dict[str, Any]) -> Any:
        return await self.request("POST", "/cluster/backup", data=params)

    async def delete_cluster_backup_job(self, job_id: str) -> Any:
        return await self.request("DELETE", f"/cluster/backup/{quote(job_id, safe='')}")

    # ── QEMU Guest Agent File Explorer & Exec ────────────────────────────
    async def qemu_agent_info(self, node: str, vmid: int) -> dict[str, Any]:
        return await self.request("GET", f"/nodes/{quote(node)}/qemu/{vmid}/agent/info")

    async def qemu_agent_file_read(self, node: str, vmid: int, file_path: str) -> dict[str, Any]:
        return await self.request(
            "GET",
            f"/nodes/{quote(node)}/qemu/{vmid}/agent/file-read",
            params={"file": file_path},
        )

    async def qemu_agent_file_write(
        self, node: str, vmid: int, file_path: str, content: str
    ) -> Any:
        return await self.request(
            "POST",
            f"/nodes/{quote(node)}/qemu/{vmid}/agent/file-write",
            data={"file": file_path, "content": content},
        )

    async def qemu_agent_exec(
        self, node: str, vmid: int, command: str | list[str]
    ) -> dict[str, Any]:
        return await self.request(
            "POST",
            f"/nodes/{quote(node)}/qemu/{vmid}/agent/exec",
            data={"command": command},
        )

    async def qemu_agent_exec_status(
        self, node: str, vmid: int, pid: int
    ) -> dict[str, Any]:
        return await self.request(
            "GET",
            f"/nodes/{quote(node)}/qemu/{vmid}/agent/exec-status",
            params={"pid": pid},
        )
