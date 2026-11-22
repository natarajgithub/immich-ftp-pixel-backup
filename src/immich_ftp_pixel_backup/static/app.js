(() => {
  const $ = (sel, el = document) => el.querySelector(sel);
  const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];

  const state = {
    config: null,
    status: null,
    editingId: null,
    pollTimer: null,
  };

  function toast(msg, kind = "ok") {
    const el = $("#toast");
    el.textContent = msg;
    el.className = `toast show ${kind}`;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.remove("show"), 3200);
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
      ...opts,
    });
    let body = null;
    const text = await res.text();
    try {
      body = text ? JSON.parse(text) : null;
    } catch {
      body = { raw: text };
    }
    if (!res.ok) {
      const detail = body?.detail || body?.error || res.statusText;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return body;
  }

  function switchTab(name) {
    $$(".nav button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
    $$(".panel").forEach((p) => p.classList.toggle("active", p.id === `panel-${name}`));
  }

  function countHtml(counts) {
    const keys = ["uploaded", "failed", "pending", "uploading", "skipped"];
    return keys
      .map((k) => {
        const n = counts?.[k] || 0;
        return `<div class="count"><strong data-count="${k}">${n}</strong><span>${k}</span></div>`;
      })
      .join("");
  }

  function failsHtml(u) {
    const fails = (u.failed_assets || [])
      .map(
        (f) =>
          `<li><code>${escapeHtml(f.asset_id)}</code> · attempts ${f.attempts}<br>${escapeHtml(
            f.last_error || ""
          )}</li>`
      )
      .join("");
    return fails || "<li>None</li>";
  }

  function eventsHtml(u) {
    const events = (u.events || [])
      .slice(0, 12)
      .map(
        (e) =>
          `<li class="${e.level === "error" ? "err" : ""}">[${escapeHtml(e.level)}] ${escapeHtml(
            e.message
          )}${e.asset_id ? ` · <code>${escapeHtml(e.asset_id)}</code>` : ""}</li>`
      )
      .join("");
    return events || "<li>None</li>";
  }

  function openDetailsKeys(root) {
    const open = new Set();
    root.querySelectorAll("details[data-section][open]").forEach((el) => {
      const card = el.closest("[data-user]");
      if (card) open.add(`${card.dataset.user}:${el.dataset.section}`);
    });
    return open;
  }

  function restoreDetails(root, open) {
    root.querySelectorAll("details[data-section]").forEach((el) => {
      const card = el.closest("[data-user]");
      if (!card) return;
      if (open.has(`${card.dataset.user}:${el.dataset.section}`)) el.open = true;
    });
  }

  function patchCard(card, u) {
    const badge = card.querySelector(".badge");
    if (badge) {
      badge.className = `badge ${u.enabled ? "on" : "off"}`;
      badge.textContent = u.enabled ? "enabled" : "disabled";
    }
    const counts = card.querySelector(".counts");
    if (counts) counts.innerHTML = countHtml(u.counts);
    const meta = card.querySelector(".meta");
    if (meta) {
      meta.innerHTML = `FTP <code>${escapeHtml(u.ftp)}</code><br>
            cron <code>${escapeHtml(u.catchup_cron || "")}</code><br>
            watermark <code>${escapeHtml(u.watermark || "none")}</code><br>
            next catchup <code>${escapeHtml(u.next_catchup || "—")}</code>`;
    }
    let err = card.querySelector(".error-box");
    if (u.last_error) {
      if (!err) {
        err = document.createElement("div");
        err.className = "error-box";
        const details = card.querySelector("details");
        card.insertBefore(err, details);
      }
      err.textContent = u.last_error;
    } else if (err) {
      err.remove();
    }
    const failDetails = card.querySelector('details[data-section="failed"]');
    if (failDetails) {
      const summary = failDetails.querySelector("summary");
      if (summary) summary.textContent = `Failed assets (${(u.failed_assets || []).length})`;
      const list = failDetails.querySelector(".fail-list");
      if (list) list.innerHTML = failsHtml(u);
    }
    const eventDetails = card.querySelector('details[data-section="events"]');
    if (eventDetails) {
      const list = eventDetails.querySelector(".event-list");
      if (list) list.innerHTML = eventsHtml(u);
    }
  }

  function renderDashboard() {
    const root = $("#dashboard");
    const users = state.status?.users || {};
    const ids = Object.keys(users);
    if (!ids.length) {
      root.innerHTML = `<div class="empty">No users configured yet. Open Settings to add one.</div>`;
      return;
    }

    const existing = [...root.querySelectorAll(".card[data-user]")];
    const existingIds = existing.map((c) => c.dataset.user);
    const sameSet =
      existingIds.length === ids.length && ids.every((id, i) => existingIds[i] === id);

    // Live poll: update cards in place so open <details> stay open.
    if (sameSet && existing.length) {
      existing.forEach((card) => patchCard(card, users[card.dataset.user]));
      return;
    }

    const open = openDetailsKeys(root);
    root.innerHTML = `<div class="grid">${ids
      .map((id) => {
        const u = users[id];
        const badge = u.enabled ? "on" : "off";
        const err = u.last_error
          ? `<div class="error-box">${escapeHtml(u.last_error)}</div>`
          : "";
        return `
        <article class="card" data-user="${escapeHtml(id)}">
          <header>
            <h2>${escapeHtml(id)}</h2>
            <span class="badge ${badge}">${u.enabled ? "enabled" : "disabled"}</span>
          </header>
          <div class="counts">${countHtml(u.counts)}</div>
          <div class="meta">
            FTP <code>${escapeHtml(u.ftp)}</code><br>
            cron <code>${escapeHtml(u.catchup_cron || "")}</code><br>
            watermark <code>${escapeHtml(u.watermark || "none")}</code><br>
            next catchup <code>${escapeHtml(u.next_catchup || "—")}</code>
          </div>
          ${err}
          <details data-section="failed">
            <summary>Failed assets (${(u.failed_assets || []).length})</summary>
            <ul class="fail-list">${failsHtml(u)}</ul>
          </details>
          <details data-section="events">
            <summary>Recent events</summary>
            <ul class="event-list">${eventsHtml(u)}</ul>
          </details>
          <div class="actions">
            <button class="btn" data-action="test-ftp" data-id="${escapeHtml(id)}">Test FTP</button>
            <button class="btn ghost" data-action="catchup" data-id="${escapeHtml(id)}">Catch up now</button>
            <button class="btn ghost" data-action="edit" data-id="${escapeHtml(id)}">Edit</button>
          </div>
        </article>`;
      })
      .join("")}</div>`;
    restoreDetails(root, open);
  }

  function escapeHtml(s) {
    return String(s ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function fillAppForm() {
    const a = state.config || {};
    $("#app-immich-url").value = a.immich_url || "";
    $("#app-catchup-cron").value = a.catchup_cron || "";
    $("#app-ready-ftp").checked = !!a.ready_require_ftp;
    $("#app-events-retain").value = a.events_retain_per_user ?? 500;
  }

  function fillUserForm(user) {
    state.editingId = user?.id || null;
    $("#user-form-title").textContent = user ? `Edit user · ${user.id}` : "Add user";
    $("#user-id").value = user?.id || "";
    $("#user-id").disabled = !!user;
    $("#user-enabled").checked = user ? !!user.enabled : true;
    $("#user-api-key").value = user?.immich_api_key || "";
    $("#user-webhook").value = user?.webhook_token || "";
    $("#user-cron").value = user?.catchup_cron || "";
    $("#ftp-host").value = user?.ftp?.host || "";
    $("#ftp-port").value = user?.ftp?.port ?? 12345;
    $("#ftp-user").value = user?.ftp?.username || "";
    $("#ftp-pass").value = user?.ftp?.password || "";
    $("#ftp-dir").value = user?.ftp?.remote_dir || "/storage/emulated/0/DCIM/ImmichSync";
    $("#btn-delete-user").hidden = !user;
  }

  function readUserForm() {
    return {
      id: $("#user-id").value.trim(),
      enabled: $("#user-enabled").checked,
      immich_api_key: $("#user-api-key").value,
      webhook_token: $("#user-webhook").value,
      catchup_cron: $("#user-cron").value.trim() || null,
      ftp: {
        host: $("#ftp-host").value.trim(),
        port: Number($("#ftp-port").value || 12345),
        username: $("#ftp-user").value,
        password: $("#ftp-pass").value,
        remote_dir: $("#ftp-dir").value.trim(),
      },
    };
  }

  function renderUserList() {
    const list = $("#user-list");
    const users = state.config?.users || [];
    if (!users.length) {
      list.innerHTML = `<div class="empty">No users yet.</div>`;
      return;
    }
    list.innerHTML = `<div class="grid">${users
      .map(
        (u) => `
      <article class="card">
        <header>
          <h2>${escapeHtml(u.id)}</h2>
          <span class="badge ${u.enabled ? "on" : "off"}">${u.enabled ? "on" : "off"}</span>
        </header>
        <div class="meta">
          FTP <code>${escapeHtml(u.ftp.host)}:${u.ftp.port}</code><br>
          dir <code>${escapeHtml(u.ftp.remote_dir)}</code>
        </div>
        <div class="actions">
          <button class="btn" data-action="edit" data-id="${escapeHtml(u.id)}">Edit</button>
          <button class="btn" data-action="test-ftp" data-id="${escapeHtml(u.id)}">Test FTP</button>
        </div>
      </article>`
      )
      .join("")}</div>`;
  }

  async function refreshStatus() {
    state.status = await api("/api/status");
    renderDashboard();
  }

  async function refreshConfig() {
    state.config = await api("/api/config");
    fillAppForm();
    renderUserList();
  }

  async function refreshAll() {
    await Promise.all([refreshConfig(), refreshStatus()]);
  }

  function startPolling() {
    stopPolling();
    const tick = async () => {
      if (document.visibilityState !== "visible") return;
      try {
        await refreshStatus();
      } catch (e) {
        console.warn(e);
      }
    };
    state.pollTimer = setInterval(tick, 2000);
  }

  function stopPolling() {
    if (state.pollTimer) clearInterval(state.pollTimer);
    state.pollTimer = null;
  }

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") startPolling();
    else stopPolling();
  });

  $$(".nav button").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });

  $("#dashboard").addEventListener("click", async (ev) => {
    const btn = ev.target.closest("button[data-action]");
    if (!btn) return;
    const id = btn.dataset.id;
    const action = btn.dataset.action;
    try {
      if (action === "test-ftp") {
        const r = await api(`/api/users/${id}/test-ftp`, { method: "POST" });
        toast(r.ok ? `FTP OK · ${r.pwd || r.remote_dir}` : r.error || "FTP failed", r.ok ? "ok" : "err");
      } else if (action === "catchup") {
        const r = await api(`/api/users/${id}/catchup-now`, { method: "POST" });
        toast(`Catch-up ${r.status}: found ${r.found}, uploaded ${r.uploaded}, failed ${r.failed}`);
        await refreshStatus();
      } else if (action === "edit") {
        const user = (state.config?.users || []).find((u) => u.id === id);
        fillUserForm(user);
        switchTab("settings");
      }
    } catch (e) {
      toast(e.message, "err");
    }
  });

  $("#user-list").addEventListener("click", async (ev) => {
    const btn = ev.target.closest("button[data-action]");
    if (!btn) return;
    const id = btn.dataset.id;
    try {
      if (btn.dataset.action === "edit") {
        fillUserForm((state.config?.users || []).find((u) => u.id === id));
      } else if (btn.dataset.action === "test-ftp") {
        const r = await api(`/api/users/${id}/test-ftp`, { method: "POST" });
        toast(r.ok ? `FTP OK · ${r.pwd || ""}` : r.error || "FTP failed", r.ok ? "ok" : "err");
      }
    } catch (e) {
      toast(e.message, "err");
    }
  });

  $("#btn-new-user").addEventListener("click", () => fillUserForm(null));

  $("#form-app").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      state.config = await api("/api/config/app", {
        method: "PUT",
        body: JSON.stringify({
          immich_url: $("#app-immich-url").value.trim(),
          catchup_cron: $("#app-catchup-cron").value.trim(),
          ready_require_ftp: $("#app-ready-ftp").checked,
          events_retain_per_user: Number($("#app-events-retain").value || 500),
        }),
      });
      fillAppForm();
      toast("App settings saved");
    } catch (e) {
      toast(e.message, "err");
    }
  });

  $("#form-user").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const body = readUserForm();
    try {
      if (state.editingId) {
        await api(`/api/users/${state.editingId}`, { method: "PUT", body: JSON.stringify(body) });
        toast(`Updated ${body.id}`);
      } else {
        await api("/api/users", { method: "POST", body: JSON.stringify(body) });
        toast(`Created ${body.id}`);
      }
      await refreshAll();
      fillUserForm((state.config?.users || []).find((u) => u.id === body.id) || null);
    } catch (e) {
      toast(e.message, "err");
    }
  });

  $("#btn-delete-user").addEventListener("click", async () => {
    if (!state.editingId) return;
    if (!confirm(`Delete user ${state.editingId}?`)) return;
    try {
      await api(`/api/users/${state.editingId}`, { method: "DELETE" });
      toast(`Deleted ${state.editingId}`);
      fillUserForm(null);
      await refreshAll();
    } catch (e) {
      toast(e.message, "err");
    }
  });

  $("#btn-test-form-ftp").addEventListener("click", async () => {
    const body = readUserForm();
    if (!body.id) {
      toast("Save the user first (needs an id on the server)", "err");
      return;
    }
    try {
      // Ensure latest form values are persisted before test when editing
      if (state.editingId) {
        await api(`/api/users/${state.editingId}`, { method: "PUT", body: JSON.stringify(body) });
      }
      const r = await api(`/api/users/${body.id}/test-ftp`, { method: "POST" });
      toast(r.ok ? `FTP OK · ${r.pwd || r.remote_dir}` : r.error || "FTP failed", r.ok ? "ok" : "err");
      await refreshConfig();
    } catch (e) {
      toast(e.message, "err");
    }
  });

  refreshAll()
    .then(() => {
      fillUserForm(null);
      startPolling();
    })
    .catch((e) => toast(e.message, "err"));
})();
