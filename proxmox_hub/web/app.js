/* Proxmox Homelab Hub — Frontend Controller (Zero-dependency ES Module) */

import { initExtras } from "/static/extras.js";
import { initMetalheart } from "/static/metalheart.js";

const SINGLE_NODE_INACTIVE_OK = new Set([
  "corosync",
  "pve-ha-crm",
  "pve-ha-lrm",
  "syslog",
  "systemd-timesyncd",
]);

const state = {
  view: "command",
  subtabs: {
    fleet: "guests",
    deploy: "community",
    storage: "pools",
    network: "ipam",
    system: "hardware",
  },
  guestLayout: "table",
  settings: null,
  mcpInfo: null,
  overview: null,
  insights: null,
  health: null,
  storage: null,
  tasks: [],
  audit: [],
  provOptions: null,
  rrdTimeframe: "hour",
  guestFilter: { search: "", type: "", status: "", tag: "", groupBy: "none" },
  guestSort: { key: "vmid", dir: "asc" },
  selectedVmids: new Set(),
  ipamFilter: { search: "", bridge: "" },
  backupFilter: "",
  auditSource: "",
  tasksErrorsOnly: false,
  drawer: { open: false, vmid: null, tab: "overview", detail: null },
};

const VM_PRESETS = [
  { label: "Ubuntu Server (2C / 4GB / 32GB)", mode: "vm", name: "ubuntu-srv", cores: 2, memory_mb: 4096, disk_size_gb: 32, ostype: "l26", bios: "seabios", tags: "linux;ubuntu" },
  { label: "Docker Host (4C / 8GB / 64GB)", mode: "vm", name: "docker-node", cores: 4, memory_mb: 8192, disk_size_gb: 64, ostype: "l26", bios: "ovmf", tags: "docker;homelab" },
  { label: "Windows 11 Lab (4C / 8GB / 80GB)", mode: "vm", name: "win11-lab", cores: 4, memory_mb: 8192, disk_size_gb: 80, ostype: "win11", bios: "ovmf", tags: "windows;lab" },
];

const LXC_PRESETS = [
  { label: "Debian Service (1C / 1GB / 8GB)", mode: "lxc", hostname: "debian-svc", cores: 1, memory_mb: 1024, disk_size_gb: 8, tags: "debian;service" },
  { label: "Alpine Micro (1C / 512MB / 4GB)", mode: "lxc", hostname: "alpine-micro", cores: 1, memory_mb: 512, disk_size_gb: 4, tags: "alpine;lightweight" },
  { label: "Docker / Media LXC (4C / 4GB / 32GB)", mode: "lxc", hostname: "app-lxc", cores: 4, memory_mb: 4096, disk_size_gb: 32, tags: "docker;lxc" },
];

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

function isReadOnly() {
  return state.settings?.safety_mode === "read_only";
}

function writeAttr() {
  return isReadOnly()
    ? `disabled title="Blocked in Read-Only Mode — switch to Safe Mode in MCP & Settings"`
    : "";
}

function esc(val) {
  return String(val ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function fmtBytes(bytes) {
  if (!bytes || bytes <= 0) return "0 B";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let idx = 0;
  let n = Number(bytes);
  while (n >= 1024 && idx < units.length - 1) {
    n /= 1024;
    idx++;
  }
  return `${n >= 100 ? n.toFixed(0) : n.toFixed(1)} ${units[idx]}`;
}

function fmtUptime(sec) {
  if (!sec || sec <= 0) return "—";
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function fmtTime(epoch) {
  if (!epoch) return "—";
  return new Date(epoch * 1000).toLocaleString([], {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function fmtAge(hours) {
  if (hours == null) return "Never";
  if (hours < 24) return `${Math.round(hours)}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

function barClass(pct) {
  if (pct >= 88) return "crit";
  if (pct >= 75) return "warn";
  return "";
}

function lifeBarClass(remainingPct) {
  if (remainingPct <= 15) return "crit";
  if (remainingPct <= 30) return "warn";
  return "ok";
}

function isIpTag(t) {
  return /^\d{1,3}(\.\d{1,3}){3}$/.test(t);
}

function vmidBlock(vmid) {
  const n = Number(vmid);
  if (n < 200) return "100–199 · Core / Desktop VMs";
  if (n < 300) return "200–299 · Docker Hosts";
  if (n < 500) return "400–499 · Media & *arr Stack";
  if (n < 1000) return "500–999 · Utilities & Monitoring";
  if (n < 7000) return "1000–6999 · Apps & Workstations";
  if (n < 8000) return "7000–7999 · Infra, AI & Nested Lab";
  return "8000+ · VPN, Media & Emulation";
}

function toast(msg, kind = "info") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok) {
    const err = new Error(data?.error || data?.detail || `Request failed (${res.status})`);
    err.status = res.status;
    err.code = data?.code;
    throw err;
  }
  return data;
}

/* ── SVG Visualizations ─────────────────────────────────────────────── */
function renderRingGauge(pct, color = "#22d3ee") {
  const clamped = Math.max(0, Math.min(100, Number(pct) || 0));
  const r = 22;
  const c = 2 * Math.PI * r;
  const offset = c - (clamped / 100) * c;
  return `
    <svg width="58" height="58" viewBox="0 0 58 58">
      <circle cx="29" cy="29" r="${r}" fill="none" stroke="#1e293b" stroke-width="5.5"></circle>
      <circle cx="29" cy="29" r="${r}" fill="none" stroke="${color}" stroke-width="5.5"
        stroke-linecap="round" stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${offset.toFixed(1)}"
        transform="rotate(-90 29 29)"></circle>
      <text x="29" y="33" text-anchor="middle" fill="#e6edf7" font-size="11.5" font-family="var(--mono)" font-weight="600">
        ${Math.round(clamped)}%
      </text>
    </svg>`;
}

function renderRrdChart(points, height = 195) {
  if (!points || points.length < 2) {
    return `<div class="muted small" style="padding:40px 0;text-align:center;">No RRD telemetry samples available yet.</div>`;
  }
  const w = 620;
  const padL = 36, padR = 12, padT = 14, padB = 26;
  const innerW = w - padL - padR;
  const innerH = height - padT - padB;

  const cpuVals = points.map((p) => Math.min(100, Math.max(0, (p.cpu || 0) * 100)));
  const memVals = points.map((p) => {
    const used = p.memused ?? p.mem ?? 0;
    const total = p.memtotal ?? p.maxmem ?? 1;
    return total ? Math.min(100, Math.max(0, (used / total) * 100)) : 0;
  });

  const toPoints = (arr) =>
    arr.map((v, i) => [
      padL + (i / (arr.length - 1)) * innerW,
      padT + innerH - (v / 100) * innerH,
    ]);

  const polyStr = (pts) => pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const areaStr = (pts) =>
    `${padL},${padT + innerH} ${polyStr(pts)} ${(padL + innerW).toFixed(1)},${padT + innerH}`;

  const cpuPts = toPoints(cpuVals);
  const memPts = toPoints(memVals);
  const lastCpu = cpuVals[cpuVals.length - 1].toFixed(1);
  const lastMem = memVals[memVals.length - 1].toFixed(1);
  const firstTs = fmtTime(points[0].time);
  const lastTs = fmtTime(points[points.length - 1].time);

  const gridLines = [0, 25, 50, 75, 100]
    .map((pct) => {
      const y = padT + innerH - (pct / 100) * innerH;
      return `
        <line x1="${padL}" y1="${y}" x2="${w - padR}" y2="${y}" stroke="#1e293b" stroke-dasharray="3 3" />
        <text x="${padL - 6}" y="${y + 4}" text-anchor="end" fill="#64748b" font-size="10" font-family="var(--mono)">${pct}%</text>`;
    })
    .join("");

  return `
    <svg viewBox="0 0 ${w} ${height}" style="width:100%;height:auto;display:block;">
      <defs>
        <linearGradient id="cpuGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#22d3ee" stop-opacity="0.32" />
          <stop offset="100%" stop-color="#22d3ee" stop-opacity="0.01" />
        </linearGradient>
        <linearGradient id="memGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#a78bfa" stop-opacity="0.24" />
          <stop offset="100%" stop-color="#a78bfa" stop-opacity="0.01" />
        </linearGradient>
      </defs>
      ${gridLines}
      <polygon points="${areaStr(memPts)}" fill="url(#memGrad)" />
      <polygon points="${areaStr(cpuPts)}" fill="url(#cpuGrad)" />
      <polyline points="${polyStr(memPts)}" fill="none" stroke="#a78bfa" stroke-width="2" />
      <polyline points="${polyStr(cpuPts)}" fill="none" stroke="#22d3ee" stroke-width="2.2" />
      <text x="${padL}" y="${height - 6}" fill="#64748b" font-size="10" font-family="var(--mono)">${esc(firstTs)}</text>
      <text x="${w - padR}" y="${height - 6}" text-anchor="end" fill="#64748b" font-size="10" font-family="var(--mono)">${esc(lastTs)}</text>
    </svg>
    <div class="chart-legend mono">
      <span><i class="legend-dot" style="background:#22d3ee"></i>CPU: ${lastCpu}%</span>
      <span><i class="legend-dot" style="background:#a78bfa"></i>Memory: ${lastMem}%</span>
    </div>`;
}

/* ── 6-Paradigm Navigation & Chrome ─────────────────────────────────── */
const VIEW_META = {
  command: { title: "Command Overview", kicker: "PARADIGM 01 // TELEMETRY & CONSTELLATION" },
  fleet: { title: "Compute & Service Fleet", kicker: "PARADIGM 02 // GUESTS · DOCKER · UPTIME · SIZING" },
  deploy: { title: "Deployment Forge", kicker: "PARADIGM 03 // COMMUNITY SCRIPTS · PROVISIONING · ISOS" },
  storage: { title: "Storage & File Matrix", kicker: "PARADIGM 04 // POOLS · ZFS · BACKUPS · EXPLORER" },
  network: { title: "Network & Access Grid", kicker: "PARADIGM 05 // IPAM · WAKE-ON-LAN · FIREWALL" },
  system: { title: "System Core & MCP", kicker: "PARADIGM 06 // IDRAC · UPDATES · AUDIT · SETTINGS" },
};

let extras = null;
let mh = null;

function navigate(view, subtab = null) {
  // Legacy aliases for backwards-compatible jump calls
  const aliasMap = {
    overview: ["command", null],
    guests: ["fleet", "guests"],
    capacity: ["fleet", "capacity"],
    provision: ["deploy", "provision"],
    isos: ["deploy", "isos"],
    files: ["storage", "files"],
    host: ["system", "maintenance"],
    tasks: ["system", "tasks"],
    settings: ["system", "settings"],
  };
  if (aliasMap[view]) {
    const [mappedView, mappedSub] = aliasMap[view];
    view = mappedView;
    if (!subtab && mappedSub) subtab = mappedSub;
  }

  state.view = view;
  if (subtab && state.subtabs[view] !== undefined) {
    state.subtabs[view] = subtab;
  }

  const meta = VIEW_META[view] || VIEW_META.command;
  $("#page-title").textContent = meta.title;
  if ($("#paradigm-kicker")) $("#paradigm-kicker").textContent = meta.kicker;

  $$("#main-nav .nav-item, #mobile-dock button").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === view)
  );
  $$(".view").forEach((s) => s.classList.toggle("active", s.id === `view-${view}`));

  // Activate active subtab inside this workspace
  const activeSub = state.subtabs[view];
  if (activeSub) {
    $$(`.subtab-bar[data-group="${view}"] .subtab`).forEach((b) =>
      b.classList.toggle("active", b.dataset.sub === activeSub)
    );
    $$(`#view-${view} .subpane`).forEach((p) =>
      p.classList.toggle("active", p.id === `sub-${view}-${activeSub}`)
    );
  }

  mh?.playChime("shift");
  loadActiveView(false);
}

function switchView(view, subtab = null) {
  navigate(view, subtab);
}

function updateTopChrome() {
  const s = state.settings;
  if (!s) return;
  $("#sidebar-host").textContent = s.proxmox_host.replace(/^https?:\/\//, "");
  const safetyMap = { read_only: "READ-ONLY", safe: "SAFE MODE", full: "FULL ACCESS" };
  $("#safety-pill").textContent = safetyMap[s.safety_mode] || s.safety_mode;
  $("#safety-pill").className = `status-pill ${s.safety_mode === "read_only" ? "warn" : "subtle"}`;

  // Disable write submit buttons cleanly in Read-Only mode
  $$("[data-write-btn]").forEach((b) => {
    b.disabled = isReadOnly();
    b.title = isReadOnly() ? "Disabled in Read-Only Mode — change Safety Mode in MCP & Settings" : "";
  });

  const dot = $("#sidebar-dot");
  const pill = $("#conn-pill");
  const banner = $("#mode-banner");

  if (s.proxmox_demo_mode) {
    dot.className = "dot demo";
    pill.className = "status-pill demo";
    pill.textContent = "DEMO MODE";
    banner.className = "banner warn";
    banner.innerHTML = `
      <div>
        <strong>Interactive Demo Mode is active</strong> — exploring a simulated Proxmox homelab.
        Switch off Demo Mode in Settings to connect live to <code>${esc(s.proxmox_host)}</code>.
      </div>
      <div class="banner-actions">
        <button class="btn btn-primary btn-sm" id="banner-goto-settings">Enter Live API Token</button>
        <button class="btn btn-ghost btn-sm" id="banner-disable-demo">Exit Demo</button>
      </div>`;
    $("#banner-goto-settings")?.addEventListener("click", () => switchView("settings"));
    $("#banner-disable-demo")?.addEventListener("click", () => toggleDemoMode(false));
  } else if (!s.is_configured) {
    dot.className = "dot offline";
    pill.className = "status-pill err";
    pill.textContent = "API KEY NEEDED";
    banner.className = "banner info";
    banner.innerHTML = `
      <div>
        <strong>Welcome! Proxmox API Token not configured yet</strong> (target: <code>${esc(s.proxmox_host)}</code>).
        Add your Proxmox API token to connect live, or launch Demo Mode to preview right now.
      </div>
      <div class="banner-actions">
        <button class="btn btn-primary btn-sm" id="banner-goto-settings">Configure API Token</button>
        <button class="btn btn-sm" id="banner-enable-demo">Try Demo Mode</button>
      </div>`;
    $("#banner-goto-settings")?.addEventListener("click", () => switchView("settings"));
    $("#banner-enable-demo")?.addEventListener("click", () => toggleDemoMode(true));
  } else {
    dot.className = "dot online";
    pill.className = "status-pill ok";
    pill.textContent = "LIVE CONNECTED";
    if (s.safety_mode === "read_only") {
      banner.className = "banner info";
      banner.innerHTML = `
        <div>
          <strong>Read-Only Guardrail Active</strong> — live monitoring is connected, and all power/create/snapshot/backup actions are locked.
        </div>
        <div class="banner-actions">
          <button class="btn btn-sm" id="banner-goto-settings">Switch Safety Mode</button>
        </div>`;
      $("#banner-goto-settings")?.addEventListener("click", () => switchView("settings"));
    } else {
      banner.className = "banner hidden";
      banner.innerHTML = "";
    }
  }
}

async function toggleDemoMode(enabled) {
  if (!state.settings) return;
  try {
    const res = await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({
        proxmox_host: state.settings.proxmox_host,
        proxmox_token_id: state.settings.proxmox_token_id,
        proxmox_verify_ssl: state.settings.proxmox_verify_ssl,
        proxmox_timeout: state.settings.proxmox_timeout,
        safety_mode: state.settings.safety_mode,
        allow_destructive: state.settings.allow_destructive,
        proxmox_demo_mode: enabled,
      }),
    });
    state.settings = res.settings;
    state.insights = null;
    updateTopChrome();
    populateSettingsForm();
    toast(enabled ? "Demo Mode enabled" : "Demo Mode disabled", "info");
    loadActiveView();
  } catch (err) {
    toast(err.message, "err");
  }
}

/* ── Loaders ────────────────────────────────────────────────────────── */
async function loadSettings() {
  const data = await api("/api/settings");
  state.settings = data.settings;
  state.mcpInfo = data.mcp;
  updateTopChrome();
  populateSettingsForm();
  renderMcpPanel();
}

async function ensureInsights(force = false) {
  if (!state.insights || force) {
    state.insights = await api("/api/fleet/insights");
    const nonTpl = (state.insights.guests || []).filter((g) => !g.template);
    $("#nav-guest-count").textContent = nonTpl.length;
  }
  return state.insights;
}

async function loadActiveView(forceInsights = true) {
  if (!state.settings) await loadSettings();
  if (!state.settings.is_configured && !state.settings.proxmox_demo_mode) {
    if (!(state.view === "system" && state.subtabs.system === "settings")) {
      renderUnconfiguredPlaceholder();
      return;
    }
    return;
  }

  try {
    if (state.view === "command") {
      await loadOverview();
    } else if (state.view === "fleet") {
      const sub = state.subtabs.fleet;
      if (sub === "guests") await loadGuests(forceInsights);
      else if (sub === "docker") await mh.loadDocker();
      else if (sub === "uptime") await mh.loadUptime(false);
      else if (sub === "capacity") await loadCapacity(forceInsights);
      else if (sub === "checkpoints") await mh.loadCheckpoints();
    } else if (state.view === "deploy") {
      const sub = state.subtabs.deploy;
      if (sub === "community") await extras.loadCommunityScripts(false);
      else if (sub === "provision") await loadProvisioning();
      else if (sub === "isos" || sub === "appliances") await extras.loadIsoHub();
    } else if (state.view === "storage") {
      const sub = state.subtabs.storage;
      if (sub === "pools" || sub === "backups") await loadStorage(forceInsights);
      else if (sub === "files") await extras.loadFileExplorer();
    } else if (state.view === "network") {
      const sub = state.subtabs.network;
      if (sub === "ipam") await loadNetworkIpam(forceInsights);
      else if (sub === "topology") {
        const [ins, ov] = await Promise.all([ensureInsights(forceInsights), state.overview ? Promise.resolve(state.overview) : api("/api/overview")]);
        mh.renderCrystariumMap(ins, ov);
      }
      else if (sub === "wol") await mh.loadWol();
      else if (sub === "firewall") await extras.loadHostMaintenance();
    } else if (state.view === "system") {
      const sub = state.subtabs.system;
      if (sub === "hardware") await mh.loadHardwareIdrac();
      else if (sub === "maintenance" || sub === "alerts") await extras.loadHostMaintenance();
      else if (sub === "tasks") await loadTasksAndAudit();
      else if (sub === "settings") await loadSettings();
    }
  } catch (err) {
    if (err.code === "NOT_CONFIGURED") {
      updateTopChrome();
      renderUnconfiguredPlaceholder();
    } else {
      toast(err.message, "err");
      $("#sidebar-dot").className = "dot offline";
      $("#conn-pill").className = "status-pill err";
      $("#conn-pill").textContent = "UNREACHABLE";
    }
  }
}

function renderUnconfiguredPlaceholder() {
  $("#overview-kpis").innerHTML = `
    <div class="card" style="grid-column:1/-1;text-align:center;padding:36px;">
      <h2>Ready to connect to ${esc(state.settings?.proxmox_host || "192.168.1.2:8006")}</h2>
      <p class="muted" style="max-width:540px;margin:8px auto 18px;">
        Paste your Proxmox API Token in <strong>MCP &amp; Settings</strong> to begin live monitoring &amp; VM management,
        or turn on <strong>Demo Mode</strong> to explore immediately.
      </p>
      <div style="display:flex;gap:10px;justify-content:center;">
        <button class="btn btn-primary" id="ph-settings">Configure API Token</button>
        <button class="btn" id="ph-demo">Launch Demo Mode</button>
      </div>
    </div>`;
  $("#ph-settings")?.addEventListener("click", () => switchView("settings"));
  $("#ph-demo")?.addEventListener("click", () => toggleDemoMode(true));
}

/* ── View 1: Overview ───────────────────────────────────────────────── */
async function loadOverview() {
  const [overview, health, tasksData, auditData] = await Promise.all([
    api("/api/overview"),
    api("/api/health-report"),
    api("/api/tasks?limit=8"),
    api("/api/audit?limit=8"),
  ]);
  state.overview = overview;
  state.health = health;

  const s = overview.summary;
  $("#nav-guest-count").textContent = s.vms_total + s.lxcs_total;

  $("#overview-kpis").innerHTML = `
    <div class="kpi-card">
      <div>
        <div class="kpi-label">Nodes Online</div>
        <div class="kpi-value">${s.nodes_online} / ${s.nodes_total}</div>
        <div class="kpi-sub">PVE ${esc(overview.version?.version || "—")} · ${s.cpu_cores_total} threads</div>
      </div>
      ${renderRingGauge(s.nodes_total ? (s.nodes_online / s.nodes_total) * 100 : 0, "#10b981")}
    </div>
    <div class="kpi-card">
      <div>
        <div class="kpi-label">Compute (CPU)</div>
        <div class="kpi-value">${s.cpu_percent}%</div>
        <div class="kpi-sub">${s.vms_running}/${s.vms_total} VMs · ${s.lxcs_running}/${s.lxcs_total} LXCs running</div>
      </div>
      ${renderRingGauge(s.cpu_percent, s.cpu_percent > 80 ? "#f59e0b" : "#22d3ee")}
    </div>
    <div class="kpi-card">
      <div>
        <div class="kpi-label">Memory Used</div>
        <div class="kpi-value">${fmtBytes(s.mem_used_bytes)}</div>
        <div class="kpi-sub">of ${fmtBytes(s.mem_total_bytes)} total RAM</div>
      </div>
      ${renderRingGauge(s.mem_percent, s.mem_percent > 85 ? "#f43f5e" : "#a78bfa")}
    </div>
    <div class="kpi-card">
      <div>
        <div class="kpi-label">Aggregate Storage</div>
        <div class="kpi-value">${fmtBytes(s.storage_used_bytes)}</div>
        <div class="kpi-sub">of ${fmtBytes(s.storage_total_bytes)} across pools</div>
      </div>
      ${renderRingGauge(s.storage_percent, s.storage_percent > 80 ? "#f59e0b" : "#10b981")}
    </div>`;

  // Health Advisor (Actionable Doctor with 1-click fixes!)
  const hBadge = $("#health-score-badge");
  hBadge.textContent = `${health.score} / 100`;
  hBadge.className = `health-pill ${health.status}`;
  $("#health-findings").innerHTML = health.findings.length
    ? health.findings
        .map((f) => {
          let actionBtn = "";
          if (f.action_upid) {
            actionBtn = `<button class="btn btn-xs" data-inspect-upid="${esc(f.action_upid)}" data-inspect-node="${esc(f.action_node || "")}">Inspect Log →</button>`;
          } else if (f.action_kind === "storage_files" && f.action_storage) {
            actionBtn = `<button class="btn btn-xs btn-primary" data-fix-storage="${esc(f.action_storage)}">Inspect '${esc(f.action_storage)}' →</button>`;
          } else if (f.action_kind === "schedule_backups") {
            actionBtn = `<button class="btn btn-xs btn-primary" data-fix-backups="1">Schedule PBS →</button>`;
          }
          return `
        <div class="finding ${esc(f.severity)}">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;">
            <div class="finding-title">[${esc(f.severity.toUpperCase())}] ${esc(f.title)}</div>
            ${actionBtn}
          </div>
          <div class="finding-detail">${esc(f.detail)}</div>
          <div class="finding-rec">→ ${esc(f.recommendation)}</div>
        </div>`;
        })
        .join("")
    : `<div class="finding"><div class="finding-title">All homelab checks passed</div><div class="finding-detail">Nodes, storage pools, ZFS, SMART life remaining, tasks, and backups look healthy.</div></div>`;

  $$("[data-inspect-upid]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      state.tasksErrorsOnly = true;
      $("#tasks-errors-only").checked = true;
      navigate("system", "tasks");
      await openTaskLog(btn.dataset.inspectUpid, btn.dataset.inspectNode);
    })
  );
  $$("[data-fix-storage]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      const targetSt = btn.dataset.fixStorage;
      navigate("storage", "files");
      setTimeout(async () => {
        const sel = $("#fe-storage-select");
        if (sel) {
          sel.value = targetSt;
          await extras.loadFileExplorer();
        }
      }, 220);
    })
  );
  $$("[data-fix-backups]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      const ins = await ensureInsights(false).catch(() => null);
      navigate("storage", "backups");
      const unbacked = (ins?.backup_coverage?.rows || [])
        .filter((r) => r.backup_status === "none")
        .map((r) => r.vmid);
      const form = $("#form-backup-schedule");
      if (form) {
        form.classList.remove("hidden");
        if (unbacked.length) form.elements.vmid.value = unbacked.join(",");
        form.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    })
  );

  // Load Live Service Pulse & Dell R640 iDRAC Power/Thermals HUD on Command Overview
  mh?.loadCommandWidgets(false).catch(() => {});

  // Nodes + RootFS + Swap + Calm Single-Node Services
  const primaryNode = overview.nodes?.[0]?.node;
  const singleNode = (overview.nodes?.length || 0) <= 1;
  if (primaryNode) {
    const [nodeDetail, rrd] = await Promise.all([
      api(`/api/nodes/${encodeURIComponent(primaryNode)}`),
      api(`/api/nodes/${encodeURIComponent(primaryNode)}/rrd?timeframe=${state.rrdTimeframe}`),
    ]);
    $("#rrd-subtitle").textContent = `Node ${primaryNode} — ${state.rrdTimeframe} window`;
    $("#node-rrd-chart").innerHTML = renderRrdChart(rrd.points);
    $("#node-kernel").textContent = nodeDetail.status?.pveversion || "";

    const st = nodeDetail.status || {};
    const rootfs = st.rootfs || {};
    const swap = st.swap || {};
    const rootPct = rootfs.total ? ((rootfs.used / rootfs.total) * 100).toFixed(1) : 0;
    const swapPct = swap.total ? ((swap.used / swap.total) * 100).toFixed(1) : 0;

    const allSvcs = nodeDetail.services || [];
    const activeSvcs = allSvcs.filter((sv) => sv.state === "running");
    const problemSvcs = allSvcs.filter(
      (sv) => sv.state !== "running" && !(singleNode && SINGLE_NODE_INACTIVE_OK.has(sv.name || sv.service))
    );
    const expectedInactive = allSvcs.filter(
      (sv) => sv.state !== "running" && singleNode && SINGLE_NODE_INACTIVE_OK.has(sv.name || sv.service)
    );

    $("#nodes-list").innerHTML = overview.nodes
      .map((n) => {
        const cpu = ((n.cpu || 0) * 100).toFixed(1);
        const memPct = n.maxmem ? ((n.mem / n.maxmem) * 100).toFixed(1) : 0;
        const isPrimary = n.node === primaryNode;
        const cpuModel = isPrimary ? st.cpuinfo?.model || "" : "";
        const loadavg = isPrimary ? (st.loadavg || []).join(" / ") : "";
        return `
          <div class="list-row" style="flex-direction:column;align-items:stretch;gap:10px;margin-bottom:10px;">
            <div style="display:flex;justify-content:space-between;align-items:center;">
              <div>
                <strong>${esc(n.node)}</strong>
                <span class="status-pill ${esc(n.status)}" style="margin-left:6px;">${esc(n.status)}</span>
                <div class="muted small">${esc(cpuModel)}</div>
              </div>
              <div class="right mono small">
                <div>Uptime: ${fmtUptime(n.uptime)}</div>
                ${loadavg ? `<div class="muted">Load: ${esc(loadavg)}</div>` : ""}
              </div>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px 14px;">
              <div class="bar-wrap">
                <div class="small mono">CPU ${cpu}% (${n.maxcpu || 0} threads)</div>
                <div class="bar-track"><div class="bar-fill ${barClass(cpu)}" style="width:${cpu}%"></div></div>
              </div>
              <div class="bar-wrap">
                <div class="small mono">RAM ${memPct}% (${fmtBytes(n.mem)} / ${fmtBytes(n.maxmem)})</div>
                <div class="bar-track"><div class="bar-fill ${barClass(memPct)}" style="width:${memPct}%"></div></div>
              </div>
              ${
                isPrimary && rootfs.total
                  ? `<div class="bar-wrap">
                      <div class="small mono">RootFS (/) ${rootPct}% (${fmtBytes(rootfs.used)} / ${fmtBytes(rootfs.total)})</div>
                      <div class="bar-track"><div class="bar-fill ${barClass(rootPct)}" style="width:${rootPct}%"></div></div>
                    </div>`
                  : ""
              }
              ${
                isPrimary && swap.total
                  ? `<div class="bar-wrap">
                      <div class="small mono">Swap ${swapPct}% (${fmtBytes(swap.used)} / ${fmtBytes(swap.total)})</div>
                      <div class="bar-track"><div class="bar-fill ${barClass(swapPct)}" style="width:${swapPct}%"></div></div>
                    </div>`
                  : ""
              }
            </div>
            ${
              isPrimary
                ? `<div class="tag-row">
                    ${problemSvcs.map((sv) => `<span class="tag tag-rose">${esc(sv.name || sv.service)}: ${esc(sv.state)}</span>`).join("")}
                    ${activeSvcs.slice(0, 7).map((sv) => `<span class="tag tag-emerald">${esc(sv.name || sv.service)}</span>`).join("")}
                    ${expectedInactive.length ? `<span class="tag" title="Expected inactive on standalone single-node host: ${esc(expectedInactive.map((s) => s.name || s.service).join(", "))}">+${expectedInactive.length} single-node inactive</span>` : ""}
                  </div>`
                : ""
            }
          </div>`;
      })
      .join("");
  }

  // Recent Activity
  const items = [];
  for (const e of (auditData.events || []).slice(0, 5)) {
    items.push({
      ts: e.timestamp,
      badge: e.source === "mcp" ? `<span class="tag tag-violet">MCP</span>` : `<span class="tag tag-cyan">UI</span>`,
      title: `${e.action} · ${e.target}`,
      status: e.status,
    });
  }
  for (const t of (tasksData.tasks || []).slice(0, 5)) {
    items.push({
      ts: t.starttime,
      badge: `<span class="tag">PVE</span>`,
      title: `${t.type} ${t.id ? `#${t.id}` : "(host)"} · ${t.user || ""}`,
      status: t.status || "running",
    });
  }
  items.sort((a, b) => (b.ts || 0) - (a.ts || 0));
  $("#overview-activity").innerHTML = items.slice(0, 8).length
    ? items
        .slice(0, 8)
        .map(
          (i) => `
        <div class="list-row">
          <div style="display:flex;align-items:center;gap:8px;min-width:0;">
            ${i.badge}
            <span class="mono small" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(i.title)}</span>
          </div>
          <div style="display:flex;align-items:center;gap:8px;flex-shrink:0;">
            <span class="muted small mono">${fmtTime(i.ts)}</span>
            <span class="status-pill ${i.status === "OK" || i.status === "ok" ? "ok" : i.status === "running" ? "warn" : "err"}">${esc(i.status)}</span>
          </div>
        </div>`
        )
        .join("")
    : `<div class="muted small">No recent events yet.</div>`;
}

/* ── View 2: Guests Fleet (Tags, Grouping, Sorting, Bulk, Console, App Links) ── */
async function loadGuests(force = true) {
  await ensureInsights(force);
  renderTagChips();
  renderGuestsTable();
}

function renderTagChips() {
  const guests = state.insights?.guests || [];
  const counts = new Map();
  for (const g of guests) {
    for (const t of g.tag_list || []) {
      if (isIpTag(t)) continue;
      counts.set(t, (counts.get(t) || 0) + 1);
    }
  }
  const topTags = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 14);
  if (!topTags.length) {
    $("#guest-tag-chips").innerHTML = "";
    return;
  }
  $("#guest-tag-chips").innerHTML =
    `<span class="muted small">Tags:</span>` +
    `<button class="tag-chip ${state.guestFilter.tag === "" ? "active" : ""}" data-tag-chip="">All (${guests.length})</button>` +
    topTags
      .map(
        ([t, c]) =>
          `<button class="tag-chip ${state.guestFilter.tag === t ? "active" : ""}" data-tag-chip="${esc(t)}">${esc(t)} (${c})</button>`
      )
      .join("");
  $$("[data-tag-chip]").forEach((btn) =>
    btn.addEventListener("click", () => {
      state.guestFilter.tag = btn.dataset.tagChip;
      renderTagChips();
      renderGuestsTable();
    })
  );
}

function updateBulkBar() {
  const n = state.selectedVmids.size;
  $("#guest-bulk-bar").classList.toggle("hidden", n === 0);
  $("#bulk-count").textContent = n;
  $$("[data-bulk]").forEach((b) => {
    b.disabled = isReadOnly();
    b.title = isReadOnly() ? "Disabled in Read-Only Mode" : "";
  });
}

function renderGuestRow(g) {
  const isRunning = g.status === "running";
  const isTpl = Boolean(g.template);
  const checked = state.selectedVmids.has(g.vmid) ? "checked" : "";
  const nonIpTags = (g.tag_list || []).filter((t) => !isIpTag(t) && t !== "community-script");
  const svcDot =
    g.service_up === true
      ? `<span class="svc-dot up" title="HTTP UP (${g.service_latency_ms}ms)"></span>`
      : g.service_up === false
        ? `<span class="svc-dot down" title="HTTP DOWN"></span>`
        : "";
  const backupPill = {
    fresh: `<span class="status-pill ok">${fmtAge(g.backup_age_hours)}</span>`,
    stale: `<span class="status-pill warn">${fmtAge(g.backup_age_hours)}</span>`,
    old: `<span class="status-pill err">${fmtAge(g.backup_age_hours)}</span>`,
    none: `<span class="status-pill subtle">None</span>`,
  }[g.backup_status || "none"];

  return `
    <tr>
      <td><input type="checkbox" class="chk-guest" data-vmid="${g.vmid}" ${checked} /></td>
      <td class="mono">
        <div><strong>${g.vmid}</strong></div>
        <div style="display:flex;gap:4px;margin-top:2px;">
          <span class="tag ${g.type === "qemu" ? "tag-cyan" : "tag-violet"}">${g.type.toUpperCase()}</span>
          <span class="status-pill ${esc(g.status)}" style="padding:1px 6px;font-size:9.5px;">${esc(g.status)}</span>
        </div>
      </td>
      <td>
        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
          <span class="guest-name" data-inspect="${g.vmid}">${esc(g.name || `guest-${g.vmid}`)}</span>
          ${isTpl ? '<span class="tag tag-amber">TPL</span>' : ""}
          ${g.service_url ? `<a class="link-pill" href="${esc(g.service_url)}" target="_blank" rel="noopener">${svcDot}${esc(g.service_label || "App")} ↗</a>` : ""}
        </div>
        <div class="tag-row">
          ${(g.all_ips || []).slice(0, 2).map((ip) => `<span class="tag tag-cyan">${esc(ip)}</span>`).join("")}
          ${(g.bind_mounts || []).length ? `<span class="tag">${g.bind_mounts.length} MP</span>` : ""}
          ${nonIpTags.slice(0, 3).map((t) => `<span class="tag">${esc(t)}</span>`).join("")}
        </div>
      </td>
      <td>
        <div class="bar-wrap" style="min-width:135px;">
          <div class="mono small">CPU ${g.cpu_percent}%/${g.maxcpu || 0}c · RAM ${fmtBytes(g.mem)}/${fmtBytes(g.maxmem)}</div>
          <div class="bar-track" style="height:6px;"><div class="bar-fill ${barClass(g.cpu_percent)}" style="width:${Math.min(100, g.cpu_percent)}%"></div></div>
          <div class="bar-track" style="height:5px;margin-top:3px;"><div class="bar-fill ${barClass(g.mem_percent)}" style="width:${Math.min(100, g.mem_percent)}%"></div></div>
        </div>
      </td>
      <td class="mono small">${fmtUptime(g.uptime)}</td>
      <td>${backupPill}</td>
      <td class="right">
        <div class="action-group">
          ${!isTpl ? `<a class="btn btn-xs btn-ghost" href="${esc(g.console_url)}" target="_blank" rel="noopener" title="Proxmox Console">&gt;_</a>` : ""}
          ${g.primary_ip ? `<button class="btn btn-xs btn-ghost" data-copy-ssh="ssh root@${esc(g.primary_ip)}" title="Copy SSH">SSH</button>` : ""}
          ${
            !isTpl
              ? isRunning
                ? `<button class="btn btn-xs btn-ghost" data-power="reboot" data-vmid="${g.vmid}" title="Reboot" ${writeAttr()}>↻</button>
                   <button class="btn btn-xs btn-ghost" data-power="shutdown" data-vmid="${g.vmid}" title="Shutdown" ${writeAttr()}>⏻</button>`
                : `<button class="btn btn-xs btn-primary" data-power="start" data-vmid="${g.vmid}" title="Start" ${writeAttr()}>▶</button>`
              : ""
          }
          <button class="btn btn-xs" data-inspect="${g.vmid}">Inspect</button>
        </div>
      </td>
    </tr>`;
}

function renderGuestCard(g) {
  const isRunning = g.status === "running";
  const isTpl = Boolean(g.template);
  const svcDot =
    g.service_up === true
      ? `<span class="svc-dot up"></span>`
      : g.service_up === false
        ? `<span class="svc-dot down"></span>`
        : "";
  return `
    <div class="guest-hud-card ${isTpl ? "template" : g.status}">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;">
        <div>
          <div class="mono small muted">${g.vmid} · <span class="tag ${g.type === "qemu" ? "tag-cyan" : "tag-violet"}">${g.type.toUpperCase()}</span></div>
          <div class="guest-name" style="font-size:14px;margin-top:2px;" data-inspect="${g.vmid}">${esc(g.name)}</div>
        </div>
        <span class="status-pill ${esc(g.status)}">${esc(g.status)}</span>
      </div>
      <div class="tag-row">
        ${(g.all_ips || []).slice(0, 2).map((ip) => `<span class="tag tag-cyan">${esc(ip)}</span>`).join("")}
        ${g.service_url ? `<a class="link-pill" href="${esc(g.service_url)}" target="_blank" rel="noopener">${svcDot}${esc(g.service_label || "Open")} ↗</a>` : ""}
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
        <div class="bar-wrap">
          <div class="mono small">CPU ${g.cpu_percent}% (${g.maxcpu || 0}c)</div>
          <div class="bar-track"><div class="bar-fill ${barClass(g.cpu_percent)}" style="width:${Math.min(100, g.cpu_percent)}%"></div></div>
        </div>
        <div class="bar-wrap">
          <div class="mono small">RAM ${g.mem_percent}% (${fmtBytes(g.mem)})</div>
          <div class="bar-track"><div class="bar-fill ${barClass(g.mem_percent)}" style="width:${Math.min(100, g.mem_percent)}%"></div></div>
        </div>
      </div>
      <div style="display:flex;justify-content:space-between;align-items:center;padding-top:4px;border-top:1px solid var(--border);">
        <span class="mono small muted">Up: ${fmtUptime(g.uptime)}</span>
        <div class="action-group">
          ${!isTpl ? `<a class="btn btn-xs btn-ghost" href="${esc(g.console_url)}" target="_blank" rel="noopener">&gt;_</a>` : ""}
          ${!isTpl && !isRunning ? `<button class="btn btn-xs btn-primary" data-power="start" data-vmid="${g.vmid}" ${writeAttr()}>▶</button>` : ""}
          <button class="btn btn-xs" data-inspect="${g.vmid}">Inspect</button>
        </div>
      </div>
    </div>`;
}

function renderGuestsTable() {
  const all = state.insights?.guests || [];
  const q = state.guestFilter.search.trim().toLowerCase();
  const filtered = all.filter((g) => {
    if (state.guestFilter.type && g.type !== state.guestFilter.type) return false;
    if (state.guestFilter.status && g.status !== state.guestFilter.status) return false;
    if (state.guestFilter.tag && !(g.tag_list || []).includes(state.guestFilter.tag)) return false;
    if (q) {
      const hay = `${g.vmid} ${g.name || ""} ${(g.all_ips || []).join(" ")} ${(g.bridges || []).join(" ")} ${(g.storages_used || []).join(" ")} ${g.tags || ""}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  const { key, dir } = state.guestSort;
  const mul = dir === "asc" ? 1 : -1;
  filtered.sort((a, b) => {
    if (key === "name") return String(a.name).localeCompare(String(b.name)) * mul;
    if (key === "status") return String(a.status).localeCompare(String(b.status)) * mul;
    if (key === "cpu") return ((a.cpu_percent || 0) - (b.cpu_percent || 0)) * mul;
    if (key === "mem") return ((a.mem || 0) - (b.mem || 0)) * mul;
    if (key === "uptime") return ((a.uptime || 0) - (b.uptime || 0)) * mul;
    return (Number(a.vmid) - Number(b.vmid)) * mul;
  });

  const isCards = state.guestLayout === "cards";
  $("#guests-table-wrap").classList.toggle("hidden", isCards);
  $("#guests-cards-wrap").classList.toggle("hidden", !isCards);

  if (!filtered.length) {
    $("#guests-tbody").innerHTML = `<tr><td colspan="7" class="muted" style="text-align:center;padding:28px;">No matching VMs or LXC containers.</td></tr>`;
    $("#guests-cards-wrap").innerHTML = `<div class="muted small" style="padding:24px;">No matching VMs or LXC containers.</div>`;
    return;
  }

  if (isCards) {
    $("#guests-cards-wrap").innerHTML = filtered.map(renderGuestCard).join("");
  } else {
    const grp = state.guestFilter.groupBy;
    if (grp === "none") {
      $("#guests-tbody").innerHTML = filtered.map(renderGuestRow).join("");
    } else {
      const groups = new Map();
      for (const g of filtered) {
        let label = "Other";
        if (grp === "block") label = vmidBlock(g.vmid);
        else if (grp === "bridge") label = (g.bridges || [])[0] || "No Bridge";
        else if (grp === "storage") label = (g.storages_used || [])[0] || "Unknown Storage";
        else if (grp === "tag") {
          const cleanTags = (g.tag_list || []).filter((t) => !isIpTag(t) && t !== "community-script");
          label = cleanTags[0] || "Untagged";
        }
        if (!groups.has(label)) groups.set(label, []);
        groups.get(label).push(g);
      }
      let html = "";
      for (const [label, list] of groups.entries()) {
        const runCnt = list.filter((x) => x.status === "running").length;
        html += `<tr class="group-row"><td colspan="7">${esc(label)} — ${list.length} guest(s) (${runCnt} running)</td></tr>`;
        html += list.map(renderGuestRow).join("");
      }
      $("#guests-tbody").innerHTML = html;
    }
  }

  $$(".chk-guest").forEach((chk) =>
    chk.addEventListener("change", () => {
      const id = Number(chk.dataset.vmid);
      if (chk.checked) state.selectedVmids.add(id);
      else state.selectedVmids.delete(id);
      updateBulkBar();
    })
  );
  $$("[data-inspect]").forEach((el) =>
    el.addEventListener("click", () => openGuestDrawer(Number(el.dataset.inspect)))
  );
  $$("[data-power]").forEach((el) =>
    el.addEventListener("click", () => runPowerAction(Number(el.dataset.vmid), el.dataset.power))
  );
  $$("[data-copy-ssh]").forEach((el) =>
    el.addEventListener("click", async () => {
      await navigator.clipboard.writeText(el.dataset.copySsh);
      toast(`Copied: ${el.dataset.copySsh}`, "info");
    })
  );
  updateBulkBar();
}

async function runPowerAction(vmid, action) {
  try {
    await api(`/api/guests/${vmid}/power`, {
      method: "POST",
      body: JSON.stringify({ action }),
    });
    toast(`Sent '${action}' to guest ${vmid}`, "ok");
    state.insights = null;
    if (state.view === "guests") await loadGuests(true);
    if (state.drawer.open && state.drawer.vmid === vmid) await refreshGuestDrawer();
  } catch (err) {
    toast(err.message, "err");
  }
}

/* ── View 3: Network & IPAM Matrix ──────────────────────────────────── */
async function loadNetworkIpam(force = true) {
  const ins = await ensureInsights(force);
  const net = ins.network_ipam || { bridges: [], entries: [], conflicts: [] };

  const cBanner = $("#ipam-conflicts");
  if (net.conflicts.length) {
    cBanner.classList.remove("hidden");
    cBanner.innerHTML = `<div><strong>IP Conflict Detected:</strong> ${net.conflicts
      .map((c) => `<code>${esc(c.ip)}</code> shared by ${esc(c.guests.join(" & "))}`)
      .join(" · ")}</div>`;
  } else {
    cBanner.classList.add("hidden");
  }

  $("#ipam-bridges-grid").innerHTML = net.bridges
    .map(
      (b) => `
      <div class="storage-card">
        <div style="display:flex;justify-content:space-between;align-items:center;">
          <strong class="mono">${esc(b.iface)}</strong>
          <span class="tag tag-cyan">${b.guest_count} guests</span>
        </div>
        <div class="mono small" style="margin:4px 0;">${esc(b.cidr || "Isolated / No Host IP")}</div>
        <div class="muted small">${esc(b.comments || (b.ports ? `Uplink port: ${b.ports}` : "Virtual bridge"))}</div>
        ${
          (b.free_ip_suggestions || []).length
            ? `<div class="muted small" style="margin-top:8px;">Next free IPs: ${b.free_ip_suggestions
                .slice(0, 4)
                .map((ip) => `<code>${esc(ip)}</code>`)
                .join(" ")}</div>`
            : ""
        }
      </div>`
    )
    .join("");

  const brSelect = $("#ipam-bridge-filter");
  const curBr = state.ipamFilter.bridge;
  brSelect.innerHTML =
    `<option value="">All Bridges</option>` +
    net.bridges.map((b) => `<option value="${esc(b.iface)}" ${curBr === b.iface ? "selected" : ""}>${esc(b.iface)} (${b.guest_count})</option>`).join("");

  renderIpamTable();
}

function renderIpamTable() {
  const entries = state.insights?.network_ipam?.entries || [];
  const q = state.ipamFilter.search.trim().toLowerCase();
  const br = state.ipamFilter.bridge;
  const rows = entries.filter((e) => {
    if (br && e.bridge !== br) return false;
    if (q) {
      const hay = `${e.ip} ${e.vmid} ${e.name} ${e.bridge} ${e.mac} ${e.nic}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  rows.sort((a, b) => (a.ip || "999").localeCompare(b.ip || "999", undefined, { numeric: true }));

  $("#ipam-tbody").innerHTML = rows.length
    ? rows
        .map(
          (e) => `
        <tr>
          <td class="mono">${e.ip ? `<strong>${esc(e.ip)}</strong>` : `<span class="muted">${e.dhcp ? "DHCP" : "—"}</span>`}</td>
          <td><span class="guest-name" data-ipam-inspect="${e.vmid}">${e.vmid} · ${esc(e.name)}</span></td>
          <td class="mono small">${esc(e.nic)}</td>
          <td><span class="tag tag-cyan">${esc(e.bridge)}</span></td>
          <td class="mono small">${esc(e.vlan || "—")}</td>
          <td class="mono small muted">${esc(e.mac || "—")}</td>
          <td><span class="status-pill ${esc(e.status)}">${esc(e.status)}</span></td>
          <td class="right">
            <div class="action-group">
              ${e.service_url ? `<a class="btn btn-xs" href="${esc(e.service_url)}" target="_blank" rel="noopener">↗ Open</a>` : ""}
              ${e.ip ? `<button class="btn btn-xs btn-ghost" data-copy-ip="${esc(e.ip)}">Copy IP</button>` : ""}
            </div>
          </td>
        </tr>`
        )
        .join("")
    : `<tr><td colspan="8" class="muted" style="text-align:center;padding:24px;">No network interfaces match this filter.</td></tr>`;

  $$("[data-ipam-inspect]").forEach((el) =>
    el.addEventListener("click", () => openGuestDrawer(Number(el.dataset.ipamInspect)))
  );
  $$("[data-copy-ip]").forEach((el) =>
    el.addEventListener("click", async () => {
      await navigator.clipboard.writeText(el.dataset.copyIp);
      toast(`Copied ${el.dataset.copyIp}`, "info");
    })
  );
}

/* ── View 4: Capacity & Right-Sizing ────────────────────────────────── */
async function loadCapacity(force = true) {
  const ins = await ensureInsights(force);
  const cap = ins.capacity;

  const memUsedPct = cap.host_mem_bytes ? ((cap.host_mem_used_bytes / cap.host_mem_bytes) * 100).toFixed(1) : 0;
  $("#capacity-kpis").innerHTML = `
    <div class="kpi-card">
      <div>
        <div class="kpi-label">vCPU Subscription (Running)</div>
        <div class="kpi-value">${cap.alloc_cores_running} / ${cap.host_cores}</div>
        <div class="kpi-sub">${cap.vcpu_ratio_running}× ratio (${cap.alloc_cores_all} total incl. stopped)</div>
      </div>
      ${renderRingGauge(Math.min(100, cap.vcpu_ratio_running * 25), cap.vcpu_ratio_running > 4 ? "#f59e0b" : "#22d3ee")}
    </div>
    <div class="kpi-card">
      <div>
        <div class="kpi-label">RAM Allocated (Running)</div>
        <div class="kpi-value">${fmtBytes(cap.alloc_mem_running_bytes)}</div>
        <div class="kpi-sub">${cap.ram_alloc_pct_running}% of ${fmtBytes(cap.host_mem_bytes)} host RAM</div>
      </div>
      ${renderRingGauge(Math.min(100, cap.ram_alloc_pct_running), cap.ram_alloc_pct_running > 95 ? "#f43f5e" : "#a78bfa")}
    </div>
    <div class="kpi-card">
      <div>
        <div class="kpi-label">RAM Actually Used</div>
        <div class="kpi-value">${fmtBytes(cap.host_mem_used_bytes)}</div>
        <div class="kpi-sub">${memUsedPct}% actual host consumption</div>
      </div>
      ${renderRingGauge(memUsedPct, "#10b981")}
    </div>
    <div class="kpi-card">
      <div>
        <div class="kpi-label">Right-Sizing Candidates</div>
        <div class="kpi-value">${cap.rightsizing.length}</div>
        <div class="kpi-sub">Over-provisioned running guests</div>
      </div>
      ${renderRingGauge(Math.min(100, cap.rightsizing.length * 10), "#f59e0b")}
    </div>`;

  $("#rightsizing-wrap").innerHTML = cap.rightsizing.length
    ? `<table class="data-table">
        <thead><tr><th>Guest</th><th>Type</th><th>CPU</th><th>Memory</th><th>Recommendation</th><th class="right">Action</th></tr></thead>
        <tbody>${cap.rightsizing
          .map(
            (r) => `
          <tr>
            <td class="mono"><strong>${r.vmid}</strong> · ${esc(r.name)}</td>
            <td><span class="tag ${r.type === "qemu" ? "tag-cyan" : "tag-violet"}">${r.type.toUpperCase()}</span></td>
            <td class="mono small">${r.cpu_percent}% / ${r.cores}c</td>
            <td class="mono small">${fmtBytes(r.mem_bytes)} / ${fmtBytes(r.maxmem_bytes)} (${r.mem_percent}%)</td>
            <td class="small">${r.suggestions.map((s) => `<div>• ${esc(s)}</div>`).join("")}</td>
            <td class="right"><button class="btn btn-xs" data-cap-inspect="${r.vmid}">Resize →</button></td>
          </tr>`
          )
          .join("")}</tbody>
      </table>`
    : `<div class="muted small">All running guests look cleanly sized!</div>`;

  $("#top-compute-wrap").innerHTML = `
    <div class="muted small" style="margin-bottom:6px;">TOP CPU</div>
    <div class="mini-list" style="margin-bottom:14px;">
      ${cap.top_cpu
        .slice(0, 6)
        .map(
          (g) => `
        <div class="list-row clickable" data-cap-inspect="${g.vmid}">
          <span class="mono small"><strong>${g.vmid}</strong> · ${esc(g.name)}</span>
          <span class="mono small tag tag-cyan">${g.cpu_percent}% (${g.maxcpu}c)</span>
        </div>`
        )
        .join("")}
    </div>
    <div class="muted small" style="margin-bottom:6px;">TOP MEMORY</div>
    <div class="mini-list">
      ${cap.top_mem
        .slice(0, 6)
        .map(
          (g) => `
        <div class="list-row clickable" data-cap-inspect="${g.vmid}">
          <span class="mono small"><strong>${g.vmid}</strong> · ${esc(g.name)}</span>
          <span class="mono small tag tag-violet">${fmtBytes(g.mem)} / ${fmtBytes(g.maxmem)}</span>
        </div>`
        )
        .join("")}
    </div>`;

  $("#top-io-wrap").innerHTML = `
    <div class="muted small" style="margin-bottom:6px;">TOP DISK I/O (READ + WRITE)</div>
    <div class="mini-list" style="margin-bottom:14px;">
      ${cap.top_io
        .slice(0, 6)
        .map(
          (g) => `
        <div class="list-row clickable" data-cap-inspect="${g.vmid}">
          <span class="mono small"><strong>${g.vmid}</strong> · ${esc(g.name)}</span>
          <span class="mono small muted">R: ${fmtBytes(g.diskread)} · W: ${fmtBytes(g.diskwrite)}</span>
        </div>`
        )
        .join("")}
    </div>
    <div class="muted small" style="margin-bottom:6px;">TOP NETWORK TRAFFIC (IN + OUT)</div>
    <div class="mini-list">
      ${cap.top_net
        .slice(0, 6)
        .map(
          (g) => `
        <div class="list-row clickable" data-cap-inspect="${g.vmid}">
          <span class="mono small"><strong>${g.vmid}</strong> · ${esc(g.name)}</span>
          <span class="mono small muted">↓ ${fmtBytes(g.netin)} · ↑ ${fmtBytes(g.netout)}</span>
        </div>`
        )
        .join("")}
    </div>`;

  $$("[data-cap-inspect]").forEach((el) =>
    el.addEventListener("click", () => openGuestDrawer(Number(el.dataset.capInspect)))
  );
}

/* ── Guest Slide-Over Inspector ─────────────────────────────────────── */
async function openGuestDrawer(vmid) {
  state.drawer = { open: true, vmid, tab: "overview", detail: null };
  $("#drawer-backdrop").classList.remove("hidden");
  $("#guest-drawer").classList.remove("hidden");
  $("#drawer-body").innerHTML = `<div class="muted">Loading guest ${vmid}...</div>`;
  await refreshGuestDrawer();
}

function closeGuestDrawer() {
  state.drawer.open = false;
  $("#drawer-backdrop").classList.add("hidden");
  $("#guest-drawer").classList.add("hidden");
}

async function refreshGuestDrawer() {
  const { vmid } = state.drawer;
  try {
    const detail = await api(`/api/guests/${vmid}`);
    state.drawer.detail = detail;
    const st = detail.status || {};
    const cfg = detail.config || {};
    const name = st.name || cfg.name || cfg.hostname || `guest-${vmid}`;
    const primaryIp = (detail.ips || [])[0];

    $("#drawer-type-tag").textContent = detail.type.toUpperCase();
    $("#drawer-type-tag").className = `tag ${detail.type === "qemu" ? "tag-cyan" : "tag-violet"}`;
    $("#drawer-title").textContent = `${vmid} · ${name}`;
    $("#drawer-status").textContent = st.status || "unknown";
    $("#drawer-status").className = `status-pill ${st.status || ""}`;
    $("#drawer-meta").textContent = `Node: ${detail.node} · IPs: ${(detail.ips || []).join(", ") || "N/A"} · Uptime: ${fmtUptime(st.uptime)}`;

    const running = st.status === "running";
    const bm = detail.bookmark;
    $("#drawer-power-bar").innerHTML = `
      ${
        cfg.template
          ? `<span class="tag tag-amber">VM Template — Clone to deploy</span>`
          : `
            <button class="btn btn-sm ${running ? "" : "btn-primary"}" data-dpower="start" ${running || isReadOnly() ? "disabled" : ""}>▶ Start</button>
            <button class="btn btn-sm" data-dpower="shutdown" ${!running || isReadOnly() ? "disabled" : ""}>⏻ Shutdown</button>
            <button class="btn btn-sm" data-dpower="reboot" ${!running || isReadOnly() ? "disabled" : ""}>↻ Reboot</button>
            <button class="btn btn-sm btn-danger" data-dpower="stop" ${!running || isReadOnly() ? "disabled" : ""}>■ Force Stop</button>
            <a class="btn btn-sm" href="${esc(detail.console_url)}" target="_blank" rel="noopener">&gt;_ Console</a>`
      }
      ${primaryIp ? `<button class="btn btn-sm btn-ghost" id="drawer-copy-ssh">SSH</button>` : ""}
      ${bm?.url ? `<a class="btn btn-sm btn-primary" href="${esc(bm.url)}" target="_blank" rel="noopener">↗ ${esc(bm.label || "Open App")}</a>` : ""}`;

    $$("[data-dpower]").forEach((b) =>
      b.addEventListener("click", () => runPowerAction(vmid, b.dataset.dpower))
    );
    $("#drawer-copy-ssh")?.addEventListener("click", async () => {
      await navigator.clipboard.writeText(`ssh root@${primaryIp}`);
      toast(`Copied: ssh root@${primaryIp}`, "info");
    });
    $$("#drawer-tabs .seg").forEach((b) =>
      b.classList.toggle("active", b.dataset.dtab === state.drawer.tab)
    );
    renderDrawerTab();
  } catch (err) {
    $("#drawer-body").innerHTML = `<div class="test-result err">${esc(err.message)}</div>`;
  }
}

function renderDrawerTab() {
  const d = state.drawer.detail;
  if (!d) return;
  const cfg = d.config || {};
  const body = $("#drawer-body");
  const descInfo = d.description_info || { has_html: false, summary: "" };

  if (state.drawer.tab === "overview") {
    const nicsHtml = (d.nics || []).length
      ? d.nics
          .map(
            (n) => `
          <div class="list-row">
            <div class="mono small"><strong>${esc(n.id)}</strong> · <span class="tag tag-cyan">${esc(n.bridge || "none")}</span> ${n.vlan ? `<span class="tag">VLAN ${esc(n.vlan)}</span>` : ""}</div>
            <div class="mono small">${esc(n.ip || (n.dhcp ? "DHCP" : "—"))} <span class="muted">${esc(n.mac || "")}</span></div>
          </div>`
          )
          .join("")
      : `<div class="muted small">No NICs parsed.</div>`;

    const mountsHtml = (d.bind_mounts || []).length
      ? d.bind_mounts
          .map(
            (m) => `
          <div class="list-row">
            <div class="mono small"><strong>${esc(m.id)}</strong> · ${esc(m.source)}</div>
            <div class="mono small muted">→ ${esc(m.target)}</div>
          </div>`
          )
          .join("")
      : "";

    const ptHtml = (d.passthroughs || []).length
      ? d.passthroughs
          .map((p) => `<span class="tag tag-amber">${esc(p.kind)} ${esc(p.id)}: ${esc(p.spec)}</span>`)
          .join(" ")
      : "";

    body.innerHTML = `
      <div class="card">
        <div class="card-header"><h2>Recent Telemetry (1H)</h2></div>
        ${renderRrdChart(d.rrd, 165)}
      </div>

      <div class="card">
        <div class="card-header"><h2>Network Interfaces, Storage &amp; Mounts</h2></div>
        <div class="mini-list" style="margin-bottom:10px;">${nicsHtml}</div>
        ${(d.storages_used || []).length ? `<div class="tag-row" style="margin-bottom:8px;"><span class="muted small">Storages:</span>${d.storages_used.map((s) => `<span class="tag tag-emerald">${esc(s)}</span>`).join("")}</div>` : ""}
        ${mountsHtml ? `<div class="muted small" style="margin:6px 0 4px;">Bind Mounts</div><div class="mini-list" style="margin-bottom:8px;">${mountsHtml}</div>` : ""}
        ${ptHtml ? `<div class="tag-row">${ptHtml}</div>` : ""}
      </div>

      <div class="card">
        <div class="card-header"><h2>Service Web UI Bookmark</h2></div>
        <form id="drawer-bm-form" class="form-grid">
          <div class="field">
            <label>App URL (blank clears override)</label>
            <input class="input mono" name="url" value="${esc(d.custom_bookmark?.url || d.bookmark?.url || "")}" placeholder="http://192.168.1.40:32400/web" />
          </div>
          <div class="field">
            <label>Label</label>
            <input class="input" name="label" value="${esc(d.custom_bookmark?.label || d.bookmark?.label || "")}" placeholder="e.g. Plex / Sonarr" />
          </div>
          <div class="field full"><button class="btn btn-sm" type="submit">Save Bookmark</button></div>
        </form>
      </div>

      <div class="card">
        <div class="card-header">
          <h2>Hardware &amp; Metadata</h2>
          ${isReadOnly() ? `<span class="tag tag-amber">READ-ONLY</span>` : ""}
        </div>
        ${
          descInfo.has_html
            ? `<div class="html-preview-box">
                <div class="small"><strong>${esc(descInfo.summary || "HTML Description")}</strong> ${descInfo.is_community_script ? '<span class="tag tag-cyan">Community Script</span>' : ""}</div>
                <div class="muted small">Raw HTML banner detected — edit below only if you want to replace it.</div>
              </div>`
            : ""
        }
        <form id="drawer-config-form" class="form-grid">
          <div class="field">
            <label>CPU Cores</label>
            <input class="input mono" name="cores" type="number" min="1" value="${esc(cfg.cores ?? 1)}" />
          </div>
          <div class="field">
            <label>Memory (MB)</label>
            <input class="input mono" name="memory_mb" type="number" step="128" min="128" value="${esc(cfg.memory ?? 1024)}" />
          </div>
          <div class="field full">
            <label>Tags (semicolon-separated)</label>
            <input class="input" name="tags" value="${esc(cfg.tags || "")}" />
          </div>
          <div class="field full">
            <label>Description / Notes</label>
            <textarea class="input" name="description">${esc(cfg.description || "")}</textarea>
          </div>
          <div class="field full checkbox-row">
            <label><input type="checkbox" name="onboot" ${cfg.onboot ? "checked" : ""} /> Start automatically on node boot</label>
          </div>
          <div class="field full">
            <button class="btn btn-primary btn-sm" type="submit" ${writeAttr()}>Save Configuration</button>
          </div>
        </form>
      </div>

      <details class="guide-box">
        <summary>Inspect Raw Proxmox Config JSON</summary>
        <pre class="code-block" style="margin-top:8px;">${esc(JSON.stringify(cfg, null, 2))}</pre>
      </details>`;

    $("#drawer-bm-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      try {
        await api(`/api/guests/${d.vmid}/bookmark`, {
          method: "PUT",
          body: JSON.stringify({
            url: String(fd.get("url") || ""),
            label: String(fd.get("label") || ""),
          }),
        });
        toast("Service bookmark saved", "ok");
        state.insights = null;
        await refreshGuestDrawer();
        if (state.view === "guests") await loadGuests(true);
      } catch (err) {
        toast(err.message, "err");
      }
    });

    $("#drawer-config-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      try {
        await api(`/api/guests/${d.vmid}/config`, {
          method: "PUT",
          body: JSON.stringify({
            cores: Number(fd.get("cores")),
            memory_mb: Number(fd.get("memory_mb")),
            tags: String(fd.get("tags") || ""),
            description: String(fd.get("description") || ""),
            onboot: fd.get("onboot") === "on",
          }),
        });
        toast(`Updated configuration for ${d.vmid}`, "ok");
        state.insights = null;
        await refreshGuestDrawer();
        if (state.view === "guests") await loadGuests(true);
      } catch (err) {
        toast(err.message, "err");
      }
    });
  } else if (state.drawer.tab === "snapshots") {
    const snaps = (d.snapshots || []).filter((s) => s.name !== "current");
    body.innerHTML = `
      <div class="card">
        <div class="card-header"><h2>Create Snapshot</h2></div>
        <form id="drawer-snap-form" class="form-grid">
          <div class="field">
            <label>Snapshot Name *</label>
            <input class="input mono" name="snapname" required pattern="[A-Za-z][A-Za-z0-9_-]*" placeholder="pre-upgrade-01" />
          </div>
          <div class="field">
            <label>Description</label>
            <input class="input" name="description" placeholder="Before kernel/app update" />
          </div>
          ${
            d.type === "qemu"
              ? `<div class="field full checkbox-row"><label><input type="checkbox" name="vmstate" /> Include RAM state (live snapshot)</label></div>`
              : ""
          }
          <div class="field full"><button class="btn btn-primary btn-sm" type="submit" ${writeAttr()}>+ Take Snapshot</button></div>
        </form>
      </div>
      <div class="card">
        <div class="card-header"><h2>Existing Snapshots (${snaps.length})</h2></div>
        <div class="mini-list">
          ${
            snaps.length
              ? snaps
                  .map(
                    (s) => `
                  <div class="list-row">
                    <div>
                      <div class="mono"><strong>${esc(s.name)}</strong> ${s.vmstate ? '<span class="tag tag-cyan">RAM</span>' : ""}</div>
                      <div class="muted small">${esc(s.description || "No description")} · ${fmtTime(s.snaptime)}</div>
                    </div>
                    <div class="action-group">
                      <button class="btn btn-xs" data-snap-rb="${esc(s.name)}" ${writeAttr()}>Rollback</button>
                      <button class="btn btn-xs btn-danger" data-snap-del="${esc(s.name)}" ${writeAttr()}>Delete</button>
                    </div>
                  </div>`
                  )
                  .join("")
              : `<div class="muted small">No snapshots taken for this guest yet.</div>`
          }
        </div>
      </div>`;

    $("#drawer-snap-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      try {
        await api(`/api/guests/${d.vmid}/snapshots`, {
          method: "POST",
          body: JSON.stringify({
            snapname: String(fd.get("snapname")),
            description: String(fd.get("description") || ""),
            vmstate: fd.get("vmstate") === "on",
          }),
        });
        toast("Snapshot created", "ok");
        await refreshGuestDrawer();
      } catch (err) {
        toast(err.message, "err");
      }
    });

    $$("[data-snap-rb]").forEach((b) =>
      b.addEventListener("click", async () => {
        const snap = b.dataset.snapRb;
        if (!confirm(`Roll back guest ${d.vmid} to snapshot '${snap}'?`)) return;
        try {
          await api(`/api/guests/${d.vmid}/snapshots/${encodeURIComponent(snap)}/rollback?confirm=true`, { method: "POST" });
          toast(`Rolled back to '${snap}'`, "ok");
          await refreshGuestDrawer();
        } catch (err) {
          toast(err.message, "err");
        }
      })
    );
    $$("[data-snap-del]").forEach((b) =>
      b.addEventListener("click", async () => {
        const snap = b.dataset.snapDel;
        if (!confirm(`Delete snapshot '${snap}' from guest ${d.vmid}?`)) return;
        try {
          await api(`/api/guests/${d.vmid}/snapshots/${encodeURIComponent(snap)}?confirm=true`, { method: "DELETE" });
          toast(`Deleted snapshot '${snap}'`, "ok");
          await refreshGuestDrawer();
        } catch (err) {
          toast(err.message, "err");
        }
      })
    );
  } else {
    body.innerHTML = `
      <div class="card">
        <div class="card-header"><h2>On-Demand Backup (VZDump)</h2></div>
        <form id="drawer-backup-form" class="form-grid">
          <div class="field">
            <label>Backup Mode</label>
            <select class="input" name="mode">
              <option value="snapshot">Snapshot (Zero downtime)</option>
              <option value="suspend">Suspend</option>
              <option value="stop">Stop (Cold backup)</option>
            </select>
          </div>
          <div class="field">
            <label>Compression</label>
            <select class="input" name="compress">
              <option value="zstd">ZSTD (Fast &amp; small)</option>
              <option value="gzip">GZIP</option>
              <option value="0">None</option>
            </select>
          </div>
          <div class="field full">
            <label>Target Backup Storage (optional — e.g. <code>pbs</code>)</label>
            <input class="input mono" name="storage" placeholder="e.g. pbs or local" />
          </div>
          <div class="field full"><button class="btn btn-primary btn-sm" type="submit" ${writeAttr()}>Trigger Backup Now</button></div>
        </form>
      </div>
      <div class="card">
        <div class="card-header"><h2>Clone Guest</h2></div>
        <form id="drawer-clone-form" class="form-grid">
          <div class="field">
            <label>New Guest Name *</label>
            <input class="input" name="new_name" required value="${esc((d.status?.name || "guest") + "-clone")}" />
          </div>
          <div class="field">
            <label>New VMID (blank = auto)</label>
            <input class="input mono" name="new_vmid" type="number" />
          </div>
          <div class="field full"><button class="btn btn-sm" type="submit" ${writeAttr()}>Clone Guest</button></div>
        </form>
      </div>
      <div class="card" style="border-color:rgba(244,63,94,0.45);">
        <div class="card-header"><h2 style="color:#fda4af;">Danger Zone</h2></div>
        <p class="muted small">Destroying a guest is permanent and blocked unless <code>ALLOW_DESTRUCTIVE=true</code> or <code>SAFETY_MODE=full</code>.</p>
        <button class="btn btn-danger btn-sm" id="btn-delete-guest" ${writeAttr()}>Permanently Destroy Guest ${d.vmid}</button>
      </div>`;

    $("#drawer-backup-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      try {
        const res = await api(`/api/guests/${d.vmid}/backup`, {
          method: "POST",
          body: JSON.stringify({
            mode: fd.get("mode"),
            compress: fd.get("compress"),
            storage: String(fd.get("storage") || "").trim() || null,
          }),
        });
        toast(`Backup task started: ${res.upid || "OK"}`, "ok");
      } catch (err) {
        toast(err.message, "err");
      }
    });

    $("#drawer-clone-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      try {
        const res = await api(`/api/guests/${d.vmid}/clone`, {
          method: "POST",
          body: JSON.stringify({
            new_name: String(fd.get("new_name")),
            new_vmid: fd.get("new_vmid") ? Number(fd.get("new_vmid")) : null,
          }),
        });
        toast(`Cloned ${d.vmid} -> ${res.new_vmid} (${res.new_name})`, "ok");
        state.insights = null;
        if (state.view === "guests") await loadGuests(true);
      } catch (err) {
        toast(err.message, "err");
      }
    });

    $("#btn-delete-guest").addEventListener("click", async () => {
      if (!confirm(`PERMANENTLY DESTROY guest ${d.vmid} and purge its disks?`)) return;
      try {
        await api(`/api/guests/${d.vmid}?confirm=true&purge=true`, { method: "DELETE" });
        toast(`Destroyed guest ${d.vmid}`, "ok");
        state.insights = null;
        closeGuestDrawer();
        if (state.view === "guests") await loadGuests(true);
      } catch (err) {
        toast(err.message, "err");
      }
    });
  }
}

/* ── View 5: Provisioning Studio ────────────────────────────────────── */
let provMode = "vm";

async function loadProvisioning() {
  const opts = await api("/api/provisioning/options");
  state.provOptions = opts;

  $("#vm-next-id").value = opts.next_vmid;
  $("#lxc-next-id").value = opts.next_vmid;

  const storageHtml = (opts.disk_storages || []).length
    ? opts.disk_storages
        .map((s) => `<option value="${esc(s.storage)}">${esc(s.storage)} (${esc(s.type)} · ${fmtBytes(s.avail)} free)</option>`)
        .join("")
    : `<option value="local-lvm">local-lvm</option>`;
  $("#vm-storage-select").innerHTML = storageHtml;
  $("#lxc-storage-select").innerHTML = storageHtml;

  const isoHtml =
    `<option value="">— No ISO (attach later / PXE) —</option>` +
    (opts.isos || [])
      .map((i) => `<option value="${esc(i.volid)}">${esc(i.volid.split("/").pop())} (${fmtBytes(i.size)})</option>`)
      .join("");
  $("#vm-iso-select").innerHTML = isoHtml;

  const tplHtml = (opts.vztmpls || []).length
    ? opts.vztmpls
        .map((t) => `<option value="${esc(t.volid)}">${esc(t.volid.split("/").pop())} (${fmtBytes(t.size)})</option>`)
        .join("")
    : `<option value="">No LXC templates found — download in Proxmox Storage → CT Templates</option>`;
  $("#lxc-template-select").innerHTML = tplHtml;

  const brHtml = (opts.bridges || ["vmbr0"])
    .map((b) => `<option value="${esc(b)}">${esc(b)}</option>`)
    .join("");
  $("#vm-bridge-select").innerHTML = brHtml;
  $("#lxc-bridge-select").innerHTML = brHtml;

  const vmtpls = opts.vm_templates || [];
  $("#vm-templates-list").innerHTML = vmtpls.length
    ? vmtpls
        .map(
          (t) => `
        <div class="list-row">
          <div>
            <div class="mono"><strong>${t.vmid}</strong> · ${esc(t.name)}</div>
            <div class="muted small">${t.maxcpu || 0} vCPU · ${fmtBytes(t.maxmem)} RAM</div>
          </div>
          <button class="btn btn-xs btn-primary" data-quick-clone="${t.vmid}" data-tpl-name="${esc(t.name)}" ${writeAttr()}>Clone →</button>
        </div>`
        )
        .join("")
    : `<div class="muted small">No VM templates detected on node ${esc(opts.node)}. Convert a cloud-init VM to a template for 5-second clones!</div>`;

  $$("[data-quick-clone]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      const srcId = Number(btn.dataset.quickClone);
      const newName = prompt(`New VM name cloned from ${btn.dataset.tplName}:`, `vm-${opts.next_vmid}`);
      if (!newName) return;
      try {
        const res = await api(`/api/guests/${srcId}/clone`, {
          method: "POST",
          body: JSON.stringify({ new_name: newName }),
        });
        toast(`Cloned template ${srcId} → VM ${res.new_vmid} (${res.new_name})`, "ok");
        state.insights = null;
        await loadProvisioning();
      } catch (err) {
        toast(err.message, "err");
      }
    })
  );

  renderProvPresets();
  updateTopChrome();
}

function renderProvPresets() {
  const list = provMode === "vm" ? VM_PRESETS : LXC_PRESETS;
  $("#prov-presets").innerHTML = list
    .map((p, idx) => `<button type="button" class="preset-chip" data-preset="${idx}">⚡ ${esc(p.label)}</button>`)
    .join("");
  $$("[data-preset]").forEach((btn) =>
    btn.addEventListener("click", () => {
      const preset = list[Number(btn.dataset.preset)];
      const form = provMode === "vm" ? $("#form-create-vm") : $("#form-create-lxc");
      Object.entries(preset).forEach(([k, v]) => {
        const input = form.elements[k];
        if (input) input.value = v;
      });
      toast(`Applied preset: ${preset.label}`, "info");
    })
  );
}

/* ── View 6: Storage, ZFS, Bind Mounts & PBS Backup Coverage ────────── */
async function loadStorage(force = true) {
  const [data, ins] = await Promise.all([api("/api/storage"), ensureInsights(force)]);
  state.storage = data;
  const byStorage = ins.storage_map?.by_storage || {};

  // 1. Storage Pools + Guest Count
  $("#storage-pools-grid").innerHTML = (data.storages || [])
    .map((st) => {
      const pct = st.total ? ((st.used / st.total) * 100).toFixed(1) : 0;
      const gList = byStorage[st.storage] || [];
      return `
        <div class="storage-card">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <strong>${esc(st.storage)}</strong>
            <div style="display:flex;gap:5px;">
              ${gList.length ? `<span class="tag tag-cyan">${gList.length} guests</span>` : ""}
              <span class="tag">${esc(st.type)}</span>
            </div>
          </div>
          <div class="muted small" style="margin:3px 0 8px;">${esc(st.content || "")}</div>
          <div class="mono small">${fmtBytes(st.used)} / ${fmtBytes(st.total)} (${pct}%)</div>
          <div class="bar-track"><div class="bar-fill ${barClass(pct)}" style="width:${pct}%"></div></div>
        </div>`;
    })
    .join("");

  // 2. ZFS Pools
  const zfs = data.zfs_pools || [];
  $("#zfs-card").classList.toggle("hidden", !zfs.length);
  if (zfs.length) {
    $("#zfs-pools-grid").innerHTML = zfs
      .map((z) => {
        const allocPct = z.size ? ((z.alloc / z.size) * 100).toFixed(1) : 0;
        const frag = z.frag ?? 0;
        const gList = byStorage[z.name] || [];
        return `
          <div class="storage-card">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
              <strong class="mono">${esc(z.name)}</strong>
              <span class="status-pill ${z.health === "ONLINE" ? "ok" : "err"}">${esc(z.health)}</span>
            </div>
            <div class="mono small">Allocated: ${fmtBytes(z.alloc)} / ${fmtBytes(z.size)} (${allocPct}%) · Free: ${fmtBytes(z.free)}</div>
            <div class="bar-track" style="margin-bottom:8px;"><div class="bar-fill ${barClass(allocPct)}" style="width:${allocPct}%"></div></div>
            <div style="display:flex;gap:8px;flex-wrap:wrap;font-size:12px;">
              <span class="tag ${frag >= 50 ? "tag-amber" : "tag-emerald"}">Frag: ${frag}%</span>
              <span class="tag">Dedup: ${z.dedup ?? 1}×</span>
              <span class="tag tag-cyan">${gList.length} guests</span>
            </div>
          </div>`;
      })
      .join("");
  }

  // 3. Physical Disks & Correct SSD Life Remaining %
  const disks = data.disks || [];
  $("#disks-table-wrap").innerHTML = disks.length
    ? `<table class="data-table">
        <thead><tr><th>Device</th><th>Model</th><th>Size</th><th>Used</th><th>SMART</th><th>Life Left</th></tr></thead>
        <tbody>${disks
          .map((d) => {
            const wear = typeof d.wearout === "number" ? d.wearout : null;
            const smartOk = ["PASSED", "OK"].includes(String(d.health).toUpperCase());
            return `
            <tr>
              <td class="mono">${esc(d.devpath)}</td>
              <td class="small">${esc(d.model || "—")}</td>
              <td class="mono small">${fmtBytes(d.size)}</td>
              <td><span class="tag">${esc(d.used || "unused")}</span></td>
              <td><span class="status-pill ${smartOk ? "ok" : "subtle"}">${esc(d.health || "N/A")}</span></td>
              <td>
                ${
                  wear !== null
                    ? `<div class="bar-wrap" style="min-width:74px;">
                        <div class="mono small">${wear}% left</div>
                        <div class="bar-track"><div class="bar-fill ${lifeBarClass(wear)}" style="width:${wear}%"></div></div>
                      </div>`
                    : `<span class="muted small">N/A</span>`
                }
              </td>
            </tr>`;
          })
          .join("")}</tbody>
      </table>`
    : `<div class="muted small">No physical disk telemetry returned.</div>`;

  // 4. LXC Bind Mounts
  const mounts = ins.storage_map?.bind_mounts || [];
  $("#bind-mounts-wrap").innerHTML = mounts.length
    ? mounts
        .map(
          (m) => `
        <div class="list-row clickable" data-st-inspect="${m.vmid}">
          <div style="min-width:0;">
            <div class="mono small"><strong>${m.vmid}</strong> · ${esc(m.name)} <span class="tag">${esc(m.id)}</span></div>
            <div class="mono small muted" style="word-break:break-all;">${esc(m.source)} → ${esc(m.target)}</div>
          </div>
          <span class="status-pill ${esc(m.status)}">${esc(m.status)}</span>
        </div>`
        )
        .join("")
    : `<div class="muted small">No LXC bind mounts (<code>mp0</code>/<code>mp1</code>) detected.</div>`;

  // 5. PBS Backup Coverage Matrix
  renderBackupCoverage();

  // 6. Jobs, Archives & Media
  const jobs = data.backup_jobs || [];
  const backups = data.backups || [];
  $("#backups-wrap").innerHTML = `
    <div style="margin-bottom:12px;">
      <div class="muted small" style="margin-bottom:6px;">SCHEDULED JOBS (${jobs.length})</div>
      ${
        jobs.length
          ? jobs
              .map(
                (j) => `
              <div class="list-row" style="margin-bottom:6px;">
                <div>
                  <div class="mono"><strong>${esc(j.schedule || "scheduled")}</strong> → ${esc(j.storage || "default")}</div>
                  <div class="muted small">Guests: ${esc(j.all ? "ALL" : j.vmid || "—")} · ${esc(j.mode || "snapshot")} (${esc(j.compress || "zstd")})</div>
                </div>
                <span class="status-pill ${j.enabled !== 0 ? "ok" : "stopped"}">${j.enabled !== 0 ? "ENABLED" : "PAUSED"}</span>
              </div>`
              )
              .join("")
          : `<div class="muted small">No PVE scheduled backup jobs found under /cluster/backup.</div>`
      }
    </div>
    <div>
      <div class="muted small" style="margin-bottom:6px;">RECENT ARCHIVES (${backups.length})</div>
      <div class="mini-list" style="max-height:200px;">
        ${
          backups.length
            ? backups
                .slice(0, 12)
                .map(
                  (b) => `
                <div class="list-row">
                  <div class="mono small" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(b.volid)}</div>
                  <div class="mono small muted" style="flex-shrink:0;">${fmtBytes(b.size)} · ${fmtTime(b.ctime)}</div>
                </div>`
                )
                .join("")
            : `<div class="muted small">No backup archives discovered.</div>`
        }
      </div>
    </div>`;

  const media = [...(data.isos || []), ...(data.templates || [])];
  $("#media-inventory").innerHTML = media.length
    ? `<div class="mini-list">${media
        .map(
          (m) => `
        <div class="list-row">
          <div style="min-width:0;">
            <span class="tag ${m.content === "iso" ? "tag-cyan" : "tag-violet"}">${esc((m.content || "").toUpperCase())}</span>
            <span class="mono small" style="margin-left:6px;word-break:break-all;">${esc(m.volid)}</span>
          </div>
          <span class="mono small muted" style="flex-shrink:0;">${fmtBytes(m.size)}</span>
        </div>`
        )
        .join("")}</div>`
    : `<div class="muted small">No ISOs or LXC templates found.</div>`;

  $$("[data-st-inspect]").forEach((el) =>
    el.addEventListener("click", () => openGuestDrawer(Number(el.dataset.stInspect)))
  );
}

function renderBackupCoverage() {
  const bc = state.insights?.backup_coverage;
  if (!bc) return;
  const s = bc.summary;
  $("#backup-coverage-sub").textContent = `${s.fresh} fresh (<48h) · ${s.stale + s.old} stale/old · ${s.none} unbacked · ${s.total_archives} total archive(s)`;

  const rows = bc.rows.filter((r) => {
    if (!state.backupFilter) return true;
    if (state.backupFilter === "stale") return r.backup_status === "stale" || r.backup_status === "old";
    return r.backup_status === state.backupFilter;
  });

  $("#backup-coverage-wrap").innerHTML = rows.length
    ? `<table class="data-table">
        <thead><tr><th>Guest</th><th>Type</th><th>State</th><th>Coverage</th><th>Last Backup</th><th>Archives</th><th>Storage</th><th class="right">Action</th></tr></thead>
        <tbody>${rows
          .map((r) => {
            const cls = r.backup_status === "fresh" ? "ok" : r.backup_status === "stale" ? "warn" : r.backup_status === "old" ? "err" : "subtle";
            return `
            <tr>
              <td class="mono"><span class="guest-name" data-bc-inspect="${r.vmid}">${r.vmid} · ${esc(r.name)}</span></td>
              <td><span class="tag ${r.type === "qemu" ? "tag-cyan" : "tag-violet"}">${r.type.toUpperCase()}</span></td>
              <td><span class="status-pill ${esc(r.status)}">${esc(r.status)}</span></td>
              <td><span class="status-pill ${cls}">${esc(r.backup_status.toUpperCase())}</span></td>
              <td class="mono small">${r.last_backup_ts ? `${fmtTime(r.last_backup_ts)} (${fmtAge(r.backup_age_hours)})` : "Never"}</td>
              <td class="mono small">${r.backup_count}</td>
              <td class="mono small">${esc(r.backup_storage || "—")}</td>
              <td class="right"><button class="btn btn-xs" data-bc-backup="${r.vmid}" data-bc-st="${esc(r.backup_storage || "")}" ${writeAttr()}>Backup Now</button></td>
            </tr>`;
          })
          .join("")}</tbody>
      </table>`
    : `<div class="muted small">No guests match this backup filter.</div>`;

  $$("[data-bc-inspect]").forEach((el) =>
    el.addEventListener("click", () => openGuestDrawer(Number(el.dataset.bcInspect)))
  );
  $$("[data-bc-backup]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      const vmid = Number(btn.dataset.bcBackup);
      const st = btn.dataset.bcSt || null;
      try {
        const res = await api(`/api/guests/${vmid}/backup`, {
          method: "POST",
          body: JSON.stringify({ storage: st, mode: "snapshot", compress: "zstd" }),
        });
        toast(`Backup started for ${vmid}: ${res.upid}`, "ok");
      } catch (err) {
        toast(err.message, "err");
      }
    })
  );
}

/* ── View 7: Tasks & MCP Audit ──────────────────────────────────────── */
async function openTaskLog(upid, node) {
  try {
    const logData = await api(
      `/api/tasks/log?upid=${encodeURIComponent(upid)}&node=${encodeURIComponent(node || "")}`
    );
    $("#task-log-panel").classList.remove("hidden");
    $("#task-log-title").textContent = logData.upid;
    $("#task-log-content").textContent = (logData.log || []).join("\n") || "(empty log)";
    $("#task-log-panel").scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (err) {
    toast(err.message, "err");
  }
}

async function loadTasksAndAudit() {
  const [tasksData, auditData] = await Promise.all([
    api(`/api/tasks?limit=35&errors_only=${state.tasksErrorsOnly}`),
    api(`/api/audit?limit=50${state.auditSource ? `&source=${state.auditSource}` : ""}`),
  ]);
  state.tasks = tasksData.tasks || [];
  state.audit = auditData.events || [];

  $("#tasks-list").innerHTML = state.tasks.length
    ? state.tasks
        .map(
          (t) => `
        <div class="list-row clickable" data-upid="${esc(t.upid)}" data-node="${esc(t.node || tasksData.node)}">
          <div>
            <div class="mono"><strong>${esc(t.type)}</strong> ${t.id ? `#${esc(t.id)}` : ""}</div>
            <div class="muted small">${esc(t.user || "")} · ${fmtTime(t.starttime)}</div>
          </div>
          <span class="status-pill ${t.status === "OK" ? "ok" : !t.status || t.status === "running" ? "warn" : "err"}">${esc(t.status || "running")}</span>
        </div>`
        )
        .join("")
    : `<div class="muted small">No tasks match this filter.</div>`;

  $$("[data-upid]").forEach((row) =>
    row.addEventListener("click", () => openTaskLog(row.dataset.upid, row.dataset.node))
  );

  $("#audit-list").innerHTML = state.audit.length
    ? state.audit
        .map(
          (e) => `
        <div class="list-row">
          <div style="min-width:0;">
            <div style="display:flex;align-items:center;gap:6px;">
              <span class="tag ${e.source === "mcp" ? "tag-violet" : "tag-cyan"}">${esc(e.source.toUpperCase())}</span>
              <span class="mono"><strong>${esc(e.action)}</strong></span>
              <span class="muted small mono">${esc(e.target)}</span>
            </div>
            <div class="muted small">${fmtTime(e.timestamp)} ${e.detail && e.status !== "ok" ? `· ${esc(e.detail)}` : ""}</div>
          </div>
          <span class="status-pill ${e.status === "ok" ? "ok" : "err"}">${esc(e.status)}</span>
        </div>`
        )
        .join("")
    : `<div class="muted small">No audit events recorded yet.</div>`;
}

/* ── View 8: Settings & MCP Hub ─────────────────────────────────────── */
function populateSettingsForm() {
  const s = state.settings;
  if (!s) return;
  const f = $("#form-settings");
  f.elements.proxmox_host.value = s.proxmox_host;
  f.elements.proxmox_token_id.value = s.proxmox_token_id;
  f.elements.proxmox_token_secret.value = "";
  f.elements.safety_mode.value = s.safety_mode;
  f.elements.proxmox_timeout.value = s.proxmox_timeout;
  if (f.elements.proxmox_ssh_user) f.elements.proxmox_ssh_user.value = s.proxmox_ssh_user || "root";
  if (f.elements.proxmox_ssh_port) f.elements.proxmox_ssh_port.value = s.proxmox_ssh_port || 22;
  if (f.elements.proxmox_ssh_key_path) f.elements.proxmox_ssh_key_path.value = s.proxmox_ssh_key_path || "";
  if (f.elements.idrac_host) f.elements.idrac_host.value = s.idrac_host || "";
  if (f.elements.idrac_user) f.elements.idrac_user.value = s.idrac_user || "root";
  if (f.elements.idrac_password) f.elements.idrac_password.value = "";
  if (f.elements.electricity_rate_kwh) f.elements.electricity_rate_kwh.value = s.electricity_rate_kwh ?? 0.26;
  if (f.elements.currency_symbol) f.elements.currency_symbol.value = s.currency_symbol || "£";
  f.elements.proxmox_verify_ssl.checked = Boolean(s.proxmox_verify_ssl);
  f.elements.allow_destructive.checked = Boolean(s.allow_destructive);
  f.elements.proxmox_demo_mode.checked = Boolean(s.proxmox_demo_mode);
  $("#saved-secret-hint").textContent = s.has_token_secret
    ? `Saved secret: ${s.proxmox_token_secret_masked}`
    : "No secret saved yet.";
}

function renderMcpPanel() {
  const m = state.mcpInfo;
  if (!m) return;
  $("#mcp-stdio-json").textContent = JSON.stringify(m.stdio_config, null, 2);
  $("#mcp-http-json").textContent = JSON.stringify(m.http_config, null, 2);
  $("#mcp-tool-count").textContent = m.tools.length;

  const riskClass = { read: "tag-emerald", write: "tag-amber", destructive: "tag-rose" };
  $("#mcp-tools-grid").innerHTML = m.tools
    .map(
      (t) => `
      <div class="tool-card">
        <div class="tool-head">
          <span class="tool-name">${esc(t.name)}</span>
          <span class="tag ${riskClass[t.risk] || ""}">${esc(t.risk.toUpperCase())}</span>
        </div>
        <div class="muted small">${esc(t.summary)}</div>
      </div>`
    )
    .join("");
}

/* ── Event Wiring ───────────────────────────────────────────────────── */
function bindEvents() {
  $$("#main-nav .nav-item, #mobile-dock button").forEach((btn) =>
    btn.addEventListener("click", () => navigate(btn.dataset.view))
  );
  $$(".subtab-bar .subtab").forEach((btn) =>
    btn.addEventListener("click", () => {
      const grp = btn.closest(".subtab-bar").dataset.group;
      navigate(grp, btn.dataset.sub);
    })
  );
  $("#btn-jump-tasks")?.addEventListener("click", () => navigate("system", "tasks"));
  $("#btn-refresh").addEventListener("click", () => loadActiveView(true));
  $("#btn-quick-provision").addEventListener("click", () => navigate("deploy", "community"));

  $$("#guest-layout-toggle .seg").forEach((btn) =>
    btn.addEventListener("click", () => {
      state.guestLayout = btn.dataset.layout;
      $$("#guest-layout-toggle .seg").forEach((b) => b.classList.toggle("active", b === btn));
      mh?.playChime("click");
      renderGuestsTable();
    })
  );

  // RRD Timeframe
  $$("#rrd-timeframe .seg").forEach((btn) =>
    btn.addEventListener("click", () => {
      state.rrdTimeframe = btn.dataset.tf;
      $$("#rrd-timeframe .seg").forEach((b) => b.classList.toggle("active", b === btn));
      loadOverview();
    })
  );

  // Guest filters, grouping, sorting & bulk actions
  $("#guest-search").addEventListener("input", (e) => {
    state.guestFilter.search = e.target.value;
    renderGuestsTable();
  });
  $$("#guest-type-filter .seg").forEach((btn) =>
    btn.addEventListener("click", () => {
      state.guestFilter.type = btn.dataset.val;
      $$("#guest-type-filter .seg").forEach((b) => b.classList.toggle("active", b === btn));
      renderGuestsTable();
    })
  );
  $$("#guest-status-filter .seg").forEach((btn) =>
    btn.addEventListener("click", () => {
      state.guestFilter.status = btn.dataset.val;
      $$("#guest-status-filter .seg").forEach((b) => b.classList.toggle("active", b === btn));
      renderGuestsTable();
    })
  );
  $("#guest-group-by").addEventListener("change", (e) => {
    state.guestFilter.groupBy = e.target.value;
    renderGuestsTable();
  });
  $$("th.sortable").forEach((th) =>
    th.addEventListener("click", () => {
      const k = th.dataset.sort;
      if (state.guestSort.key === k) {
        state.guestSort.dir = state.guestSort.dir === "asc" ? "desc" : "asc";
      } else {
        state.guestSort = { key: k, dir: k === "cpu" || k === "mem" ? "desc" : "asc" };
      }
      renderGuestsTable();
    })
  );
  $("#chk-select-all").addEventListener("change", (e) => {
    $$(".chk-guest").forEach((c) => {
      c.checked = e.target.checked;
      const id = Number(c.dataset.vmid);
      if (e.target.checked) state.selectedVmids.add(id);
      else state.selectedVmids.delete(id);
    });
    updateBulkBar();
  });
  $("#btn-clear-bulk").addEventListener("click", () => {
    state.selectedVmids.clear();
    $("#chk-select-all").checked = false;
    renderGuestsTable();
  });
  $$("[data-bulk]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      const action = btn.dataset.bulk;
      const vmids = [...state.selectedVmids];
      if (!vmids.length) return;
      if (!confirm(`Run '${action}' on ${vmids.length} selected guest(s) [${vmids.join(", ")}]?`)) return;
      try {
        const res = await api("/api/guests/bulk", {
          method: "POST",
          body: JSON.stringify({ vmids, action }),
        });
        toast(`Bulk ${action}: ${res.succeeded}/${res.requested} succeeded`, res.failed ? "err" : "ok");
        state.selectedVmids.clear();
        state.insights = null;
        await loadGuests(true);
      } catch (err) {
        toast(err.message, "err");
      }
    })
  );

  // Network IPAM filters
  $("#ipam-search").addEventListener("input", (e) => {
    state.ipamFilter.search = e.target.value;
    renderIpamTable();
  });
  $("#ipam-bridge-filter").addEventListener("change", (e) => {
    state.ipamFilter.bridge = e.target.value;
    renderIpamTable();
  });

  // Backup coverage filter
  $$("#backup-filter .seg").forEach((btn) =>
    btn.addEventListener("click", () => {
      state.backupFilter = btn.dataset.val;
      $$("#backup-filter .seg").forEach((b) => b.classList.toggle("active", b === btn));
      renderBackupCoverage();
    })
  );

  // Provisioning tabs & submit
  $$("#prov-mode-tabs .seg").forEach((btn) =>
    btn.addEventListener("click", () => {
      provMode = btn.dataset.pmode;
      $$("#prov-mode-tabs .seg").forEach((b) => b.classList.toggle("active", b === btn));
      $("#form-create-vm").classList.toggle("hidden", provMode !== "vm");
      $("#form-create-lxc").classList.toggle("hidden", provMode !== "lxc");
      renderProvPresets();
    })
  );

  $("#form-create-vm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      const res = await api("/api/provisioning/vm", {
        method: "POST",
        body: JSON.stringify({
          name: String(fd.get("name")),
          vmid: fd.get("vmid") ? Number(fd.get("vmid")) : null,
          cores: Number(fd.get("cores")),
          memory_mb: Number(fd.get("memory_mb")),
          disk_storage: String(fd.get("disk_storage")),
          disk_size_gb: Number(fd.get("disk_size_gb")),
          iso: String(fd.get("iso") || "") || null,
          bridge: String(fd.get("bridge")),
          vlan_tag: fd.get("vlan_tag") ? Number(fd.get("vlan_tag")) : null,
          ostype: String(fd.get("ostype")),
          bios: String(fd.get("bios")),
          tags: String(fd.get("tags") || ""),
          description: String(fd.get("description") || ""),
          qemu_agent: fd.get("qemu_agent") === "on",
          onboot: fd.get("onboot") === "on",
          start_after_create: fd.get("start_after_create") === "on",
        }),
      });
      toast(`Created QEMU VM ${res.vmid} (${res.name})`, "ok");
      state.insights = null;
      e.target.reset();
      await loadProvisioning();
    } catch (err) {
      toast(err.message, "err");
    }
  });

  $("#form-create-lxc").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      const res = await api("/api/provisioning/lxc", {
        method: "POST",
        body: JSON.stringify({
          hostname: String(fd.get("hostname")),
          vmid: fd.get("vmid") ? Number(fd.get("vmid")) : null,
          ostemplate: String(fd.get("ostemplate")),
          cores: Number(fd.get("cores")),
          memory_mb: Number(fd.get("memory_mb")),
          disk_storage: String(fd.get("disk_storage")),
          disk_size_gb: Number(fd.get("disk_size_gb")),
          bridge: String(fd.get("bridge")),
          ip_config: String(fd.get("ip_config") || "dhcp"),
          gateway: String(fd.get("gateway") || "").trim() || null,
          password: String(fd.get("password") || "").trim() || null,
          tags: String(fd.get("tags") || ""),
          unprivileged: fd.get("unprivileged") === "on",
          nesting: fd.get("nesting") === "on",
          onboot: fd.get("onboot") === "on",
          start_after_create: fd.get("start_after_create") === "on",
        }),
      });
      toast(`Created LXC ${res.vmid} (${res.hostname})`, "ok");
      state.insights = null;
      e.target.reset();
      await loadProvisioning();
    } catch (err) {
      toast(err.message, "err");
    }
  });

  // Tasks & Audit filters
  $("#tasks-errors-only").addEventListener("change", (e) => {
    state.tasksErrorsOnly = e.target.checked;
    loadTasksAndAudit();
  });
  $$("#audit-source-filter .seg").forEach((btn) =>
    btn.addEventListener("click", () => {
      state.auditSource = btn.dataset.val;
      $$("#audit-source-filter .seg").forEach((b) => b.classList.toggle("active", b === btn));
      loadTasksAndAudit();
    })
  );
  $("#btn-close-log").addEventListener("click", () => $("#task-log-panel").classList.add("hidden"));

  // Drawer
  $("#btn-close-drawer").addEventListener("click", closeGuestDrawer);
  $("#drawer-backdrop").addEventListener("click", closeGuestDrawer);
  $$("#drawer-tabs .seg").forEach((btn) =>
    btn.addEventListener("click", () => {
      state.drawer.tab = btn.dataset.dtab;
      $$("#drawer-tabs .seg").forEach((b) => b.classList.toggle("active", b === btn));
      renderDrawerTab();
    })
  );

  // Settings form & test
  $("#form-settings").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      const res = await api("/api/settings", {
        method: "PUT",
        body: JSON.stringify({
          proxmox_host: String(fd.get("proxmox_host")),
          proxmox_token_id: String(fd.get("proxmox_token_id") || ""),
          proxmox_token_secret: String(fd.get("proxmox_token_secret") || "") || null,
          proxmox_timeout: Number(fd.get("proxmox_timeout") || 30),
          proxmox_ssh_user: String(fd.get("proxmox_ssh_user") || "root"),
          proxmox_ssh_port: Number(fd.get("proxmox_ssh_port") || 22),
          proxmox_ssh_key_path: String(fd.get("proxmox_ssh_key_path") || ""),
          idrac_host: String(fd.get("idrac_host") || ""),
          idrac_user: String(fd.get("idrac_user") || "root"),
          idrac_password: String(fd.get("idrac_password") || "") || null,
          electricity_rate_kwh: Number(fd.get("electricity_rate_kwh") || 0.26),
          currency_symbol: String(fd.get("currency_symbol") || "£"),
          safety_mode: String(fd.get("safety_mode")),
          proxmox_verify_ssl: fd.get("proxmox_verify_ssl") === "on",
          allow_destructive: fd.get("allow_destructive") === "on",
          proxmox_demo_mode: fd.get("proxmox_demo_mode") === "on",
        }),
      });
      state.settings = res.settings;
      state.insights = null;
      updateTopChrome();
      populateSettingsForm();
      toast("Settings saved to .env", "ok");
    } catch (err) {
      toast(err.message, "err");
    }
  });

  $("#btn-test-conn").addEventListener("click", async () => {
    const box = $("#test-conn-result");
    box.className = "test-result";
    box.textContent = "Testing connection...";
    try {
      const res = await api("/api/settings/test", { method: "POST" });
      box.className = "test-result ok";
      box.innerHTML = `<strong>Connected!</strong> Proxmox VE ${esc(res.version?.version)} (${res.demo_mode ? "Demo Mode" : esc(res.host)}) — Nodes: <code>${esc((res.nodes || []).join(", "))}</code>`;
    } catch (err) {
      box.className = "test-result err";
      box.textContent = `Connection failed: ${err.message}`;
    }
  });

  $("#btn-copy-stdio").addEventListener("click", async () => {
    await navigator.clipboard.writeText($("#mcp-stdio-json").textContent);
    toast("Copied stdio MCP config JSON", "ok");
  });
  $("#btn-copy-http").addEventListener("click", async () => {
    await navigator.clipboard.writeText($("#mcp-http-json").textContent);
    toast("Copied Streamable HTTP MCP config JSON", "ok");
  });

  // Auto-refresh
  setInterval(() => {
    if (!$("#auto-refresh-chk").checked) return;
    if ((state.view === "command" || (state.view === "system" && state.subtabs.system === "tasks")) && !state.drawer.open) {
      loadActiveView(false);
    }
  }, 10000);
}

/* ── Boot ───────────────────────────────────────────────────────────── */
(async function init() {
  const sharedCtx = {
    $,
    $$,
    api,
    esc,
    fmtBytes,
    fmtTime,
    toast,
    state,
    isReadOnly,
    writeAttr,
    switchView,
    navigate,
    openGuestDrawer,
    ensureInsights,
    loadActiveView,
    renderRingGauge,
  };
  mh = initMetalheart(sharedCtx);
  extras = initExtras(sharedCtx);
  bindEvents();
  await loadSettings();
  await loadActiveView(true);
})();
