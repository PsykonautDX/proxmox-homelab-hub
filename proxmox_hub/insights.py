"""Fleet intelligence helpers: NIC/IPAM parsing, mount/storage extraction, service URL detection, and HTML description cleanup."""

from __future__ import annotations

import ipaddress
import re
from typing import Any

# Auto-detected default web UI ports/schemes for common homelab apps
KNOWN_SERVICE_RULES: list[tuple[str, str, int, str, str]] = [
    # (substring in guest name, scheme, port, path, label)
    ("plex", "http", 32400, "/web", "Plex"),
    ("sonarr", "http", 8989, "", "Sonarr"),
    ("radarr", "http", 7878, "", "Radarr"),
    ("prowlarr", "http", 9696, "", "Prowlarr"),
    ("tdarr", "http", 8265, "", "Tdarr"),
    ("deluge", "http", 8112, "", "Deluge"),
    ("notifiarr", "http", 5454, "", "Notifiarr"),
    ("profilarr", "http", 6868, "", "Profilarr"),
    ("cleanuparr", "http", 11011, "", "Cleanuparr"),
    ("tunarr", "http", 8000, "", "Tunarr"),
    ("bitmagnet", "http", 3333, "", "Bitmagnet"),
    ("changedetection", "http", 5000, "", "ChangeDetection"),
    ("gitea", "http", 3000, "", "Gitea"),
    ("romm", "http", 8080, "", "RomM"),
    ("yamtrack", "http", 8000, "", "Yamtrack"),
    ("prox-backup", "https", 8007, "", "Proxmox Backup"),
    ("pbs", "https", 8007, "", "Proxmox Backup"),
    ("opnsense", "https", 443, "", "OPNsense"),
    ("truenas", "http", 80, "", "TrueNAS"),
    ("nested-pve", "https", 8006, "", "Nested PVE"),
    ("home-assistant", "http", 8123, "", "Home Assistant"),
    ("adguard", "http", 3000, "", "AdGuard"),
    ("grafana", "http", 3000, "", "Grafana"),
    ("jellyfin", "http", 8096, "", "Jellyfin"),
    ("apt-cacher", "http", 3142, "/acng-report.html", "Apt-Cacher"),
]

DOCKER_IFACE_PREFIXES = ("lo", "docker", "br-", "veth", "virbr", "cni", "flannel", "cali", "tailscale")


def parse_nic_entries(config: dict[str, Any], guest_type: str) -> list[dict[str, Any]]:
    """Parse all net0, net1, ... definitions from QEMU or LXC config."""
    nics: list[dict[str, Any]] = []
    net_keys = sorted(
        (k for k in config if re.fullmatch(r"net\d+", k)),
        key=lambda x: int(x[3:]),
    )
    for key in net_keys:
        raw = str(config.get(key, ""))
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        kv: dict[str, str] = {}
        mac = ""
        model = ""
        for idx, part in enumerate(parts):
            if "=" in part:
                k, v = part.split("=", 1)
                if idx == 0 and guest_type == "qemu" and k in ("virtio", "e1000", "rtl8139", "vmxnet3"):
                    model = k
                    mac = v
                else:
                    kv[k] = v
        ip_raw = kv.get("ip", "")
        ip_addr = ""
        cidr = ""
        if ip_raw and ip_raw.lower() != "dhcp":
            cidr = ip_raw
            ip_addr = ip_raw.split("/")[0]
        nics.append(
            {
                "id": key,
                "name": kv.get("name", key),
                "bridge": kv.get("bridge", ""),
                "vlan": kv.get("tag", ""),
                "mac": kv.get("hwaddr", mac),
                "model": model or kv.get("type", ""),
                "ip": ip_addr,
                "cidr": cidr,
                "dhcp": ip_raw.lower() == "dhcp",
                "gateway": kv.get("gw", ""),
            }
        )
    return nics


def extract_agent_ips(agent_net: dict[str, Any] | None) -> tuple[list[str], list[str]]:
    """Extract (lan_ipv4s, secondary_ips) from QEMU guest agent, filtering Docker bridges."""
    lan_ips: list[str] = []
    other_ips: list[str] = []
    if not isinstance(agent_net, dict):
        return lan_ips, other_ips
    for iface in agent_net.get("result") or []:
        iname = str(iface.get("name", "")).lower()
        is_virtual = iname.startswith(DOCKER_IFACE_PREFIXES)
        for addr in iface.get("ip-addresses") or []:
            ip = str(addr.get("ip-address", ""))
            atype = str(addr.get("ip-address-type", ""))
            if not ip or ip.startswith(("127.", "fe80:", "::1", "169.254.")):
                continue
            if atype == "ipv4" and not is_virtual:
                # Filter default Docker RFC1918 container subnets (172.17-31.x.1)
                if re.match(r"^172\.(1[6-9]|2\d|3[01])\.\d+\.1$", ip):
                    other_ips.append(ip)
                elif ip not in lan_ips:
                    lan_ips.append(ip)
            elif ip not in other_ips and ip not in lan_ips:
                other_ips.append(ip)
    return lan_ips, other_ips


def extract_tag_ips(tags: list[str]) -> list[str]:
    """Pull valid IPv4 addresses stored as Proxmox tags (common homelab convention)."""
    found: list[str] = []
    for t in tags:
        try:
            ip = ipaddress.IPv4Address(t.strip())
            if str(ip) not in found:
                found.append(str(ip))
        except Exception:
            continue
    return found


def parse_bind_mounts(config: dict[str, Any]) -> list[dict[str, str]]:
    """Parse LXC mp0, mp1, ... bind/storage mount points."""
    mounts: list[dict[str, str]] = []
    mp_keys = sorted(
        (k for k in config if re.fullmatch(r"mp\d+", k)),
        key=lambda x: int(x[2:]),
    )
    for key in mp_keys:
        raw = str(config.get(key, ""))
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        source = parts[0] if parts else ""
        kv = dict(p.split("=", 1) for p in parts[1:] if "=" in p)
        mounts.append(
            {
                "id": key,
                "source": source,
                "target": kv.get("mp", ""),
                "size": kv.get("size", ""),
                "ro": kv.get("ro", "0"),
                "backup": kv.get("backup", "1"),
            }
        )
    return mounts


def parse_storages_used(config: dict[str, Any]) -> list[str]:
    """Identify which Proxmox storage pools hold this guest's disks."""
    disk_keys = [
        k
        for k in config
        if k == "rootfs"
        or re.fullmatch(r"(scsi|virtio|sata|ide|efidisk|tpmstate|unused)\d+", k)
    ]
    storages: list[str] = []
    for k in disk_keys:
        val = str(config.get(k, ""))
        if "media=cdrom" in val or val.startswith("none"):
            continue
        if ":" in val:
            st = val.split(":", 1)[0].strip()
            if st and st not in storages and not st.startswith("/"):
                storages.append(st)
    return storages


def parse_passthroughs(config: dict[str, Any]) -> list[dict[str, str]]:
    """Extract hostpci* and usb* hardware passthrough devices."""
    items: list[dict[str, str]] = []
    for k, v in config.items():
        if re.fullmatch(r"(hostpci|usb)\d+", k):
            items.append({"id": k, "kind": "PCIe" if k.startswith("hostpci") else "USB", "spec": str(v)})
    return items


def parse_description_summary(raw_desc: str | None) -> dict[str, Any]:
    """Detect Community-Scripts HTML banners and produce a clean plain-text summary."""
    text = str(raw_desc or "").strip()
    if not text:
        return {"has_html": False, "summary": "", "is_community_script": False}
    has_html = bool(re.search(r"<(div|a|img|h[1-6]|p|span)\b", text, re.IGNORECASE))
    is_cs = "community-scripts" in text.lower() or "helper-scripts.com" in text.lower()
    if has_html:
        h_match = re.search(r"<h[1-6][^>]*>(.*?)</h[1-6]>", text, re.IGNORECASE | re.DOTALL)
        if h_match:
            clean = re.sub(r"<[^>]+>", "", h_match.group(1)).strip()
        else:
            clean = re.sub(r"<[^>]+>", " ", text)
            clean = re.sub(r"\s+", " ", clean).strip()[:140]
        return {"has_html": True, "summary": clean, "is_community_script": is_cs}
    first_line = text.splitlines()[0].strip()[:140]
    return {"has_html": False, "summary": first_line, "is_community_script": is_cs}


def detect_service_url(name: str, primary_ip: str | None) -> dict[str, str] | None:
    """Auto-suggest a clickable Web UI link for well-known homelab services."""
    if not primary_ip:
        return None
    low = (name or "").lower()
    for needle, scheme, port, path, label in KNOWN_SERVICE_RULES:
        if needle in low:
            port_part = "" if (scheme == "http" and port == 80) or (scheme == "https" and port == 443) else f":{port}"
            return {
                "url": f"{scheme}://{primary_ip}{port_part}{path}",
                "label": label,
                "auto": "true",
            }
    return None


def suggest_free_ips(cidr_str: str, used_ips: set[str], limit: int = 6) -> list[str]:
    """Suggest available host IPs inside a /24 subnet starting from .10."""
    if not cidr_str:
        return []
    try:
        net = ipaddress.IPv4Network(cidr_str, strict=False)
    except Exception:
        return []
    if net.prefixlen < 24:
        return []
    free: list[str] = []
    for host in net.hosts():
        last_octet = int(str(host).split(".")[-1])
        if last_octet < 10 or last_octet >= 250:
            continue
        ip_s = str(host)
        if ip_s not in used_ips:
            free.append(ip_s)
            if len(free) >= limit:
                break
    return free
