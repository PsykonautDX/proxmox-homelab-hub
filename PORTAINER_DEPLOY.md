# Deploying Proxmox Homelab Hub in Portainer

**No environment variables or YAML editing required.** Deploy the stack as-is, open the WebUI, and enter your Proxmox host, API token, and optional iDRAC/TrueNAS/SSH settings directly in **06 SYSTEM → MCP & Settings**. All settings, audit logs, uptime history, bookmarks, alerts, and Wake-on-LAN devices persist automatically in the named Docker volume `proxmox_hub_data` (`/app/data`).

---

## Option 1: Portainer Web Editor (Fastest — Zero Config)
1. In **Portainer → Stacks → + Add stack → Web editor**, name the stack `proxmox-homelab-hub`.
2. Paste this compose file as-is:
   ```yaml
   services:
     proxmox-hub:
       image: ghcr.io/psykonautdx/proxmox-homelab-hub:latest
       container_name: proxmox-homelab-hub
       restart: unless-stopped
       ports:
         - "8806:8806"
       environment:
         - TZ=Europe/London
       volumes:
         - proxmox_hub_data:/app/data

   volumes:
     proxmox_hub_data:
   ```
3. Click **Deploy the stack**.

---

## Option 2: Portainer Git Stack (Straight from GitHub)
1. In **Portainer → Stacks → + Add stack → Repository**:
   - **Name:** `proxmox-homelab-hub`
   - **Repository URL:** `https://github.com/PsykonautDX/proxmox-homelab-hub.git`
   - **Repository reference:** `refs/heads/main`
   - **Compose path:** `docker-compose.yml`
   - **Authentication:** `OFF` (public repository)
2. Leave environment variables empty and click **Deploy the stack**.

---

## First-Time Setup in the WebUI
1. Open **`http://<your-docker-host-ip>:8806`**.
2. Go to **06 SYSTEM → MCP & Settings** (or click **Enter Live API Token** on the welcome screen — you can also explore **Interactive Demo Mode** first!).
3. Enter your **Proxmox Host URL** (`https://<proxmox-ip>:8006`), **API Token ID** (`user@realm!tokenname`), and **API Token Secret**, then click **Save Settings** → **Test Connection**.
   - Settings save automatically to `/app/data/.env` inside the Docker volume and survive container restarts and updates.
4. **Endpoints:**
   - **WebUI Dashboard:** `http://<your-docker-host-ip>:8806`
   - **Streamable HTTP MCP:** `http://<your-docker-host-ip>:8806/mcp`
   - **Healthcheck:** `http://<your-docker-host-ip>:8806/health`
- **Wake-on-LAN Note:** To send UDP Magic Packets (`255.255.255.255`) from inside Docker to physical LAN devices, comment out `ports:` and uncomment `network_mode: host`.
