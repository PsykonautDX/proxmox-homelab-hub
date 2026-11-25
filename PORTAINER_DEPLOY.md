# Deploying Proxmox Homelab Hub in Portainer

Zero secrets are baked into the image. All settings, SQLite audit logs, uptime history, custom service bookmarks, alert rules, and Wake-on-LAN devices persist automatically inside the named Docker volume `proxmox_hub_data` (`/app/data`).

---

## Option 1: Portainer Web Editor (Pre-Built Image — Fastest)
1. In **Portainer → Stacks → + Add stack → Web editor**, name the stack `proxmox-homelab-hub`.
2. Paste the contents of [`docker-compose.portainer.yml`](./docker-compose.portainer.yml):
   ```yaml
   services:
     proxmox-hub:
       image: ghcr.io/psykonautdx/proxmox-homelab-hub:latest
       container_name: proxmox-homelab-hub
       restart: unless-stopped
       ports:
         - "8806:8806"
       environment:
         TZ: Europe/London
         WEBUI_HOST: 0.0.0.0
         WEBUI_PORT: 8806
         PROXMOX_DATA_DIR: /app/data
         PROXMOX_ENV_FILE: /app/data/.env
         PROXMOX_HOST: https://your-proxmox-ip:8006
         PROXMOX_TOKEN_ID: ""
         PROXMOX_TOKEN_SECRET: ""
         PROXMOX_VERIFY_SSL: "false"
         SAFETY_MODE: safe
         ALLOW_DESTRUCTIVE: "false"
         PROXMOX_DEMO_MODE: "false"
       volumes:
         - proxmox_hub_data:/app/data

   volumes:
     proxmox_hub_data:
   ```
3. Either fill in `PROXMOX_HOST`, `PROXMOX_TOKEN_ID`, and `PROXMOX_TOKEN_SECRET` now — or leave them blank (or set `PROXMOX_DEMO_MODE: "true"` to preview with demo data) and configure them later inside the WebUI (`06 SYSTEM → MCP & Settings`).
4. Click **Deploy the stack**.

---

## Option 2: Portainer Git Stack (Build Straight From GitHub)
1. In **Portainer → Stacks → + Add stack**:
   - **Name:** `proxmox-homelab-hub`
   - **Build method:** **Repository**
   - **Repository URL:** `https://github.com/PsykonautDX/proxmox-homelab-hub.git`
   - **Repository reference:** `refs/heads/main`
   - **Compose path:** `docker-compose.yml`
2. *(Optional)* Add environment variables (`PROXMOX_HOST`, `PROXMOX_TOKEN_ID`, `PROXMOX_TOKEN_SECRET`, `PROXMOX_DEMO_MODE`), or leave blank and enter them in the WebUI after startup.
3. Click **Deploy the stack** (Portainer clones this repo and builds the image automatically).

---

## After Deploying
- **WebUI Dashboard:** `http://<your-docker-host-ip>:8806`
- **Streamable HTTP MCP Endpoint:** `http://<your-docker-host-ip>:8806/mcp`
- **Healthcheck:** `http://<your-docker-host-ip>:8806/health`
- **Create a Proxmox API Token:** In Proxmox VE → **Datacenter → Permissions → API Tokens → Add** (use `PVEAuditor` on `/` for read-only monitoring, or uncheck *Privilege Separation* / add `PVEVMAdmin` for full controls). Paste the Token ID (`user@realm!tokenname`) and Secret into **06 SYSTEM → MCP & Settings** in the WebUI.
- **Wake-on-LAN Note:** To send UDP Magic Packets (`255.255.255.255`) from inside Docker to physical LAN devices, comment out `ports:` and uncomment `network_mode: host` in the compose file.
