"""Configuration management with .env loading and runtime updates."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("PROXMOX_DATA_DIR") or (BASE_DIR / "data"))
ENV_PATH = Path(os.environ.get("PROXMOX_ENV_FILE") or (BASE_DIR / ".env"))


class AppSettings(BaseModel):
    proxmox_host: str = Field(default="https://192.168.1.2:8006")
    proxmox_token_id: str = Field(default="")
    proxmox_token_secret: str = Field(default="")
    proxmox_verify_ssl: bool = Field(default=False)
    proxmox_timeout: float = Field(default=30.0)

    safety_mode: Literal["read_only", "safe", "full"] = Field(default="safe")
    allow_destructive: bool = Field(default=False)
    proxmox_demo_mode: bool = Field(default=False)

    webui_host: str = Field(default="127.0.0.1")
    webui_port: int = Field(default=8806)

    # Optional SSH for direct one-click Community Scripts deployment on the Proxmox host
    proxmox_ssh_user: str = Field(default="root")
    proxmox_ssh_port: int = Field(default=22)
    proxmox_ssh_key_path: str = Field(default="")

    # Optional Dell iDRAC Redfish API (PowerEdge R640 wattage, temps, fans, PSUs)
    idrac_host: str = Field(default="")
    idrac_user: str = Field(default="root")
    idrac_password: str = Field(default="")
    electricity_rate_kwh: float = Field(default=0.26)
    currency_symbol: str = Field(default="£")

    # Optional TrueNAS Host (read-only monitoring via JSON-RPC 2.0 WebSocket / REST v2 fallback)
    truenas_host: str = Field(default="")
    truenas_api_key: str = Field(default="")
    truenas_verify_ssl: bool = Field(default=False)

    @property
    def truenas_configured(self) -> bool:
        return bool(self.truenas_host.strip() and self.truenas_api_key.strip())

    @property
    def is_configured(self) -> bool:
        return bool(
            self.proxmox_host.strip()
            and self.proxmox_token_id.strip()
            and self.proxmox_token_secret.strip()
        )

    @property
    def normalized_host(self) -> str:
        host = self.proxmox_host.strip().rstrip("/")
        if host and not host.startswith(("http://", "https://")):
            host = f"https://{host}"
        return host

    def public_dict(self) -> dict:
        secret = self.proxmox_token_secret
        masked = ""
        if secret:
            if len(secret) <= 8:
                masked = "•" * len(secret)
            else:
                masked = f"{secret[:4]}{'•' * (len(secret) - 8)}{secret[-4:]}"
        tn_key = self.truenas_api_key
        tn_masked = ""
        if tn_key:
            if len(tn_key) <= 8:
                tn_masked = "•" * len(tn_key)
            else:
                tn_masked = f"{tn_key[:4]}{'•' * min(12, len(tn_key) - 8)}{tn_key[-4:]}"
        return {
            "proxmox_host": self.normalized_host,
            "proxmox_token_id": self.proxmox_token_id,
            "proxmox_token_secret_masked": masked,
            "has_token_secret": bool(secret),
            "proxmox_verify_ssl": self.proxmox_verify_ssl,
            "proxmox_timeout": self.proxmox_timeout,
            "safety_mode": self.safety_mode,
            "allow_destructive": self.allow_destructive,
            "proxmox_demo_mode": self.proxmox_demo_mode,
            "is_configured": self.is_configured,
            "webui_host": self.webui_host,
            "webui_port": self.webui_port,
            "proxmox_ssh_user": self.proxmox_ssh_user,
            "proxmox_ssh_port": self.proxmox_ssh_port,
            "proxmox_ssh_key_path": self.proxmox_ssh_key_path,
            "idrac_host": self.idrac_host,
            "idrac_user": self.idrac_user,
            "has_idrac_password": bool(self.idrac_password),
            "electricity_rate_kwh": self.electricity_rate_kwh,
            "currency_symbol": self.currency_symbol,
            "truenas_host": self.truenas_host,
            "truenas_api_key_masked": tn_masked,
            "has_truenas_api_key": bool(tn_key),
            "truenas_verify_ssl": self.truenas_verify_ssl,
            "truenas_configured": self.truenas_configured,
        }


def _parse_bool(val: str | bool | None, default: bool = False) -> bool:
    if isinstance(val, bool):
        return val
    if val is None:
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def load_settings(env_path: Path | None = None) -> AppSettings:
    resolved_env = env_path or Path(os.environ.get("PROXMOX_ENV_FILE") or ENV_PATH)
    file_vals: dict[str, str | None] = {}
    if resolved_env.exists():
        file_vals = dotenv_values(resolved_env)

    # Values persisted via WebUI into PROXMOX_ENV_FILE (e.g. /app/data/.env in Docker)
    # override initial container env vars when non-empty; otherwise fall back to os.environ.
    prefer_file = bool(os.environ.get("PROXMOX_ENV_FILE")) and resolved_env.exists()

    def get(key: str, default: str = "") -> str:
        fval = file_vals.get(key)
        eval_ = os.environ.get(key)
        if prefer_file:
            if fval is not None and fval != "":
                return fval
            if eval_ is not None and eval_ != "":
                return eval_
            return default
        if eval_ is not None and eval_ != "":
            return eval_
        return fval if fval is not None else default

    safety = get("SAFETY_MODE", "safe").strip().lower()
    if safety not in ("read_only", "safe", "full"):
        safety = "safe"

    try:
        timeout = float(get("PROXMOX_TIMEOUT", "30"))
    except ValueError:
        timeout = 30.0

    try:
        port = int(get("WEBUI_PORT", "8806"))
    except ValueError:
        port = 8806

    try:
        ssh_port = int(get("PROXMOX_SSH_PORT", "22"))
    except ValueError:
        ssh_port = 22

    try:
        elec_rate = float(get("ELECTRICITY_RATE_KWH", "0.26"))
    except ValueError:
        elec_rate = 0.26

    return AppSettings(
        proxmox_host=get("PROXMOX_HOST", "https://192.168.1.2:8006"),
        proxmox_token_id=get("PROXMOX_TOKEN_ID", ""),
        proxmox_token_secret=get("PROXMOX_TOKEN_SECRET", ""),
        proxmox_verify_ssl=_parse_bool(get("PROXMOX_VERIFY_SSL", "false"), False),
        proxmox_timeout=timeout,
        safety_mode=safety,  # type: ignore[arg-type]
        allow_destructive=_parse_bool(get("ALLOW_DESTRUCTIVE", "false"), False),
        proxmox_demo_mode=_parse_bool(get("PROXMOX_DEMO_MODE", "false"), False),
        webui_host=get("WEBUI_HOST", "127.0.0.1"),
        webui_port=port,
        proxmox_ssh_user=get("PROXMOX_SSH_USER", "root"),
        proxmox_ssh_port=ssh_port,
        proxmox_ssh_key_path=get("PROXMOX_SSH_KEY_PATH", ""),
        idrac_host=get("IDRAC_HOST", ""),
        idrac_user=get("IDRAC_USER", "root"),
        idrac_password=get("IDRAC_PASSWORD", ""),
        electricity_rate_kwh=elec_rate,
        currency_symbol=get("CURRENCY_SYMBOL", "£"),
        truenas_host=get("TRUENAS_HOST", ""),
        truenas_api_key=get("TRUENAS_API_KEY", ""),
        truenas_verify_ssl=_parse_bool(get("TRUENAS_VERIFY_SSL", "false"), False),
    )


def save_settings(settings: AppSettings, env_path: Path | None = None) -> None:
    """Persist settings to .env (or PROXMOX_ENV_FILE inside Docker volume) and mirror into os.environ."""
    resolved_env = env_path or Path(os.environ.get("PROXMOX_ENV_FILE") or ENV_PATH)
    resolved_env.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Proxmox VE Connection",
        f"PROXMOX_HOST={settings.normalized_host}",
        f"PROXMOX_TOKEN_ID={settings.proxmox_token_id.strip()}",
        f"PROXMOX_TOKEN_SECRET={settings.proxmox_token_secret.strip()}",
        f"PROXMOX_VERIFY_SSL={'true' if settings.proxmox_verify_ssl else 'false'}",
        f"PROXMOX_TIMEOUT={settings.proxmox_timeout}",
        "",
        "# Safety & Guardrails (read_only | safe | full)",
        f"SAFETY_MODE={settings.safety_mode}",
        f"ALLOW_DESTRUCTIVE={'true' if settings.allow_destructive else 'false'}",
        "",
        "# Demo / Preview Mode",
        f"PROXMOX_DEMO_MODE={'true' if settings.proxmox_demo_mode else 'false'}",
        "",
        "# WebUI & Streamable HTTP MCP Server",
        f"WEBUI_HOST={settings.webui_host}",
        f"WEBUI_PORT={settings.webui_port}",
        "",
        "# Optional SSH for Direct Community-Scripts Deployment",
        f"PROXMOX_SSH_USER={settings.proxmox_ssh_user}",
        f"PROXMOX_SSH_PORT={settings.proxmox_ssh_port}",
        f"PROXMOX_SSH_KEY_PATH={settings.proxmox_ssh_key_path}",
        "",
        "# Optional Dell iDRAC Redfish API (PowerEdge Hardware/Power Telemetry)",
        f"IDRAC_HOST={settings.idrac_host}",
        f"IDRAC_USER={settings.idrac_user}",
        f"IDRAC_PASSWORD={settings.idrac_password}",
        f"ELECTRICITY_RATE_KWH={settings.electricity_rate_kwh}",
        f"CURRENCY_SYMBOL={settings.currency_symbol}",
        "",
        "# Optional Bare-Metal TrueNAS Host (Read-Only Monitoring)",
        f"TRUENAS_HOST={settings.truenas_host.strip()}",
        f"TRUENAS_API_KEY={settings.truenas_api_key.strip()}",
        f"TRUENAS_VERIFY_SSL={'true' if settings.truenas_verify_ssl else 'false'}",
        "",
    ]
    resolved_env.write_text("\n".join(lines), encoding="utf-8")

    # Sync process environment so reloads pick up the latest values
    os.environ["PROXMOX_HOST"] = settings.normalized_host
    os.environ["PROXMOX_TOKEN_ID"] = settings.proxmox_token_id.strip()
    os.environ["PROXMOX_TOKEN_SECRET"] = settings.proxmox_token_secret.strip()
    os.environ["PROXMOX_VERIFY_SSL"] = "true" if settings.proxmox_verify_ssl else "false"
    os.environ["PROXMOX_TIMEOUT"] = str(settings.proxmox_timeout)
    os.environ["SAFETY_MODE"] = settings.safety_mode
    os.environ["ALLOW_DESTRUCTIVE"] = "true" if settings.allow_destructive else "false"
    os.environ["PROXMOX_DEMO_MODE"] = "true" if settings.proxmox_demo_mode else "false"
    os.environ["PROXMOX_SSH_USER"] = settings.proxmox_ssh_user.strip()
    os.environ["PROXMOX_SSH_PORT"] = str(settings.proxmox_ssh_port)
    os.environ["PROXMOX_SSH_KEY_PATH"] = settings.proxmox_ssh_key_path.strip()
    os.environ["IDRAC_HOST"] = settings.idrac_host.strip()
    os.environ["IDRAC_USER"] = settings.idrac_user.strip()
    os.environ["IDRAC_PASSWORD"] = settings.idrac_password.strip()
    os.environ["ELECTRICITY_RATE_KWH"] = str(settings.electricity_rate_kwh)
    os.environ["CURRENCY_SYMBOL"] = settings.currency_symbol.strip()
    os.environ["TRUENAS_HOST"] = settings.truenas_host.strip()
    os.environ["TRUENAS_API_KEY"] = settings.truenas_api_key.strip()
    os.environ["TRUENAS_VERIFY_SSL"] = "true" if settings.truenas_verify_ssl else "false"
