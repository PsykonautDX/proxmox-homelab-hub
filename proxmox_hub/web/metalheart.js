/* Metalheart / FFXIII Modules: Theme, Crystalline SFX, Crystarium Map, Uptime, Docker, Checkpoints, WoL & Dell iDRAC */

export function initMetalheart(ctx) {
  const { $, $$, api, esc, fmtBytes, fmtTime, toast, state, isReadOnly, writeAttr, openGuestDrawer, navigate, renderRingGauge } = ctx;

  /* ── 1. Dual Theme Switcher (Gran Pulse Obsidian / Cocoon Frost) ───── */
  let sfxEnabled = localStorage.getItem("pve_sfx") === "1";
  let audioCtx = null;

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("pve_theme", theme);
    const btn = $("#btn-theme-toggle");
    if (btn) btn.textContent = theme === "cocoon" ? "◈ Obsidian" : "◇ Frost";
  }

  function updateSfxBtn() {
    const btn = $("#btn-sfx-toggle");
    if (btn) btn.textContent = sfxEnabled ? "♪ SFX" : "🔇 SFX";
  }

  function playChime(kind = "click") {
    if (!sfxEnabled) return;
    try {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      if (!audioCtx) audioCtx = new AC();
      const now = audioCtx.currentTime;
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = "sine";
      if (kind === "shift") {
        osc.frequency.setValueAtTime(660, now);
        osc.frequency.exponentialRampToValueAtTime(1180, now + 0.09);
      } else {
        osc.frequency.setValueAtTime(920, now);
        osc.frequency.exponentialRampToValueAtTime(1320, now + 0.055);
      }
      gain.gain.setValueAtTime(0.035, now);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.12);
      osc.connect(gain);
      gain.connect(audioCtx.destination);
      osc.start(now);
      osc.stop(now + 0.13);
    } catch {
      /* ignore autoplay restrictions */
    }
  }

  applyTheme(localStorage.getItem("pve_theme") || "obsidian");
  updateSfxBtn();

  $("#btn-theme-toggle")?.addEventListener("click", () => {
    const cur = document.documentElement.getAttribute("data-theme") || "obsidian";
    applyTheme(cur === "obsidian" ? "cocoon" : "obsidian");
    playChime("shift");
  });
  $("#btn-sfx-toggle")?.addEventListener("click", () => {
    sfxEnabled = !sfxEnabled;
    localStorage.setItem("pve_sfx", sfxEnabled ? "1" : "0");
    updateSfxBtn();
    if (sfxEnabled) playChime("shift");
  });

  /* ── 2. Interactive Crystarium Constellation Map (Pure SVG) ────────── */
  let crystMode = "network";

  function renderCrystariumMap(insights, overview) {
    const wrap = $("#crystarium-map");
    if (!wrap) return;
    const guests = (insights?.guests || []).filter((g) => !g.template);
    const nodeName = overview?.nodes?.[0]?.node || "PROXMOX";
    if (!guests.length) {
      wrap.innerHTML = `<div class="muted small" style="padding:48px;text-align:center;">Waiting for cluster topology...</div>`;
      return;
    }

    const w = 1040, h = 330, cx = w / 2, cy = h / 2;

    // Group guests by bridge or by storage pool
    const groupsMap = new Map();
    for (const g of guests) {
      const key =
        crystMode === "storage"
          ? (g.storages_used?.[0] || "other")
          : (g.bridges?.[0] || "unassigned");
      if (!groupsMap.has(key)) groupsMap.set(key, []);
      groupsMap.get(key).push(g);
    }
    const groups = [...groupsMap.entries()].sort((a, b) => b[1].length - a[1].length).slice(0, 5);

    const lines = [];
    const hubs = [];
    const nodes = [];

    const hubRadiusX = 245, hubRadiusY = 92;
    groups.forEach(([gName, list], gIdx) => {
      const angle = (gIdx / Math.max(1, groups.length)) * Math.PI * 2 - Math.PI / 2;
      const hx = cx + Math.cos(angle) * hubRadiusX;
      const hy = cy + Math.sin(angle) * hubRadiusY;

      lines.push(`<line x1="${cx}" y1="${cy}" x2="${hx.toFixed(1)}" y2="${hy.toFixed(1)}" stroke="url(#filGrad)" stroke-width="1.8" stroke-opacity="0.7" />`);
      hubs.push(`
        <g>
          <polygon points="${hx},${hy - 13} ${hx + 13},${hy} ${hx},${hy + 13} ${hx - 13},${hy}" fill="rgba(56,232,255,0.22)" stroke="#38e8ff" stroke-width="1.4" />
          <text x="${hx}" y="${hy - 18}" text-anchor="middle" fill="var(--cyan)" font-size="11" font-family="var(--mono)" font-weight="700">${esc(gName)} (${list.length})</text>
        </g>`);

      // Fan guest crystals in an outer arc around their hub
      const maxShown = Math.min(list.length, 16);
      const spread = Math.min(Math.PI * 1.15, Math.max(0.7, maxShown * 0.22));
      for (let i = 0; i < maxShown; i++) {
        const g = list[i];
        const frac = maxShown === 1 ? 0 : i / (maxShown - 1) - 0.5;
        const a2 = angle + frac * spread;
        const ring = i % 2 === 0 ? 78 : 112;
        const gx = Math.max(36, Math.min(w - 36, hx + Math.cos(a2) * ring));
        const gy = Math.max(24, Math.min(h - 24, hy + Math.sin(a2) * (ring * 0.68)));

        const running = g.status === "running";
        const downSvc = running && g.service_up === false;
        const fill = downSvc ? "#fb5678" : running ? (g.type === "qemu" ? "#38e8ff" : "#2ee6a6") : "#64748b";
        const r = Math.min(10, Math.max(4.5, 4.5 + ((g.cpu_percent || 0) / 14)));

        lines.push(`<line x1="${hx.toFixed(1)}" y1="${hy.toFixed(1)}" x2="${gx.toFixed(1)}" y2="${gy.toFixed(1)}" stroke="${fill}" stroke-width="0.9" stroke-opacity="0.38" />`);
        nodes.push(`
          <g class="crystarium-node" data-cryst-vmid="${g.vmid}">
            <title>${esc(g.vmid)} · ${esc(g.name)} (${esc(g.type.toUpperCase())}, ${esc(g.status)}) — CPU ${g.cpu_percent}% · IP ${esc(g.primary_ip || "N/A")}</title>
            <circle cx="${gx.toFixed(1)}" cy="${gy.toFixed(1)}" r="${(r + 3.5).toFixed(1)}" fill="${fill}" fill-opacity="0.16" />
            <circle cx="${gx.toFixed(1)}" cy="${gy.toFixed(1)}" r="${r.toFixed(1)}" fill="${fill}" stroke="#ffffff" stroke-opacity="0.65" stroke-width="0.8" />
            <text x="${gx.toFixed(1)}" y="${(gy + r + 11).toFixed(1)}" text-anchor="middle" fill="var(--text-muted)" font-size="9" font-family="var(--mono)">${esc(String(g.name || g.vmid).slice(0, 12))}</text>
          </g>`);
      }
    });

    wrap.innerHTML = `
      <svg viewBox="0 0 ${w} ${h}" style="width:100%;height:auto;display:block;">
        <defs>
          <linearGradient id="filGrad" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stop-color="#38e8ff" />
            <stop offset="100%" stop-color="#c4b5fd" />
          </linearGradient>
          <radialGradient id="coreGlow" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stop-color="#38e8ff" stop-opacity="0.55" />
            <stop offset="100%" stop-color="#38e8ff" stop-opacity="0" />
          </radialGradient>
        </defs>
        <circle cx="${cx}" cy="${cy}" r="145" fill="none" stroke="var(--border)" stroke-dasharray="3 6" />
        <circle cx="${cx}" cy="${cy}" r="265" fill="none" stroke="var(--border)" stroke-dasharray="2 8" />
        ${lines.join("")}
        ${hubs.join("")}
        ${nodes.join("")}
        <g>
          <circle cx="${cx}" cy="${cy}" r="42" fill="url(#coreGlow)" />
          <polygon points="${cx},${cy - 22} ${cx + 22},${cy} ${cx},${cy + 22} ${cx - 22},${cy}" fill="var(--surface-solid)" stroke="#38e8ff" stroke-width="2" />
          <text x="${cx}" y="${cy + 4}" text-anchor="middle" fill="var(--cyan)" font-size="9.5" font-family="var(--mono)" font-weight="700">HOST</text>
          <text x="${cx}" y="${cy + 36}" text-anchor="middle" fill="var(--text-strong)" font-size="11" font-family="var(--mono)" font-weight="700">${esc(nodeName)}</text>
        </g>
      </svg>`;

    $$("[data-cryst-vmid]").forEach((el) =>
      el.addEventListener("click", () => {
        playChime("click");
        openGuestDrawer(Number(el.dataset.crystVmid));
      })
    );
  }

  $$("#crystarium-mode .seg").forEach((btn) =>
    btn.addEventListener("click", () => {
      crystMode = btn.dataset.cmode;
      $$("#crystarium-mode .seg").forEach((b) => b.classList.toggle("active", b === btn));
      playChime("click");
      if (state.insights && state.overview) renderCrystariumMap(state.insights, state.overview);
    })
  );

  /* ── 3. Live Service Uptime Monitor (Mini Uptime Kuma) ─────────────── */
  async function loadUptime(runFresh = false) {
    const data = await api(runFresh ? "/api/uptime/probe" : "/api/uptime", {
      method: runFresh ? "POST" : "GET",
    });
    const s = data.summary || { total: 0, up: 0, down: 0, avg_latency_ms: 0 };
    const upPct = s.total ? Math.round((s.up / s.total) * 100) : 100;

    $("#uptime-kpis").innerHTML = `
      <div class="kpi-card">
        <div><div class="kpi-label">Services Monitored</div><div class="kpi-value">${s.total}</div><div class="kpi-sub">Auto-detected &amp; bookmarked</div></div>
        ${renderRingGauge(upPct, s.down ? "#fb5678" : "#2ee6a6")}
      </div>
      <div class="kpi-card">
        <div><div class="kpi-label">Responding (UP)</div><div class="kpi-value" style="color:var(--emerald)">${s.up}</div><div class="kpi-sub">HTTP &lt; 500 healthy</div></div>
        ${renderRingGauge(upPct, "#2ee6a6")}
      </div>
      <div class="kpi-card">
        <div><div class="kpi-label">Unreachable (DOWN)</div><div class="kpi-value" style="color:${s.down ? "var(--rose)" : "var(--text-muted)"}">${s.down}</div><div class="kpi-sub">Fires webhook if enabled</div></div>
        ${renderRingGauge(s.total ? (s.down / s.total) * 100 : 0, "#fb5678")}
      </div>
      <div class="kpi-card">
        <div><div class="kpi-label">Avg Latency</div><div class="kpi-value">${s.avg_latency_ms} ms</div><div class="kpi-sub">Local LAN response time</div></div>
        ${renderRingGauge(Math.max(10, 100 - s.avg_latency_ms / 4), "#38e8ff")}
      </div>`;

    const rows = data.services || [];
    $("#uptime-tbody").innerHTML = rows.length
      ? rows
          .map(
            (r) => `
          <tr>
            <td><span class="guest-name" data-up-inspect="${r.vmid}">${r.vmid} · ${esc(r.name)}</span></td>
            <td class="mono small muted">${esc(r.url)}</td>
            <td><span class="status-pill ${r.up ? "ok" : "err"}">${r.up ? `UP ${r.status_code || ""}` : "DOWN"}</span></td>
            <td class="mono small">${r.latency_ms} ms</td>
            <td class="mono small"><strong>${r.uptime_pct}%</strong> <span class="muted">(${r.checks_count})</span></td>
            <td>
              <div class="uptime-bars">
                ${(r.history || []).map((t) => `<span class="u-tick ${t.up ? "" : "down"}" title="${t.up ? "UP" : "DOWN"} · ${t.ms}ms · ${fmtTime(t.ts)}"></span>`).join("")}
              </div>
            </td>
            <td class="right"><a class="btn btn-xs" href="${esc(r.url)}" target="_blank" rel="noopener">↗ Open</a></td>
          </tr>`
          )
          .join("")
      : `<tr><td colspan="7" class="muted" style="text-align:center;padding:24px;">Click "Probe All Services Now" to check your homelab web apps.</td></tr>`;

    $$("[data-up-inspect]").forEach((el) =>
      el.addEventListener("click", () => openGuestDrawer(Number(el.dataset.upInspect)))
    );
  }

  $("#btn-uptime-probe")?.addEventListener("click", async () => {
    playChime("click");
    toast("Probing all homelab web services...", "info");
    try {
      await loadUptime(true);
      state.insights = null;
      toast("Service uptime probe complete", "ok");
    } catch (err) {
      toast(err.message, "err");
    }
  });

  /* ── 4. Unified Docker Container Fleet ─────────────────────────────── */
  async function loadDocker() {
    $("#docker-tbody").innerHTML = `<tr><td colspan="6" class="muted" style="text-align:center;padding:24px;">Scanning Docker hosts via QEMU Agent &amp; SSH...</td></tr>`;
    const data = await api("/api/docker/containers");
    const s = data.summary || { hosts: 0, total: 0, running: 0, stopped: 0 };
    $("#docker-kpis").innerHTML = `
      <div class="kpi-card"><div><div class="kpi-label">Docker Hosts</div><div class="kpi-value">${s.hosts}</div><div class="kpi-sub">VMs &amp; LXCs scanned</div></div>${renderRingGauge(100, "#38e8ff")}</div>
      <div class="kpi-card"><div><div class="kpi-label">Running Containers</div><div class="kpi-value">${s.running}</div><div class="kpi-sub">of ${s.total} total</div></div>${renderRingGauge(s.total ? (s.running / s.total) * 100 : 0, "#2ee6a6")}</div>
      <div class="kpi-card"><div><div class="kpi-label">Stopped / Exited</div><div class="kpi-value">${s.stopped}</div><div class="kpi-sub">Inactive containers</div></div>${renderRingGauge(s.total ? (s.stopped / s.total) * 100 : 0, "#fbbf24")}</div>`;

    $("#docker-hosts-bar").innerHTML = (data.hosts || [])
      .map(
        (h) =>
          `<span class="tag ${h.ok ? "tag-emerald" : "tag-amber"}" title="${esc(h.note || "")}">${h.vmid} ${esc(h.name)} (${esc(h.method)}): ${h.ok ? `${h.count} containers` : esc(h.note || "unavailable")}</span>`
      )
      .join("");

    const rows = data.containers || [];
    $("#docker-tbody").innerHTML = rows.length
      ? rows
          .map(
            (c) => `
          <tr>
            <td class="mono"><strong>${esc(c.name)}</strong><div class="muted small">${esc(c.id)}</div></td>
            <td><span class="guest-name" data-dk-inspect="${c.host_vmid}">${c.host_vmid} · ${esc(c.host_name)}</span></td>
            <td class="mono small muted">${esc(c.image)}</td>
            <td><span class="status-pill ${c.state === "running" ? "ok" : "err"}">${esc(c.state)}</span><div class="muted small">${esc(c.status)}</div></td>
            <td class="mono small muted" style="max-width:220px;word-break:break-word;">${esc(c.ports || "—")}</td>
            <td class="right">
              <div class="action-group">
                <button class="btn btn-xs btn-ghost" data-dk-act="logs" data-dk-host="${c.host_vmid}" data-dk-name="${esc(c.name)}">Logs</button>
                ${
                  c.state === "running"
                    ? `<button class="btn btn-xs" data-dk-act="restart" data-dk-host="${c.host_vmid}" data-dk-name="${esc(c.name)}" ${writeAttr()}>↻</button>
                       <button class="btn btn-xs btn-danger" data-dk-act="stop" data-dk-host="${c.host_vmid}" data-dk-name="${esc(c.name)}" ${writeAttr()}>■</button>`
                    : `<button class="btn btn-xs btn-primary" data-dk-act="start" data-dk-host="${c.host_vmid}" data-dk-name="${esc(c.name)}" ${writeAttr()}>▶</button>`
                }
              </div>
            </td>
          </tr>`
          )
          .join("")
      : `<tr><td colspan="6" class="muted" style="text-align:center;padding:24px;">No Docker containers discovered yet. Ensure QEMU Guest Agent is running on Docker VMs (or SSH key auth is set for Docker LXCs).</td></tr>`;

    $$("[data-dk-inspect]").forEach((el) => el.addEventListener("click", () => openGuestDrawer(Number(el.dataset.dkInspect))));
    $$("[data-dk-act]").forEach((btn) =>
      btn.addEventListener("click", async () => {
        const action = btn.dataset.dkAct;
        const host_vmid = Number(btn.dataset.dkHost);
        const container_name = btn.dataset.dkName;
        playChime("click");
        try {
          const res = await api("/api/docker/action", {
            method: "POST",
            body: JSON.stringify({ host_vmid, container_name, action }),
          });
          if (action === "logs") {
            $("#docker-log-panel").classList.remove("hidden");
            $("#docker-log-title").textContent = `${host_vmid} / ${container_name} (logs)`;
            $("#docker-log-content").textContent = res.output || "(no logs)";
          } else {
            toast(`Docker ${action}: ${container_name}`, "ok");
            await loadDocker();
          }
        } catch (err) {
          toast(err.message, "err");
        }
      })
    );
  }

  $("#btn-docker-refresh")?.addEventListener("click", () => { playChime("click"); loadDocker(); });
  $("#btn-close-docker-log")?.addEventListener("click", () => $("#docker-log-panel").classList.add("hidden"));

  /* ── 5. Fleet Snapshot Checkpoints ─────────────────────────────────── */
  async function loadCheckpoints() {
    const data = await api("/api/snapshots/all");
    $("#all-snaps-count").textContent = data.total_snapshots || 0;
    const groups = data.tag_groups || [];
    $("#checkpoint-groups-grid").innerHTML = groups.length
      ? groups
          .slice(0, 10)
          .map(
            (g) => `
          <div class="storage-card">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
              <strong class="mono">#${esc(g.tag)}</strong>
              <span class="tag tag-cyan">${g.count} guests</span>
            </div>
            <button class="btn btn-xs btn-primary" data-chk-tag="${esc(g.tag)}" data-chk-cnt="${g.count}" ${writeAttr()}>📸 Checkpoint Group</button>
          </div>`
          )
          .join("")
      : `<div class="muted small">Tag your guests (e.g. <code>arr</code>, <code>ai</code>, <code>docker</code>) to create 1-click group checkpoints.</div>`;

    $$("[data-chk-tag]").forEach((btn) =>
      btn.addEventListener("click", async () => {
        const tag = btn.dataset.chkTag;
        if (!confirm(`Snapshot all ${btn.dataset.chkCnt} guest(s) tagged '${tag}'?`)) return;
        playChime("click");
        try {
          const res = await api("/api/snapshots/checkpoint", {
            method: "POST",
            body: JSON.stringify({ tag }),
          });
          toast(`Checkpoint '${res.snapname}': ${res.succeeded}/${res.requested} succeeded`, res.failed ? "err" : "ok");
          await loadCheckpoints();
        } catch (err) {
          toast(err.message, "err");
        }
      })
    );

    const snaps = data.snapshots || [];
    $("#all-snaps-wrap").innerHTML = snaps.length
      ? `<table class="data-table">
          <thead><tr><th>Guest</th><th>Snapshot</th><th>RAM</th><th>Description</th><th>Taken</th><th class="right">Inspect</th></tr></thead>
          <tbody>${snaps
            .map(
              (s) => `
            <tr>
              <td class="mono"><strong>${s.vmid}</strong> · ${esc(s.guest_name)}</td>
              <td class="mono"><span class="tag tag-cyan">${esc(s.snapname)}</span></td>
              <td>${s.vmstate ? '<span class="tag tag-emerald">Yes</span>' : '<span class="muted small">No</span>'}</td>
              <td class="small muted">${esc(s.description || "—")}</td>
              <td class="mono small">${fmtTime(s.snaptime)}</td>
              <td class="right"><button class="btn btn-xs" data-snap-inspect="${s.vmid}">Manage →</button></td>
            </tr>`
            )
            .join("")}</tbody>
        </table>`
      : `<div class="muted small">Zero snapshots currently exist across your ${data.total_guests} guests. Use a group checkpoint above before running updates!</div>`;

    $$("[data-snap-inspect]").forEach((b) => b.addEventListener("click", () => openGuestDrawer(Number(b.dataset.snapInspect))));
  }

  $("#btn-chk-refresh")?.addEventListener("click", () => { playChime("click"); loadCheckpoints(); });

  /* ── 6. Wake-on-LAN Portal ─────────────────────────────────────────── */
  async function loadWol() {
    const res = await api("/api/wol/devices");
    const devs = res.devices || [];
    $("#wol-devices-list").innerHTML = devs.length
      ? devs
          .map(
            (d) => `
          <div class="list-row">
            <div>
              <div style="display:flex;align-items:center;gap:8px;">
                <span class="svc-dot ${d.online === true ? "up" : d.online === false ? "down" : ""}"></span>
                <strong>${esc(d.name)}</strong>
                <span class="tag mono">${esc(d.mac)}</span>
                ${d.ip ? `<span class="tag tag-cyan mono">${esc(d.ip)}</span>` : ""}
              </div>
              <div class="muted small">${esc(d.notes || "")} ${d.last_woken_at ? `· Last woken ${fmtTime(d.last_woken_at)}` : ""}</div>
            </div>
            <div class="action-group">
              <button class="btn btn-xs btn-primary" data-wol-wake="${d.id}" ${writeAttr()}>⚡ Wake</button>
              <button class="btn btn-xs btn-danger" data-wol-del="${d.id}">✕</button>
            </div>
          </div>`
          )
          .join("")
      : `<div class="muted small">No physical LAN devices registered yet. Add a desktop, backup server, or NAS on the right.</div>`;

    $$("[data-wol-wake]").forEach((btn) =>
      btn.addEventListener("click", async () => {
        playChime("shift");
        try {
          const r = await api("/api/wol/wake", {
            method: "POST",
            body: JSON.stringify({ device_id: Number(btn.dataset.wolWake) }),
          });
          toast(`Magic Packet sent to ${r.device} (${r.mac})`, "ok");
          await loadWol();
        } catch (err) {
          toast(err.message, "err");
        }
      })
    );
    $$("[data-wol-del]").forEach((btn) =>
      btn.addEventListener("click", async () => {
        await api(`/api/wol/devices/${btn.dataset.wolDel}`, { method: "DELETE" });
        toast("Removed LAN device", "info");
        await loadWol();
      })
    );
  }

  $("#btn-wol-refresh")?.addEventListener("click", () => { playChime("click"); loadWol(); });
  $("#form-wol-add")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      await api("/api/wol/devices", {
        method: "POST",
        body: JSON.stringify({
          name: String(fd.get("name")),
          mac: String(fd.get("mac")),
          ip: String(fd.get("ip") || ""),
          broadcast: String(fd.get("broadcast") || "255.255.255.255"),
          notes: String(fd.get("notes") || ""),
        }),
      });
      toast("LAN device saved", "ok");
      e.target.reset();
      await loadWol();
    } catch (err) {
      toast(err.message, "err");
    }
  });

  /* ── 7. Dell PowerEdge R640 Hardware & Power Telemetry (iDRAC) ─────── */
  async function loadHardwareIdrac() {
    const data = await api("/api/hardware/idrac");
    const wrap = $("#idrac-wrap");
    if (!data.configured) {
      wrap.innerHTML = `
        <div class="card" style="text-align:center;padding:34px;">
          <h2>Dell PowerEdge iDRAC Redfish Telemetry</h2>
          <p class="muted" style="max-width:560px;margin:8px auto 16px;">${esc(data.message)}</p>
          <button class="btn btn-primary" id="btn-idrac-goto-settings">Configure iDRAC in Settings →</button>
        </div>`;
      $("#btn-idrac-goto-settings")?.addEventListener("click", () => navigate("system", "settings"));
      return;
    }
    if (!data.connected) {
      wrap.innerHTML = `<div class="card"><div class="test-result err">${esc(data.error || "Unable to connect to iDRAC.")}</div></div>`;
      return;
    }
    const p = data.power || {};
    wrap.innerHTML = `
      <div class="kpi-grid" style="margin-bottom:16px;">
        <div class="kpi-card">
          <div><div class="kpi-label">Live Power Draw</div><div class="kpi-value">${p.watts_now} W</div><div class="kpi-sub">Avg ${p.watts_avg ?? "—"}W · Peak ${p.watts_max ?? "—"}W</div></div>
          ${renderRingGauge(Math.min(100, (p.watts_now / 750) * 100), "#38e8ff")}
        </div>
        <div class="kpi-card">
          <div><div class="kpi-label">Est. Monthly Energy</div><div class="kpi-value">${p.monthly_kwh} kWh</div><div class="kpi-sub">${esc(p.currency)}${p.monthly_cost}/mo @ ${esc(p.currency)}${p.rate_kwh}/kWh</div></div>
          ${renderRingGauge(Math.min(100, p.monthly_kwh / 4), "#c4b5fd")}
        </div>
        <div class="kpi-card">
          <div><div class="kpi-label">Chassis Model</div><div class="kpi-value" style="font-size:16px;">${esc(data.model)}</div><div class="kpi-sub mono">Tag: ${esc(data.service_tag || "—")} · BIOS ${esc(data.bios_version || "—")}</div></div>
          <span class="status-pill ${data.health === "OK" ? "ok" : "warn"}">${esc(data.health)}</span>
        </div>
      </div>
      <div class="two-col">
        <div class="card">
          <div class="card-header"><h2>Thermal Sensors (°C)</h2></div>
          <div class="mini-list">
            ${(data.temperatures || [])
              .map(
                (t) => `
              <div class="list-row">
                <div><strong>${esc(t.name)}</strong><div class="muted small">Warn ≥ ${t.upper_warn ?? "—"}°C</div></div>
                <div class="bar-wrap" style="max-width:160px;">
                  <div class="mono small right">${t.celsius}°C</div>
                  <div class="bar-track"><div class="bar-fill ${t.celsius >= 75 ? "crit" : t.celsius >= 60 ? "warn" : "ok"}" style="width:${Math.min(100, t.celsius)}%"></div></div>
                </div>
              </div>`
              )
              .join("")}
          </div>
        </div>
        <div class="card">
          <div class="card-header"><h2>Cooling Fans &amp; PSUs</h2></div>
          <div class="mini-list">
            ${(data.fans || [])
              .map((f) => `<div class="list-row"><span class="mono small">${esc(f.name)}</span><span class="mono small tag tag-cyan">${f.rpm} RPM</span></div>`)
              .join("")}
            ${(data.psus || [])
              .map((ps) => `<div class="list-row"><span class="mono small">${esc(ps.name)}</span><span class="status-pill ${ps.health === "OK" ? "ok" : "err"}">${ps.watts ?? "—"}W / ${ps.capacity_watts ?? "—"}W · ${esc(ps.health)}</span></div>`)
              .join("")}
          </div>
        </div>
      </div>`;
  }

  /* ── 7b. Bare-Metal TrueNAS Host Monitoring (Strictly Read-Only) ────── */
  let tnDatasetPoolFilter = "";

  function renderTrueNasMini(tnRes) {
    const miniEl = $("#cmd-truenas-mini");
    const subEl = $("#cmd-truenas-sub");
    const pillEl = $("#cmd-truenas-pill");
    const openBtn = $("#btn-cmd-truenas-open");
    if (!miniEl) return;

    if (!tnRes || !tnRes.configured) {
      if (pillEl) pillEl.classList.add("hidden");
      if (openBtn) openBtn.classList.add("hidden");
      if (subEl) subEl.textContent = "Connect your bare-metal TrueNAS host for live read-only ZFS & disk health";
      miniEl.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;padding:6px 0;">
          <span class="muted small">${esc(tnRes?.message || "Optional: add TrueNAS host URL & API key in System → MCP & Settings.")}</span>
          <button class="btn btn-xs btn-primary" id="btn-cmd-tn-setup">Configure TrueNAS →</button>
        </div>`;
      $("#btn-cmd-tn-setup")?.addEventListener("click", () => navigate("system", "settings"));
      return;
    }

    if (!tnRes.connected) {
      if (pillEl) {
        pillEl.className = "status-pill err";
        pillEl.textContent = "UNREACHABLE";
      }
      if (openBtn && tnRes.url) {
        openBtn.href = tnRes.url;
        openBtn.classList.remove("hidden");
      }
      miniEl.innerHTML = `<div class="test-result err" style="margin:4px 0;">${esc(tnRes.error || "Unable to reach TrueNAS.")}</div>`;
      return;
    }

    const sys = tnRes.system || {};
    const sum = tnRes.summary || {};
    const pools = tnRes.pools || [];
    const degraded = sum.pools_degraded > 0;

    if (pillEl) {
      pillEl.className = `status-pill ${degraded || sum.critical_alerts > 0 ? "err" : sum.warning_alerts > 0 ? "warn" : "ok"}`;
      pillEl.textContent = degraded
        ? `${sum.pools_degraded} POOL DEGRADED`
        : sum.active_alerts > 0
          ? `${sum.active_alerts} ALERT${sum.active_alerts === 1 ? "" : "S"}`
          : "ALL POOLS ONLINE";
    }
    if (openBtn && tnRes.url) {
      openBtn.href = tnRes.url;
      openBtn.classList.remove("hidden");
    }
    if (subEl) {
      const mem = sys.memory || {};
      const arcTxt = mem.zfs_arc ? ` · ARC ${fmtBytes(mem.zfs_arc)}` : "";
      subEl.textContent = `${sys.hostname || "TrueNAS"} · ${sys.version || ""} · ${fmtBytes(sum.used_bytes)} / ${fmtBytes(sum.total_bytes)} (${sum.used_pct}%)${arcTxt}`;
    }

    miniEl.innerHTML = pools.length
      ? `<div class="storage-grid" style="grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px;">
          ${pools
            .map((p) => {
              const isOk = p.status === "ONLINE" && p.healthy;
              const fillCls = !isOk || p.used_pct >= 90 ? "crit" : p.used_pct >= 80 ? "warn" : "ok";
              return `
              <div class="storage-card" style="padding:10px 12px;cursor:pointer;" data-cmd-tn-jump="1">
                <div style="display:flex;justify-content:space-between;align-items:center;gap:6px;margin-bottom:5px;">
                  <strong class="mono">${esc(p.name)}</strong>
                  <span class="status-pill ${isOk ? "ok" : "err"}" style="padding:1px 7px;font-size:10px;">${esc(p.status)}</span>
                </div>
                <div class="mono small" style="display:flex;justify-content:space-between;margin-bottom:4px;">
                  <span>${fmtBytes(p.allocated)} / ${fmtBytes(p.size)}</span>
                  <strong>${p.used_pct}%</strong>
                </div>
                <div class="bar-track" style="height:6px;margin-bottom:6px;"><div class="bar-fill ${fillCls}" style="width:${Math.min(100, p.used_pct)}%"></div></div>
                <div class="muted small mono" style="display:flex;justify-content:space-between;gap:6px;">
                  <span style="color:${p.disks_with_errors ? "var(--rose)" : "inherit"}">${p.disks_with_errors ? `⚠ ${p.disks_with_errors}/${p.disks_total} disk err` : `${p.disks_total} disks OK`}</span>
                  <span>${p.vdev_summary ? esc(p.vdev_summary) : "ZFS"}</span>
                </div>
              </div>`;
            })
            .join("")}
        </div>`
      : `<div class="muted small">Connected to ${esc(sys.hostname || "TrueNAS")} — no ZFS pools reported.</div>`;

    $$("[data-cmd-tn-jump]").forEach((el) =>
      el.addEventListener("click", () => navigate("storage", "truenas"))
    );
  }

  async function loadTrueNAS(forceRefresh = false) {
    const wrap = $("#truenas-wrap");
    if (!wrap) return;
    wrap.innerHTML = `<div class="card"><div class="muted small" style="padding:24px;text-align:center;">Querying bare-metal TrueNAS host (read-only)...</div></div>`;

    const data = await api(`/api/storage/truenas${forceRefresh ? "?refresh=true" : ""}`);
    if (!data.configured) {
      wrap.innerHTML = `
        <div class="card" style="text-align:center;padding:34px;">
          <h2>Bare-Metal TrueNAS Monitoring (Read-Only)</h2>
          <p class="muted" style="max-width:600px;margin:8px auto 14px;">${esc(data.message)}</p>
          <div class="muted small" style="max-width:560px;margin:0 auto 18px;text-align:left;background:var(--surface-2);padding:12px 14px;border:1px solid var(--border);border-radius:8px;">
            <strong>Quick Setup (TrueNAS SCALE 25.10 Goldeye / 25.04+ / CORE):</strong><br/>
            1. In TrueNAS WebUI, click the top-right <strong>User icon → API Keys → Add</strong>.<br/>
            2. Copy the generated key and paste your TrueNAS URL (e.g. <code>https://your-truenas-ip</code>) &amp; key into <strong>06 System → MCP &amp; Settings</strong>.<br/>
            3. Click <strong>Save Settings</strong> → <strong>⚡ Test TrueNAS</strong>. Only read-only queries are ever executed.
          </div>
          <button class="btn btn-primary" id="btn-tn-goto-settings">Configure TrueNAS in Settings →</button>
        </div>`;
      $("#btn-tn-goto-settings")?.addEventListener("click", () => navigate("system", "settings"));
      return;
    }

    if (!data.connected) {
      wrap.innerHTML = `
        <div class="card">
          <div class="card-header">
            <h2>Bare-Metal TrueNAS Connection Error</h2>
            <div style="display:flex;gap:6px;">
              <button class="btn btn-xs" id="btn-tn-retry">↻ Retry</button>
              <button class="btn btn-xs btn-primary" id="btn-tn-fix-settings">Settings →</button>
            </div>
          </div>
          <div class="test-result err">${esc(data.error || "Unable to connect to TrueNAS.")}</div>
        </div>`;
      $("#btn-tn-retry")?.addEventListener("click", () => loadTrueNAS(true));
      $("#btn-tn-fix-settings")?.addEventListener("click", () => navigate("system", "settings"));
      return;
    }

    const sys = data.system || {};
    const mem = sys.memory || {};
    const sum = data.summary || {};
    const pools = data.pools || [];
    const disks = data.disks || [];
    const shares = data.shares || [];
    const services = data.services || [];
    const alerts = data.alerts || [];
    const links = data.proxmox_links || [];
    const allDatasets = data.datasets || [];

    const poolNames = [...new Set(pools.map((p) => p.name))];
    if (tnDatasetPoolFilter && !poolNames.includes(tnDatasetPoolFilter)) tnDatasetPoolFilter = "";

    const healthyPct = sum.pools_total ? Math.round((sum.pools_online / sum.pools_total) * 100) : 100;
    const memSubParts = [];
    if (mem.zfs_arc) memSubParts.push(`ARC ${fmtBytes(mem.zfs_arc)}`);
    if (mem.services) memSubParts.push(`Svc ${fmtBytes(mem.services)}`);
    if (mem.free) memSubParts.push(`Free ${fmtBytes(mem.free)}`);
    const loadStr = (sys.loadavg || []).length ? `Load ${(sys.loadavg || []).map((x) => Number(x).toFixed(2)).join(" / ")}` : "";

    wrap.innerHTML = `
      <div class="card" style="margin-bottom:14px;">
        <div class="card-header" style="margin-bottom:8px;">
          <div>
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
              <h2>${esc(sys.hostname || "TrueNAS")}</h2>
              <span class="tag tag-cyan mono">${esc(sys.version || "TrueNAS")}</span>
              <span class="tag tag-emerald">READ-ONLY</span>
              <span class="tag mono">${esc((data.transport || "ws").toUpperCase())}</span>
              ${sys.ecc_memory ? '<span class="tag tag-violet">ECC RAM</span>' : ""}
            </div>
            <p class="muted small mono" style="margin-top:4px;">
              ${esc(sys.cpu_model || "CPU")} (${sys.cores || "?"} threads) · Up ${esc(sys.uptime || "—")} ${loadStr ? `· ${esc(loadStr)}` : ""}
            </p>
          </div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;">
            ${data.url ? `<a class="btn btn-xs" href="${esc(data.url)}" target="_blank" rel="noopener">↗ Open TrueNAS UI</a>` : ""}
            <button class="btn btn-xs btn-primary" id="btn-tn-refresh">↻ Refresh</button>
          </div>
        </div>
        ${
          services.length
            ? `<div class="tag-row" style="gap:6px;">
                ${services
                  .map(
                    (sv) =>
                      `<span class="tag ${sv.state === "RUNNING" ? "tag-emerald" : ""}" title="${sv.enable ? "Enabled on boot" : "Manual"}">${esc(sv.service.toUpperCase())}: ${esc(sv.state)}</span>`
                  )
                  .join("")}
              </div>`
            : ""
        }
      </div>

      <div class="kpi-grid" style="margin-bottom:14px;">
        <div class="kpi-card">
          <div>
            <div class="kpi-label">ZFS Pools Health</div>
            <div class="kpi-value" style="color:${sum.pools_degraded ? "var(--rose)" : "var(--emerald)"}">${sum.pools_online} / ${sum.pools_total}</div>
            <div class="kpi-sub">${sum.pools_degraded ? `${sum.pools_degraded} pool(s) degraded` : "All pools ONLINE"}</div>
          </div>
          ${renderRingGauge(healthyPct, sum.pools_degraded ? "#fb5678" : "#2ee6a6")}
        </div>
        <div class="kpi-card">
          <div>
            <div class="kpi-label">Usable Storage</div>
            <div class="kpi-value">${fmtBytes(sum.used_bytes)}</div>
            <div class="kpi-sub">of ${fmtBytes(sum.total_bytes)} · ${fmtBytes(sum.free_bytes)} free</div>
          </div>
          ${renderRingGauge(sum.used_pct, sum.used_pct >= 90 ? "#fb5678" : sum.used_pct >= 80 ? "#fbbf24" : "#38e8ff")}
        </div>
        <div class="kpi-card">
          <div>
            <div class="kpi-label">Memory &amp; ZFS ARC</div>
            <div class="kpi-value">${fmtBytes(mem.total || sys.physmem)}</div>
            <div class="kpi-sub">${memSubParts.length ? esc(memSubParts.join(" · ")) : (sys.cpu_usage_pct != null ? `CPU ${sys.cpu_usage_pct}%` : "Physical RAM")}</div>
          </div>
          ${renderRingGauge(mem.used_pct ?? (sys.cpu_usage_pct ?? 0), "#c4b5fd")}
        </div>
        <div class="kpi-card">
          <div>
            <div class="kpi-label">Disks &amp; Alerts</div>
            <div class="kpi-value" style="color:${sum.disks_with_errors || sum.critical_alerts ? "var(--rose)" : "var(--text-strong)"}">${sum.active_alerts} alerts</div>
            <div class="kpi-sub">${sum.disks_total} disks · ${sum.disks_with_errors} with errors${sum.max_disk_temp != null ? ` · max ${sum.max_disk_temp}°C` : ""}</div>
          </div>
          ${renderRingGauge(sum.disks_total ? Math.max(0, 100 - (sum.disks_with_errors / sum.disks_total) * 100) : 100, sum.disks_with_errors ? "#fb5678" : "#2ee6a6")}
        </div>
      </div>

      <div class="card" style="margin-bottom:14px;">
        <div class="card-header">
          <h2>ZFS Storage Pools (${pools.length})</h2>
          <span class="muted small">Usable capacity, vdev topology, disk errors &amp; last scrub</span>
        </div>
        <div class="storage-grid">
          ${pools
            .map((p) => {
              const isOk = p.status === "ONLINE" && p.healthy;
              const fillCls = !isOk || p.used_pct >= 90 ? "crit" : p.used_pct >= 80 ? "warn" : "ok";
              const scrub = p.last_scrub || {};
              const errList = (p.errored_disks || [])
                .map((d) => `${d.name} (${d.status}: R:${d.read_errors} W:${d.write_errors} C:${d.checksum_errors})`)
                .join(", ");
              return `
              <div class="storage-card">
                <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:6px;">
                  <div>
                    <strong class="mono" style="font-size:14px;">${esc(p.name)}</strong>
                    ${p.vdev_summary ? `<span class="tag" style="margin-left:6px;">${esc(p.vdev_summary)}</span>` : ""}
                  </div>
                  <span class="status-pill ${isOk ? "ok" : "err"}">${esc(p.status)}</span>
                </div>
                <div class="mono small" style="display:flex;justify-content:space-between;margin-bottom:5px;">
                  <span>${fmtBytes(p.allocated)} / ${fmtBytes(p.size)} (${fmtBytes(p.free)} free)</span>
                  <strong>${p.used_pct}%</strong>
                </div>
                <div class="bar-track" style="margin-bottom:8px;"><div class="bar-fill ${fillCls}" style="width:${Math.min(100, p.used_pct)}%"></div></div>
                <div class="muted small mono" style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;">
                  <span style="color:${p.disks_with_errors ? "var(--rose)" : "var(--emerald)"}">
                    Disks w/ Errors: <strong>${p.disks_with_errors} of ${p.disks_total}</strong>
                  </span>
                  <span>Frag: ${p.fragmentation != null ? `${p.fragmentation}%` : "—"}</span>
                  <span>Scrub: ${esc(scrub.state || "—")}${scrub.end_time ? ` (${fmtTime(scrub.end_time)})` : ""}${scrub.errors ? ` · ${scrub.errors} err` : ""}</span>
                </div>
                ${errList ? `<div class="finding critical" style="margin-top:8px;padding:7px 9px;"><div class="mono small">${esc(errList)}</div></div>` : ""}
              </div>`;
            })
            .join("")}
        </div>
      </div>

      <div class="two-col" style="margin-bottom:14px;">
        <div class="card">
          <div class="card-header">
            <h2>Active TrueNAS Alerts (${alerts.length})</h2>
            <span class="muted small">${sum.critical_alerts} critical · ${sum.warning_alerts} warning</span>
          </div>
          <div class="findings-list" style="max-height:300px;overflow:auto;">
            ${
              alerts.length
                ? alerts
                    .map((a) => {
                      const cls = ["CRITICAL", "ALERT", "EMERGENCY", "ERROR"].includes(a.level)
                        ? "critical"
                        : a.level === "WARNING"
                          ? "warning"
                          : "info";
                      return `
                      <div class="finding ${cls}">
                        <div class="finding-title">[${esc(a.level)}] ${esc(a.klass || "System Alert")}</div>
                        <div class="finding-detail">${esc(a.message)}</div>
                        ${a.timestamp ? `<div class="muted small mono" style="margin-top:3px;">${fmtTime(a.timestamp)}</div>` : ""}
                      </div>`;
                    })
                    .join("")
                : `<div class="finding"><div class="finding-title">Zero active TrueNAS alerts</div><div class="finding-detail">No pending warnings or critical alerts on ${esc(sys.hostname || "TrueNAS")}.</div></div>`
            }
          </div>
        </div>

        <div class="card">
          <div class="card-header">
            <h2>Shares &amp; Proxmox Storage Links (${shares.length})</h2>
            <span class="muted small">${links.length} mounted in Proxmox</span>
          </div>
          ${
            links.length
              ? `<div style="margin-bottom:10px;">
                  <div class="muted small" style="margin-bottom:5px;">MOUNTED IN PROXMOX DATACENTER</div>
                  <div class="mini-list">
                    ${links
                      .map(
                        (lk) => `
                      <div class="list-row">
                        <div>
                          <span class="tag tag-cyan mono">${esc(lk.proxmox_storage)}</span>
                          <span class="tag">${esc(lk.type.toUpperCase())}</span>
                          <span class="mono small" style="margin-left:6px;">${esc(lk.export)}</span>
                        </div>
                        <span class="muted small mono">${esc(lk.content)}</span>
                      </div>`
                      )
                      .join("")}
                  </div>
                </div>`
              : ""
          }
          <div class="mini-list" style="max-height:240px;overflow:auto;">
            ${
              shares.length
                ? shares
                    .map(
                      (sh) => `
                    <div class="list-row">
                      <div style="min-width:0;">
                        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
                          <span class="tag ${sh.protocol === "NFS" ? "tag-cyan" : sh.protocol === "SMB" ? "tag-violet" : "tag-amber"}">${esc(sh.protocol)}</span>
                          <strong class="mono small">${esc(sh.name)}</strong>
                          ${sh.readonly ? '<span class="tag">RO</span>' : ""}
                          ${!sh.enabled ? '<span class="tag tag-rose">DISABLED</span>' : ""}
                        </div>
                        <div class="muted small mono">${esc(sh.path)}${sh.comment ? ` · ${esc(sh.comment)}` : ""}</div>
                      </div>
                    </div>`
                    )
                    .join("")
                : `<div class="muted small">No NFS, SMB, or iSCSI shares configured.</div>`
            }
          </div>
        </div>
      </div>

      <div class="card table-card" style="margin-bottom:14px;">
        <div class="card-header" style="padding:14px 18px 8px;">
          <h2>Physical Disks, Temperatures &amp; ZFS Errors (${disks.length})</h2>
          <span class="muted small">Sorted with errored drives first</span>
        </div>
        <table class="data-table">
          <thead>
            <tr>
              <th>Disk</th>
              <th>Pool</th>
              <th>Model &amp; Serial</th>
              <th>Size</th>
              <th>Temp</th>
              <th>ZFS State &amp; Errors (R/W/C)</th>
            </tr>
          </thead>
          <tbody>
            ${
              disks.length
                ? disks
                    .map((d) => {
                      const errSum = (d.read_errors || 0) + (d.write_errors || 0) + (d.checksum_errors || 0);
                      const hasErr = errSum > 0 || (d.zfs_status !== "ONLINE" && d.zfs_status !== "UNUSED");
                      const tempCls = d.temperature >= 55 ? "crit" : d.temperature >= 48 ? "warn" : "ok";
                      return `
                      <tr>
                        <td class="mono"><strong>${esc(d.name)}</strong> <span class="tag">${esc(d.type)}</span></td>
                        <td class="mono small">${d.pool ? `<span class="tag tag-cyan">${esc(d.pool)}</span>` : '<span class="muted">—</span>'}</td>
                        <td><div>${esc(d.model)}</div><div class="muted small mono">${esc(d.serial || "—")}</div></td>
                        <td class="mono small">${fmtBytes(d.size)}</td>
                        <td class="mono small">
                          ${
                            d.temperature != null
                              ? `<div class="bar-wrap" style="min-width:74px;"><div>${d.temperature}°C</div><div class="bar-track" style="height:5px;"><div class="bar-fill ${tempCls}" style="width:${Math.min(100, d.temperature)}%"></div></div></div>`
                              : '<span class="muted">—</span>'
                          }
                        </td>
                        <td class="mono small">
                          <span class="status-pill ${hasErr ? "err" : d.pool ? "ok" : "subtle"}">${esc(d.zfs_status)}</span>
                          ${errSum > 0 ? `<span style="color:var(--rose);margin-left:6px;">${d.read_errors}/${d.write_errors}/${d.checksum_errors}</span>` : ""}
                        </td>
                      </tr>`;
                    })
                    .join("")
                : `<tr><td colspan="6" class="muted" style="text-align:center;padding:20px;">No disks reported.</td></tr>`
            }
          </tbody>
        </table>
      </div>

      <div class="card table-card">
        <div class="card-header" style="padding:14px 18px 8px;">
          <h2>ZFS Datasets (${allDatasets.length})</h2>
          <select id="tn-ds-pool-filter" class="input mono" style="max-width:200px;">
            <option value="">All Pools</option>
            ${poolNames.map((pn) => `<option value="${esc(pn)}" ${tnDatasetPoolFilter === pn ? "selected" : ""}>${esc(pn)}</option>`).join("")}
          </select>
        </div>
        <table class="data-table">
          <thead><tr><th>Dataset</th><th>Mountpoint</th><th>Used</th><th>Available</th><th>Compression</th></tr></thead>
          <tbody id="tn-datasets-tbody"></tbody>
        </table>
      </div>`;

    const renderDatasets = () => {
      const filtered = tnDatasetPoolFilter
        ? allDatasets.filter((ds) => ds.pool === tnDatasetPoolFilter)
        : allDatasets;
      $("#tn-datasets-tbody").innerHTML = filtered.length
        ? filtered
            .map(
              (ds) => `
            <tr>
              <td class="mono small"><strong>${esc(ds.id)}</strong></td>
              <td class="mono small muted">${esc(ds.mountpoint || "—")}</td>
              <td class="mono small">${fmtBytes(ds.used)}</td>
              <td class="mono small">${fmtBytes(ds.available)}</td>
              <td class="mono small">${esc(ds.compression || "—")} ${ds.compressratio ? `(${esc(ds.compressratio)})` : ""}</td>
            </tr>`
            )
            .join("")
        : `<tr><td colspan="5" class="muted" style="text-align:center;padding:18px;">No matching datasets.</td></tr>`;
    };
    renderDatasets();

    $("#tn-ds-pool-filter")?.addEventListener("change", (e) => {
      tnDatasetPoolFilter = e.target.value;
      renderDatasets();
    });
    $("#btn-tn-refresh")?.addEventListener("click", () => {
      playChime("click");
      loadTrueNAS(true);
    });
  }

  /* ── 8. Command Overview High-Signal Widgets (Service Pulse + iDRAC + TrueNAS Mini-HUD) ── */
  async function loadCommandWidgets(runFreshProbe = false) {
    const [upRes, hwRes, tnRes] = await Promise.all([
      api(runFreshProbe ? "/api/uptime/probe" : "/api/uptime", { method: runFreshProbe ? "POST" : "GET" }).catch(() => null),
      api("/api/hardware/idrac").catch(() => null),
      api("/api/storage/truenas").catch(() => null),
    ]);

    // 1. Live Service Pulse & Quick Launch
    const pulseEl = $("#cmd-service-pulse");
    if (pulseEl && upRes) {
      const s = upRes.summary || { total: 0, up: 0, down: 0, avg_latency_ms: 0 };
      if ($("#cmd-uptime-sub")) {
        $("#cmd-uptime-sub").textContent = `${s.up}/${s.total} services responding · avg ${s.avg_latency_ms} ms`;
      }
      const list = upRes.services || [];
      pulseEl.innerHTML = list.length
        ? list
            .map(
              (sv) => `
            <a class="link-pill" href="${esc(sv.url)}" target="_blank" rel="noopener"
              style="${sv.up ? "" : "background:var(--rose-dim);color:var(--rose);border-color:var(--rose);"}"
              title="${esc(sv.url)} — ${sv.up ? `UP (${sv.latency_ms}ms)` : `DOWN (${sv.error || "unreachable"})`}">
              <span class="svc-dot ${sv.up ? "up" : "down"}"></span>
              <span>${esc(sv.name)}</span>
              <span class="muted" style="font-size:10px;">${sv.up ? `${Math.round(sv.latency_ms)}ms` : "DOWN"}</span>
            </a>`
            )
            .join("")
        : `<span class="muted small">No web services discovered yet.</span>`;
    }

    // 2. Dell R640 Power & Thermals Mini-HUD
    const hwEl = $("#cmd-idrac-mini");
    if (hwEl && hwRes) {
      if (!hwRes.configured || !hwRes.connected) {
        hwEl.innerHTML = `<div class="muted small" style="padding:10px 0;">${esc(hwRes.error || hwRes.message || "iDRAC not configured.")}</div>`;
      } else {
        const p = hwRes.power || {};
        const temps = (hwRes.temperatures || []).slice(0, 4);
        const fans = hwRes.fans || [];
        const avgRpm = fans.length ? Math.round(fans.reduce((a, f) => a + (Number(f.rpm) || 0), 0) / fans.length) : 0;
        if ($("#cmd-idrac-sub")) {
          $("#cmd-idrac-sub").textContent = `${hwRes.model || "Dell PowerEdge"} · Tag ${hwRes.service_tag || "—"} · Health ${hwRes.health || "OK"}`;
        }
        hwEl.innerHTML = `
          <div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-bottom:10px;">
            <div class="storage-card" style="padding:9px 11px;">
              <div class="kpi-label">Power Draw</div>
              <div class="mono" style="font-size:18px;font-weight:700;color:var(--text-strong);">${p.watts_now} W</div>
              <div class="muted small">Avg ${p.watts_avg ?? "—"}W</div>
            </div>
            <div class="storage-card" style="padding:9px 11px;">
              <div class="kpi-label">Est. Monthly</div>
              <div class="mono" style="font-size:18px;font-weight:700;color:var(--cyan);">${esc(p.currency || "£")}${p.monthly_cost}</div>
              <div class="muted small">${p.monthly_kwh} kWh/mo</div>
            </div>
            <div class="storage-card" style="padding:9px 11px;">
              <div class="kpi-label">Cooling Fans</div>
              <div class="mono" style="font-size:18px;font-weight:700;color:var(--text-strong);">${avgRpm || "—"}</div>
              <div class="muted small">${fans.length} fans · avg RPM</div>
            </div>
          </div>
          <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px 12px;">
            ${temps
              .map(
                (t) => `
              <div class="bar-wrap">
                <div class="mono small" style="display:flex;justify-content:space-between;"><span>${esc(t.name)}</span><strong>${t.celsius}°C</strong></div>
                <div class="bar-track" style="height:6px;"><div class="bar-fill ${t.celsius >= 75 ? "crit" : t.celsius >= 60 ? "warn" : "ok"}" style="width:${Math.min(100, t.celsius)}%"></div></div>
              </div>`
              )
              .join("")}
          </div>`;
      }
    }

    // 3. Bare-Metal TrueNAS Storage Mini-HUD
    renderTrueNasMini(tnRes);
  }

  $("#btn-cmd-probe")?.addEventListener("click", async () => {
    playChime("click");
    toast("Probing all homelab services...", "info");
    await loadCommandWidgets(true);
    toast("Service pulse refreshed", "ok");
  });
  $("#btn-cmd-uptime-all")?.addEventListener("click", () => navigate("fleet", "uptime"));
  $("#btn-cmd-idrac-all")?.addEventListener("click", () => navigate("system", "hardware"));
  $("#btn-cmd-truenas-all")?.addEventListener("click", () => navigate("storage", "truenas"));

  return {
    playChime,
    renderCrystariumMap,
    loadCommandWidgets,
    loadUptime,
    loadDocker,
    loadCheckpoints,
    loadWol,
    loadHardwareIdrac,
    loadTrueNAS,
  };
}
