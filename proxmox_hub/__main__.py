"""CLI entry point for Proxmox Homelab Hub (WebUI + MCP Server)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import uvicorn

from .config import load_settings
from .mcp_server import create_mcp_server
from .service import ProxmoxService


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="proxmox_hub",
        description="Proxmox Homelab Hub — Custom WebUI & Dual-Transport MCP Server",
    )
    sub = parser.add_subparsers(dest="command", help="Mode to run")

    web_p = sub.add_parser("web", help="Run the Custom WebUI + REST API + Streamable HTTP MCP (/mcp)")
    web_p.add_argument("--host", default=None, help="Bind host (default from .env: 127.0.0.1)")
    web_p.add_argument("--port", type=int, default=None, help="Bind port (default from .env: 8806)")
    web_p.add_argument("--demo", action="store_true", help="Force Demo Mode on startup")

    mcp_p = sub.add_parser("mcp", help="Run the MCP Server over stdio (for Claude Desktop, Cursor, Antigravity)")
    mcp_p.add_argument("--demo", action="store_true", help="Force Demo Mode")

    sub.add_parser("check", help="Test connectivity to the configured Proxmox VE host")

    args = parser.parse_args()
    command = args.command or "web"
    settings = load_settings()

    if getattr(args, "demo", False):
        settings.proxmox_demo_mode = True

    if command == "mcp":
        service = ProxmoxService(settings=settings)
        mcp = create_mcp_server(service)
        mcp.run(transport="stdio")
        return

    if command == "check":
        async def _check() -> int:
            service = ProxmoxService(settings=settings)
            try:
                res = await service.test_connection()
                print(json.dumps(res, indent=2))
                return 0
            except Exception as exc:
                print(f"Connection check failed: {exc}", file=sys.stderr)
                return 1
            finally:
                await service.close()

        raise SystemExit(asyncio.run(_check()))

    # Default: web (WebUI + REST API + mounted Streamable HTTP MCP at /mcp)
    from .web_app import create_app

    host = args.host or settings.webui_host
    port = args.port or settings.webui_port
    app = create_app(settings=settings)
    print(f"Proxmox Homelab Hub WebUI : http://{host}:{port}")
    print(f"MCP Streamable HTTP       : http://{host}:{port}/mcp")
    print(f"Target Proxmox VE         : {settings.normalized_host} (demo={settings.proxmox_demo_mode})")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
