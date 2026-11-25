"""Curated Homelab ISO Catalog, Live Official Mirror & GitHub Release ISO Search, and Remote URL Probe."""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

import httpx

CURATED_ISOS: list[dict[str, str]] = [
    {
        "name": "Ubuntu 24.04.2 LTS Server (amd64)",
        "category": "Linux Server",
        "filename": "ubuntu-24.04.2-live-server-amd64.iso",
        "url": "https://releases.ubuntu.com/24.04.2/ubuntu-24.04.2-live-server-amd64.iso",
        "description": "Most popular homelab Linux server LTS (cloud-init & Docker ready).",
    },
    {
        "name": "Ubuntu 24.04.2 LTS Cloud Image (qcow2)",
        "category": "Cloud Images",
        "filename": "noble-server-cloudimg-amd64.img",
        "url": "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img",
        "description": "Official Ubuntu 24.04 cloud-init image for instant Proxmox VM templates.",
    },
    {
        "name": "Debian 12 Bookworm Netinst (amd64)",
        "category": "Linux Server",
        "filename": "debian-12.10.0-amd64-netinst.iso",
        "url": "https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/debian-12.10.0-amd64-netinst.iso",
        "description": "Rock-solid lightweight Debian network installer.",
    },
    {
        "name": "Debian 12 Generic Cloud Image (qcow2)",
        "category": "Cloud Images",
        "filename": "debian-12-genericcloud-amd64.qcow2",
        "url": "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-amd64.qcow2",
        "description": "Minimal Debian cloud-init disk image with QEMU Guest Agent support.",
    },
    {
        "name": "Alpine Linux 3.21 Virtual (x86_64)",
        "category": "Linux Server",
        "filename": "alpine-virt-3.21.3-x86_64.iso",
        "url": "https://dl-cdn.alpinelinux.org/alpine/v3.21/releases/x86_64/alpine-virt-3.21.3-x86_64.iso",
        "description": "Ultra-minimal (~65 MB) musl/BusyBox VM image ideal for micro-services.",
    },
    {
        "name": "Rocky Linux 9 Minimal (x86_64)",
        "category": "Linux Server",
        "filename": "Rocky-9-latest-x86_64-minimal.iso",
        "url": "https://download.rockylinux.org/pub/rocky/9/isos/x86_64/Rocky-9-latest-x86_64-minimal.iso",
        "description": "RHEL-compatible enterprise Linux minimal ISO.",
    },
    {
        "name": "AlmaLinux 9 Minimal (x86_64)",
        "category": "Linux Server",
        "filename": "AlmaLinux-9-latest-x86_64-minimal.iso",
        "url": "https://repo.almalinux.org/almalinux/9/isos/x86_64/AlmaLinux-9-latest-x86_64-minimal.iso",
        "description": "Community-owned RHEL binary-compatible minimal ISO.",
    },
    {
        "name": "Fedora Server 41 Netinst (x86_64)",
        "category": "Linux Server",
        "filename": "Fedora-Server-netinst-x86_64-41-1.4.iso",
        "url": "https://download.fedoraproject.org/pub/fedora/linux/releases/41/Server/x86_64/iso/Fedora-Server-netinst-x86_64-41-1.4.iso",
        "description": "Modern upstream Linux server with Cockpit built-in.",
    },
    {
        "name": "Arch Linux Latest (x86_64)",
        "category": "Linux Server",
        "filename": "archlinux-x86_64.iso",
        "url": "https://geo.mirror.pkgbuild.com/iso/latest/archlinux-x86_64.iso",
        "description": "Rolling-release Arch Linux installation ISO.",
    },
    {
        "name": "NixOS Minimal (x86_64)",
        "category": "Linux Server",
        "filename": "latest-nixos-minimal-x86_64-linux.iso",
        "url": "https://channels.nixos.org/nixos-24.11/latest-nixos-minimal-x86_64-linux.iso",
        "description": "Declarative, reproducible NixOS minimal installer ISO.",
    },
    {
        "name": "VirtIO Windows Drivers (Latest Stable)",
        "category": "Windows / Drivers",
        "filename": "virtio-win.iso",
        "url": "https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/virtio-win.iso",
        "description": "Essential VirtIO SCSI, Balloon, NIC, and QEMU Guest Agent drivers for Windows VMs.",
    },
    {
        "name": "Proxmox VE (Nested Lab)",
        "category": "Hypervisor & Storage",
        "filename": "proxmox-ve_8.3-1.iso",
        "url": "https://enterprise.proxmox.com/iso/proxmox-ve_8.3-1.iso",
        "description": "Proxmox VE installer for nested virtualization sandboxes.",
    },
    {
        "name": "Proxmox Backup Server",
        "category": "Hypervisor & Storage",
        "filename": "proxmox-backup-server_3.3-1.iso",
        "url": "https://enterprise.proxmox.com/iso/proxmox-backup-server_3.3-1.iso",
        "description": "Deduplicated incremental backup server for PVE.",
    },
    {
        "name": "TrueNAS SCALE 24.10 (Electric Eel)",
        "category": "Hypervisor & Storage",
        "filename": "TrueNAS-SCALE-24.10.2.iso",
        "url": "https://download.sys.truenas.net/TrueNAS-SCALE-ElectricEel/24.10.2/TrueNAS-SCALE-24.10.2.iso",
        "description": "ZFS NAS & storage appliance OS.",
    },
    {
        "name": "OPNsense 25.1 DVD (amd64)",
        "category": "Networking & Firewall",
        "filename": "OPNsense-25.1-dvd-amd64.iso.bz2",
        "url": "https://mirror.ams1.nl.leaseweb.net/opnsense/releases/25.1/OPNsense-25.1-dvd-amd64.iso.bz2",
        "description": "Open-source FreeBSD router/firewall appliance.",
    },
    {
        "name": "Netboot.xyz (Universal PXE / Multi-OS ISO)",
        "category": "Rescue & Utilities",
        "filename": "netboot.xyz.iso",
        "url": "https://boot.netboot.xyz/ipxe/netboot.xyz.iso",
        "description": "Tiny (~3 MB) bootable ISO that network-boots 50+ OS installers & rescue tools.",
    },
    {
        "name": "SystemRescue 11.03 (amd64)",
        "category": "Rescue & Utilities",
        "filename": "systemrescue-11.03-amd64.iso",
        "url": "https://fastly-cdn.system-rescue.org/releases/11.03/systemrescue-11.03-amd64.iso",
        "description": "Bootable Linux rescue toolkit for ZFS, LVM, partition repair, and data recovery.",
    },
    {
        "name": "GParted Live (amd64)",
        "category": "Rescue & Utilities",
        "filename": "gparted-live-1.7.0-1-amd64.iso",
        "url": "https://downloads.sourceforge.net/gparted/gparted-live-1.7.0-1-amd64.iso",
        "description": "Graphical disk partition resizing & recovery live ISO.",
    },
]

OFFICIAL_MIRROR_INDEXES: list[tuple[str, str, str]] = [
    # (keywords, label, directory_index_url)
    ("proxmox pve pbs backup mail", "Proxmox Official ISO Mirror", "https://enterprise.proxmox.com/iso/"),
    ("debian linux", "Debian Current amd64 CD Mirror", "https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/"),
    ("alpine linux minimal", "Alpine Linux Latest Stable Mirror", "https://dl-cdn.alpinelinux.org/alpine/latest-stable/releases/x86_64/"),
    ("ubuntu linux server", "Ubuntu 24.04 Official Releases", "https://releases.ubuntu.com/24.04/"),
]


def _guess_filename(url: str, content_disposition: str = "") -> str:
    if content_disposition:
        m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";\r\n]+)"?', content_disposition, re.I)
        if m:
            return m.group(1).strip()
    path = urlparse(url).path.rstrip("/")
    tail = unquote(path.split("/")[-1]) if path else ""
    if tail and "." in tail:
        return tail
    return "download.iso"


async def probe_remote_url(url: str, timeout: float = 12.0) -> dict[str, Any]:
    """Verify a remote ISO/image URL with HTTP HEAD before handing it to Proxmox download-url."""
    clean = url.strip()
    if not clean.startswith(("http://", "https://")):
        return {"ok": False, "url": clean, "error": "URL must start with http:// or https://"}

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(timeout),
        headers={"User-Agent": "ProxmoxHomelabHub/1.0"},
    ) as client:
        try:
            resp = await client.head(clean)
            if resp.status_code >= 400 or resp.status_code == 405:
                resp = await client.get(clean, headers={"Range": "bytes=0-0"})
            final_url = str(resp.url)
            size_hdr = resp.headers.get("content-length")
            content_range = resp.headers.get("content-range", "")
            size_bytes: int | None = None
            if "/" in content_range:
                tail = content_range.split("/")[-1]
                if tail.isdigit():
                    size_bytes = int(tail)
            elif size_hdr and size_hdr.isdigit():
                size_bytes = int(size_hdr)

            filename = _guess_filename(final_url, resp.headers.get("content-disposition", ""))
            ctype = resp.headers.get("content-type", "")
            is_direct = (
                filename.lower().endswith(
                    (".iso", ".img", ".qcow2", ".raw", ".tar.gz", ".tar.zst", ".tar.xz", ".gz", ".xz", ".bz2")
                )
                or "octet-stream" in ctype.lower()
                or "iso9660" in ctype.lower()
            )
            return {
                "ok": resp.status_code < 400,
                "status_code": resp.status_code,
                "url": final_url,
                "filename": filename,
                "size_bytes": size_bytes,
                "content_type": ctype,
                "is_direct_download": is_direct,
            }
        except Exception as exc:
            return {"ok": False, "url": clean, "error": str(exc)}


async def _scan_mirror_index(
    client: httpx.AsyncClient, label: str, base_url: str, query_low: str
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    try:
        resp = await client.get(base_url)
        if resp.status_code >= 400:
            return []
        hrefs = re.findall(r'href="([^"]+\.(?:iso|img|qcow2))"', resp.text, re.IGNORECASE)
        seen: set[str] = set()
        for h in hrefs:
            if h.startswith(("?", "/")):
                continue
            full_url = urljoin(base_url, h)
            if full_url in seen:
                continue
            seen.add(full_url)
            fn = _guess_filename(full_url)
            if query_low and not any(tok in fn.lower() or tok in label.lower() for tok in query_low.split()):
                continue
            results.append(
                {
                    "title": f"{label} — {fn}",
                    "url": full_url,
                    "filename": fn,
                    "snippet": f"Live official directory listing from {urlparse(base_url).netloc}",
                    "is_direct_iso": True,
                }
            )
    except Exception:
        return []
    return results[-8:]


async def _search_github_iso_releases(
    client: httpx.AsyncClient, query: str
) -> list[dict[str, Any]]:
    """Query GitHub's public REST API for official repos publishing direct .iso / .img / .qcow2 release assets."""
    results: list[dict[str, Any]] = []
    try:
        r = await client.get(
            "https://api.github.com/search/repositories",
            params={"q": f"{query} iso", "sort": "stars", "per_page": 5},
            headers={"Accept": "application/vnd.github+json"},
        )
        if r.status_code != 200:
            return []
        repos = r.json().get("items") or []

        async def check_repo(repo: dict[str, Any]) -> list[dict[str, Any]]:
            full_name = repo.get("full_name")
            if not full_name:
                return []
            rel_resp = await client.get(
                f"https://api.github.com/repos/{full_name}/releases/latest",
                headers={"Accept": "application/vnd.github+json"},
            )
            if rel_resp.status_code != 200:
                return []
            rel = rel_resp.json()
            found: list[dict[str, Any]] = []
            for asset in rel.get("assets") or []:
                aname = str(asset.get("name") or "")
                dl = str(asset.get("browser_download_url") or "")
                if not dl:
                    continue
                if re.search(r"\.(iso|qcow2|img\.xz|img\.gz|iso\.gz|iso\.bz2)$", aname, re.I):
                    size_mb = round((asset.get("size") or 0) / (1024 * 1024), 1)
                    found.append(
                        {
                            "title": f"{full_name} ({rel.get('tag_name', 'latest')}) — {aname}",
                            "url": dl,
                            "filename": aname,
                            "snippet": f"GitHub Release asset · {size_mb} MiB · {repo.get('description') or ''}"[:200],
                            "is_direct_iso": True,
                        }
                    )
            return found[:3]

        batches = await asyncio.gather(*(check_repo(rp) for rp in repos), return_exceptions=True)
        for b in batches:
            if isinstance(b, list):
                results.extend(b)
    except Exception:
        return []
    return results[:10]


async def search_web_isos(query: str, include_web: bool = True) -> dict[str, Any]:
    """Search the verified homelab ISO library, live official distro mirrors, and GitHub ISO releases."""
    q = (query or "").strip()
    q_low = q.lower()

    curated_matches = [
        item
        for item in CURATED_ISOS
        if not q_low
        or q_low in item["name"].lower()
        or q_low in item["category"].lower()
        or q_low in item["description"].lower()
        or q_low in item["filename"].lower()
    ]

    web_results: list[dict[str, Any]] = []
    web_error: str | None = None

    if include_web and q:
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(10.0),
                headers={"User-Agent": "ProxmoxHomelabHub/1.0"},
            ) as client:
                tasks = []
                for keywords, label, mirror_url in OFFICIAL_MIRROR_INDEXES:
                    if any(tok in keywords for tok in q_low.split()):
                        tasks.append(_scan_mirror_index(client, label, mirror_url, q_low))
                tasks.append(_search_github_iso_releases(client, q))
                batches = await asyncio.gather(*tasks, return_exceptions=True)
                seen: set[str] = set()
                for b in batches:
                    if isinstance(b, list):
                        for item in b:
                            if item["url"] not in seen:
                                seen.add(item["url"])
                                web_results.append(item)
        except Exception as exc:
            web_error = str(exc)

    return {
        "query": q,
        "curated": curated_matches,
        "web_results": web_results[:14],
        "web_error": web_error,
    }
