"""SQLite-backed audit log and per-guest service bookmark store."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .config import DATA_DIR


class AuditLogger:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or (DATA_DIR / "audit.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    action TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    target TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    upid TEXT,
                    detail TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_events(timestamp DESC)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS guest_bookmarks (
                    vmid INTEGER PRIMARY KEY,
                    url TEXT NOT NULL,
                    label TEXT NOT NULL DEFAULT '',
                    updated_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS alert_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    enabled INTEGER NOT NULL DEFAULT 0,
                    provider TEXT NOT NULL DEFAULT 'discord',
                    webhook_url TEXT NOT NULL DEFAULT '',
                    min_health_score INTEGER NOT NULL DEFAULT 75,
                    max_storage_pct INTEGER NOT NULL DEFAULT 85,
                    max_cpu_pct INTEGER NOT NULL DEFAULT 90,
                    max_ram_pct INTEGER NOT NULL DEFAULT 90,
                    notify_failed_tasks INTEGER NOT NULL DEFAULT 1,
                    last_sent_at INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS uptime_checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vmid INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    up INTEGER NOT NULL,
                    status_code INTEGER,
                    latency_ms REAL NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    checked_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_uptime_vmid_ts ON uptime_checks(vmid, checked_at DESC)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS wol_devices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    mac TEXT NOT NULL UNIQUE,
                    ip TEXT NOT NULL DEFAULT '',
                    broadcast TEXT NOT NULL DEFAULT '255.255.255.255',
                    notes TEXT NOT NULL DEFAULT '',
                    last_woken_at INTEGER NOT NULL DEFAULT 0
                )
                """
            )

    def record(
        self,
        *,
        source: str,
        action: str,
        risk: str,
        target: str = "",
        params: dict[str, Any] | None = None,
        status: str = "ok",
        upid: str | None = None,
        detail: str = "",
    ) -> int:
        sanitized = {}
        for k, v in (params or {}).items():
            if any(secret_key in k.lower() for secret_key in ("password", "secret", "token", "key")):
                sanitized[k] = "***REDACTED***"
            else:
                sanitized[k] = v
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO audit_events
                (timestamp, source, action, risk, target, params_json, status, upid, detail)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(time.time()),
                    source,
                    action,
                    risk,
                    target,
                    json.dumps(sanitized, default=str),
                    status,
                    upid,
                    detail[:800],
                ),
            )
            return int(cur.lastrowid or 0)

    def list_recent(
        self, limit: int = 50, source: str | None = None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM audit_events"
        args: list[Any] = []
        if source:
            query += " WHERE source = ?"
            args.append(source)
        query += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._lock, self._connect() as conn:
            rows = conn.execute(query, args).fetchall()
        results: list[dict[str, Any]] = []
        for r in rows:
            item = dict(r)
            try:
                item["params"] = json.loads(item.pop("params_json"))
            except Exception:
                item["params"] = {}
            results.append(item)
        return results

    # ── Guest Service Bookmarks ──────────────────────────────────────────
    def get_bookmarks(self) -> dict[int, dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT vmid, url, label, updated_at FROM guest_bookmarks").fetchall()
        return {int(r["vmid"]): dict(r) for r in rows}

    def set_bookmark(self, vmid: int, url: str, label: str = "") -> dict[str, Any] | None:
        clean_url = url.strip()
        with self._lock, self._connect() as conn:
            if not clean_url:
                conn.execute("DELETE FROM guest_bookmarks WHERE vmid = ?", (int(vmid),))
                return None
            now = int(time.time())
            conn.execute(
                """
                INSERT INTO guest_bookmarks (vmid, url, label, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(vmid) DO UPDATE SET
                    url = excluded.url,
                    label = excluded.label,
                    updated_at = excluded.updated_at
                """,
                (int(vmid), clean_url, label.strip(), now),
            )
            return {"vmid": int(vmid), "url": clean_url, "label": label.strip(), "updated_at": now}

    # ── Alert Webhook Config ─────────────────────────────────────────────
    def get_alert_config(self) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM alert_config WHERE id = 1").fetchone()
        if not row:
            return {
                "enabled": False,
                "provider": "discord",
                "webhook_url": "",
                "min_health_score": 75,
                "max_storage_pct": 85,
                "max_cpu_pct": 90,
                "max_ram_pct": 90,
                "notify_failed_tasks": True,
                "last_sent_at": 0,
            }
        d = dict(row)
        d.pop("id", None)
        d["enabled"] = bool(d["enabled"])
        d["notify_failed_tasks"] = bool(d["notify_failed_tasks"])
        return d

    def save_alert_config(self, cfg: dict[str, Any]) -> dict[str, Any]:
        current = self.get_alert_config()
        merged = {**current, **cfg}
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO alert_config
                (id, enabled, provider, webhook_url, min_health_score, max_storage_pct, max_cpu_pct, max_ram_pct, notify_failed_tasks, last_sent_at)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    enabled = excluded.enabled,
                    provider = excluded.provider,
                    webhook_url = excluded.webhook_url,
                    min_health_score = excluded.min_health_score,
                    max_storage_pct = excluded.max_storage_pct,
                    max_cpu_pct = excluded.max_cpu_pct,
                    max_ram_pct = excluded.max_ram_pct,
                    notify_failed_tasks = excluded.notify_failed_tasks,
                    last_sent_at = excluded.last_sent_at
                """,
                (
                    1 if merged["enabled"] else 0,
                    str(merged["provider"]),
                    str(merged["webhook_url"]).strip(),
                    int(merged["min_health_score"]),
                    int(merged["max_storage_pct"]),
                    int(merged["max_cpu_pct"]),
                    int(merged["max_ram_pct"]),
                    1 if merged["notify_failed_tasks"] else 0,
                    int(merged.get("last_sent_at") or 0),
                ),
            )
        return self.get_alert_config()

    # ── Uptime History Store ─────────────────────────────────────────────
    def record_uptime_batch(self, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        now = int(time.time())
        cutoff = now - 86400  # Keep rolling 24h
        with self._lock, self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO uptime_checks (vmid, name, url, up, status_code, latency_ms, error, checked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        int(r["vmid"]),
                        str(r["name"]),
                        str(r["url"]),
                        1 if r["up"] else 0,
                        r.get("status_code"),
                        float(r.get("latency_ms") or 0.0),
                        str(r.get("error") or "")[:240],
                        int(r.get("checked_at") or now),
                    )
                    for r in records
                ],
            )
            conn.execute("DELETE FROM uptime_checks WHERE checked_at < ?", (cutoff,))

    def get_uptime_summary(self) -> dict[int, dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT vmid, name, url, up, status_code, latency_ms, error, checked_at "
                "FROM uptime_checks ORDER BY checked_at DESC LIMIT 1200"
            ).fetchall()
        grouped: dict[int, list[dict[str, Any]]] = {}
        for r in rows:
            grouped.setdefault(int(r["vmid"]), []).append(dict(r))

        out: dict[int, dict[str, Any]] = {}
        for vmid, checks in grouped.items():
            latest = checks[0]
            total = len(checks)
            up_cnt = sum(1 for c in checks if c["up"])
            avg_lat = round(sum(float(c["latency_ms"] or 0) for c in checks) / total, 1) if total else 0.0
            # Chronological oldest -> newest last 20 ticks for spark bar
            ticks = [
                {"up": bool(c["up"]), "ms": round(float(c["latency_ms"] or 0), 1), "ts": c["checked_at"]}
                for c in reversed(checks[:20])
            ]
            out[vmid] = {
                "vmid": vmid,
                "name": latest["name"],
                "url": latest["url"],
                "up": bool(latest["up"]),
                "status_code": latest["status_code"],
                "latency_ms": round(float(latest["latency_ms"] or 0), 1),
                "avg_latency_ms": avg_lat,
                "uptime_pct": round((up_cnt / total) * 100, 1) if total else 0.0,
                "checks_count": total,
                "error": latest["error"],
                "checked_at": latest["checked_at"],
                "history": ticks,
            }
        return out

    # ── Wake-on-LAN Device Store ─────────────────────────────────────────
    def list_wol_devices(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM wol_devices ORDER BY name ASC").fetchall()
        return [dict(r) for r in rows]

    def upsert_wol_device(
        self, name: str, mac: str, ip: str = "", broadcast: str = "255.255.255.255", notes: str = ""
    ) -> dict[str, Any]:
        norm_mac = mac.strip().upper().replace("-", ":")
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO wol_devices (name, mac, ip, broadcast, notes)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(mac) DO UPDATE SET
                    name = excluded.name,
                    ip = excluded.ip,
                    broadcast = excluded.broadcast,
                    notes = excluded.notes
                """,
                (name.strip(), norm_mac, ip.strip(), broadcast.strip() or "255.255.255.255", notes.strip()),
            )
            row = conn.execute("SELECT * FROM wol_devices WHERE mac = ?", (norm_mac,)).fetchone()
        return dict(row)

    def mark_wol_woken(self, device_id: int) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE wol_devices SET last_woken_at = ? WHERE id = ?", (int(time.time()), int(device_id)))

    def delete_wol_device(self, device_id: int) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM wol_devices WHERE id = ?", (int(device_id),))
