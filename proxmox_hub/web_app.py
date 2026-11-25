"""FastAPI backend powering the Custom WebUI, REST API, and Streamable HTTP MCP endpoint."""

from __future__ import annotations

import sys
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .client import ProxmoxAPIError, ProxmoxNotConfiguredError
from .config import BASE_DIR, AppSettings, load_settings
from .mcp_server import MCP_TOOL_CATALOG, create_mcp_server
from .service import ProxmoxService, SafetyPolicyError

WEB_DIR = Path(__file__).resolve().parent / "web"


# ── Request Models ───────────────────────────────────────────────────────
class SettingsUpdateRequest(BaseModel):
    proxmox_host: str = Field(default="https://192.168.1.2:8006")
    proxmox_token_id: str = Field(default="")
    proxmox_token_secret: str | None = Field(default=None)
    proxmox_verify_ssl: bool = Field(default=False)
    proxmox_timeout: float = Field(default=30.0)
    safety_mode: Literal["read_only", "safe", "full"] = Field(default="safe")
    allow_destructive: bool = Field(default=False)
    proxmox_demo_mode: bool = Field(default=False)
    proxmox_ssh_user: str = Field(default="root")
    proxmox_ssh_port: int = Field(default=22, ge=1, le=65535)
    proxmox_ssh_key_path: str = Field(default="")
    idrac_host: str = Field(default="")
    idrac_user: str = Field(default="root")
    idrac_password: str | None = Field(default=None)
    electricity_rate_kwh: float = Field(default=0.26, ge=0.0, le=10.0)
    currency_symbol: str = Field(default="£")


class PowerActionRequest(BaseModel):
    action: str
    node: str | None = None
    guest_type: str | None = None
    wait: bool = False


class GuestConfigUpdateRequest(BaseModel):
    cores: int | None = None
    memory_mb: int | None = None
    onboot: bool | None = None
    tags: str | None = None
    description: str | None = None
    name: str | None = None
    node: str | None = None
    guest_type: str | None = None


class CreateVMRequest(BaseModel):
    name: str = Field(min_length=1, max_length=63)
    node: str | None = None
    vmid: int | None = None
    cores: int = Field(default=2, ge=1, le=128)
    sockets: int = Field(default=1, ge=1, le=4)
    memory_mb: int = Field(default=2048, ge=256)
    disk_storage: str = Field(default="local-lvm")
    disk_size_gb: int = Field(default=32, ge=4)
    iso: str | None = None
    bridge: str = Field(default="vmbr0")
    vlan_tag: int | None = None
    ostype: str = Field(default="l26")
    bios: str = Field(default="seabios")
    cpu_type: str = Field(default="host")
    qemu_agent: bool = Field(default=True)
    onboot: bool = Field(default=False)
    start_after_create: bool = Field(default=False)
    tags: str = Field(default="")
    description: str = Field(default="")


class CreateLXCRequest(BaseModel):
    hostname: str = Field(min_length=1, max_length=63)
    ostemplate: str = Field(min_length=1)
    node: str | None = None
    vmid: int | None = None
    cores: int = Field(default=1, ge=1, le=128)
    memory_mb: int = Field(default=1024, ge=128)
    swap_mb: int = Field(default=512, ge=0)
    disk_storage: str = Field(default="local-lvm")
    disk_size_gb: int = Field(default=8, ge=1)
    bridge: str = Field(default="vmbr0")
    ip_config: str = Field(default="dhcp")
    gateway: str | None = None
    password: str | None = None
    ssh_public_keys: str | None = None
    unprivileged: bool = Field(default=True)
    nesting: bool = Field(default=True)
    onboot: bool = Field(default=True)
    start_after_create: bool = Field(default=True)
    tags: str = Field(default="")
    description: str = Field(default="")


class CloneGuestRequest(BaseModel):
    new_name: str = Field(min_length=1, max_length=63)
    new_vmid: int | None = None
    full_clone: bool = True
    target_storage: str | None = None
    node: str | None = None
    guest_type: str | None = None


class CreateSnapshotRequest(BaseModel):
    snapname: str = Field(min_length=1, max_length=40)
    description: str = Field(default="")
    vmstate: bool = Field(default=False)
    node: str | None = None
    guest_type: str | None = None


class TriggerBackupRequest(BaseModel):
    storage: str | None = None
    mode: Literal["snapshot", "suspend", "stop"] = "snapshot"
    compress: Literal["zstd", "gzip", "lzo", "0"] = "zstd"
    notes: str = ""
    node: str | None = None


class BulkGuestActionRequest(BaseModel):
    vmids: list[int] = Field(min_length=1)
    action: Literal["start", "shutdown", "reboot", "stop", "snapshot", "backup"]
    snapname: str | None = None
    storage: str | None = None


class GuestBookmarkRequest(BaseModel):
    url: str = Field(default="")
    label: str = Field(default="")


class DownloadIsoRequest(BaseModel):
    storage: str
    url: str
    filename: str
    content: Literal["iso", "vztmpl"] = "iso"
    checksum: str | None = None
    checksum_algorithm: str | None = None
    node: str | None = None


class DownloadApplianceRequest(BaseModel):
    storage: str
    template: str
    node: str | None = None


class GuestFileWriteRequest(BaseModel):
    path: str
    content: str


class GuestExecRequest(BaseModel):
    command: str


class CreateBackupScheduleRequest(BaseModel):
    storage: str
    schedule: str = Field(default="02:00")
    vmid: str | None = None
    all_guests: bool = False
    mode: Literal["snapshot", "suspend", "stop"] = "snapshot"
    compress: Literal["zstd", "gzip", "lzo", "0"] = "zstd"
    comment: str = ""
    enabled: bool = True


class AlertConfigRequest(BaseModel):
    enabled: bool = False
    provider: Literal["discord", "slack", "ntfy", "generic"] = "discord"
    webhook_url: str = ""
    min_health_score: int = Field(default=75, ge=0, le=100)
    max_storage_pct: int = Field(default=85, ge=50, le=99)
    max_cpu_pct: int = Field(default=90, ge=50, le=100)
    max_ram_pct: int = Field(default=90, ge=50, le=100)
    notify_failed_tasks: bool = True


class CommunityScriptActionRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=120)
    script_type: str = Field(default="lxc")
    script_url: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class DockerActionRequest(BaseModel):
    host_vmid: int
    container_name: str
    action: Literal["start", "stop", "restart", "logs"]
    tail: int = Field(default=100, ge=10, le=500)


class WolDeviceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    mac: str = Field(min_length=12, max_length=32)
    ip: str = Field(default="")
    broadcast: str = Field(default="255.255.255.255")
    notes: str = Field(default="")


class WolWakeRequest(BaseModel):
    device_id: int | None = None
    mac: str | None = None
    broadcast: str = Field(default="255.255.255.255")


class GroupCheckpointRequest(BaseModel):
    tag: str = Field(min_length=1)
    snapname: str | None = None
    description: str = Field(default="")


def create_app(settings: AppSettings | None = None) -> FastAPI:
    active_settings = settings or load_settings()
    service = ProxmoxService(settings=active_settings)
    mcp = create_mcp_server(service)
    mcp_http_app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        host=active_settings.webui_host,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(mcp.session_manager.run())
            yield
            await service.close()

    app = FastAPI(
        title="Proxmox Homelab Hub",
        version="1.0.0",
        description="Custom WebUI & Dual-Transport MCP Server for Proxmox VE",
        lifespan=lifespan,
    )
    app.state.service = service
    app.state.mcp = mcp

    # ── Error Handling ───────────────────────────────────────────────────
    @app.exception_handler(ProxmoxNotConfiguredError)
    async def handle_not_configured(_req: Request, exc: ProxmoxNotConfiguredError):
        return JSONResponse(
            status_code=428,
            content={"error": exc.message, "code": "NOT_CONFIGURED"},
        )

    @app.exception_handler(SafetyPolicyError)
    async def handle_safety(_req: Request, exc: SafetyPolicyError):
        return JSONResponse(
            status_code=403,
            content={"error": exc.message, "code": "SAFETY_BLOCKED"},
        )

    @app.exception_handler(ProxmoxAPIError)
    async def handle_pve_error(_req: Request, exc: ProxmoxAPIError):
        status = exc.status_code if exc.status_code and 400 <= exc.status_code < 600 else 502
        return JSONResponse(
            status_code=status,
            content={"error": exc.message, "code": "PROXMOX_ERROR", "details": exc.details},
        )

    # ── Health & Settings ────────────────────────────────────────────────
    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "configured": service.settings.is_configured,
            "demo_mode": service.settings.proxmox_demo_mode,
            "safety_mode": service.settings.safety_mode,
        }

    @app.get("/api/settings")
    async def get_settings(request: Request):
        python_exe = sys.executable
        stdio_config = {
            "mcpServers": {
                "proxmox-homelab": {
                    "command": python_exe,
                    "args": ["-m", "proxmox_hub", "mcp"],
                    "cwd": str(BASE_DIR),
                }
            }
        }
        base = str(request.base_url).rstrip("/")
        http_url = f"{base}/mcp" if base else f"http://{service.settings.webui_host}:{service.settings.webui_port}/mcp"
        http_config = {
            "mcpServers": {
                "proxmox-homelab": {
                    "url": http_url,
                }
            }
        }
        return {
            "settings": service.settings.public_dict(),
            "mcp": {
                "stdio_config": stdio_config,
                "http_config": http_config,
                "http_url": http_url,
                "tools": MCP_TOOL_CATALOG,
            },
        }

    @app.put("/api/settings")
    async def update_settings(payload: SettingsUpdateRequest):
        secret = (
            payload.proxmox_token_secret.strip()
            if payload.proxmox_token_secret is not None and payload.proxmox_token_secret.strip() != ""
            else service.settings.proxmox_token_secret
        )
        idrac_pw = (
            payload.idrac_password.strip()
            if payload.idrac_password is not None and payload.idrac_password.strip() != ""
            else service.settings.idrac_password
        )
        updated = AppSettings(
            proxmox_host=payload.proxmox_host,
            proxmox_token_id=payload.proxmox_token_id,
            proxmox_token_secret=secret,
            proxmox_verify_ssl=payload.proxmox_verify_ssl,
            proxmox_timeout=payload.proxmox_timeout,
            safety_mode=payload.safety_mode,
            allow_destructive=payload.allow_destructive,
            proxmox_demo_mode=payload.proxmox_demo_mode,
            webui_host=service.settings.webui_host,
            webui_port=service.settings.webui_port,
            proxmox_ssh_user=payload.proxmox_ssh_user,
            proxmox_ssh_port=payload.proxmox_ssh_port,
            proxmox_ssh_key_path=payload.proxmox_ssh_key_path,
            idrac_host=payload.idrac_host,
            idrac_user=payload.idrac_user,
            idrac_password=idrac_pw,
            electricity_rate_kwh=payload.electricity_rate_kwh,
            currency_symbol=payload.currency_symbol,
        )
        await service.reload_settings(updated, persist=True)
        service.audit.record(
            source="webui",
            action="update_settings",
            risk="write",
            target="settings",
            params={
                "host": updated.normalized_host,
                "safety_mode": updated.safety_mode,
                "allow_destructive": updated.allow_destructive,
                "demo_mode": updated.proxmox_demo_mode,
            },
            status="ok",
        )
        return {"settings": service.settings.public_dict()}

    @app.post("/api/settings/test")
    async def test_connection():
        return await service.test_connection()

    # ── Overview, Nodes, Telemetry ───────────────────────────────────────
    @app.get("/api/overview")
    async def get_overview():
        return await service.get_cluster_overview(source="webui")

    @app.get("/api/nodes/{node}")
    async def get_node(node: str):
        return await service.get_node_details(node=node, source="webui")

    @app.get("/api/nodes/{node}/rrd")
    async def get_node_rrd(node: str, timeframe: str = Query("hour")):
        return await service.get_node_rrd(node=node, timeframe=timeframe, source="webui")

    # ── Guests ───────────────────────────────────────────────────────────
    @app.get("/api/guests")
    async def list_guests(
        node: str | None = None,
        guest_type: str | None = Query(None, alias="type"),
        status: str | None = None,
        tag: str | None = None,
    ):
        guests = await service.list_guests(
            node=node, guest_type=guest_type, status=status, tag=tag, source="webui"
        )
        return {"guests": guests}

    @app.get("/api/guests/{vmid}")
    async def get_guest(
        vmid: int,
        node: str | None = None,
        guest_type: str | None = Query(None, alias="type"),
        timeframe: str = Query("hour"),
    ):
        return await service.get_guest_detail(
            vmid=vmid, node=node, guest_type=guest_type, timeframe=timeframe, source="webui"
        )

    @app.post("/api/guests/{vmid}/power")
    async def guest_power(vmid: int, body: PowerActionRequest):
        return await service.guest_power(
            vmid=vmid,
            action=body.action,
            node=body.node,
            guest_type=body.guest_type,
            wait=body.wait,
            source="webui",
        )

    @app.put("/api/guests/{vmid}/config")
    async def update_guest_config(vmid: int, body: GuestConfigUpdateRequest):
        return await service.update_guest_config(
            vmid=vmid,
            cores=body.cores,
            memory_mb=body.memory_mb,
            onboot=body.onboot,
            tags=body.tags,
            description=body.description,
            name=body.name,
            node=body.node,
            guest_type=body.guest_type,
            source="webui",
        )

    @app.post("/api/guests/{vmid}/clone")
    async def clone_guest(vmid: int, body: CloneGuestRequest):
        return await service.clone_guest(
            source_vmid=vmid,
            new_name=body.new_name,
            new_vmid=body.new_vmid,
            full_clone=body.full_clone,
            target_storage=body.target_storage,
            node=body.node,
            guest_type=body.guest_type,
            source="webui",
        )

    @app.delete("/api/guests/{vmid}")
    async def delete_guest(
        vmid: int,
        confirm: bool = Query(False),
        purge: bool = Query(True),
        node: str | None = None,
    ):
        return await service.delete_guest(
            vmid=vmid, confirm=confirm, purge=purge, node=node, source="webui"
        )

    # ── Snapshots & Backups ──────────────────────────────────────────────
    @app.post("/api/guests/{vmid}/snapshots")
    async def create_snapshot(vmid: int, body: CreateSnapshotRequest):
        return await service.create_snapshot(
            vmid=vmid,
            snapname=body.snapname,
            description=body.description,
            vmstate=body.vmstate,
            node=body.node,
            guest_type=body.guest_type,
            source="webui",
        )

    @app.post("/api/guests/{vmid}/snapshots/{snapname}/rollback")
    async def rollback_snapshot(
        vmid: int, snapname: str, confirm: bool = Query(False), node: str | None = None
    ):
        return await service.rollback_snapshot(
            vmid=vmid, snapname=snapname, confirm=confirm, node=node, source="webui"
        )

    @app.delete("/api/guests/{vmid}/snapshots/{snapname}")
    async def delete_snapshot(
        vmid: int, snapname: str, confirm: bool = Query(False), node: str | None = None
    ):
        return await service.delete_snapshot(
            vmid=vmid, snapname=snapname, confirm=confirm, node=node, source="webui"
        )

    @app.post("/api/guests/{vmid}/backup")
    async def trigger_backup(vmid: int, body: TriggerBackupRequest):
        return await service.trigger_backup(
            vmid=vmid,
            storage=body.storage,
            mode=body.mode,
            compress=body.compress,
            notes=body.notes,
            node=body.node,
            source="webui",
        )

    # ── Provisioning ─────────────────────────────────────────────────────
    @app.get("/api/provisioning/options")
    async def get_provisioning_options(node: str | None = None):
        return await service.get_provisioning_options(node=node)

    @app.post("/api/provisioning/vm")
    async def create_vm(body: CreateVMRequest):
        return await service.create_vm(**body.model_dump(), source="webui")

    @app.post("/api/provisioning/lxc")
    async def create_lxc(body: CreateLXCRequest):
        return await service.create_lxc(**body.model_dump(), source="webui")

    # ── Storage, Tasks, Health, Audit ────────────────────────────────────
    @app.get("/api/storage")
    async def get_storage(node: str | None = None):
        return await service.get_storage_and_disks(node=node, source="webui")

    @app.get("/api/tasks")
    async def get_tasks(
        node: str | None = None,
        limit: int = Query(35, ge=1, le=200),
        vmid: int | None = None,
        errors_only: bool = False,
    ):
        return await service.list_tasks(
            node=node, limit=limit, vmid=vmid, errors_only=errors_only, source="webui"
        )

    @app.get("/api/tasks/log")
    async def get_task_log(upid: str, node: str | None = None):
        if not upid:
            raise HTTPException(status_code=400, detail="upid is required")
        return await service.get_task_detail(upid=upid, node=node, source="webui")

    @app.get("/api/health-report")
    async def get_health_report():
        return await service.analyze_homelab_health(source="webui")

    @app.get("/api/audit")
    async def get_audit(
        limit: int = Query(50, ge=1, le=250),
        source: str | None = None,
    ):
        return {"events": service.audit.list_recent(limit=limit, source=source)}

    @app.get("/api/fleet/insights")
    async def get_fleet_insights(refresh: bool = Query(False)):
        return await service.get_fleet_insights(source="webui", force=refresh)

    @app.post("/api/guests/bulk")
    async def bulk_guest_action(body: BulkGuestActionRequest):
        return await service.bulk_guest_action(
            vmids=body.vmids,
            action=body.action,
            snapname=body.snapname,
            storage=body.storage,
            source="webui",
        )

    @app.put("/api/guests/{vmid}/bookmark")
    async def set_guest_bookmark(vmid: int, body: GuestBookmarkRequest):
        bm = service.set_guest_bookmark(vmid, body.url, body.label)
        return {"vmid": vmid, "bookmark": bm}

    # ── ISO & LXC Template Hub ───────────────────────────────────────────
    @app.get("/api/isos/search")
    async def search_isos(q: str = Query(""), web: bool = Query(True)):
        return await service.search_isos(query=q, include_web=web)

    @app.get("/api/isos/probe")
    async def probe_iso(url: str = Query(...)):
        return await service.probe_iso_url(url)

    @app.post("/api/isos/download")
    async def download_iso(body: DownloadIsoRequest):
        return await service.download_iso_from_url(**body.model_dump(), source="webui")

    @app.get("/api/appliances")
    async def list_appliances(q: str = Query(""), section: str = Query(""), node: str | None = None):
        return await service.list_lxc_appliances(node=node, search=q, section=section, source="webui")

    @app.post("/api/appliances/download")
    async def download_appliance(body: DownloadApplianceRequest):
        return await service.download_lxc_appliance(**body.model_dump(), source="webui")

    # ── Dual-Mode File Explorer (Storage Volumes & QEMU Guest Agent) ─────
    @app.get("/api/files/storage")
    async def browse_storage_files(
        storage: str | None = None, content: str | None = None, node: str | None = None
    ):
        return await service.browse_storage_volumes(
            storage=storage, content=content, node=node, source="webui"
        )

    @app.delete("/api/files/storage")
    async def delete_storage_file(
        storage: str = Query(...),
        volid: str = Query(...),
        confirm: bool = Query(False),
        node: str | None = None,
    ):
        return await service.delete_storage_volume(
            storage, volid, confirm=confirm, node=node, source="webui"
        )

    @app.post("/api/files/storage/upload")
    async def upload_storage_file(
        storage: str = Form(...),
        content: str = Form("iso"),
        file: UploadFile = File(...),
    ):
        raw = await file.read()
        return await service.upload_storage_file(
            storage=storage,
            content=content,
            filename=file.filename or "upload.bin",
            file_bytes=raw,
            source="webui",
        )

    @app.get("/api/files/guest/vms")
    async def list_agent_vms():
        return {"vms": await service.list_agent_capable_vms()}

    @app.get("/api/files/guest/{vmid}/ls")
    async def guest_ls(vmid: int, path: str = Query("/")):
        return await service.guest_agent_list_dir(vmid, path=path, source="webui")

    @app.get("/api/files/guest/{vmid}/read")
    async def guest_read_file(vmid: int, path: str = Query(...)):
        return await service.guest_agent_read_file(vmid, file_path=path, source="webui")

    @app.post("/api/files/guest/{vmid}/write")
    async def guest_write_file(vmid: int, body: GuestFileWriteRequest):
        return await service.guest_agent_write_file(
            vmid, file_path=body.path, content=body.content, source="webui"
        )

    @app.post("/api/files/guest/{vmid}/exec")
    async def guest_exec(vmid: int, body: GuestExecRequest):
        return await service.guest_agent_exec_command(vmid, command=body.command, source="webui")

    # ── Host Maintenance, APT Updates, Syslog, Firewall & Backup Scheduler
    @app.get("/api/host/maintenance")
    async def host_maintenance(
        node: str | None = None,
        syslog_limit: int = Query(120, ge=10, le=500),
        syslog_service: str | None = None,
    ):
        return await service.get_host_maintenance(
            node=node, syslog_limit=syslog_limit, syslog_service=syslog_service, source="webui"
        )

    @app.post("/api/host/apt-refresh")
    async def refresh_apt(node: str | None = None):
        return await service.refresh_host_apt(node=node, source="webui")

    @app.post("/api/backups/schedules")
    async def create_backup_schedule(body: CreateBackupScheduleRequest):
        return await service.create_backup_schedule(**body.model_dump(), source="webui")

    @app.delete("/api/backups/schedules/{job_id}")
    async def delete_backup_schedule(job_id: str, confirm: bool = Query(False)):
        return await service.delete_backup_schedule(job_id, confirm=confirm, source="webui")

    # ── Smart Alert Webhooks ─────────────────────────────────────────────
    @app.get("/api/alerts/config")
    async def get_alert_config():
        return {"config": service.get_alert_config()}

    @app.put("/api/alerts/config")
    async def save_alert_config(body: AlertConfigRequest):
        return {"config": service.save_alert_config(body.model_dump())}

    @app.post("/api/alerts/test")
    async def test_alert_webhook():
        return await service.send_webhook_alert(
            title="Test Notification",
            message=f"Connected to Proxmox Homelab Hub ({service.settings.normalized_host}). Alert webhooks are working!",
            severity="ok",
        )

    @app.post("/api/alerts/evaluate")
    async def evaluate_alerts(force_send: bool = Query(False)):
        return await service.evaluate_and_notify(force_send=force_send, source="webui")

    # ── Community-Scripts.org Catalog, Advanced Setup & Live SSH Deploy ──
    @app.get("/api/community-scripts")
    async def list_community_scripts(
        q: str = Query(""),
        category: str = Query(""),
        script_type: str = Query("", alias="type"),
        sort: str = Query("latest"),
        include_dev: bool = Query(False),
        refresh: bool = Query(False),
        limit: int = Query(80, ge=1, le=300),
    ):
        return await service.list_community_scripts(
            query=q,
            category=category,
            script_type=script_type,
            sort=sort,
            include_dev=include_dev,
            refresh=refresh,
            limit=limit,
        )

    @app.get("/api/community-scripts/{slug}/defaults")
    async def get_community_script_defaults(
        slug: str, script_type: str = Query("lxc", alias="type")
    ):
        return await service.get_community_script_setup(slug=slug, script_type=script_type)

    @app.post("/api/community-scripts/preview")
    async def preview_community_script(body: CommunityScriptActionRequest):
        return service.preview_community_script_command(
            slug=body.slug,
            script_type=body.script_type,
            script_url=body.script_url,
            options=body.options,
        )

    @app.post("/api/community-scripts/deploy")
    async def deploy_community_script(body: CommunityScriptActionRequest):
        return await service.deploy_community_script(
            slug=body.slug,
            script_type=body.script_type,
            script_url=body.script_url,
            options=body.options,
            source="webui",
        )

    @app.get("/api/community-scripts/jobs")
    async def list_community_jobs():
        return {"jobs": service.community.list_jobs()}

    @app.get("/api/community-scripts/jobs/{job_id}")
    async def get_community_job(job_id: str):
        job = service.community.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
        return job

    @app.post("/api/community-scripts/test-ssh")
    async def test_community_ssh():
        return await service.test_community_ssh()

    # ── Service Uptime Monitor (Mini Uptime Kuma) ────────────────────────
    @app.get("/api/uptime")
    async def get_uptime(probe: bool = Query(False)):
        if probe:
            return await service.run_uptime_probe(source="webui")
        return await service.get_uptime_dashboard(run_if_empty=True)

    @app.post("/api/uptime/probe")
    async def trigger_uptime_probe():
        return await service.run_uptime_probe(source="webui")

    # ── Unified Docker Container Manager ─────────────────────────────────
    @app.get("/api/docker/containers")
    async def list_docker():
        return await service.list_docker_containers(source="webui")

    @app.post("/api/docker/action")
    async def docker_action(body: DockerActionRequest):
        return await service.docker_container_action(**body.model_dump(), source="webui")

    # ── Dell iDRAC Redfish Hardware & Power Telemetry ────────────────────
    @app.get("/api/hardware/idrac")
    async def get_hardware_idrac():
        return await service.get_hardware_telemetry(source="webui")

    # ── Wake-on-LAN Portal ───────────────────────────────────────────────
    @app.get("/api/wol/devices")
    async def list_wol_devices(check: bool = Query(True)):
        return {"devices": await service.list_wol_devices(check_online=check)}

    @app.post("/api/wol/devices")
    async def save_wol_device(body: WolDeviceRequest):
        try:
            dev = service.save_wol_device(**body.model_dump())
            return {"device": dev}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.delete("/api/wol/devices/{device_id}")
    async def delete_wol_device(device_id: int):
        service.delete_wol_device(device_id)
        return {"deleted": True, "id": device_id}

    @app.post("/api/wol/wake")
    async def wake_wol(body: WolWakeRequest):
        try:
            return await service.send_wol(**body.model_dump(), source="webui")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    # ── Fleet-Wide Snapshot Checkpoints ──────────────────────────────────
    @app.get("/api/snapshots/all")
    async def list_all_snapshots():
        return await service.list_all_snapshots(source="webui")

    @app.post("/api/snapshots/checkpoint")
    async def create_group_checkpoint(body: GroupCheckpointRequest):
        return await service.create_group_checkpoint(**body.model_dump(), source="webui")

    # ── Static WebUI Assets ──────────────────────────────────────────────
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_index():
        return FileResponse(WEB_DIR / "index.html")

    # ── Mount Streamable HTTP MCP Endpoint directly at /mcp (no 307 redirect) ──
    app.mount("/", mcp_http_app)

    return app
