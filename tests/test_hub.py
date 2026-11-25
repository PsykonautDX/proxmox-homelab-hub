"""End-to-end tests for Proxmox Homelab Hub (REST API, Safety Guardrails, WebUI & MCP Server)."""

from __future__ import annotations

import json
import pytest
from httpx import ASGITransport, AsyncClient

from proxmox_hub.audit import AuditLogger
from proxmox_hub.config import AppSettings
from proxmox_hub.mcp_server import MCP_TOOL_CATALOG, create_mcp_server
from proxmox_hub.service import ProxmoxService, SafetyPolicyError
from proxmox_hub.web_app import create_app


@pytest.mark.asyncio
async def test_unconfigured_returns_428_not_fake_data(tmp_path):
    settings = AppSettings(
        proxmox_host="https://192.168.1.2:8006",
        proxmox_token_id="",
        proxmox_token_secret="",
        proxmox_demo_mode=False,
    )
    app = create_app(settings=settings)
    app.state.service.audit = AuditLogger(db_path=tmp_path / "audit.db")

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r_health = await client.get("/health")
            assert r_health.status_code == 200
            assert r_health.json()["configured"] is False

            r_over = await client.get("/api/overview")
            assert r_over.status_code == 428
            assert r_over.json()["code"] == "NOT_CONFIGURED"

            r_html = await client.get("/")
            assert r_html.status_code == 200
            assert "CRYSTARIUM" in r_html.text
            for asset in ("/static/styles.css", "/static/app.js", "/static/extras.js", "/static/metalheart.js"):
                ra = await client.get(asset)
                assert ra.status_code == 200


@pytest.mark.asyncio
async def test_full_demo_webui_rest_lifecycle(tmp_path):
    settings = AppSettings(
        proxmox_host="https://192.168.1.2:8006",
        proxmox_demo_mode=True,
        safety_mode="safe",
        allow_destructive=False,
    )
    app = create_app(settings=settings)
    app.state.service.audit = AuditLogger(db_path=tmp_path / "audit.db")

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Overview & Health & Node RRD
            ov = (await client.get("/api/overview")).json()
            assert ov["demo_mode"] is True
            assert ov["summary"]["nodes_online"] == 1
            assert ov["summary"]["vms_total"] >= 3

            rrd = (await client.get("/api/nodes/pve/rrd?timeframe=day")).json()
            assert len(rrd["points"]) > 10

            hr = (await client.get("/api/health-report")).json()
            assert 0 <= hr["score"] <= 100
            assert any("zfs-tank" in f["title"] for f in hr["findings"])

            # 2. Provisioning options + Create VM + Create LXC + Clone
            opts = (await client.get("/api/provisioning/options")).json()
            assert opts["next_vmid"] >= 103
            assert len(opts["isos"]) >= 1
            assert len(opts["vztmpls"]) >= 1

            vm_res = await client.post(
                "/api/provisioning/vm",
                json={
                    "name": "pytest-ubuntu",
                    "cores": 2,
                    "memory_mb": 2048,
                    "disk_storage": "local-lvm",
                    "disk_size_gb": 20,
                    "tags": "test;ci",
                },
            )
            assert vm_res.status_code == 200
            new_vmid = vm_res.json()["vmid"]

            lxc_res = await client.post(
                "/api/provisioning/lxc",
                json={
                    "hostname": "pytest-alpine",
                    "ostemplate": opts["vztmpls"][0]["volid"],
                    "cores": 1,
                    "memory_mb": 512,
                    "disk_size_gb": 4,
                    "start_after_create": True,
                },
            )
            assert lxc_res.status_code == 200
            new_ctid = lxc_res.json()["vmid"]

            # 3. Power, Config, Snapshot, Backup
            p_res = await client.post(f"/api/guests/{new_vmid}/power", json={"action": "start"})
            assert p_res.status_code == 200

            cfg_res = await client.put(
                f"/api/guests/{new_vmid}/config",
                json={"cores": 4, "memory_mb": 4096, "tags": "updated"},
            )
            assert cfg_res.status_code == 200

            snap_res = await client.post(
                f"/api/guests/{new_vmid}/snapshots",
                json={"snapname": "pre-test", "description": "clean state"},
            )
            assert snap_res.status_code == 200

            bkp_res = await client.post(
                f"/api/guests/{new_vmid}/backup",
                json={"storage": "pbs-backup", "mode": "snapshot", "compress": "zstd"},
            )
            assert bkp_res.status_code == 200
            assert bkp_res.json()["upid"].startswith("UPID:")

            # 4. Safety guardrail: destructive delete blocked in 'safe' mode
            await client.post(f"/api/guests/{new_vmid}/power", json={"action": "stop"})
            del_blocked = await client.delete(f"/api/guests/{new_vmid}?confirm=true")
            assert del_blocked.status_code == 403
            assert del_blocked.json()["code"] == "SAFETY_BLOCKED"

            # Enable destructive mode in runtime settings & verify confirm gate
            app.state.service.settings.allow_destructive = True
            del_no_confirm = await client.delete(f"/api/guests/{new_vmid}?confirm=false")
            assert del_no_confirm.status_code == 403

            del_ok = await client.delete(f"/api/guests/{new_vmid}?confirm=true")
            assert del_ok.status_code == 200

            # 5. Storage, Tasks, Logs & Audit
            st = (await client.get("/api/storage")).json()
            assert len(st["storages"]) >= 3
            assert len(st["disks"]) >= 2

            tasks = (await client.get("/api/tasks")).json()["tasks"]
            assert len(tasks) >= 3
            log_res = (await client.get(f"/api/tasks/log?upid={tasks[0]['upid']}&node=pve")).json()
            assert "TASK OK" in "\n".join(log_res["log"])

            audit = (await client.get("/api/audit")).json()["events"]
            actions = {e["action"] for e in audit}
            assert "create_vm" in actions
            assert "create_lxc" in actions
            assert "delete_guest" in actions
            assert new_ctid > 0

            # 6. Fleet Insights (IPAM, Backup Coverage, Capacity, Storage Map) & Bookmarks & Bulk Ops
            ins = (await client.get("/api/fleet/insights")).json()
            assert len(ins["guests"]) >= 5
            assert len(ins["network_ipam"]["bridges"]) >= 1
            assert ins["backup_coverage"]["summary"]["total_guests"] >= 5
            assert ins["capacity"]["host_cores"] == 16
            assert "local-lvm" in ins["storage_map"]["by_storage"]
            # Console URL & auto-detected service link (home-assistant / jellyfin)
            ha = next(g for g in ins["guests"] if g["vmid"] == 101)
            assert "novnc=1" in ha["console_url"]
            assert ha["service_url"] and ":8123" in ha["service_url"]

            # Save custom bookmark
            bm_res = await client.put(
                "/api/guests/100/bookmark",
                json={"url": "https://portainer.lab.local", "label": "Portainer"},
            )
            assert bm_res.status_code == 200
            assert bm_res.json()["bookmark"]["url"] == "https://portainer.lab.local"

            # Bulk snapshot
            bulk_res = await client.post(
                "/api/guests/bulk",
                json={"vmids": [100, 101], "action": "snapshot", "snapname": "bulk-ci-snap"},
            )
            assert bulk_res.status_code == 200
            assert bulk_res.json()["succeeded"] == 2

            # Verify SSD wearout fix: 96% remaining life should NOT trigger wearout warning
            assert not any("wear" in f["title"].lower() for f in hr["findings"])

            # 7. ISO Hub, Appliances, File Explorer (Storage & Guest Agent), Host Updates & Alerts
            iso_search = (await client.get("/api/isos/search?q=ubuntu&web=false")).json()
            assert len(iso_search["curated"]) >= 1

            dl_iso = await client.post(
                "/api/isos/download",
                json={
                    "storage": "local",
                    "url": "https://example.com/alpine.iso",
                    "filename": "alpine.iso",
                    "content": "iso",
                },
            )
            assert dl_iso.status_code == 200
            assert dl_iso.json()["upid"].startswith("UPID:")

            apls = (await client.get("/api/appliances")).json()
            assert len(apls["appliances"]) >= 3
            dl_apl = await client.post(
                "/api/appliances/download",
                json={"storage": "local", "template": apls["appliances"][0]["template"]},
            )
            assert dl_apl.status_code == 200

            files = (await client.get("/api/files/storage?storage=local")).json()
            assert any("alpine.iso" in v["volid"] for v in files["volumes"])

            # Guest Agent File Explorer & Exec
            agent_vms = (await client.get("/api/files/guest/vms")).json()["vms"]
            assert any(v["vmid"] == 100 for v in agent_vms)
            ls_res = (await client.get("/api/files/guest/100/ls?path=/etc")).json()
            assert any(e["name"] == "hosts" for e in ls_res["entries"])
            rd_res = (await client.get("/api/files/guest/100/read?path=/etc/hosts")).json()
            assert "docker-core" in rd_res["content"]
            wr_res = await client.post(
                "/api/files/guest/100/write",
                json={"path": "/etc/hosts", "content": "127.0.0.1 localhost\n"},
            )
            assert wr_res.status_code == 200
            ex_res = await client.post(
                "/api/files/guest/100/exec", json={"command": "uname -a"}
            )
            assert ex_res.status_code == 200
            assert ex_res.json()["exitcode"] == 0

            # Host Maintenance, Backup Schedule & Alerts
            maint = (await client.get("/api/host/maintenance")).json()
            assert len(maint["apt_updates"]) >= 1
            assert len(maint["syslog"]) >= 1
            assert maint["firewall"]["options"]["enable"] == 1

            sched = await client.post(
                "/api/backups/schedules",
                json={"storage": "pbs-backup", "schedule": "03:00", "all_guests": True},
            )
            assert sched.status_code == 200

            al_put = await client.put(
                "/api/alerts/config",
                json={"enabled": True, "provider": "discord", "webhook_url": "", "min_health_score": 80},
            )
            assert al_put.status_code == 200
            al_eval = await client.post("/api/alerts/evaluate")
            assert al_eval.status_code == 200
            assert "health_score" in al_eval.json()

            # 8. Community Scripts Command Builder & Demo SSH Deploy
            prev_res = await client.post(
                "/api/community-scripts/preview",
                json={
                    "slug": "docker",
                    "script_type": "lxc",
                    "options": {
                        "unattended": True,
                        "ctid": 250,
                        "hostname": "docker-03",
                        "cpu": 4,
                        "ram": 4096,
                        "disk": 20,
                        "container_storage": "VM-STORE-01",
                        "template_storage": "ISO",
                        "bridge": "vmbr0",
                        "net": "192.168.1.95/24",
                        "gateway": "192.168.1.1",
                        "password": "supersecret",
                        "apt_cacher": "yes",
                        "apt_cacher_ip": "192.168.1.19",
                    },
                },
            )
            assert prev_res.status_code == 200
            pdata = prev_res.json()
            assert "MODE=unattended" in pdata["command"]
            assert "var_container_storage=VM-STORE-01" in pdata["command"]
            assert "var_apt_cacher_ip=192.168.1.19" in pdata["command"]
            assert "supersecret" not in pdata["display_command"]

            ssh_test = await client.post("/api/community-scripts/test-ssh")
            assert ssh_test.status_code == 200
            assert ssh_test.json()["ok"] is True

            dep = await client.post(
                "/api/community-scripts/deploy",
                json={"slug": "docker", "script_type": "lxc", "options": {"unattended": True, "cpu": 2}},
            )
            assert dep.status_code == 200
            job_id = dep.json()["job_id"]
            job_res = await client.get(f"/api/community-scripts/jobs/{job_id}")
            assert job_res.status_code == 200
            assert job_res.json()["slug"] == "docker"

            # 9. Service Uptime, Unified Docker, Dell iDRAC, Wake-on-LAN & Group Snapshot Checkpoints
            up_res = await client.post("/api/uptime/probe")
            assert up_res.status_code == 200
            assert up_res.json()["summary"]["total"] >= 2
            assert up_res.json()["summary"]["up"] >= 2

            dk_res = await client.get("/api/docker/containers")
            assert dk_res.status_code == 200
            assert dk_res.json()["summary"]["total"] >= 4
            dk_log = await client.post(
                "/api/docker/action",
                json={"host_vmid": 100, "container_name": "traefik", "action": "logs"},
            )
            assert dk_log.status_code == 200
            assert "traefik" in dk_log.json()["output"]

            hw_res = await client.get("/api/hardware/idrac")
            assert hw_res.status_code == 200
            assert hw_res.json()["power"]["watts_now"] > 0

            wol_add = await client.post(
                "/api/wol/devices",
                json={"name": "Test-NAS", "mac": "AA:BB:CC:DD:EE:FF", "ip": "192.168.1.240"},
            )
            assert wol_add.status_code == 200
            wol_id = wol_add.json()["device"]["id"]
            wol_list = (await client.get("/api/wol/devices?check=false")).json()["devices"]
            assert any(d["id"] == wol_id for d in wol_list)

            chk_res = await client.post(
                "/api/snapshots/checkpoint",
                json={"tag": "homelab", "snapname": "chk-ci-test"},
            )
            assert chk_res.status_code == 200
            assert chk_res.json()["succeeded"] >= 1
            all_snaps = (await client.get("/api/snapshots/all")).json()
            assert all_snaps["total_snapshots"] >= 1


@pytest.mark.asyncio
async def test_mcp_server_tools_resources_prompts_and_http(tmp_path):
    settings = AppSettings(
        proxmox_host="https://192.168.1.2:8006",
        proxmox_demo_mode=True,
        safety_mode="safe",
        allow_destructive=False,
    )
    audit = AuditLogger(db_path=tmp_path / "mcp_audit.db")
    service = ProxmoxService(settings=settings, audit=audit)
    mcp = create_mcp_server(service)

    tools = await mcp.list_tools()
    tool_names = {t.name for t in tools}
    catalog_names = {t["name"] for t in MCP_TOOL_CATALOG}
    assert tool_names == catalog_names
    assert len(tools) == 44

    resources = await mcp.list_resources()
    assert len(resources) == 3

    prompts = await mcp.list_prompts()
    assert {p.name for p in prompts} == {
        "homelab_daily_checkup",
        "plan_new_workload",
        "safe_maintenance_workflow",
    }

    # Invoke MCP tools directly
    ov_res = await mcp.call_tool("get_cluster_overview", {})
    ov_text = ov_res[0].text if isinstance(ov_res, list) else ov_res.content[0].text
    ov_json = json.loads(ov_text)
    assert ov_json["summary"]["nodes_online"] == 1

    # Destructive tool must enforce safety guardrail
    with pytest.raises((SafetyPolicyError, Exception)):
        await service.delete_guest(102, confirm=True, source="mcp")

    # Verify audit recorded MCP calls
    mcp_events = audit.list_recent(limit=20, source="mcp")
    assert any(e["action"] == "get_cluster_overview" for e in mcp_events)

    # Verify Streamable HTTP /mcp endpoint responds directly (no redirect) to MCP initialize & tools/list
    app = create_app(settings=settings)
    app.state.service.audit = audit
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8806") as client:
            headers = {
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            }
            init_payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest-client", "version": "1.0"},
                },
            }
            resp = await client.post("/mcp", json=init_payload, headers=headers)
            assert resp.status_code == 200
            assert "proxmox-homelab-hub" in resp.text

            list_payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
            resp2 = await client.post("/mcp", json=list_payload, headers=headers)
            assert resp2.status_code == 200
            assert "analyze_homelab_health" in resp2.text
            assert "create_vm" in resp2.text
