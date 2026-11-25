"""Wake-on-LAN (WoL) UDP Magic Packet sender and quick LAN reachability check."""

from __future__ import annotations

import asyncio
import re
import socket


def normalize_mac(mac: str) -> bytes:
    cleaned = re.sub(r"[^0-9A-Fa-f]", "", mac or "")
    if len(cleaned) != 12:
        raise ValueError(f"Invalid MAC address '{mac}'. Expected 12 hex digits (e.g. AA:BB:CC:DD:EE:FF).")
    return bytes.fromhex(cleaned)


def send_magic_packet(mac: str, broadcast: str = "255.255.255.255", port: int = 9) -> dict:
    mac_bytes = normalize_mac(mac)
    packet = b"\xff" * 6 + mac_bytes * 16
    target = (broadcast or "255.255.255.255").strip()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, (target, port))
    pretty_mac = ":".join(f"{b:02X}" for b in mac_bytes)
    return {"sent": True, "mac": pretty_mac, "broadcast": target, "port": port}


async def check_tcp_reachable(ip: str, ports: tuple[int, ...] = (22, 80, 443, 445, 3389, 8006)) -> bool:
    host = (ip or "").strip()
    if not host:
        return False

    async def _try(port: int) -> bool:
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=0.6)
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            return False

    results = await asyncio.gather(*(_try(p) for p in ports))
    return any(results)
