/* Modular controllers for ISO Hub, Dual-Mode File Explorer, Host & Alerts, Backup Scheduler, and Ctrl+K Palette */

export function initExtras(ctx) {
  const { $, $$, api, esc, fmtBytes, fmtTime, toast, state, isReadOnly, writeAttr, switchView, openGuestDrawer, ensureInsights } = ctx;

  let isoCache = null;
  let aplCache = null;
  let csCatalog = null;
  let csSelected = null;
  let csPollTimer = null;
  let feCache = null;
  let geCurrentPath = "/";
  let geCurrentFile = null;
  let geSelectedVmid = null;

  /* ── 1. ISO & Template Hub ─────────────────────────────────────────── */
  async function loadIsoHub() {
    const [prov, isoData, aplData] = await Promise.all([
      api("/api/provisioning/options"),
      isoCache ? Promise.resolve(isoCache) : api("/api/isos/search?q=&web=false"),
      aplCache ? Promise.resolve(aplCache) : api("/api/appliances"),
    ]);
    isoCache = isoData;
    aplCache = aplData;

    // Pick ISO-capable storages (prefer "ISO" if present!)
    const stRes = await api("/api/files/storage");
    const isoStorages = (stRes.storages || []).filter((s) =>
      String(s.content || "").split(",").some((c) => ["iso", "vztmpl"].includes(c.trim()))
    );
    const preferred = isoStorages.find((s) => s.storage.toLowerCase() === "iso") || isoStorages[0];
    $("#iso-target-storage").innerHTML = isoStorages.length
      ? isoStorages
          .map(
            (s) =>
              `<option value="${esc(s.storage)}" ${preferred && preferred.storage === s.storage ? "selected" : ""}>${esc(s.storage)} (${fmtBytes(s.avail)} free)</option>`
          )
          .join("")
      : `<option value="local">local</option>`;

    renderIsoResults(isoData);
    $("#apl-count").textContent = aplData.total || (aplData.appliances || []).length;
    $("#apl-section").innerHTML =
      `<option value="">All Sections</option>` +
      (aplData.sections || []).map((sec) => `<option value="${esc(sec)}">${esc(sec)}</option>`).join("");
    renderAppliances();
    // Pre-fetch community scripts catalog in background for instant badge & Latest strip
    if (!csCatalog) {
      loadCommunityScripts(false).catch(() => {});
    }
  }

  /* ── 1b. Community-Scripts.org Catalog & Advanced LXC Wizard ───────── */
  async function loadCommunityScripts(force = false) {
    const q = ($("#cs-search")?.value || "").trim();
    const cat = $("#cs-category")?.value || "";
    const stype = $("#cs-type")?.value || "";
    const sort = $("#cs-sort")?.value || "latest";
    const includeDev = Boolean($("#cs-include-dev")?.checked);
    const params = new URLSearchParams({
      q,
      category: cat,
      type: stype,
      sort,
      include_dev: includeDev ? "true" : "false",
      refresh: force ? "true" : "false",
      limit: "90",
    });
    const data = await api(`/api/community-scripts?${params}`);
    csCatalog = data;
    $("#cs-count").textContent = data.total_catalog || 0;
    $("#cs-meta-label").textContent = `${data.matched} matching of ${data.total_catalog} scripts (${data.source})`;

    // Populate 28 official categories dropdown once
    const catSel = $("#cs-category");
    if (catSel && catSel.options.length <= 1) {
      catSel.innerHTML =
        `<option value="">All Categories (${(data.categories || []).length})</option>` +
        (data.categories || [])
          .map((c) => `<option value="${esc(c.name)}">${esc(c.name)}</option>`)
          .join("");
    }

    // Latest scripts horizontal strip
    $("#cs-latest-strip").innerHTML = (data.latest || [])
      .map(
        (s) => `
        <div class="latest-card" data-cs-open="${esc(s.slug)}" data-cs-type="${esc(s.type)}" data-cs-name="${esc(s.name)}" data-cs-desc="${esc(s.description)}" data-cs-docs="${esc(s.docs_url)}">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:5px;">
            ${s.logo ? `<img class="script-logo" src="${esc(s.logo)}" alt="" loading="lazy" onerror="this.style.display='none'" />` : ""}
            <strong class="small" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(s.name)}</strong>
            <span class="tag tag-emerald" style="margin-left:auto;">${esc((s.type || "lxc").toUpperCase())}</span>
          </div>
          <div class="muted small mono" style="margin-bottom:4px;">Added ${esc(s.script_created || "recent")}</div>
          <div class="muted small" style="display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;">${esc(s.description || "")}</div>
        </div>`
      )
      .join("");

    // Main catalog grid (entire card clickable + fast Quick Setup / Advanced button)
    const list = data.scripts || [];
    $("#cs-grid").innerHTML = list.length
      ? list
          .map(
            (s) => `
          <div class="tool-card clickable-card" style="display:flex;flex-direction:column;justify-content:space-between;gap:8px;cursor:pointer;"
            data-cs-open="${esc(s.slug)}" data-cs-type="${esc(s.type)}" data-cs-name="${esc(s.name)}" data-cs-desc="${esc(s.description)}" data-cs-docs="${esc(s.docs_url)}">
            <div>
              <div class="tool-head">
                <div style="display:flex;align-items:center;gap:8px;min-width:0;">
                  ${s.logo ? `<img class="script-logo" src="${esc(s.logo)}" alt="" loading="lazy" onerror="this.style.display='none'" />` : ""}
                  <span class="tool-name" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(s.name)}</span>
                </div>
                <div style="display:flex;gap:4px;flex-shrink:0;">
                  ${s.is_dev ? `<span class="tag tag-amber">DEV</span>` : ""}
                  <span class="tag ${s.type === "lxc" ? "tag-violet" : s.type === "vm" ? "tag-cyan" : "tag-emerald"}">${esc((s.type || "lxc").toUpperCase())}</span>
                </div>
              </div>
              <div class="muted small" style="display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;margin-bottom:6px;">${esc(s.description || "")}</div>
              <div class="tag-row">
                ${(s.categories || []).slice(0, 2).map((c) => `<span class="tag">${esc(c)}</span>`).join("")}
                ${s.script_created ? `<span class="tag">${esc(s.script_created)}</span>` : ""}
              </div>
            </div>
            <div style="display:flex;justify-content:space-between;align-items:center;gap:6px;padding-top:5px;border-top:1px solid var(--border);">
              <a class="muted small" href="${esc(s.docs_url)}" target="_blank" rel="noopener" data-stop-prop="1" style="text-decoration:none;">Docs ↗</a>
              <span class="tag tag-cyan">Configure &amp; Deploy →</span>
            </div>
          </div>`
          )
          .join("")
      : `<div class="muted small">No community scripts matched your filters.</div>`;

    $$("[data-stop-prop]").forEach((a) => a.addEventListener("click", (e) => e.stopPropagation()));
    $$("[data-cs-open]").forEach((el) =>
      el.addEventListener("click", () =>
        openCommunitySetup({
          slug: el.dataset.csOpen,
          type: el.dataset.csType || "lxc",
          name: el.dataset.csName || el.dataset.csOpen,
          description: el.dataset.csDesc || "",
          docs_url: el.dataset.csDocs || `https://community-scripts.org/scripts/${el.dataset.csOpen}`,
        })
      )
    );
  }

  async function openCommunitySetup(scriptMeta) {
    csSelected = { ...scriptMeta, script_url: null };
    if (csPollTimer) clearInterval(csPollTimer);
    $("#cs-modal-backdrop").classList.remove("hidden");
    const advDrawer = $("#cs-advanced-drawer");
    if (advDrawer) advDrawer.open = false;
    $("#cs-m-type").textContent = (scriptMeta.type || "lxc").toUpperCase();
    $("#cs-m-title").textContent = `${scriptMeta.name}`;
    $("#cs-m-desc").textContent = scriptMeta.description || "";
    $("#cs-m-defaults-pills").innerHTML = `<span class="tag tag-cyan">Loading upstream script defaults...</span>`;
    $("#cs-cmd-preview").textContent = "Fetching upstream defaults & host storages...";
    $("#cs-deploy-log-wrap").classList.add("hidden");
    $("#btn-cs-deploy").disabled = isReadOnly();

    // Immediately pre-fill slug as hostname while upstream defaults load
    const f = $("#form-cs-setup");
    f.elements.hostname.value = scriptMeta.slug;
    f.elements.tags.value = scriptMeta.slug;

    try {
      const setup = await api(
        `/api/community-scripts/${encodeURIComponent(scriptMeta.slug)}/defaults?type=${encodeURIComponent(scriptMeta.type || "lxc")}`
      );
      csSelected.script_url = setup.script_url;
      const d = setup.defaults || {};
      const hc = setup.host_context || {};

      // Upstream default spec badges right under header
      $("#cs-m-defaults-pills").innerHTML = [
        `<span class="tag tag-cyan">${esc(d.var_os || "debian")} ${esc(d.var_version || "13")}</span>`,
        `<span class="tag">${d.var_cpu || 2} vCPU</span>`,
        `<span class="tag">${d.var_ram || 2048} MB RAM</span>`,
        `<span class="tag">${d.var_disk || 8} GB Disk</span>`,
        `<span class="tag ${d.var_unprivileged !== 0 ? "tag-emerald" : "tag-amber"}">${d.var_unprivileged !== 0 ? "Unprivileged" : "Privileged"}</span>`,
        d.var_gpu === "yes" ? `<span class="tag tag-violet">GPU Default: Yes</span>` : "",
        setup.supports_alpine ? `<span class="tag">Alpine option supported</span>` : "",
      ].filter(Boolean).join("");

      // Populate container & template storage dropdowns from live host
      const cStorages = hc.container_storages || [];
      const prefCont =
        cStorages.find((s) => s.type === "zfspool") ||
        cStorages.find((s) => s.storage !== "local-lvm") ||
        cStorages[0];
      $("#cs-container-storage").innerHTML =
        `<option value="">— Auto / Default —</option>` +
        cStorages
          .map(
            (s) =>
              `<option value="${esc(s.storage)}" ${prefCont && prefCont.storage === s.storage ? "selected" : ""}>${esc(s.storage)} (${fmtBytes(s.avail)} free)</option>`
          )
          .join("");

      const tStorages = hc.template_storages || [];
      const prefTpl = tStorages.find((s) => s.storage.toLowerCase() === "iso") || tStorages[0];
      $("#cs-template-storage").innerHTML =
        `<option value="">— Auto —</option>` +
        tStorages
          .map(
            (s) =>
              `<option value="${esc(s.storage)}" ${prefTpl && prefTpl.storage === s.storage ? "selected" : ""}>${esc(s.storage)} (${fmtBytes(s.avail)} free)</option>`
          )
          .join("");

      const bridges = hc.bridges || ["vmbr0"];
      const prefBr = bridges.includes("vmbr0") ? "vmbr0" : bridges[0];
      $("#cs-bridge").innerHTML = bridges
        .map((b) => `<option value="${esc(b)}" ${b === prefBr ? "selected" : ""}>${esc(b)}</option>`)
        .join("");

      // Pre-populate ALL fields from upstream community-script defaults + live host context
      f.elements.ctid.value = hc.next_vmid || "";
      f.elements.hostname.value = scriptMeta.slug;
      f.elements.tags.value = d.var_tags || scriptMeta.slug;
      f.elements.os.value = ["debian", "ubuntu", "alpine", "rockylinux"].includes(d.var_os) ? d.var_os : "debian";
      f.elements.version.value = d.var_version || "13";
      f.elements.cpu.value = d.var_cpu || 2;
      f.elements.ram.value = d.var_ram || 2048;
      f.elements.disk.value = d.var_disk || 8;
      f.elements.net.value = "dhcp";
      f.elements.gateway.value = "";
      f.elements.vlan.value = "";
      f.elements.dns.value = "";
      f.elements.mtu.value = "";
      f.elements.ipv6_method.value = "";
      f.elements.password.value = "";
      f.elements.ssh_authorized_key.value = "";
      f.elements.unattended.checked = true;
      f.elements.unprivileged.checked = d.var_unprivileged !== 0;
      f.elements.nesting.checked = d.var_nesting !== 0;
      f.elements.keyctl.checked = d.var_keyctl !== 0;
      f.elements.fuse.checked = d.var_fuse === "yes";
      f.elements.tun.checked = d.var_tun === "yes";
      f.elements.gpu.checked = d.var_gpu === "yes";
      f.elements.ssh.checked = d.var_ssh === "yes";
      f.elements.protection.checked = false;
      f.elements.verbose.checked = false;
      f.elements.apt_cacher_ip.value = hc.apt_cacher_ip || "";
      f.elements.apt_cacher.checked = false;

      const shellBtn = $("#btn-cs-shell");
      if (hc.host_shell_url) {
        shellBtn.href = hc.host_shell_url;
        shellBtn.classList.remove("hidden");
      } else {
        shellBtn.classList.add("hidden");
      }

      await updateCommunityPreview();
    } catch (err) {
      $("#cs-m-defaults-pills").innerHTML = `<span class="tag tag-rose">Failed to load defaults</span>`;
      $("#cs-cmd-preview").textContent = `Error loading defaults: ${err.message}`;
    }
  }

  function collectCommunityOptions() {
    const f = $("#form-cs-setup");
    return {
      unattended: f.elements.unattended.checked,
      ctid: f.elements.ctid.value ? Number(f.elements.ctid.value) : null,
      hostname: f.elements.hostname.value.trim() || null,
      os: f.elements.os.value || null,
      version: f.elements.version.value.trim() || null,
      cpu: f.elements.cpu.value ? Number(f.elements.cpu.value) : null,
      ram: f.elements.ram.value ? Number(f.elements.ram.value) : null,
      disk: f.elements.disk.value ? Number(f.elements.disk.value) : null,
      tags: f.elements.tags.value.trim() || null,
      timezone: f.elements.timezone.value.trim() || null,
      container_storage: f.elements.container_storage.value || null,
      template_storage: f.elements.template_storage.value || null,
      bridge: f.elements.bridge.value || null,
      net: f.elements.net.value.trim() || "dhcp",
      gateway: f.elements.gateway.value.trim() || null,
      vlan: f.elements.vlan.value ? Number(f.elements.vlan.value) : null,
      dns: f.elements.dns.value.trim() || null,
      mtu: f.elements.mtu.value ? Number(f.elements.mtu.value) : null,
      ipv6_method: f.elements.ipv6_method.value || null,
      password: f.elements.password.value || null,
      ssh_authorized_key: f.elements.ssh_authorized_key.value.trim() || null,
      unprivileged: f.elements.unprivileged.checked,
      nesting: f.elements.nesting.checked,
      keyctl: f.elements.keyctl.checked,
      fuse: f.elements.fuse.checked ? "yes" : null,
      tun: f.elements.tun.checked ? "yes" : null,
      gpu: f.elements.gpu.checked ? "yes" : null,
      ssh: f.elements.ssh.checked ? "yes" : null,
      protection: f.elements.protection.checked ? "yes" : null,
      apt_cacher: f.elements.apt_cacher.checked ? "yes" : null,
      apt_cacher_ip: f.elements.apt_cacher.checked ? f.elements.apt_cacher_ip.value.trim() || null : null,
      verbose: f.elements.verbose.checked ? "yes" : null,
    };
  }

  let csLastRealCmd = "";
  async function updateCommunityPreview() {
    if (!csSelected) return;
    const opts = collectCommunityOptions();
    try {
      const res = await api("/api/community-scripts/preview", {
        method: "POST",
        body: JSON.stringify({
          slug: csSelected.slug,
          script_type: csSelected.type || "lxc",
          script_url: csSelected.script_url,
          options: opts,
        }),
      });
      csLastRealCmd = res.command;
      $("#cs-cmd-preview").textContent = res.display_command;
    } catch (err) {
      $("#cs-cmd-preview").textContent = `Preview error: ${err.message}`;
    }
  }

  function closeCommunityModal() {
    $("#cs-modal-backdrop").classList.add("hidden");
    if (csPollTimer) {
      clearInterval(csPollTimer);
      csPollTimer = null;
    }
  }

  function renderIsoResults(data) {
    const curated = data.curated || [];
    $("#iso-curated-grid").innerHTML = curated.length
      ? curated
          .map(
            (i) => `
          <div class="tool-card">
            <div class="tool-head">
              <span class="tool-name">${esc(i.name)}</span>
              <span class="tag tag-cyan">${esc(i.category)}</span>
            </div>
            <div class="muted small" style="margin-bottom:8px;">${esc(i.description)}</div>
            <div style="display:flex;justify-content:space-between;align-items:center;gap:6px;">
              <span class="mono small muted" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(i.filename)}</span>
              <button class="btn btn-xs btn-primary" data-dl-iso-url="${esc(i.url)}" data-dl-iso-fn="${esc(i.filename)}" ${writeAttr()}>⬇ Pull to PVE</button>
            </div>
          </div>`
          )
          .join("")
      : `<div class="muted small">No curated ISOs match that search.</div>`;

    const webRes = data.web_results || [];
    $("#iso-web-results-wrap").classList.toggle("hidden", !webRes.length && !data.web_error);
    if (data.web_error && !webRes.length) {
      $("#iso-web-results").innerHTML = `<div class="muted small">Web search unavailable: ${esc(data.web_error)}</div>`;
    } else if (webRes.length) {
      $("#iso-web-results").innerHTML = webRes
        .map(
          (w) => `
          <div class="list-row">
            <div style="min-width:0;">
              <div style="display:flex;align-items:center;gap:6px;">
                ${w.is_direct_iso ? `<span class="tag tag-emerald">DIRECT ISO</span>` : `<span class="tag">PAGE</span>`}
                <a class="guest-name" href="${esc(w.url)}" target="_blank" rel="noopener">${esc(w.title || w.url)}</a>
              </div>
              <div class="muted small" style="word-break:break-all;">${esc(w.url)}</div>
              ${w.snippet ? `<div class="muted small">${esc(w.snippet)}</div>` : ""}
            </div>
            <div class="action-group" style="flex-shrink:0;">
              <button class="btn btn-xs" data-use-iso-url="${esc(w.url)}">Probe</button>
              ${w.is_direct_iso ? `<button class="btn btn-xs btn-primary" data-dl-iso-url="${esc(w.url)}" data-dl-iso-fn="${esc(w.filename)}" ${writeAttr()}>⬇ Pull</button>` : ""}
            </div>
          </div>`
        )
        .join("");
    }

    $$("[data-dl-iso-url]").forEach((btn) =>
      btn.addEventListener("click", () => pullIsoToProxmox(btn.dataset.dlIsoUrl, btn.dataset.dlIsoFn))
    );
    $$("[data-use-iso-url]").forEach((btn) =>
      btn.addEventListener("click", () => {
        $("#iso-direct-url").value = btn.dataset.useIsoUrl;
        probeDirectUrl();
      })
    );
  }

  async function pullIsoToProxmox(url, filename) {
    const storage = $("#iso-target-storage").value || "local";
    try {
      const res = await api("/api/isos/download", {
        method: "POST",
        body: JSON.stringify({ storage, url, filename, content: "iso" }),
      });
      toast(`Proxmox started downloading ${filename} → ${storage} (${res.upid || "OK"})`, "ok");
    } catch (err) {
      toast(err.message, "err");
    }
  }

  async function probeDirectUrl() {
    const url = $("#iso-direct-url").value.trim();
    if (!url) return;
    const box = $("#iso-probe-result");
    box.className = "test-result";
    box.textContent = "Probing remote ISO URL...";
    try {
      const res = await api(`/api/isos/probe?url=${encodeURIComponent(url)}`);
      if (!res.ok) {
        box.className = "test-result err";
        box.textContent = `Probe failed: ${res.error || `HTTP ${res.status_code}`}`;
        return;
      }
      box.className = "test-result ok";
      box.innerHTML = `<strong>Reachable (${res.status_code})</strong> · File: <code>${esc(res.filename)}</code> · Size: <strong>${res.size_bytes ? fmtBytes(res.size_bytes) : "Unknown"}</strong> · ${res.is_direct_download ? '<span class="tag tag-emerald">Direct Download Ready</span>' : '<span class="tag tag-amber">HTML Page (paste direct .iso link)</span>'}`;
      $("#iso-direct-url").dataset.probedFilename = res.filename;
    } catch (err) {
      box.className = "test-result err";
      box.textContent = err.message;
    }
  }

  function renderAppliances() {
    const list = aplCache?.appliances || [];
    const q = ($("#apl-search").value || "").trim().toLowerCase();
    const sec = ($("#apl-section").value || "").trim().toLowerCase();
    const filtered = list.filter((a) => {
      if (sec && String(a.section || "").toLowerCase() !== sec) return false;
      if (q) {
        const hay = `${a.template || ""} ${a.os || ""} ${a.headline || ""} ${a.package || ""}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });

    $("#apl-list").innerHTML = filtered.length
      ? filtered
          .slice(0, 60)
          .map(
            (a) => `
          <div class="tool-card">
            <div class="tool-head">
              <span class="tool-name">${esc(a.package || a.os || a.template)}</span>
              <span class="tag ${a.section === "system" ? "tag-emerald" : "tag-violet"}">${esc(a.section || "system")}</span>
            </div>
            <div class="muted small" style="margin-bottom:8px;">${esc(a.headline || a.template)}</div>
            <div style="display:flex;justify-content:space-between;align-items:center;gap:6px;">
              <span class="mono small muted" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(a.template)}</span>
              <button class="btn btn-xs btn-primary" data-dl-apl="${esc(a.template)}" ${writeAttr()}>⬇ Pull</button>
            </div>
          </div>`
          )
          .join("")
      : `<div class="muted small">No templates match your search.</div>`;

    $$("[data-dl-apl]").forEach((btn) =>
      btn.addEventListener("click", async () => {
        const storage = $("#iso-target-storage").value || "local";
        const template = btn.dataset.dlApl;
        try {
          await api("/api/appliances/download", {
            method: "POST",
            body: JSON.stringify({ storage, template }),
          });
          toast(`Downloading LXC template ${template} → ${storage}`, "ok");
        } catch (err) {
          toast(err.message, "err");
        }
      })
    );
  }

  /* ── 2. Dual-Mode File Explorer ────────────────────────────────────── */
  async function loadFileExplorer() {
    const st = $("#fe-storage-select").value || "";
    const content = $("#fe-content-select").value || "";
    const data = await api(
      `/api/files/storage?${new URLSearchParams({ ...(st ? { storage: st } : {}), ...(content ? { content } : {}) })}`
    );
    feCache = data;

    if (!$("#fe-storage-select").options.length) {
      $("#fe-storage-select").innerHTML =
        `<option value="">All Storages (${(data.storages || []).length})</option>` +
        (data.storages || []).map((s) => `<option value="${esc(s.storage)}">${esc(s.storage)} (${esc(s.type)})</option>`).join("");
      const uploadable = (data.storages || []).filter((s) =>
        String(s.content || "").split(",").some((c) => ["iso", "vztmpl", "snippets"].includes(c.trim()))
      );
      $("#upload-storage-select").innerHTML = uploadable
        .map((s) => `<option value="${esc(s.storage)}">${esc(s.storage)}</option>`)
        .join("");
    }
    $("#file-summary-label").textContent = `${data.volumes.length} volume(s) · ${fmtBytes(data.total_bytes)}`;
    renderStorageFilesTable();
  }

  function renderStorageFilesTable() {
    const q = ($("#fe-search").value || "").trim().toLowerCase();
    const rows = (feCache?.volumes || []).filter((v) => {
      if (!q) return true;
      return `${v.volid} ${v.storage} ${v.content} ${v.vmid || ""}`.toLowerCase().includes(q);
    });
    $("#fe-storage-tbody").innerHTML = rows.length
      ? rows
          .slice(0, 150)
          .map(
            (v) => `
          <tr>
            <td class="mono small" style="word-break:break-all;"><strong>${esc(v.volid)}</strong></td>
            <td><span class="tag tag-cyan">${esc(v.storage)}</span></td>
            <td><span class="tag">${esc(v.content || v.format || "—")}</span></td>
            <td class="mono small">${v.vmid ? `<span class="guest-name" data-fe-inspect="${v.vmid}">${v.vmid}</span>` : "—"}</td>
            <td class="mono small">${fmtBytes(v.size)}</td>
            <td class="mono small muted">${fmtTime(v.ctime)}</td>
            <td class="right">
              <button class="btn btn-xs btn-danger" data-del-vol="${esc(v.volid)}" data-del-st="${esc(v.storage)}" ${writeAttr()}>Delete</button>
            </td>
          </tr>`
          )
          .join("")
      : `<tr><td colspan="7" class="muted" style="text-align:center;padding:24px;">No volumes found.</td></tr>`;

    $$("[data-fe-inspect]").forEach((el) =>
      el.addEventListener("click", () => openGuestDrawer(Number(el.dataset.feInspect)))
    );
    $$("[data-del-vol]").forEach((btn) =>
      btn.addEventListener("click", async () => {
        const volid = btn.dataset.delVol;
        const storage = btn.dataset.delSt;
        if (!confirm(`Permanently delete '${volid}' from storage '${storage}'?`)) return;
        try {
          await api(
            `/api/files/storage?storage=${encodeURIComponent(storage)}&volid=${encodeURIComponent(volid)}&confirm=true`,
            { method: "DELETE" }
          );
          toast(`Deleted ${volid}`, "ok");
          await loadFileExplorer();
        } catch (err) {
          toast(err.message, "err");
        }
      })
    );
  }

  async function loadGuestAgentVms() {
    $("#ge-dir-list").innerHTML = `<div class="muted small">Scanning running QEMU VMs for active QEMU Guest Agent...</div>`;
    const res = await api("/api/files/guest/vms");
    const vms = res.vms || [];
    $("#ge-vm-select").innerHTML = vms.length
      ? vms.map((v) => `<option value="${v.vmid}">${v.vmid} · ${esc(v.name)} (agent ${esc(v.agent_version)})</option>`).join("")
      : `<option value="">No running QEMU VMs with active Guest Agent</option>`;
    if (vms.length) {
      geSelectedVmid = vms[0].vmid;
      await browseGuestDir("/");
    } else {
      $("#ge-dir-list").innerHTML = `<div class="muted small">Start a QEMU VM with <code>qemu-guest-agent</code> installed to browse its filesystem without SSH.</div>`;
    }
  }

  async function browseGuestDir(path) {
    const vmid = Number($("#ge-vm-select").value);
    if (!vmid) return;
    geSelectedVmid = vmid;
    geCurrentPath = path || "/";
    $("#ge-path-input").value = geCurrentPath;
    $("#ge-dir-list").innerHTML = `<div class="muted small">Listing ${esc(geCurrentPath)}...</div>`;
    try {
      const res = await api(`/api/files/guest/${vmid}/ls?path=${encodeURIComponent(geCurrentPath)}`);
      const entries = res.entries || [];
      $("#ge-dir-list").innerHTML = entries.length
        ? entries
            .map(
              (e) => `
            <div class="list-row clickable" data-ge-entry="${esc(e.path)}" data-ge-dir="${e.is_dir ? "1" : "0"}">
              <div class="mono small">${e.is_dir ? "📁" : "📄"} <strong>${esc(e.name)}</strong></div>
              <div class="mono small muted">${e.is_dir ? "DIR" : fmtBytes(e.size)}</div>
            </div>`
            )
            .join("")
        : `<div class="muted small">Empty directory.</div>`;

      $$("[data-ge-entry]").forEach((row) =>
        row.addEventListener("click", () => {
          if (row.dataset.geDir === "1") browseGuestDir(row.dataset.geEntry);
          else readGuestFile(row.dataset.geEntry);
        })
      );
    } catch (err) {
      $("#ge-dir-list").innerHTML = `<div class="test-result err">${esc(err.message)}</div>`;
    }
  }

  async function readGuestFile(path) {
    if (!geSelectedVmid) return;
    geCurrentFile = path;
    $("#ge-editor-path").textContent = `Loading ${path}...`;
    try {
      const res = await api(`/api/files/guest/${geSelectedVmid}/read?path=${encodeURIComponent(path)}`);
      $("#ge-editor-path").textContent = `${path}${res.truncated ? " (truncated)" : ""}`;
      $("#ge-editor-content").value = res.content || "";
      $("#btn-ge-save").classList.remove("hidden");
      $("#btn-ge-save").disabled = isReadOnly();
    } catch (err) {
      $("#ge-editor-path").textContent = path;
      toast(err.message, "err");
    }
  }

  /* ── 3. Host Maintenance, APT Updates, Syslog, Firewall & Alerts ───── */
  async function loadHostMaintenance() {
    const svc = ($("#syslog-service").value || "").trim();
    const [maint, alertData] = await Promise.all([
      api(`/api/host/maintenance?syslog_limit=120${svc ? `&syslog_service=${encodeURIComponent(svc)}` : ""}`),
      api("/api/alerts/config"),
    ]);

    const updates = maint.apt_updates || [];
    $("#apt-count").textContent = updates.length;
    $("#apt-updates-wrap").innerHTML = updates.length
      ? updates
          .map(
            (u) => `
          <div class="list-row">
            <div style="min-width:0;">
              <div class="mono small"><strong>${esc(u.Package)}</strong> <span class="tag">${esc(u.Origin || "")}</span></div>
              <div class="muted small">${esc(u.Title || "")}</div>
            </div>
            <div class="mono small right" style="flex-shrink:0;">
              <span class="muted">${esc(u.OldVersion || "—")}</span> → <span style="color:var(--cyan);">${esc(u.Version)}</span>
            </div>
          </div>`
          )
          .join("")
      : `<div class="finding"><div class="finding-title">Host is fully up to date</div><div class="finding-detail">No pending APT updates reported on ${esc(maint.node)}.</div></div>`;

    $("#host-syslog").textContent = (maint.syslog || []).join("\n") || "(no syslog lines returned)";

    const fw = maint.firewall || {};
    const fwEnabled = Boolean(fw.options?.enable);
    $("#fw-status-pill").textContent = fwEnabled ? "ENABLED" : "DISABLED";
    $("#fw-status-pill").className = `status-pill ${fwEnabled ? "ok" : "subtle"}`;
    const allRules = [
      ...(fw.cluster_rules || []).map((r) => ({ ...r, _scope: "Cluster" })),
      ...(fw.node_rules || []).map((r) => ({ ...r, _scope: maint.node })),
    ];
    $("#fw-rules-wrap").innerHTML = allRules.length
      ? `<table class="data-table">
          <thead><tr><th>Scope</th><th>Dir</th><th>Action</th><th>Proto/Port</th><th>Source</th><th>Comment</th></tr></thead>
          <tbody>${allRules
            .map(
              (r) => `
            <tr>
              <td><span class="tag">${esc(r._scope)}</span></td>
              <td class="mono small">${esc(r.type)}</td>
              <td><span class="status-pill ${r.action === "ACCEPT" ? "ok" : "err"}">${esc(r.action)}</span></td>
              <td class="mono small">${esc(r.proto || "any")}:${esc(r.dport || "*")}</td>
              <td class="mono small">${esc(r.source || "any")}</td>
              <td class="small muted">${esc(r.comment || "")}</td>
            </tr>`
            )
            .join("")}</tbody>
        </table>`
      : `<div class="muted small">No cluster or node firewall rules defined (${fwEnabled ? "Firewall enabled" : "Datacenter firewall is currently disabled"}).</div>`;

    // Populate alert webhook form
    const cfg = alertData.config;
    const f = $("#form-alerts");
    f.elements.provider.value = cfg.provider || "discord";
    f.elements.webhook_url.value = cfg.webhook_url || "";
    f.elements.min_health_score.value = cfg.min_health_score ?? 75;
    f.elements.max_storage_pct.value = cfg.max_storage_pct ?? 85;
    f.elements.max_cpu_pct.value = cfg.max_cpu_pct ?? 90;
    f.elements.max_ram_pct.value = cfg.max_ram_pct ?? 90;
    f.elements.enabled.checked = Boolean(cfg.enabled);
    f.elements.notify_failed_tasks.checked = Boolean(cfg.notify_failed_tasks);
  }

  /* ── 4. Global Command Palette (Ctrl+K) ────────────────────────────── */
  function openCmdK() {
    $("#cmdk-backdrop").classList.remove("hidden");
    $("#cmdk-input").value = "";
    $("#cmdk-input").focus();
    renderCmdK("");
  }

  function closeCmdK() {
    $("#cmdk-backdrop").classList.add("hidden");
  }

  function renderCmdK(query) {
    const q = query.trim().toLowerCase();
    const items = [
      { kind: "01 CMD", title: "Command Overview & Crystarium Map", sub: "KPIs, constellation map, RRD & Actionable Doctor", run: () => ctx.navigate("command") },
      { kind: "02 FLT", title: "VMs & LXCs Fleet", sub: "Cards/Table, tags, bulk actions & console", run: () => ctx.navigate("fleet", "guests") },
      { kind: "02 FLT", title: "Docker Containers (Unified)", sub: "Inspect & control containers across Docker VMs/LXCs", run: () => ctx.navigate("fleet", "docker") },
      { kind: "02 FLT", title: "Service Uptime Monitor", sub: "Live HTTP latency & 24h uptime spark-bars", run: () => ctx.navigate("fleet", "uptime") },
      { kind: "02 FLT", title: "Capacity & Right-Sizing", sub: "Oversubscription & top I/O talkers", run: () => ctx.navigate("fleet", "capacity") },
      { kind: "02 FLT", title: "Snapshot Checkpoints", sub: "1-click group snapshot checkpoints", run: () => ctx.navigate("fleet", "checkpoints") },
      { kind: "03 DEP", title: "Community Scripts (780+)", sub: "Latest scripts & Advanced LXC Setup wizard", run: () => ctx.navigate("deploy", "community") },
      { kind: "03 DEP", title: "Provisioning Studio", sub: "Create new QEMU VM or LXC container", run: () => ctx.navigate("deploy", "provision") },
      { kind: "03 DEP", title: "ISO Library & Web Search", sub: "Search mirrors/GitHub & pull directly to Proxmox", run: () => ctx.navigate("deploy", "isos") },
      { kind: "04 STR", title: "Storage, ZFS & SMART", sub: "Pools, ZFS fragmentation, SSD life & bind mounts", run: () => ctx.navigate("storage", "pools") },
      { kind: "04 STR", title: "PBS Backup Coverage & Scheduler", sub: "Coverage matrix & scheduled job builder", run: () => ctx.navigate("storage", "backups") },
      { kind: "04 STR", title: "Dual-Mode File Explorer", sub: "Proxmox volumes & QEMU Guest Agent FS/Exec", run: () => ctx.navigate("storage", "files") },
      { kind: "05 NET", title: "Network & IPAM Matrix", sub: "Bridges, NICs, MACs & free IPs", run: () => ctx.navigate("network", "ipam") },
      { kind: "05 NET", title: "Wake-on-LAN Portal", sub: "Wake physical LAN desktops, NAS & backup servers", run: () => ctx.navigate("network", "wol") },
      { kind: "06 SYS", title: "Dell R640 Power & Thermals (iDRAC)", sub: "Watts draw, monthly cost, temps & fan RPMs", run: () => ctx.navigate("system", "hardware") },
      { kind: "06 SYS", title: "APT Updates & Host Syslog", sub: "Pending PVE updates & live syslog", run: () => ctx.navigate("system", "maintenance") },
      { kind: "06 SYS", title: "Tasks & MCP Audit Log", sub: "Proxmox tasks & SQLite MCP audit trail", run: () => ctx.navigate("system", "tasks") },
      { kind: "06 SYS", title: "Smart Alert Webhooks", sub: "Discord, Slack & ntfy alert thresholds", run: () => ctx.navigate("system", "alerts") },
      { kind: "06 SYS", title: "MCP & Settings Hub", sub: "API token, SSH, iDRAC & 44 MCP tools", run: () => ctx.navigate("system", "settings") },
    ];

    for (const g of state.insights?.guests || []) {
      items.push({
        kind: g.type.toUpperCase(),
        title: `${g.vmid} · ${g.name}`,
        sub: `${g.status} · ${(g.all_ips || []).join(", ") || "no IP"} · ${g.tags || ""}`,
        run: () => openGuestDrawer(g.vmid),
      });
      if (g.service_url) {
        items.push({
          kind: "APP ↗",
          title: `Open ${g.service_label || g.name}`,
          sub: g.service_url,
          run: () => window.open(g.service_url, "_blank", "noopener"),
        });
      }
    }

    const matched = items.filter((i) => !q || `${i.kind} ${i.title} ${i.sub}`.toLowerCase().includes(q)).slice(0, 14);
    $("#cmdk-results").innerHTML = matched.length
      ? matched
          .map(
            (m, idx) => `
          <div class="cmdk-item" data-cmdk-idx="${idx}">
            <div>
              <div><strong>${esc(m.title)}</strong></div>
              <div class="muted small mono">${esc(m.sub)}</div>
            </div>
            <span class="tag tag-cyan">${esc(m.kind)}</span>
          </div>`
          )
          .join("")
      : `<div class="muted small" style="padding:14px;">No matches for "${esc(query)}".</div>`;

    $$("[data-cmdk-idx]").forEach((el) =>
      el.addEventListener("click", () => {
        closeCmdK();
        matched[Number(el.dataset.cmdkIdx)].run();
      })
    );
  }

  /* ── Wire Extra Events ─────────────────────────────────────────────── */
  // ISO Hub events
  $$("#iso-hub-tabs .seg").forEach((btn) =>
    btn.addEventListener("click", () => {
      const t = btn.dataset.itab;
      $$("#iso-hub-tabs .seg").forEach((b) => b.classList.toggle("active", b === btn));
      $("#iso-curated-pane").classList.toggle("hidden", t !== "curated");
      $("#iso-appliances-pane").classList.toggle("hidden", t !== "appliances");
      $("#iso-community-pane").classList.toggle("hidden", t !== "community");
      if (t === "community" && !csCatalog) loadCommunityScripts(false);
    })
  );
  let csSearchTimer = null;
  $("#cs-search")?.addEventListener("input", () => {
    clearTimeout(csSearchTimer);
    csSearchTimer = setTimeout(() => loadCommunityScripts(false), 180);
  });
  $("#cs-category")?.addEventListener("change", () => loadCommunityScripts(false));
  $("#cs-type")?.addEventListener("change", () => loadCommunityScripts(false));
  $("#cs-sort")?.addEventListener("change", () => loadCommunityScripts(false));
  $("#cs-include-dev")?.addEventListener("change", () => loadCommunityScripts(false));
  $("#btn-cs-refresh")?.addEventListener("click", async () => {
    toast("Refreshing live catalog from community-scripts.org...", "info");
    await loadCommunityScripts(true);
    toast("Community Scripts catalog refreshed", "ok");
  });
  $("#btn-cs-test-ssh")?.addEventListener("click", async () => {
    toast("Testing SSH connection to Proxmox host...", "info");
    const res = await api("/api/community-scripts/test-ssh", { method: "POST" });
    if (res.ok) toast(`SSH Ready! ${res.output}`, "ok");
    else toast(`SSH not ready: ${res.output} — ${res.hint || ""}`, "err");
  });
  $("#btn-cs-close")?.addEventListener("click", closeCommunityModal);
  $("#cs-modal-backdrop")?.addEventListener("click", (e) => {
    if (e.target.id === "cs-modal-backdrop") closeCommunityModal();
  });
  $("#form-cs-setup")?.addEventListener("input", () => updateCommunityPreview());
  $("#form-cs-setup")?.addEventListener("change", () => updateCommunityPreview());
  $("#btn-cs-copy")?.addEventListener("click", async () => {
    await navigator.clipboard.writeText(csLastRealCmd || $("#cs-cmd-preview").textContent);
    toast("Copied Advanced LXC command to clipboard!", "ok");
  });
  $("#form-cs-setup")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!csSelected) return;
    if (!confirm(`Deploy '${csSelected.name}' directly to your Proxmox host via SSH?`)) return;
    const opts = collectCommunityOptions();
    $("#cs-deploy-log-wrap").classList.remove("hidden");
    $("#cs-deploy-status").textContent = "Starting SSH deployment...";
    $("#cs-deploy-log").textContent = "Connecting to Proxmox host...\n";
    try {
      const job = await api("/api/community-scripts/deploy", {
        method: "POST",
        body: JSON.stringify({
          slug: csSelected.slug,
          script_type: csSelected.type || "lxc",
          script_url: csSelected.script_url,
          options: opts,
        }),
      });
      toast(`Started deployment job ${job.job_id}`, "info");
      if (csPollTimer) clearInterval(csPollTimer);
      csPollTimer = setInterval(async () => {
        try {
          const j = await api(`/api/community-scripts/jobs/${encodeURIComponent(job.job_id)}`);
          $("#cs-deploy-status").textContent = `Job ${j.job_id} · Status: ${j.status.toUpperCase()}${j.returncode !== null ? ` (exit=${j.returncode})` : ""}`;
          const pre = $("#cs-deploy-log");
          pre.textContent = (j.lines || []).join("\n");
          pre.scrollTop = pre.scrollHeight;
          if (j.status !== "running") {
            clearInterval(csPollTimer);
            csPollTimer = null;
            state.insights = null;
            toast(
              j.status === "succeeded" ? `Deployed ${csSelected.name} successfully!` : `Deployment exited with code ${j.returncode}`,
              j.status === "succeeded" ? "ok" : "err"
            );
          }
        } catch {
          clearInterval(csPollTimer);
          csPollTimer = null;
        }
      }, 1000);
    } catch (err) {
      $("#cs-deploy-status").textContent = `Failed: ${err.message}`;
      toast(err.message, "err");
    }
  });
  $("#form-iso-search").addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = $("#iso-search-input").value.trim();
    toast(q ? `Searching homelab library & web for "${q}"...` : "Showing full library", "info");
    try {
      const data = await api(`/api/isos/search?q=${encodeURIComponent(q)}&web=${q ? "true" : "false"}`);
      renderIsoResults(data);
    } catch (err) {
      toast(err.message, "err");
    }
  });
  $("#btn-iso-probe").addEventListener("click", probeDirectUrl);
  $("#form-iso-direct").addEventListener("submit", async (e) => {
    e.preventDefault();
    const url = $("#iso-direct-url").value.trim();
    if (!url) return;
    const fn = $("#iso-direct-url").dataset.probedFilename || url.split("/").pop().split("?")[0] || "image.iso";
    await pullIsoToProxmox(url, fn);
  });
  $("#apl-search").addEventListener("input", renderAppliances);
  $("#apl-section").addEventListener("change", renderAppliances);

  // File Explorer events
  $$("#file-mode-tabs .seg").forEach((btn) =>
    btn.addEventListener("click", () => {
      const m = btn.dataset.fmode;
      $$("#file-mode-tabs .seg").forEach((b) => b.classList.toggle("active", b === btn));
      $("#files-storage-pane").classList.toggle("hidden", m !== "storage");
      $("#files-guest-pane").classList.toggle("hidden", m !== "guest");
      if (m === "guest") loadGuestAgentVms();
    })
  );
  $("#fe-storage-select").addEventListener("change", loadFileExplorer);
  $("#fe-content-select").addEventListener("change", loadFileExplorer);
  $("#fe-search").addEventListener("input", renderStorageFilesTable);
  $("#btn-toggle-upload").addEventListener("click", () => $("#form-file-upload").classList.toggle("hidden"));
  $("#btn-cancel-upload").addEventListener("click", () => $("#form-file-upload").classList.add("hidden"));
  $("#form-file-upload").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    toast("Uploading file to Proxmox...", "info");
    try {
      const res = await fetch("/api/files/storage/upload", { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Upload failed");
      toast(`Uploaded ${data.filename} to ${data.storage}`, "ok");
      $("#form-file-upload").classList.add("hidden");
      e.target.reset();
      await loadFileExplorer();
    } catch (err) {
      toast(err.message, "err");
    }
  });
  $("#ge-vm-select").addEventListener("change", () => browseGuestDir("/"));
  $("#btn-ge-go").addEventListener("click", () => browseGuestDir($("#ge-path-input").value));
  $("#btn-ge-up").addEventListener("click", () => {
    const parts = geCurrentPath.split("/").filter(Boolean);
    parts.pop();
    browseGuestDir("/" + parts.join("/"));
  });
  $$("[data-ge-jump]").forEach((b) => b.addEventListener("click", () => browseGuestDir(b.dataset.geJump)));
  $("#btn-ge-save").addEventListener("click", async () => {
    if (!geSelectedVmid || !geCurrentFile) return;
    try {
      await api(`/api/files/guest/${geSelectedVmid}/write`, {
        method: "POST",
        body: JSON.stringify({ path: geCurrentFile, content: $("#ge-editor-content").value }),
      });
      toast(`Saved ${geCurrentFile}`, "ok");
    } catch (err) {
      toast(err.message, "err");
    }
  });
  $("#form-ge-exec").addEventListener("submit", async (e) => {
    e.preventDefault();
    const cmd = $("#ge-exec-cmd").value.trim();
    const vmid = Number($("#ge-vm-select").value);
    if (!cmd || !vmid) return;
    const outBox = $("#ge-exec-output");
    outBox.classList.remove("hidden");
    outBox.textContent = `Running '${cmd}' on VM ${vmid}...`;
    try {
      const res = await api(`/api/files/guest/${vmid}/exec`, {
        method: "POST",
        body: JSON.stringify({ command: cmd }),
      });
      outBox.textContent = `$ ${cmd}  (exit=${res.exitcode})\n${res.stdout || ""}${res.stderr ? "\n[stderr]\n" + res.stderr : ""}`;
    } catch (err) {
      outBox.textContent = `Error: ${err.message}`;
    }
  });

  // Backup Scheduler events
  $("#btn-toggle-bkp-sched")?.addEventListener("click", () => $("#form-backup-schedule").classList.toggle("hidden"));
  $("#btn-cancel-bkp-sched")?.addEventListener("click", () => $("#form-backup-schedule").classList.add("hidden"));
  $("#form-backup-schedule")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      await api("/api/backups/schedules", {
        method: "POST",
        body: JSON.stringify({
          storage: String(fd.get("storage")),
          schedule: String(fd.get("schedule")),
          vmid: String(fd.get("vmid") || "").trim() || null,
          all_guests: fd.get("all_guests") === "on",
          mode: String(fd.get("mode")),
          compress: String(fd.get("compress")),
          comment: String(fd.get("comment") || ""),
        }),
      });
      toast("Backup schedule created!", "ok");
      $("#form-backup-schedule").classList.add("hidden");
      ctx.loadActiveView(true);
    } catch (err) {
      toast(err.message, "err");
    }
  });

  // Host & Alerts events
  $("#btn-apt-refresh").addEventListener("click", async () => {
    try {
      await api("/api/host/apt-refresh", { method: "POST" });
      toast("APT refresh task triggered on Proxmox host", "ok");
      setTimeout(loadHostMaintenance, 2500);
    } catch (err) {
      toast(err.message, "err");
    }
  });
  $("#btn-syslog-refresh").addEventListener("click", loadHostMaintenance);
  $("#form-alerts").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      await api("/api/alerts/config", {
        method: "PUT",
        body: JSON.stringify({
          provider: String(fd.get("provider")),
          webhook_url: String(fd.get("webhook_url") || ""),
          min_health_score: Number(fd.get("min_health_score") || 75),
          max_storage_pct: Number(fd.get("max_storage_pct") || 85),
          max_cpu_pct: Number(fd.get("max_cpu_pct") || 90),
          max_ram_pct: Number(fd.get("max_ram_pct") || 90),
          enabled: fd.get("enabled") === "on",
          notify_failed_tasks: fd.get("notify_failed_tasks") === "on",
        }),
      });
      toast("Alert webhook settings saved", "ok");
    } catch (err) {
      toast(err.message, "err");
    }
  });
  $("#btn-alert-test").addEventListener("click", async () => {
    try {
      await api("/api/alerts/test", { method: "POST" });
      toast("Test alert delivered!", "ok");
    } catch (err) {
      toast(err.message, "err");
    }
  });
  $("#btn-alert-eval").addEventListener("click", async () => {
    const box = $("#alert-eval-result");
    box.className = "test-result";
    box.textContent = "Evaluating homelab thresholds...";
    try {
      const res = await api("/api/alerts/evaluate", { method: "POST" });
      box.className = `test-result ${res.triggers.length ? "err" : "ok"}`;
      box.innerHTML = `<strong>Health ${res.health_score}/100</strong> · ${res.triggers.length} trigger(s) · Webhook sent: <code>${res.webhook_sent}</code>${res.triggers.length ? "<br>" + res.triggers.map((t) => "• " + esc(t)).join("<br>") : ""}`;
    } catch (err) {
      box.className = "test-result err";
      box.textContent = err.message;
    }
  });

  // Ctrl+K Command Palette events
  $("#btn-cmdk").addEventListener("click", async () => {
    if (!state.insights && (state.settings?.is_configured || state.settings?.proxmox_demo_mode)) {
      ensureInsights(false).catch(() => {});
    }
    openCmdK();
  });
  $("#cmdk-backdrop").addEventListener("click", (e) => {
    if (e.target.id === "cmdk-backdrop") closeCmdK();
  });
  $("#cmdk-input").addEventListener("input", (e) => renderCmdK(e.target.value));
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      if ($("#cmdk-backdrop").classList.contains("hidden")) openCmdK();
      else closeCmdK();
    } else if (e.key === "Escape" && !$("#cmdk-backdrop").classList.contains("hidden")) {
      closeCmdK();
    }
  });

  return { loadIsoHub, loadCommunityScripts, loadFileExplorer, loadHostMaintenance };
}
