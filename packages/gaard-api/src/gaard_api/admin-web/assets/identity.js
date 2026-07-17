export function createIdentityModule({ api, escapeHtml, state, render, setMessage }) {
  let selected = null;
  let paneWidth = 420;
  let loaded = false;
  let editAction = null;
  let createError = "";
  let temporaryPassword = "";
  let refreshOnAttach = false;
  let pageResizeObserver = null;

  function providerSpecific(user) {
    const entries = Object.entries(user.attributes || {});
    if (!entries.length) return "";
    return `<h3>Provider specific</h3><table class="identity-provider-table"><tbody>${entries.map(([key, value]) => `<tr><th>${escapeHtml(key)}</th><td>${escapeHtml(Array.isArray(value) ? value.join(", ") : value)}</td></tr>`).join("")}</tbody></table>`;
  }

  function actionButton(action, label, enabled) {
    return `<button type="button" data-identity-action="${action}" ${enabled ? "" : "disabled"}>${label}</button>`;
  }

  function userDashboards(user) {
    const dashboards = user.dashboards || [];
    if (!dashboards.length) return `<h3>Dashboards</h3><p class="identity-empty">No dashboards created.</p>`;
    return `<h3>Dashboards</h3><table class="identity-provider-table"><thead><tr><th>Name</th><th>Last updated</th></tr></thead><tbody>${dashboards.map((dashboard) => `<tr><td><strong>${escapeHtml(dashboard.name || "Untitled dashboard")}</strong>${dashboard.description ? `<br><span class="identity-dashboard-description">${escapeHtml(dashboard.description)}</span>` : ""}</td><td>${escapeHtml(dashboard.updated_at || "-")}</td></tr>`).join("")}</tbody></table>`;
  }

  function renderPane(user) {
    const editable = Boolean(user.editable_name || user.editable_password);
    const deletable = user.provider_id === "local" && user.role === "user";
    return `<aside class="identity-pane" style="width:${paneWidth}px">
      <div class="identity-resizer" id="identity-resizer" title="Drag to resize"></div>
      <header><div><h2>${escapeHtml(user.name || user.username)}</h2><p>${escapeHtml(user.provider)}</p></div><button id="identity-close" type="button" aria-label="Close">×</button></header>
      <div class="identity-pane-content">
        <dl class="identity-basics"><dt>Name</dt><dd>${escapeHtml(user.name || user.username)}</dd><dt>Username</dt><dd>${escapeHtml(user.username)}${user.overshadowed ? ` <span class="identity-warning" title="Overshadowed by ${escapeHtml(user.overshadowed_by?.username || "another user")} from ${escapeHtml(user.overshadowed_by?.provider || "another provider")}">⚠️</span>` : ""}</dd></dl>
        <div class="identity-sessions"><div class="identity-sessions-summary"><span>Active sessions</span><strong>${escapeHtml(user.sessions_count ?? 0)}</strong></div>${actionButton("sessions", "Clear sessons", Number(user.sessions_count || 0) > 0)}</div>
        <div class="identity-actions">${actionButton("username", "Change username", editable)}${actionButton("password", "Change password", Boolean(user.editable_password))}${deletable ? actionButton("delete", "Delete user", true) : ""}</div>
        ${userDashboards(user)}
        ${providerSpecific(user)}
      </div>
    </aside>`;
  }

  function renderEditModal() {
    if (!editAction || !selected) return "";
    if (editAction === "delete") return `<div class="modal-backdrop" data-identity-modal-backdrop><section class="modal-panel modal-panel-small" role="dialog" aria-modal="true" aria-labelledby="identity-modal-title"><div class="modal-header"><div><h2 id="identity-modal-title">Delete user</h2><p>Delete ${escapeHtml(selected.name || selected.username)} and their saved application data?</p></div></div><div class="form-actions"><button type="button" data-identity-modal-cancel>Cancel</button><button class="danger" type="button" id="identity-delete-confirm">Delete</button></div></section></div>`;
    if (editAction === "sessions") return `<div class="modal-backdrop" data-identity-modal-backdrop><section class="modal-panel modal-panel-small" role="dialog" aria-modal="true" aria-labelledby="identity-modal-title"><div class="modal-header"><div><h2 id="identity-modal-title">Clear sessions</h2><p>Sign ${escapeHtml(selected.name || selected.username)} out of every other session. The session making this request is kept.</p></div></div><div class="form-actions"><button type="button" data-identity-modal-cancel>Cancel</button><button class="danger" type="button" id="identity-sessions-clear-confirm">Clear sessions</button></div></section></div>`;
    const isPassword = editAction === "password";
    const label = isPassword ? "New password" : "New username";
    const value = isPassword ? "" : escapeHtml(selected.username);
    return `<div class="modal-backdrop" data-identity-modal-backdrop><section class="modal-panel modal-panel-small" role="dialog" aria-modal="true" aria-labelledby="identity-modal-title"><div class="modal-header"><div><h2 id="identity-modal-title">${isPassword ? "Change password" : "Change username"}</h2><p>${escapeHtml(selected.name || selected.username)}</p></div></div><form id="identity-edit-modal-form" class="form-grid"><label>${label}<input name="value" ${isPassword ? "type=\"password\" minlength=\"8\" autocomplete=\"new-password\"" : "autocomplete=\"username\""} value="${value}" required /></label><div class="form-actions"><button type="button" data-identity-modal-cancel>Cancel</button><button class="primary" type="submit">Apply now</button></div></form></section></div>`;
  }

  function renderCreateModal() {
    if (temporaryPassword) return `<div class="modal-backdrop" data-identity-modal-backdrop><section class="modal-panel modal-panel-small" role="dialog" aria-modal="true" aria-labelledby="identity-modal-title"><div class="modal-header"><div><h2 id="identity-modal-title">User created</h2><p>Share this temporary password securely. The user must change it after signing in.</p></div></div><label>Temporary password<input value="${escapeHtml(temporaryPassword)}" readonly /></label><div class="form-actions"><button class="primary" type="button" data-identity-modal-cancel>Done</button></div></section></div>`;
    if (editAction !== "create") return "";
    return `<div class="modal-backdrop" data-identity-modal-backdrop><section class="modal-panel modal-panel-small" role="dialog" aria-modal="true" aria-labelledby="identity-modal-title"><div class="modal-header"><div><h2 id="identity-modal-title">Create user</h2></div></div><form id="identity-create-modal-form" class="form-grid"><label>Name<input name="display_name" maxlength="255" autocomplete="name" /></label><label>Username<input name="username" maxlength="255" autocomplete="username" required /></label>${createError ? `<div class="error" role="alert">${escapeHtml(createError)}</div>` : ""}<div class="form-actions"><button type="button" data-identity-modal-cancel>Cancel</button><button class="primary" type="submit">Create user</button></div></form></section></div>`;
  }

  function renderIdentity() {
    const rows = (state.identities || []).map((user) => `<tr class="identity-table-row${selected?.id === user.id ? " selected" : ""}" data-identity-id="${escapeHtml(user.id)}"><td>${escapeHtml(user.name || user.username)}</td><td>${escapeHtml(user.username)}${user.overshadowed ? ` <span class="identity-warning" title="Overshadowed by ${escapeHtml(user.overshadowed_by?.username || "another user")} from ${escapeHtml(user.overshadowed_by?.provider || "another provider")}">⚠️</span>` : ""}</td><td>${escapeHtml(user.role)}</td><td>${escapeHtml(user.provider)}</td></tr>`).join("");
    return `<section class="panel identities-page"><div class="panel-header"><h2>Identities</h2><div class="form-actions"><button id="identity-create" class="primary" type="button">Create user</button><button id="identity-refresh" type="button">Refresh</button></div></div><div class="table-wrap"><table><thead><tr><th>Name</th><th>Username</th><th>Role</th><th>Provider</th></tr></thead><tbody>${rows || `<tr><td colspan="4">No users found.</td></tr>`}</tbody></table></div>${selected ? renderPane(selected) : ""}</section>${renderEditModal()}${renderCreateModal()}`;
  }

  function clampPaneWidth() {
    const page = document.querySelector(".identities-page");
    const pane = document.querySelector(".identity-pane");
    if (!page || !pane) return;
    const maximum = Math.max(320, Math.floor(page.getBoundingClientRect().width));
    paneWidth = Math.min(paneWidth, maximum);
    pane.style.width = `${paneWidth}px`;
  }

  function observePageWidth() {
    const page = document.querySelector(".identities-page");
    if (!page || !window.ResizeObserver) return;
    pageResizeObserver?.disconnect();
    pageResizeObserver = new ResizeObserver(clampPaneWidth);
    pageResizeObserver.observe(page);
  }

  async function load(refresh = false, refreshAfterLoad = false) {
    try {
      state.identities = (await api(`/api/v1/admin/identities${refresh ? "?refresh=true" : ""}`)).items || [];
      if (selected) selected = state.identities.find((item) => item.id === selected.id) || null;
      loaded = true;
      render();
      if (refreshAfterLoad) void load(true);
    } catch (error) { setMessage("error", error.message); }
  }

  function attachResize() {
    clampPaneWidth(); observePageWidth();
    document.querySelector("#identity-resizer")?.addEventListener("pointerdown", (event) => {
      event.preventDefault(); const startX = event.clientX; const startWidth = paneWidth;
      const move = (moveEvent) => { const page = document.querySelector(".identities-page"); const maximum = Math.max(320, Math.floor(page?.getBoundingClientRect().width || 320)); paneWidth = Math.min(maximum, Math.max(320, startWidth + startX - moveEvent.clientX)); clampPaneWidth(); };
      const stop = () => { window.removeEventListener("pointermove", move); };
      window.addEventListener("pointermove", move); window.addEventListener("pointerup", stop, { once: true });
    });
  }

  function attachModal() {
    document.querySelector("[data-identity-modal-cancel]")?.addEventListener("click", () => { editAction = null; createError = ""; temporaryPassword = ""; render(); });
    document.querySelector("[data-identity-modal-backdrop]")?.addEventListener("click", (event) => { if (event.target === event.currentTarget) { editAction = null; createError = ""; temporaryPassword = ""; render(); } });
    document.querySelector("#identity-edit-modal-form")?.addEventListener("submit", async (event) => { event.preventDefault(); const value = String(new FormData(event.currentTarget).get("value") || ""); const body = editAction === "password" ? { new_password: value } : { username: value }; try { await api(`/api/v1/admin/identities/${encodeURIComponent(selected.id)}`, { method: "PATCH", body: JSON.stringify(body) }); editAction = null; setMessage("success", "Identity updated. The affected user has been signed out."); await load(); } catch (error) { setMessage("error", error.message); } });
    document.querySelector("#identity-create-modal-form")?.addEventListener("submit", async (event) => { event.preventDefault(); const form = new FormData(event.currentTarget); createError = ""; try { const result = await api("/api/v1/admin/identities", { method: "POST", body: JSON.stringify({ display_name: form.get("display_name"), username: form.get("username") }) }); temporaryPassword = result.temporary_password || ""; await load(); } catch (error) { createError = error.message; render(); } });
    document.querySelector("#identity-delete-confirm")?.addEventListener("click", async () => { try { await api(`/api/v1/admin/identities/${encodeURIComponent(selected.id)}`, { method: "DELETE" }); selected = null; editAction = null; setMessage("success", "Built-in user deleted."); await load(); } catch (error) { setMessage("error", error.message); } });
    document.querySelector("#identity-sessions-clear-confirm")?.addEventListener("click", async () => { try { const result = await api(`/api/v1/admin/identities/${encodeURIComponent(selected.id)}/sessions`, { method: "DELETE" }); editAction = null; setMessage("success", `${result.cleared_sessions || 0} session(s) cleared.`); await load(); } catch (error) { setMessage("error", error.message); } });
  }

  function attach() {
    document.querySelector("#identity-create")?.addEventListener("click", () => { editAction = "create"; createError = ""; temporaryPassword = ""; render(); });
    document.querySelector("#identity-refresh")?.addEventListener("click", () => void load(true));
    document.querySelectorAll("[data-identity-id]").forEach((row) => row.addEventListener("click", () => { selected = state.identities.find((item) => item.id === row.dataset.identityId) || null; render(); }));
    document.querySelector("#identity-close")?.addEventListener("click", () => { selected = null; render(); });
    document.querySelectorAll("[data-identity-action]").forEach((button) => button.addEventListener("click", () => { editAction = button.dataset.identityAction; render(); }));
    attachResize(); attachModal();
    if (!loaded || refreshOnAttach) {
      const loadFromCacheFirst = (state.identities || []).length === 0;
      refreshOnAttach = false;
      void load(!loadFromCacheFirst, loadFromCacheFirst);
    }
  }
  function activate() { refreshOnAttach = true; }
  return { render: renderIdentity, attach, activate };
}
