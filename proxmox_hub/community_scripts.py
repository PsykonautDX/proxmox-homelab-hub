"""Live Community-Scripts.org catalog, upstream defaults parser, Advanced LXC command builder, and SSH deployer."""

from __future__ import annotations

import asyncio
import json
import re
import shlex
import time
import uuid
from typing import Any
from urllib.parse import urlparse

import httpx

CATALOG_URL = "https://community-scripts.org/scripts"
RAW_REPO_BASE = "https://raw.githubusercontent.com/community-scripts/ProxmoxVE/main"

TYPE_TO_DIR: dict[str, str] = {
    "lxc": "ct",
    "ct": "ct",
    "vm": "vm",
    "pve": "tools/pve",
    "addon": "tools/addon",
    "turnkey": "turnkey",
}

ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def script_raw_url(slug: str, script_type: str = "lxc") -> str:
    folder = TYPE_TO_DIR.get(script_type.lower(), "ct")
    return f"{RAW_REPO_BASE}/{folder}/{slug}.sh"


def _parse_rsc_catalog(page_html: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract the embedded scripts and script_categories arrays from community-scripts.org/scripts."""
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', page_html, re.S)
    decoded_parts: list[str] = []
    for ch in chunks:
        try:
            decoded_parts.append(json.loads(f'"{ch}"'))
        except Exception:
            decoded_parts.append(ch)
    decoded = "".join(decoded_parts)
    decoder = json.JSONDecoder()

    scripts: list[dict[str, Any]] = []
    categories: list[dict[str, Any]] = []

    for m in re.finditer(r'"scripts":\[', decoded):
        try:
            cand, _ = decoder.raw_decode(decoded, m.end() - 1)
            if isinstance(cand, list) and cand and isinstance(cand[0], dict) and "slug" in cand[0]:
                scripts = cand
                break
        except Exception:
            continue

    for m in re.finditer(r'"categories":\[', decoded):
        try:
            cand, _ = decoder.raw_decode(decoded, m.end() - 1)
            if isinstance(cand, list) and cand and isinstance(cand[0], dict) and "id" in cand[0] and "name" in cand[0]:
                categories = cand
                break
        except Exception:
            continue

    return scripts, categories


class CommunityScriptsHub:
    """Fetches & caches community-scripts.org metadata, inspects script defaults, and manages SSH deployments."""

    def __init__(self) -> None:
        self._cache: tuple[float, dict[str, Any]] | None = None
        self._defaults_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._jobs: dict[str, dict[str, Any]] = {}

    async def get_catalog(self, force: bool = False) -> dict[str, Any]:
        if not force and self._cache and (time.monotonic() - self._cache[0]) < 1800:
            return self._cache[1]

        scripts_out: list[dict[str, Any]] = []
        cats_out: list[dict[str, Any]] = []
        source_label = "community-scripts.org"

        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(15.0),
                headers={"User-Agent": "ProxmoxHomelabHub/1.0"},
            ) as client:
                resp = await client.get(CATALOG_URL)
                resp.raise_for_status()
                raw_scripts, raw_cats = _parse_rsc_catalog(resp.text)

            cat_by_id = {
                str(c["id"]): {
                    "id": str(c["id"]),
                    "name": str(c.get("name") or c["id"]),
                    "description": str(c.get("description") or ""),
                    "sort_order": int(c.get("sort_order") or 999),
                }
                for c in raw_cats
            }
            cats_out = sorted(cat_by_id.values(), key=lambda x: (x["sort_order"], x["name"]))

            for s in raw_scripts:
                slug = str(s.get("slug") or "").strip()
                if not slug:
                    continue
                t_raw = s.get("type")
                stype = str(t_raw.get("type") if isinstance(t_raw, dict) else (t_raw or "lxc")).lower()
                cat_ids = [str(cid) for cid in (s.get("categories") or [])]
                cat_names = [cat_by_id[cid]["name"] for cid in cat_ids if cid in cat_by_id]
                created = str(s.get("script_created") or "")[:10]
                updated = str(s.get("script_updated") or "")[:10]
                scripts_out.append(
                    {
                        "slug": slug,
                        "name": str(s.get("name") or slug),
                        "description": str(s.get("description") or ""),
                        "type": stype,
                        "categories": cat_names,
                        "category_ids": cat_ids,
                        "script_created": created,
                        "script_updated": updated,
                        "logo": str(s.get("logo") or ""),
                        "updateable": bool(s.get("updateable", False)),
                        "is_dev": bool(s.get("is_dev", False)),
                        "script_url": script_raw_url(slug, stype),
                        "docs_url": f"https://community-scripts.org/scripts/{slug}",
                    }
                )
        except Exception:
            # Graceful fallback to GitHub ct/ listing if community-scripts.org is unreachable
            source_label = "github-fallback"
            try:
                async with httpx.AsyncClient(
                    follow_redirects=True,
                    timeout=httpx.Timeout(12.0),
                    headers={"User-Agent": "ProxmoxHomelabHub/1.0"},
                ) as client:
                    r = await client.get(
                        "https://api.github.com/repos/community-scripts/ProxmoxVE/contents/ct"
                    )
                    if r.status_code == 200:
                        for item in r.json():
                            fn = str(item.get("name", ""))
                            if fn.endswith(".sh"):
                                slug = fn[:-3]
                                scripts_out.append(
                                    {
                                        "slug": slug,
                                        "name": slug.replace("-", " ").title(),
                                        "description": "Community LXC helper script",
                                        "type": "lxc",
                                        "categories": [],
                                        "category_ids": [],
                                        "script_created": "",
                                        "script_updated": "",
                                        "logo": "",
                                        "updateable": True,
                                        "is_dev": False,
                                        "script_url": script_raw_url(slug, "lxc"),
                                        "docs_url": f"https://community-scripts.org/scripts/{slug}",
                                    }
                                )
            except Exception:
                pass

        payload = {
            "source": source_label,
            "fetched_at": int(time.time()),
            "total": len(scripts_out),
            "categories": cats_out,
            "scripts": scripts_out,
        }
        if scripts_out:
            self._cache = (time.monotonic(), payload)
        return payload

    async def inspect_script_defaults(
        self, slug: str, script_type: str = "lxc"
    ) -> dict[str, Any]:
        """Fetch the upstream .sh script from GitHub and extract its default var_* parameters."""
        cache_key = f"{script_type}:{slug}"
        cached = self._defaults_cache.get(cache_key)
        if cached and (time.monotonic() - cached[0]) < 1800:
            return cached[1]

        candidates = [script_raw_url(slug, script_type)]
        for folder in ("ct", "vm", "tools/pve", "tools/addon", "turnkey"):
            u = f"{RAW_REPO_BASE}/{folder}/{slug}.sh"
            if u not in candidates:
                candidates.append(u)

        text = ""
        resolved_url = candidates[0]
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(10.0),
            headers={"User-Agent": "ProxmoxHomelabHub/1.0"},
        ) as client:
            for url in candidates:
                try:
                    r = await client.get(url)
                    if r.status_code == 200 and "#!/" in r.text[:80]:
                        text = r.text
                        resolved_url = url
                        break
                except Exception:
                    continue

        # Handle dual-OS scripts (e.g. `if [[ "$var_os" == "alpine" ]]; then ... else ... fi`)
        # Prefer the Debian/else branch for primary defaults so we never mix debian + alpine 3.24
        primary_text = text
        dual_os = False
        alpine_block = re.search(
            r'if\s+\[\[\s*"\$\{var_os:-\}"\s*==\s*"alpine"\s*\]\];\s*then(.*?)else(.*?)fi',
            text,
            re.S,
        )
        if alpine_block:
            dual_os = True
            primary_text = text[: alpine_block.start()] + "\n" + alpine_block.group(2) + "\n" + text[alpine_block.end() :]

        def grab_var(var_name: str, fallback: str = "") -> str:
            # Match var_cpu="${var_cpu:-2}" or var_cpu=${var_cpu:-2} or var_cpu=2
            m = re.search(
                rf'^\s*(?:export\s+)?{var_name}=["\']?\$\{{{var_name}:-([^}}"\']*)\}}["\']?',
                primary_text,
                re.M,
            )
            if m:
                return m.group(1).strip()
            m2 = re.search(rf'^\s*(?:export\s+)?{var_name}=["\']?([^"\'\n#]+)', primary_text, re.M)
            if m2:
                val = m2.group(1).strip()
                if not val.startswith("$"):
                    return val
            return fallback

        app_match = re.search(r'^APP="([^"]+)"', text, re.M)
        src_match = re.search(r"^#\s*Source:\s*(https?://\S+)", text, re.M)

        def to_int(val: str, default: int) -> int:
            try:
                return int(val)
            except Exception:
                return default

        defaults = {
            "slug": slug,
            "app": app_match.group(1) if app_match else slug,
            "script_type": script_type,
            "script_url": resolved_url,
            "source_url": src_match.group(1) if src_match else "",
            "docs_url": f"https://community-scripts.org/scripts/{slug}",
            "found_upstream": bool(text),
            "supports_alpine": dual_os,
            "defaults": {
                "var_os": grab_var("var_os", "debian"),
                "var_version": grab_var("var_version", "13"),
                "var_cpu": to_int(grab_var("var_cpu", "2"), 2),
                "var_ram": to_int(grab_var("var_ram", "2048"), 2048),
                "var_disk": to_int(grab_var("var_disk", "8"), 8),
                "var_unprivileged": to_int(grab_var("var_unprivileged", "1"), 1),
                "var_tags": grab_var("var_tags", slug),
                "var_gpu": grab_var("var_gpu", "no"),
                "var_fuse": grab_var("var_fuse", "no"),
                "var_tun": grab_var("var_tun", "no"),
                "var_nesting": to_int(grab_var("var_nesting", "1"), 1),
                "var_keyctl": to_int(grab_var("var_keyctl", "1"), 1),
                "var_ssh": grab_var("var_ssh", "no"),
            },
        }
        self._defaults_cache[cache_key] = (time.monotonic(), defaults)
        return defaults

    @staticmethod
    def build_advanced_command(
        script_url: str,
        opts: dict[str, Any],
        redact_secrets: bool = False,
    ) -> str:
        """Build an exact community-scripts/core Advanced LXC bash command."""
        env_pairs: list[tuple[str, str]] = []

        if opts.get("unattended", True):
            env_pairs.append(("MODE", "unattended"))
            env_pairs.append(("var_unattended", "yes"))

        mapping = [
            ("ctid", "var_ctid"),
            ("hostname", "var_hostname"),
            ("os", "var_os"),
            ("version", "var_version"),
            ("cpu", "var_cpu"),
            ("ram", "var_ram"),
            ("disk", "var_disk"),
            ("unprivileged", "var_unprivileged"),
            ("tags", "var_tags"),
            ("container_storage", "var_container_storage"),
            ("template_storage", "var_template_storage"),
            ("bridge", "var_brg"),
            ("net", "var_net"),
            ("gateway", "var_gateway"),
            ("vlan", "var_vlan"),
            ("mtu", "var_mtu"),
            ("dns", "var_ns"),
            ("searchdomain", "var_searchdomain"),
            ("ipv6_method", "var_ipv6_method"),
            ("timezone", "var_timezone"),
            ("password", "var_pw"),
            ("ssh", "var_ssh"),
            ("ssh_authorized_key", "var_ssh_authorized_key"),
            ("protection", "var_protection"),
            ("nesting", "var_nesting"),
            ("keyctl", "var_keyctl"),
            ("fuse", "var_fuse"),
            ("tun", "var_tun"),
            ("gpu", "var_gpu"),
            ("mknod", "var_mknod"),
            ("apt_cacher", "var_apt_cacher"),
            ("apt_cacher_ip", "var_apt_cacher_ip"),
            ("verbose", "var_verbose"),
        ]

        for field_key, var_name in mapping:
            val = opts.get(field_key)
            if val is None or val == "":
                continue
            if isinstance(val, bool):
                if var_name in ("var_unprivileged", "var_nesting", "var_keyctl"):
                    sval = "1" if val else "0"
                else:
                    sval = "yes" if val else "no"
            else:
                sval = str(val).strip()
            if not sval:
                continue
            if redact_secrets and field_key == "password":
                sval = "***REDACTED***"
            env_pairs.append((var_name, sval))

        # Avoid setting gateway when using dhcp
        if str(opts.get("net", "")).strip().lower() == "dhcp":
            env_pairs = [(k, v) for k, v in env_pairs if k != "var_gateway"]

        # Avoid setting apt_cacher_ip if apt_cacher is not enabled
        if opts.get("apt_cacher") in (False, "no", "", None):
            env_pairs = [(k, v) for k, v in env_pairs if k not in ("var_apt_cacher", "var_apt_cacher_ip")]

        prefix = " \\\n  ".join(f"{k}={shlex.quote(v)}" for k, v in env_pairs)
        cmd = f'bash -c "$(curl -fsSL {shlex.quote(script_url)})"'
        if prefix:
            return f"{prefix} \\\n  {cmd}"
        return cmd

    # ── SSH Host Execution & Live Streaming Jobs ─────────────────────────
    @staticmethod
    def _ssh_base_args(host_url: str, user: str, port: int, key_path: str) -> list[str]:
        parsed = urlparse(host_url if "://" in host_url else f"https://{host_url}")
        ssh_host = parsed.hostname or host_url
        args = [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=8",
            "-p", str(port),
        ]
        if key_path.strip():
            args.extend(["-i", key_path.strip()])
        args.append(f"{user.strip() or 'root'}@{ssh_host}")
        return args

    async def test_ssh(
        self, host_url: str, user: str, port: int, key_path: str, demo_mode: bool = False
    ) -> dict[str, Any]:
        if demo_mode:
            return {"ok": True, "demo_mode": True, "output": "pve-manager/8.3.2 (Demo SSH Ready)"}
        args = self._ssh_base_args(host_url, user, port, key_path) + ["pveversion"]
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=12.0)
            out_s = stdout.decode("utf-8", errors="replace").strip()
            err_s = stderr.decode("utf-8", errors="replace").strip()
            return {
                "ok": proc.returncode == 0,
                "returncode": proc.returncode,
                "output": out_s or err_s,
                "hint": "" if proc.returncode == 0 else (
                    "Configure passwordless SSH key auth to Proxmox (`ssh-copy-id root@192.168.1.2`) "
                    "or use Copy Command + Open Proxmox Host Shell."
                ),
            }
        except Exception as exc:
            return {"ok": False, "output": str(exc)}

    def start_ssh_deploy(
        self,
        *,
        slug: str,
        script_url: str,
        command: str,
        display_command: str,
        host_url: str,
        user: str,
        port: int,
        key_path: str,
        demo_mode: bool = False,
    ) -> dict[str, Any]:
        job_id = f"cs-{uuid.uuid4().hex[:8]}"
        job: dict[str, Any] = {
            "job_id": job_id,
            "slug": slug,
            "script_url": script_url,
            "command": display_command,
            "status": "running",
            "returncode": None,
            "started_at": int(time.time()),
            "finished_at": None,
            "lines": [f"$ {display_command}", ""],
        }
        self._jobs[job_id] = job
        asyncio.create_task(
            self._run_job(job, command, host_url, user, port, key_path, demo_mode)
        )
        return self.get_job(job_id)  # type: ignore[return-value]

    async def _run_job(
        self,
        job: dict[str, Any],
        command: str,
        host_url: str,
        user: str,
        port: int,
        key_path: str,
        demo_mode: bool,
    ) -> None:
        if demo_mode:
            for msg in [
                f"[DEMO] Connecting to {user}@192.168.1.2:{port}...",
                f"[DEMO] Fetching {job['script_url']}...",
                "[DEMO] Running unattended Advanced LXC creation...",
                "[DEMO] Pulling container template and provisioning rootfs...",
                "[DEMO] Starting container and installing application...",
                f"[DEMO] Successfully deployed '{job['slug']}'!",
            ]:
                job["lines"].append(msg)
                await asyncio.sleep(0.15)
            job["status"] = "succeeded"
            job["returncode"] = 0
            job["finished_at"] = int(time.time())
            return

        # Flatten line continuations for clean remote shell execution
        flat_cmd = command.replace("\\\n", " ")
        args = self._ssh_base_args(host_url, user, port, key_path) + [flat_cmd]
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            assert proc.stdout is not None
            while True:
                raw_line = await proc.stdout.readline()
                if not raw_line:
                    break
                clean = strip_ansi(raw_line.decode("utf-8", errors="replace")).rstrip("\r\n")
                job["lines"].append(clean)
                if len(job["lines"]) > 1500:
                    job["lines"] = job["lines"][-1200:]
            rc = await proc.wait()
            job["returncode"] = rc
            job["status"] = "succeeded" if rc == 0 else "failed"
        except Exception as exc:
            job["lines"].append(f"[ERROR] {exc}")
            job["returncode"] = -1
            job["status"] = "failed"
        finally:
            job["finished_at"] = int(time.time())

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        job = self._jobs.get(job_id)
        if not job:
            return None
        return {**job, "lines": list(job["lines"])}

    def list_jobs(self) -> list[dict[str, Any]]:
        items = sorted(self._jobs.values(), key=lambda x: x["started_at"], reverse=True)
        return [{**j, "line_count": len(j["lines"]), "lines": j["lines"][-20:]} for j in items[:20]]
