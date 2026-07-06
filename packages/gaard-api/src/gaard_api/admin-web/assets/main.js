// src/main.ts
var app = document.querySelector("#app");
var licensePackageUpdatePollTimer = null;
var builtInSectionLabels = {
  overview: "Overview",
  widgets: "Widgets",
  "data-audit": "Data audit",
  prompts: "Prompts",
  "schema-cache": "Schema cache",
  "business-logic": "Business logic",
  "llm-config": "LLM",
  reasoning: "Reasoning",
  "governance-policy": "Governance policy",
  identity: "Identities",
  datasources: "Data sources",
  license: "License",
  "admin-audit": "Admin audit"
};
var state = {
  token: localStorage.getItem("gaard_admin_token"),
  username: localStorage.getItem("gaard_admin_username") || "",
  mustChangePassword: localStorage.getItem("gaard_admin_must_change") === "true",
  mobileMenuOpen: false,
  openMenuGroups: {},
  section: "overview",
  error: "",
  success: "",
  overview: null,
  overviewWidgetConfigs: [],
  overviewWidgetDatasources: [],
  selectedOverviewWidgetKey: "",
  overviewEditorWidgetKey: null,
  overviewPlacementSlot: null,
  confirmDialog: null,
  draggedOverviewWidgetKey: null,
  overviewResizeState: null,
  overviewExtraSlots: 0,
  overviewRemovedEmptySlots: [],
  overviewLoading: Boolean(localStorage.getItem("gaard_admin_token") && localStorage.getItem("gaard_admin_must_change") !== "true"),
  overviewRefreshing: false,
  overviewTablePages: {},
  dataAudit: [],
  dataAuditType: "",
  dataAuditOutputClassification: "",
  dataAuditSqlContains: "",
  adminAudit: [],
  auditSettings: null,
  prompts: [],
  selectedPromptKey: "",
  schemaCache: null,
  businessLogic: [],
  businessLogicDatasource: null,
  businessLogicDatasources: [],
  businessLogicEditorId: null,
  llmConfig: null,
  reasoningConfig: null,
  governancePolicy: null,
  datasources: [],
  datasourceTypes: [],
  selectedDatasourceId: null,
  datasourceSchema: null,
  datasourceSchemaLoading: false,
  datasourceSchemaError: "",
  datasourceSchemaSelectedObjectName: "",
  datasourceSchemaShowEnabledOnly: false,
  datasourceSchemaDraftTables: null,
  extensionSections: [],
  extensionsLoaded: false,
  license: null,
  licensePackageUpdate: null
};
var packageUpdateStages = [
  { key: "downloading", label: "Downloading" },
  { key: "decompressing", label: "Decompressing" },
  { key: "analyzing", label: "Analyzing" },
  { key: "installing", label: "Installing" }
];
var dataAuditTypes = [
  { value: "", label: "All types" },
  { value: "info", label: "Info" },
  { value: "sql_error", label: "SQL error" },
  { value: "access_error", label: "Access error" }
];
var outputClassifications = [
  { value: "", label: "All classifications" },
  { value: "personal_data", label: "Personal data" },
  { value: "sensitive_data", label: "Sensitive data" },
  { value: "technical_data", label: "Technical data" },
  { value: "neutral_data", label: "Neutral data" },
  { value: "unknown", label: "Unknown" }
];
var OVERVIEW_TABLE_PAGE_SIZE = 10;
var OVERVIEW_GRID_COLUMNS = 4;
var OVERVIEW_MIN_GRID_SLOTS = 8;
var OVERVIEW_SLOT_INCREMENT = 1;
var OVERVIEW_MAX_GRID_SLOTS = 40;
var ALLOWED_WIDGET_HTML_TAGS = /* @__PURE__ */ new Set(["A", "B", "I", "UL", "LI"]);
var DROPPED_WIDGET_HTML_TAGS = /* @__PURE__ */ new Set(["SCRIPT", "STYLE", "IFRAME", "OBJECT", "EMBED", "TEMPLATE", "SVG", "MATH"]);
function getMenuGroups() {
  return [
    {
      key: "dashboards",
      label: "Dashboards",
      sections: [
        { key: "overview", label: builtInSectionLabels.overview }
      ]
    },
    {
      key: "governance",
      label: "Governance",
      sections: [
        { key: "data-audit", label: builtInSectionLabels["data-audit"] },
        { key: "business-logic", label: builtInSectionLabels["business-logic"] },
        { key: "admin-audit", label: builtInSectionLabels["admin-audit"] }
      ]
    },
    {
      key: "configuration",
      label: "Configuration",
      sections: [
        { key: "llm-config", label: builtInSectionLabels["llm-config"] },
        { key: "reasoning", label: builtInSectionLabels.reasoning },
        { key: "datasources", label: builtInSectionLabels.datasources },
        { key: "prompts", label: builtInSectionLabels.prompts },
        { key: "widgets", label: builtInSectionLabels.widgets },
        { key: "identity", label: builtInSectionLabels.identity },
        { key: "governance-policy", label: builtInSectionLabels["governance-policy"] },
        { key: "license", label: builtInSectionLabels.license }
      ]
    },
    {
      key: "extensions",
      label: "Extensions",
      sections: state.extensionSections.map((section) => ({
        key: section.section_id,
        label: section.label
      })),
      emptyLabel: "No active extensions"
    }
  ];
}
function getSections() {
  return getMenuGroups().flatMap((group) => group.sections);
}
function getSectionLabel(section) {
  const menuSection = getSections().find((item) => item.key === section);
  if (menuSection) return menuSection.label;
  if (isExtensionSection(section)) return getExtensionSection(section)?.label || "Extension";
  return builtInSectionLabels[section];
}
function isExtensionSection(section) {
  return section.startsWith("extension:");
}
function getExtensionSection(section) {
  if (!isExtensionSection(section)) return null;
  return state.extensionSections.find((item) => item.section_id === section) || null;
}
function isMenuGroupActive(group) {
  return group.sections.some((section) => section.key === state.section);
}
function renderNavigation() {
  return getMenuGroups().map((group) => renderMenuGroup(group)).join("");
}
function renderSidebar() {
  return `
      <aside class="sidebar${state.mobileMenuOpen ? " menu-open" : ""}">
        <div class="sidebar-header">
          <div class="brand">
            <img class="brand-logo" src="/admin/assets/getgaard.svg" alt="" aria-hidden="true" />
            <div class="brand-copy"><strong>GAARD Admin Console</strong><span>${escapeHtml(formatLicenseEditionLabel(state.license))}</span></div>
          </div>
          <button class="menu-toggle" id="mobile-menu-button" type="button" aria-label="${state.mobileMenuOpen ? "Close navigation" : "Open navigation"}" aria-expanded="${state.mobileMenuOpen}" aria-controls="admin-navigation">
            <span></span><span></span><span></span>
          </button>
        </div>
        <nav class="nav" id="admin-navigation">
          ${renderNavigation()}
        </nav>
        <div class="sidebar-footer"><span>${escapeHtml(state.username)}</span><button id="logout-button">Sign out</button></div>
      </aside>`;
}
function renderMenuGroup(group) {
  const isOpen = Boolean(state.openMenuGroups[group.key]);
  const isActive = isMenuGroupActive(group);
  const submenu = isOpen ? renderSubmenu(group) : "";
  return `
    <div class="nav-group${isActive ? " active" : ""}">
      <button
        type="button"
        class="nav-group-button${isActive ? " active" : ""}"
        data-menu-group="${escapeHtml(group.key)}"
        aria-expanded="${isOpen}"
      >
        <span>${escapeHtml(group.label)}</span>
        <span class="nav-chevron" aria-hidden="true">${isOpen ? "\u25BE" : "\u25B8"}</span>
      </button>
      ${submenu}
    </div>`;
}
function renderSubmenu(group) {
  if (group.sections.length === 0) {
    return `<div class="submenu"><div class="nav-empty">${escapeHtml(group.emptyLabel || "No items")}</div></div>`;
  }
  return `
    <div class="submenu">
      ${group.sections.map((section) => `
        <button
          type="button"
          data-section="${escapeHtml(section.key)}"
          class="submenu-button${section.key === state.section ? " active" : ""}"
        >
          ${escapeHtml(section.label)}
        </button>`).join("")}
    </div>`;
}
function escapeHtml(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}
function renderWidgetContent(value) {
  const documentFragment = new DOMParser().parseFromString(String(value ?? ""), "text/html");
  return Array.from(documentFragment.body.childNodes).map(renderWidgetContentNode).join("");
}
function renderWidgetContentNode(node) {
  if (node.nodeType === Node.TEXT_NODE) {
    return escapeHtml(node.textContent || "");
  }
  if (node.nodeType !== Node.ELEMENT_NODE) {
    return "";
  }
  const element = node;
  const tagName = element.tagName.toUpperCase();
  if (DROPPED_WIDGET_HTML_TAGS.has(tagName)) {
    return "";
  }
  const children = Array.from(element.childNodes).map(renderWidgetContentNode).join("");
  if (!ALLOWED_WIDGET_HTML_TAGS.has(tagName)) {
    return children;
  }
  const tag = tagName.toLowerCase();
  if (tag === "a") {
    const href = sanitizeWidgetHref(element.getAttribute("href"));
    return `<a${href ? ` href="${escapeHtml(href)}"` : ""}>${children}</a>`;
  }
  return `<${tag}>${children}</${tag}>`;
}
function sanitizeWidgetHref(value) {
  const href = String(value || "").trim().replace(/[\u0000-\u001F\u007F\s]+/g, "");
  if (!href) {
    return "";
  }
  try {
    const parsed = new URL(href, window.location.origin);
    if (["http:", "https:", "mailto:", "tel:"].includes(parsed.protocol)) {
      return href;
    }
  } catch {
    return "";
  }
  return "";
}
function formatAuditTime(value) {
  const raw = String(value ?? "");
  const match = raw.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?/);
  if (match) {
    const millis = (match[5] || "000").slice(0, 3).padEnd(3, "0");
    return `${match[1]} ${match[2]}:${match[3]}:${match[4]}:${millis}`;
  }
  return raw;
}
function formatLicenseDate(value) {
  const raw = String(value ?? "").trim();
  if (!raw) return "-";
  const match = raw.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})/);
  if (match) return `${match[1]} ${match[2]}:${match[3]}`;
  return raw;
}
function formatLicenseEditionLabel(license) {
  const plan = String(license?.plan || "community")
    .trim()
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .toLowerCase();
  const normalized = plan || "community";
  return `${normalized.charAt(0).toUpperCase()}${normalized.slice(1)} edition`;
}
function formatLicenseMessage(license) {
  const message = String(license?.message || "").trim();
  if (!message) return "";
  if (/account is deleted/i.test(message)) {
    return "License validation returned a deleted status. Update the key or run a recheck after changes in GAARD Website.";
  }
  return message;
}
function extractErrorMessage(value) {
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
      try {
        return extractErrorMessage(JSON.parse(trimmed));
      } catch {
        return value;
      }
    }
    return value;
  }
  if (value && typeof value === "object") {
    const record = value;
    if (typeof record.message === "string") return record.message;
    if (typeof record.detail === "string") return record.detail;
    if (record.error) return extractErrorMessage(record.error);
    if (Array.isArray(record.detail) && record.detail.length) {
      return extractErrorMessage(record.detail[0]);
    }
    return JSON.stringify(value);
  }
  return String(value ?? "");
}
async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Content-Type", "application/json");
  if (state.token) headers.set("Authorization", `Bearer ${state.token}`);
  const response = await fetch(path, { ...options, headers });
  const payload = await response.json().catch(() => ({}));
  if (response.status === 401) {
    logout();
    throw new Error("Session expired.");
  }
  if (!response.ok) {
    throw new Error(extractErrorMessage(payload.detail ?? payload.error ?? payload.message ?? "Request failed."));
  }
  return payload;
}
function setMessage(type, value) {
  state.error = type === "error" ? value : "";
  state.success = type === "success" ? value : "";
  const region = document.querySelector("#message-region");
  if (region) {
    region.innerHTML = renderMessages();
  }
}
function renderMessages() {
  return `
          ${state.error ? `<div class="error">${escapeHtml(state.error)}</div>` : ""}
          ${state.success ? `<div class="success">${escapeHtml(state.success)}</div>` : ""}`;
}
function persistAuth(token, username, mustChangePassword) {
  state.token = token;
  state.username = username;
  state.mustChangePassword = mustChangePassword;
  localStorage.setItem("gaard_admin_token", token);
  localStorage.setItem("gaard_admin_username", username);
  localStorage.setItem("gaard_admin_must_change", String(mustChangePassword));
}
function logout() {
  state.token = null;
  state.username = "";
  state.mustChangePassword = false;
  state.overviewLoading = false;
  state.overviewRefreshing = false;
  state.overviewEditorWidgetKey = null;
  state.openMenuGroups = {};
  state.extensionSections = [];
  state.extensionsLoaded = false;
  localStorage.removeItem("gaard_admin_token");
  localStorage.removeItem("gaard_admin_username");
  localStorage.removeItem("gaard_admin_must_change");
  render();
}
function render() {
  if (!app) return;
  if (!state.token) return renderLogin();
  if (state.mustChangePassword) return renderPasswordChange();
  renderShell();
}
function renderLogin() {
  app.innerHTML = `
    <main class="login-shell">
      <section class="login-panel">
        <h1>GAARD Admin Console</h1>
        <p>Sign in with an administrator account.</p>
        <form id="login-form" class="form-grid">
          <label>Username<input name="username" autocomplete="username" value="admin" /></label>
          <label>Password<input name="password" type="password" autocomplete="current-password" value="admin" /></label>
          ${state.error ? `<div class="error">${escapeHtml(state.error)}</div>` : ""}
          <div class="form-actions"><button class="primary" type="submit">Sign in</button></div>
        </form>
      </section>
    </main>`;
  document.querySelector("#login-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const result = await api("/api/v1/admin/auth/login", {
        method: "POST",
        body: JSON.stringify({ username: form.get("username"), password: form.get("password") })
      });
      persistAuth(result.token, result.username, result.must_change_password);
      state.overviewLoading = !result.must_change_password && state.section === "overview";
      setMessage("success", "");
      await loadShellLicense();
      render();
      if (!result.must_change_password) await loadCurrentSection();
    } catch (error) {
      setMessage("error", error.message);
      render();
    }
  });
}
function renderPasswordChange() {
  app.innerHTML = `
    <main class="login-shell">
      <section class="login-panel">
        <h1>Change password</h1>
        <p>The default administrator password must be changed.</p>
        <form id="password-form" class="form-grid">
          <label>Current password<input name="current_password" type="password" /></label>
          <label>New password<input name="new_password" type="password" /></label>
          ${state.error ? `<div class="error">${escapeHtml(state.error)}</div>` : ""}
          <div class="form-actions">
            <button type="button" id="logout-button">Sign out</button>
            <button class="primary" type="submit">Save password</button>
          </div>
        </form>
      </section>
    </main>`;
  document.querySelector("#logout-button")?.addEventListener("click", logout);
  document.querySelector("#password-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const result = await api("/api/v1/admin/auth/change-password", {
        method: "POST",
        body: JSON.stringify({
          current_password: form.get("current_password"),
          new_password: form.get("new_password")
        })
      });
      state.mustChangePassword = result.must_change_password;
      state.overviewLoading = !state.mustChangePassword && state.section === "overview";
      localStorage.setItem("gaard_admin_must_change", String(result.must_change_password));
      setMessage("success", "Password changed.");
      await loadShellLicense();
      render();
      await loadCurrentSection();
    } catch (error) {
      setMessage("error", error.message);
      render();
    }
  });
}
function renderShell() {
  const activeLabel = getSectionLabel(state.section);
  app.innerHTML = `
    <div class="app-shell">
      ${renderSidebar()}
      <main class="main">
        <header class="topbar">
          <h1>${escapeHtml(activeLabel || "Admin")}</h1>
          <div class="topbar-actions"><span>${escapeHtml(state.username)}</span><button id="top-logout-button">Sign out</button></div>
        </header>
        <section class="content">
          <div id="message-region">${renderMessages()}</div>
          ${renderSection()}
        </section>
      </main>
    </div>
    ${renderOverviewWidgetModal()}
    ${renderConfirmDialog()}`;
  attachShellHandlers();
  resizeExtensionFrames();
}
function attachShellHandlers() {
  document.querySelectorAll("[data-menu-group]").forEach((button) => {
    button.addEventListener("click", () => {
      const groupKey = button.dataset.menuGroup;
      if (!groupKey) return;
      state.openMenuGroups[groupKey] = !state.openMenuGroups[groupKey];
      updateSidebar();
    });
  });
  document.querySelectorAll("[data-section]").forEach((button) => {
    button.addEventListener("click", async () => {
      state.section = button.dataset.section;
      state.mobileMenuOpen = false;
      state.overviewEditorWidgetKey = null;
      state.overviewLoading = state.section === "overview";
      setMessage("success", "");
      render();
      await loadCurrentSection();
    });
  });
  document.querySelector("#mobile-menu-button")?.addEventListener("click", () => {
    state.mobileMenuOpen = !state.mobileMenuOpen;
    updateSidebar();
  });
  document.querySelector("#logout-button")?.addEventListener("click", logout);
  document.querySelector("#top-logout-button")?.addEventListener("click", logout);
  attachSectionHandlers();
}
function updateSidebar() {
  const sidebarHost = document.querySelector(".app-shell > .sidebar");
  if (!sidebarHost) return render();
  sidebarHost.outerHTML = renderSidebar();
  attachShellHandlers();
}
function renderSection() {
  if (state.section === "overview") return renderOverview();
  if (state.section === "widgets") return renderWidgets();
  if (state.section === "data-audit") return renderDataAudit();
  if (state.section === "prompts") return renderPrompts();
  if (state.section === "schema-cache") return renderSchemaCache();
  if (state.section === "business-logic") return renderBusinessLogicSuggestions();
  if (state.section === "llm-config") return renderLlmConfig();
  if (state.section === "reasoning") return renderReasoningConfig();
  if (state.section === "governance-policy") return renderGovernancePolicy();
  if (state.section === "identity") return renderStub("Identity connector", "FreeIPA connector configuration is planned.");
  if (state.section === "datasources") return renderDatasources();
  if (state.section === "license") return renderLicense();
  if (state.section === "admin-audit") return renderAdminAudit();
  if (isExtensionSection(state.section)) return renderExtensionSection();
  return "";
}
function renderExtensionSection() {
  const section = getExtensionSection(state.section);
  if (!section) {
    return renderStub(
      "Extension unavailable",
      "This extension section is no longer available. Reinstall or enable its plugin, then refresh the admin console."
    );
  }
  return `
    <iframe
      class="extension-frame"
      data-extension-frame="${escapeHtml(section.section_id)}"
      title="${escapeHtml(section.label)}"
      src="${escapeHtml(section.path)}"
      loading="lazy"
    ></iframe>`;
}
function resizeExtensionFrames() {
  document.querySelectorAll(".extension-frame").forEach((frame) => {
    if (!frame.dataset.extensionHeightReady) {
      if (isExtensionFrameDocumentReady(frame)) {
        initializeExtensionFrameHeight(frame);
      } else {
        frame.addEventListener("load", () => initializeExtensionFrameHeight(frame), { once: true });
      }
    } else {
      setExtensionFrameHeight(frame, getExtensionFrameMaxHeight(frame));
    }
  });
}
function isExtensionFrameDocumentReady(frame) {
  try {
    return frame.contentDocument?.readyState === "complete";
  } catch {
    return false;
  }
}
function initializeExtensionFrameHeight(frame) {
  frame.dataset.extensionHeightReady = "true";
  setExtensionFrameHeight(frame, getExtensionFrameMaxHeight(frame));
}
function getExtensionFrameMaxHeight(frame) {
  const content = frame.closest(".content");
  if (!content) return Number.POSITIVE_INFINITY;
  const styles = window.getComputedStyle(content);
  const paddingTop = Number.parseFloat(styles.paddingTop) || 0;
  const paddingBottom = Number.parseFloat(styles.paddingBottom) || 0;
  const rowGap = Number.parseFloat(styles.rowGap || styles.gap) || 0;
  const messageRegion = content.querySelector("#message-region");
  const reservedHeight =
    paddingTop +
    paddingBottom +
    (messageRegion?.offsetHeight || 0) +
    (messageRegion ? rowGap : 0);
  return Math.max(1, content.clientHeight - reservedHeight);
}
function setExtensionFrameHeight(frame, height) {
  const maxHeight = getExtensionFrameMaxHeight(frame);
  frame.style.height = `${Math.ceil(Math.min(height+2, maxHeight))}px`;//+2 because of border 1px
}
window.addEventListener("message", (event) => {
  if (event.origin !== window.location.origin) return;
  const data = event.data;
  const frame = Array.from(document.querySelectorAll(".extension-frame")).find((item) => item.contentWindow === event.source);
  if (!frame) return;
  if (!data || data.type === "gaard:extension:height") {
    if (!frame.dataset.extensionHeightReady) return;
    const height = Number(data?.height);
    if (!Number.isFinite(height) || height <= 0) return;
    setExtensionFrameHeight(frame, height);
    return;
  }
  if (data.type === "gaard:admin-api-request") {
    void handleExtensionAdminApiRequest(frame, data);
  }
});
window.addEventListener("resize", resizeExtensionFrames);
async function handleExtensionAdminApiRequest(frame, data) {
  const requestId = String(data.requestId || "");
  const request = data.request || {};
  const method = String(request.method || "GET").toUpperCase();
  const path = String(request.path || "");
  const allowedRequests = new Set([
    "POST /api/v1/admin/datasources",
    "POST /api/v1/admin/datasources/test"
  ]);
  if (!requestId) return;
  try {
    if (!allowedRequests.has(`${method} ${path}`)) {
      throw new Error("Extension request is not allowed.");
    }
    const payload = await api(path, {
      method,
      body: JSON.stringify(request.body || {})
    });
    frame.contentWindow?.postMessage(
      {
        type: "gaard:admin-api-response",
        requestId,
        ok: true,
        payload
      },
      window.location.origin
    );
  } catch (error) {
    frame.contentWindow?.postMessage(
      {
        type: "gaard:admin-api-response",
        requestId,
        ok: false,
        error: error.message || String(error)
      },
      window.location.origin
    );
  }
}
function renderOverview() {
  const overview = state.overview;
  const widgets = overview?.widgets || [];
  const isLoading = state.overviewLoading || state.overviewRefreshing;
  const showInitialLoader = isLoading && !overview;
  const slotCount = showInitialLoader ? OVERVIEW_MIN_GRID_SLOTS : getOverviewSlotCount(widgets);
  const canAddSlots = slotCount < OVERVIEW_MAX_GRID_SLOTS;
  return `
    <div class="toolbar overview-toolbar">
      <div class="refresh-status" aria-live="polite">
        ${isLoading ? `<span class="spinner" aria-hidden="true"></span><span>Refreshing</span>` : ""}
      </div>
      <button class="primary" type="button" id="overview-refresh" ${isLoading ? "disabled" : ""}>Refresh</button>
    </div>
    <div class="overview-grid">
      ${showInitialLoader ? renderOverviewLoading() : renderOverviewGrid(widgets, slotCount)}
    </div>
    <div class="overview-grid-actions">
      <button type="button" id="overview-add-slots" ${showInitialLoader || !canAddSlots ? "disabled" : ""}>Add empty slots</button>
      <span>${escapeHtml(`${slotCount}/${OVERVIEW_MAX_GRID_SLOTS} slots`)}</span>
    </div>`;
}
function renderOverviewLoading() {
  return `
    <section class="overview-loading" aria-live="polite">
      <span class="spinner" aria-hidden="true"></span>
      <span>Refreshing</span>
    </section>`;
}
function getOverviewBaseSlotCount(widgets) {
  const layout = buildOverviewLayout(widgets);
  const occupiedSlots = Array.from(layout.occupiedSlots);
  return Math.min(
    OVERVIEW_MAX_GRID_SLOTS,
    Math.max(
      OVERVIEW_MIN_GRID_SLOTS,
      state.overviewExtraSlots,
      occupiedSlots.length ? Math.max(...occupiedSlots) + 1 : 0
    )
  );
}
function getOverviewRemovedEmptySlots(widgets, baseSlotCount = getOverviewBaseSlotCount(widgets)) {
  const layout = buildOverviewLayout(widgets);
  return state.overviewRemovedEmptySlots.filter((slot, index, items) => Number.isInteger(slot) && slot >= 0 && slot < baseSlotCount && !layout.occupiedSlots.has(slot) && items.indexOf(slot) === index).sort((left, right) => left - right);
}
function getOverviewSlotCount(widgets) {
  const baseSlotCount = getOverviewBaseSlotCount(widgets);
  return Math.max(OVERVIEW_MIN_GRID_SLOTS, baseSlotCount - getOverviewRemovedEmptySlots(widgets, baseSlotCount).length);
}
function logicalToDisplayOverviewSlot(logicalSlot, removedSlots) {
  let removedBefore = 0;
  for (const removedSlot of removedSlots) {
    if (removedSlot < logicalSlot) {
      removedBefore += 1;
      continue;
    }
    break;
  }
  return logicalSlot - removedBefore;
}
function displayToLogicalOverviewSlot(displaySlot, removedSlots) {
  let logicalSlot = displaySlot;
  for (const removedSlot of removedSlots) {
    if (removedSlot <= logicalSlot) {
      logicalSlot += 1;
    } else {
      break;
    }
  }
  return logicalSlot;
}
function renderOverviewGrid(widgets, slotCount = getOverviewSlotCount(widgets)) {
  const layout = buildOverviewLayout(widgets);
  const removedSlots = getOverviewRemovedEmptySlots(widgets);
  return Array.from({ length: slotCount }, (_, displaySlot) => {
    const logicalSlot = displayToLogicalOverviewSlot(displaySlot, removedSlots);
    const widget = layout.widgetSlots.get(logicalSlot);
    if (widget) {
      return renderOverviewGridWidget(widget, displaySlot);
    }
    if (layout.occupiedSlots.has(logicalSlot)) {
      return "";
    }
    return renderOverviewEmptySlot(logicalSlot, displaySlot);
  }).join("");
}
function buildOverviewLayout(widgets) {
  const widgetSlots = /* @__PURE__ */ new Map();
  const occupiedSlots = /* @__PURE__ */ new Set();
  widgets.filter((widget) => widget.active !== false).sort((left, right) => (left.position || 0) - (right.position || 0)).forEach((widget) => {
    const width = getOverviewWidgetGridWidth(widget);
    let slot = overviewSlotFromPosition(widget.position);
    while (!canPlaceOverviewWidget(slot, width, occupiedSlots)) {
      slot += 1;
    }
    widgetSlots.set(slot, widget);
    for (let offset = 0; offset < width; offset += 1) {
      occupiedSlots.add(slot + offset);
    }
  });
  return { widgetSlots, occupiedSlots };
}
function getActiveOverviewWidgets() {
  return (state.overview?.widgets || []).filter((widget) => widget.active !== false);
}
function getOverviewWidgetByKey(widgetKey) {
  return getActiveOverviewWidgets().find((widget) => widget.widget_key === widgetKey) || null;
}
function getOverviewOccupiedSlots(widgets) {
  const occupiedSlots = /* @__PURE__ */ new Set();
  widgets.forEach((widget) => {
    const slot = overviewSlotFromPosition(widget.position);
    const width = getOverviewWidgetGridWidth(widget);
    for (let offset = 0; offset < width; offset += 1) {
      occupiedSlots.add(slot + offset);
    }
  });
  return occupiedSlots;
}
function getOverviewLayoutForWidgets(widgets) {
  return buildOverviewLayout(
    widgets.map((widget) => ({ ...widget }))
  );
}
function getOverviewLayoutWithoutWidget(widgetKey) {
  return getOverviewLayoutForWidgets(
    getActiveOverviewWidgets().filter((widget) => widget.widget_key !== widgetKey)
  );
}
function getOverviewResizeBounds(widgetKey) {
  const widget = getOverviewWidgetByKey(widgetKey);
  if (!widget) return null;
  const slot = overviewSlotFromPosition(widget.position);
  const width = getOverviewWidgetGridWidth(widget);
  const rowStart = Math.floor(slot / OVERVIEW_GRID_COLUMNS) * OVERVIEW_GRID_COLUMNS;
  const rowEnd = rowStart + OVERVIEW_GRID_COLUMNS - 1;
  const startRight = slot + width - 1;
  const occupiedSlots = getOverviewLayoutWithoutWidget(widgetKey).occupiedSlots;
  let freeLeft = 0;
  for (let candidate = slot - 1; candidate >= rowStart && !occupiedSlots.has(candidate); candidate -= 1) {
    freeLeft += 1;
  }
  let freeRight = 0;
  for (let candidate = startRight + 1; candidate <= rowEnd && !occupiedSlots.has(candidate); candidate += 1) {
    freeRight += 1;
  }
  return {
    slot,
    width,
    rowStart,
    rowEnd,
    startRight,
    minLeftSlot: slot - freeLeft,
    maxRightSlot: startRight + freeRight
  };
}
function getOverviewResizeProposal(resizeState, clientX) {
  if (!resizeState) return null;
  if (resizeState.edge === "left") {
    const deltaColumns = Math.round((resizeState.startX - clientX) / resizeState.columnUnit);
    const proposedLeft = Math.max(
      resizeState.minLeftSlot,
      Math.min(resizeState.startRight, resizeState.startSlot - deltaColumns)
    );
    return {
      slot: proposedLeft,
      width: resizeState.startRight - proposedLeft + 1
    };
  }
  const deltaColumns = Math.round((clientX - resizeState.startX) / resizeState.columnUnit);
  const proposedRight = Math.max(
    resizeState.startSlot,
    Math.min(resizeState.maxRightSlot, resizeState.startRight + deltaColumns)
  );
  return {
    slot: resizeState.startSlot,
    width: proposedRight - resizeState.startSlot + 1
  };
}
function canPlaceOverviewWidget(slot, width, occupiedSlots) {
  if (slot < 0) {
    return false;
  }
  const column = slot % OVERVIEW_GRID_COLUMNS;
  if (column + width > OVERVIEW_GRID_COLUMNS) {
    return false;
  }
  for (let offset = 0; offset < width; offset += 1) {
    if (occupiedSlots.has(slot + offset)) {
      return false;
    }
  }
  return true;
}
function findAvailableOverviewSlot(slot, width, occupiedSlots) {
  let candidate = Math.max(0, slot);
  while (!canPlaceOverviewWidget(candidate, width, occupiedSlots)) {
    candidate += 1;
  }
  return candidate;
}
function overviewSlotFromPosition(position) {
  const numeric = Number(position);
  if (!Number.isFinite(numeric) || numeric < 10) {
    return 0;
  }
  return Math.max(0, Math.floor(numeric / 10) - 1);
}
function overviewPositionFromSlot(slot) {
  return (slot + 1) * 10;
}
function getOverviewWidgetGridWidth(widget) {
  const fallback = widget.widget_type === "scalar" ? 1 : OVERVIEW_GRID_COLUMNS;
  const width = Number(widget.grid_width || fallback);
  return Math.max(1, Math.min(OVERVIEW_GRID_COLUMNS, Number.isFinite(width) ? Math.floor(width) : fallback));
}
function getOverviewGridPlacementStyle(slot, width = 1) {
  const columnStart = slot % OVERVIEW_GRID_COLUMNS + 1;
  const rowStart = Math.floor(slot / OVERVIEW_GRID_COLUMNS) + 1;
  return `grid-column: ${columnStart} / span ${width}; grid-row: ${rowStart};`;
}
function renderOverviewGridWidget(widget, slot) {
  const result = widget.result;
  const width = getOverviewWidgetGridWidth(widget);
  return `
    <section class="widget-card overview-widget-slot overview-widget-${escapeHtml(widget.widget_type)}" draggable="true" data-overview-widget-key="${escapeHtml(widget.widget_key)}" data-overview-widget-slot="${escapeHtml(slot)}" style="${escapeHtml(getOverviewGridPlacementStyle(slot, width))}">
      <div class="widget-card-header">
        <div>
          <span>${escapeHtml(widget.datasource_key)}</span>
          <strong>${escapeHtml(widget.label)}</strong>
        </div>
        <div class="widget-card-actions">
          ${renderEditWidgetButton(widget.widget_key)}
        </div>
      </div>
      <div class="widget-card-main">
        ${renderOverviewWidgetBody(widget, result)}
      </div>
      <button class="overview-resize-handle overview-resize-handle-left" type="button" data-overview-resize-handle="${escapeHtml(widget.widget_key)}" data-edge="left" aria-label="Resize widget from left edge" title="Resize from left edge"></button>
      <button class="overview-resize-handle overview-resize-handle-right" type="button" data-overview-resize-handle="${escapeHtml(widget.widget_key)}" data-edge="right" aria-label="Resize widget from right edge" title="Resize from right edge"></button>
    </section>`;
}
function renderOverviewWidgetBody(widget, result) {
  if (!result) {
    return `<div class="empty-state">No data yet.</div>`;
  }
  if (result.status !== "ok") {
    return `<div class="error">${escapeHtml(result.error || "Widget query failed.")}</div>`;
  }
  if ((result.result_mode || widget.result_mode) === "interpretation") {
    const interpretation = result.interpretation || result.answer || result.value || "-";
    return `<div class="widget-card-value widget-card-interpretation">${renderWidgetContent(interpretation)}</div>`;
  }
  if (widget.widget_type === "scalar") {
    return `<div class="widget-card-value">${renderWidgetContent(result.value ?? "-")}</div>`;
  }
  if (widget.widget_type === "table") {
    return renderOverviewTable(widget.widget_key, result);
  }
  return renderTimeSeriesChart(result);
}
function renderOverviewEmptySlot(logicalSlot, displaySlot = logicalSlot) {
  return `
    <section class="overview-empty-slot" style="${escapeHtml(getOverviewGridPlacementStyle(displaySlot, 1))}">
      <div class="overview-empty-slot-actions">
        <button type="button" data-overview-empty-slot="${escapeHtml(logicalSlot)}" aria-label="Add widget to slot ${escapeHtml(displaySlot + 1)}">+</button>
        <button type="button" class="icon-button danger" data-overview-remove-slot="${escapeHtml(logicalSlot)}" aria-label="Remove empty slot ${escapeHtml(displaySlot + 1)}" title="Remove empty slot">
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            <path d="M3 6h18" />
            <path d="M8 6V4h8v2" />
            <path d="M19 6l-1 14H6L5 6" />
            <path d="M10 11v5" />
            <path d="M14 11v5" />
          </svg>
        </button>
      </div>
      ${state.overviewPlacementSlot === logicalSlot ? renderOverviewPlacementPanel(logicalSlot) : ""}
    </section>`;
}
function renderOverviewPlacementPanel(slot) {
  const availableWidgets = state.overviewWidgetConfigs.filter((widget) => widget.active === false);
  return `
    <div class="overview-placement-panel">
      <label>Widget
        <select data-overview-placement-select="${escapeHtml(slot)}" ${availableWidgets.length ? "" : "disabled"}>
          ${availableWidgets.length ? availableWidgets.map((widget) => `<option value="${escapeHtml(widget.widget_key)}">${escapeHtml(widget.label)} (${escapeHtml(widget.widget_key)}, ${escapeHtml(getOverviewWidgetGridWidth(widget))} cols)</option>`).join("") : `<option>No inactive widgets</option>`}
        </select>
      </label>
      <div class="button-row">
        <button type="button" data-overview-place-widget="${escapeHtml(slot)}" ${availableWidgets.length ? "" : "disabled"}>Add selected</button>
        <button type="button" class="primary" data-overview-new-widget="${escapeHtml(slot)}">New widget</button>
      </div>
    </div>`;
}
function renderEditWidgetButton(widgetKey) {
  return `
    <button class="icon-button" type="button" data-edit-overview-widget="${escapeHtml(widgetKey)}" aria-label="Edit widget source" title="Edit source">
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d="M12 20h9" />
        <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
      </svg>
    </button>`;
}
function renderOverviewWidgetModal() {
  const widget = getOverviewEditorWidget();
  if (!widget) return "";
  return `
    <div class="modal-backdrop" data-overview-widget-backdrop>
      <section class="modal-panel" role="dialog" aria-modal="true" aria-labelledby="overview-widget-modal-title">
        <div class="modal-header">
          <div>
            <h2 id="overview-widget-modal-title">Edit widget source</h2>
            <p>${escapeHtml(widget.label)}</p>
          </div>
          <button type="button" data-close-overview-widget>Close</button>
        </div>
        <form class="form-grid" data-overview-widget-form="${escapeHtml(widget.widget_key)}">
        <input type="hidden" name="active" value="${escapeHtml(widget.active !== false ? "true" : "false")}" />
        <label>Label<input name="label" value="${escapeHtml(widget.label)}" /></label>
        <div class="subgrid">
          <label>Type<select name="widget_type">${renderWidgetTypeOptions(widget.widget_type)}</select></label>
          <label>Datasource<select name="datasource_key">${renderOverviewDatasourceOptions(widget.datasource_key)}</select></label>
        </div>
        <label>Result mode<select name="result_mode">${renderOverviewWidgetResultModeOptions(widget.result_mode)}</select></label>
        <label>Question<textarea name="question">${escapeHtml(widget.question)}</textarea></label>
        <label>Generated SQL<textarea class="textarea-small" readonly>${escapeHtml(widget.result?.sql || widget.sql || "")}</textarea></label>
        <div class="form-actions">
          <button type="button" data-close-overview-widget>Cancel</button>
          <button class="primary" type="submit">Save and refresh</button>
        </div>
        </form>
      </section>
    </div>`;
}
function renderConfirmDialog() {
  const dialog = state.confirmDialog;
  if (!dialog) return "";
  return `
    <div class="modal-backdrop" data-confirm-backdrop>
      <section class="modal-panel modal-panel-small" role="dialog" aria-modal="true" aria-labelledby="confirm-dialog-title">
        <div class="modal-header">
          <div>
            <h2 id="confirm-dialog-title">${escapeHtml(dialog.title || "Confirm action")}</h2>
            <p>${escapeHtml(dialog.message || "")}</p>
          </div>
        </div>
        <div class="panel-actions modal-actions">
          <button type="button" data-confirm-cancel>Cancel</button>
          <button type="button" class="danger" data-confirm-accept>${escapeHtml(dialog.confirmLabel || "Delete")}</button>
        </div>
      </section>
    </div>`;
}
function requestConfirmation({ title, message, confirmLabel = "Delete" }) {
  return new Promise((resolve) => {
    state.confirmDialog = { title, message, confirmLabel, resolve };
    render();
  });
}
function closeConfirmDialog(accepted) {
  const dialog = state.confirmDialog;
  if (!dialog) return;
  state.confirmDialog = null;
  dialog.resolve(accepted);
  render();
}
function getOverviewEditorWidget() {
  if (!state.overviewEditorWidgetKey) return null;
  return (state.overview?.widgets || []).find(
    (widget) => widget.widget_key === state.overviewEditorWidgetKey
  ) || null;
}
function renderWidgetTypeOptions(selected) {
  return ["scalar", "timeseries", "table"].map((value) => `<option value="${value}" ${value === selected ? "selected" : ""}>${value}</option>`).join("");
}
function renderOverviewDatasourceOptions(selected) {
  const datasources = state.overviewWidgetDatasources.length ? state.overviewWidgetDatasources : state.overview?.datasources || [];
  return datasources.map((item) => `<option value="${escapeHtml(item.connector_key)}" ${item.connector_key === selected ? "selected" : ""}>${escapeHtml(item.name)} (${escapeHtml(item.connector_key)})</option>`).join("");
}
function renderTimeSeriesChart(result) {
  const points = normalizeChartPoints(result);
  if (!points.length) {
    return `<div class="empty-state">No data yet.</div>`;
  }
  const max = Math.max(...points.map((point) => point.value), 1);
  const dates = Array.from(new Set(points.map((point) => point.date)));
  const series = Array.from(new Set(points.map((point) => point.series)));
  return `
    <div class="chart">
      ${dates.map((date) => {
    const datePoints = points.filter((point) => point.date === date);
    return `<div class="chart-row">
          <div class="chart-date">${escapeHtml(date)}</div>
          <div class="chart-bars">
            ${datePoints.map((point) => `<div class="chart-bar" title="${escapeHtml(`${point.series}: ${point.value}`)}" style="width: ${Math.max(4, point.value / max * 100)}%"><span>${escapeHtml(point.series)}: ${escapeHtml(point.value)}</span></div>`).join("")}
          </div>
        </div>`;
  }).join("")}
    </div>
    <div class="chart-legend">${series.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>`;
}
function normalizeChartPoints(result) {
  const rows = result.rows || [];
  const columns = result.columns || Object.keys(rows[0] || {});
  if (!rows.length || columns.length < 2) {
    return [];
  }
  const dateColumn = columns[0];
  if (columns.length === 3 && rows.some((row) => !isNumeric(row[columns[1]]) && isNumeric(row[columns[2]]))) {
    return rows.filter((row) => isNumeric(row[columns[2]])).map((row) => ({
      date: formatChartDate(row[dateColumn]),
      series: String(row[columns[1]] ?? "series"),
      value: Number(row[columns[2]])
    }));
  }
  return rows.flatMap(
    (row) => columns.slice(1).filter((column) => isNumeric(row[column])).map((column) => ({
      date: formatChartDate(row[dateColumn]),
      series: column,
      value: Number(row[column])
    }))
  );
}
function formatChartDate(value) {
  return String(value ?? "").slice(0, 10);
}
function isNumeric(value) {
  return value !== null && value !== "" && !Array.isArray(value) && Number.isFinite(Number(value));
}
function renderOverviewTable(widgetKey, result) {
  const rows = result.rows || [];
  const columns = result.columns?.length ? result.columns : Object.keys(rows[0] || {});
  if (!columns.length) {
    return `<div class="empty-state">No data yet.</div>`;
  }
  const totalPages = Math.max(1, Math.ceil(rows.length / OVERVIEW_TABLE_PAGE_SIZE));
  const currentPage = Math.min(
    Math.max(state.overviewTablePages[widgetKey] || 0, 0),
    totalPages - 1
  );
  const start = currentPage * OVERVIEW_TABLE_PAGE_SIZE;
  const pageRows = rows.slice(start, start + OVERVIEW_TABLE_PAGE_SIZE);
  if (state.overviewTablePages[widgetKey] !== currentPage) {
    state.overviewTablePages[widgetKey] = currentPage;
  }
  return `
    <div class="table-wrap overview-table-wrap">
      <table>
        <thead><tr>${columns.map((column) => `<th>${escapeHtml(column)}</th>`).join("")}</tr></thead>
        <tbody>
          ${pageRows.length ? pageRows.map((row) => `<tr>${columns.map((column) => `<td>${formatOverviewTableCell(row[column])}</td>`).join("")}</tr>`).join("") : `<tr><td colspan="${escapeHtml(columns.length)}" class="empty-state">No rows.</td></tr>`}
        </tbody>
      </table>
    </div>
    <div class="table-pagination" aria-label="${escapeHtml(`${widgetKey} pagination`)}">
      <span class="table-pagination-info">${escapeHtml(rows.length ? `${start + 1}-${Math.min(start + OVERVIEW_TABLE_PAGE_SIZE, rows.length)} of ${rows.length}` : "0 rows")}</span>
      <div class="button-row">
        <button type="button" data-overview-table-page="${escapeHtml(widgetKey)}" data-page="${escapeHtml(currentPage - 1)}" ${currentPage === 0 ? "disabled" : ""}>Previous</button>
        <span class="badge">Page ${escapeHtml(currentPage + 1)} / ${escapeHtml(totalPages)}</span>
        <button type="button" data-overview-table-page="${escapeHtml(widgetKey)}" data-page="${escapeHtml(currentPage + 1)}" ${currentPage >= totalPages - 1 ? "disabled" : ""}>Next</button>
      </div>
    </div>`;
}
function formatOverviewTableCell(value) {
  if (value === null || value === void 0 || value === "") {
    return `<span class="muted">-</span>`;
  }
  if (typeof value === "object") {
    return `<code>${escapeHtml(JSON.stringify(value))}</code>`;
  }
  return renderWidgetContent(value);
}
function renderDataAudit() {
  return `
    <section class="panel">
      <div class="panel-header">
        <h2>Data query audit</h2>
        <div class="audit-controls">
          <form id="data-audit-filter-form" class="form-actions">
            <label>Type<select id="data-audit-type">${renderDataAuditTypeOptions()}</select></label>
            <label>Output classification<select id="data-audit-output-classification">${renderOutputClassificationOptions()}</select></label>
            <label>SQL contains<input id="data-audit-sql-contains" name="sql_contains" value="${escapeHtml(state.dataAuditSqlContains)}" /></label>
            <button type="submit">Apply</button>
          </form>
          <form id="retention-form" class="form-actions">
            <label>Retention days<input name="retention" type="number" min="1" max="3650" value="${escapeHtml(state.auditSettings?.data_query_retention_days ?? 90)}" /></label>
            <button class="primary" type="submit">Save</button>
          </form>
        </div>
      </div>
      <div class="table-wrap"><table>
        <thead><tr><th>Time</th><th>Type</th><th>Output classification</th><th>Learning</th><th>User</th><th>Datasource</th><th>Question</th><th>Answer</th><th>SQL</th><th>Metadata</th></tr></thead>
        <tbody>${state.dataAudit.map((item) => `<tr><td>${escapeHtml(formatAuditTime(item.occurred_at))}</td><td>${escapeHtml(item.audit_type || "info")}</td><td>${escapeHtml(item.output_classification || "unknown")}</td><td>${renderAuditLearning(item)}</td><td>${escapeHtml(item.user_id)}</td><td>${escapeHtml(item.datasource_id)}</td><td>${escapeHtml(item.question)}</td><td>${escapeHtml(item.answer)}</td><td><code>${escapeHtml(item.sql)}</code></td><td>${renderAuditMetadata(item)}</td></tr>`).join("")}</tbody>
      </table></div>
    </section>`;
}
function renderAuditMetadata(item) {
  const metadata = item.metadata || {};
  if (!Object.keys(metadata).length) return "";
  return `<pre class="metadata-json">${escapeHtml(JSON.stringify(metadata, null, 2))}</pre>`;
}
function renderAuditLearning(item) {
  const learning = item.metadata?.business_logic_learning;
  if (!learning) return "";
  return `
    <span>${escapeHtml(learning.message || "")}</span>
    ${learning.suggestion_id ? `<button type="button" data-open-business-logic>Open suggestions</button>` : ""}`;
}
function renderDataAuditTypeOptions() {
  return dataAuditTypes.map((type) => `<option value="${escapeHtml(type.value)}" ${state.dataAuditType === type.value ? "selected" : ""}>${escapeHtml(type.label)}</option>`).join("");
}
function renderOutputClassificationOptions() {
  return outputClassifications.map((item) => `<option value="${escapeHtml(item.value)}" ${state.dataAuditOutputClassification === item.value ? "selected" : ""}>${escapeHtml(item.label)}</option>`).join("");
}
function renderWidgets() {
  const selectedWidget = getSelectedOverviewWidgetConfig();
  const creating = state.selectedOverviewWidgetKey === "__new__";
  return `
    <div class="split widgets-editor">
      <section class="panel">
        <div class="panel-header">
          <h2>Widgets</h2>
          <button type="button" id="new-overview-widget">New</button>
        </div>
        <div class="panel-body list widget-config-list">
          ${state.overviewWidgetConfigs.length ? state.overviewWidgetConfigs.map((widget) => renderWidgetConfigListItem(widget, selectedWidget?.widget_key === widget.widget_key && !creating)).join("") : `<p class="muted">No widgets defined.</p>`}
        </div>
      </section>
      <section class="panel">
        <div class="panel-header">
          <h2>${creating ? "New widget" : escapeHtml(selectedWidget?.label || "Widget settings")}</h2>
        </div>
        <div class="panel-body">
          ${creating || selectedWidget ? renderOverviewWidgetSettingsForm(selectedWidget) : `<p class="muted">Select a widget to edit its settings.</p>`}
        </div>
      </section>
    </div>`;
}
function renderWidgetConfigListItem(widget, active) {
  return `
    <div class="widget-config-row ${active ? "active" : ""}">
      <input type="checkbox" data-overview-widget-active="${escapeHtml(widget.widget_key)}" aria-label="Enable ${escapeHtml(widget.label)}" ${widget.active ? "checked" : ""} />
      <button class="widget-config-select" type="button" data-overview-widget-select="${escapeHtml(widget.widget_key)}">
        <strong>${escapeHtml(widget.label)}</strong>
        <span>${escapeHtml(widget.widget_key)} \xB7 ${escapeHtml(widget.widget_type)} \xB7 ${escapeHtml(formatOverviewWidgetResultMode(widget.result_mode))}</span>
      </button>
      <button class="icon-button danger widget-config-delete" type="button" data-overview-widget-delete="${escapeHtml(widget.widget_key)}" aria-label="Delete ${escapeHtml(widget.label)}" title="Delete widget">
        <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <path d="M3 6h18" />
          <path d="M8 6V4h8v2" />
          <path d="M19 6l-1 14H6L5 6" />
          <path d="M10 11v5" />
          <path d="M14 11v5" />
        </svg>
      </button>
    </div>`;
}
function getSelectedOverviewWidgetConfig() {
  if (state.selectedOverviewWidgetKey === "__new__") {
    return null;
  }
  return state.overviewWidgetConfigs.find((widget) => widget.widget_key === state.selectedOverviewWidgetKey) || state.overviewWidgetConfigs[0] || null;
}
function getOverviewWidgetFormPosition(widget) {
  if (widget) {
    return widget.position || 100;
  }
  return overviewPositionFromSlot(state.overviewPlacementSlot ?? 0);
}
function getDefaultOverviewWidgetGridWidth(widgetType) {
  return widgetType === "scalar" ? 1 : OVERVIEW_GRID_COLUMNS;
}
function renderOverviewWidgetResultModeOptions(selected = "data") {
  const options = [
    { value: "data", label: "Zwr\xF3\u0107 dane" },
    { value: "interpretation", label: "Interpretuj dane" }
  ];
  return options.map((option) => `<option value="${escapeHtml(option.value)}" ${option.value === selected ? "selected" : ""}>${escapeHtml(option.label)}</option>`).join("");
}
function formatOverviewWidgetResultMode(value = "data") {
  return value === "interpretation" ? "interpretation" : "data";
}
function renderOverviewWidgetSettingsForm(widget) {
  const creating = widget === null;
  const resultMode = widget?.result_mode || "data";
  return `
    <form class="form-grid" id="overview-widget-settings-form" data-widget-mode="${creating ? "create" : "update"}" data-widget-key="${escapeHtml(widget?.widget_key || "")}">
      ${creating ? `<label>Widget key<input name="widget_key" value="" placeholder="custom_widget_key" /></label>` : `<input type="hidden" name="widget_key" value="${escapeHtml(widget?.widget_key || "")}" />`}
      <label>Label<input name="label" value="${escapeHtml(widget?.label || "")}" /></label>
      <div class="subgrid">
        <label>Type<select name="widget_type">${renderWidgetTypeOptions(widget?.widget_type || "scalar")}</select></label>
        <label>Datasource<select name="datasource_key">${renderOverviewDatasourceOptions(widget?.datasource_key || "metadata-db")}</select></label>
      </div>
      <label>Result mode<select name="result_mode">${renderOverviewWidgetResultModeOptions(resultMode)}</select></label>
      <label class="inline-check"><input name="active" type="checkbox" ${widget?.active || creating ? "checked" : ""} /> Enabled</label>
      <label>Question<textarea name="question">${escapeHtml(widget?.question || "")}</textarea></label>
      <label>Generated SQL<textarea class="textarea-small" readonly>${escapeHtml(widget?.sql || "")}</textarea></label>
      <div class="form-actions">
        <button class="primary" type="submit">${creating ? "Create and refresh" : "Save and refresh"}</button>
      </div>
    </form>`;
}
function renderPrompts() {
  const selected = state.prompts.find((prompt) => prompt.prompt_key === state.selectedPromptKey) || state.prompts[0];
  return `
    <div class="split">
      <section class="panel">
        <div class="panel-header"><h2>Prompt templates</h2></div>
        <div class="panel-body list">${state.prompts.map((prompt) => `<button data-prompt="${prompt.prompt_key}" class="${selected?.prompt_key === prompt.prompt_key ? "active" : ""}"><strong>${escapeHtml(prompt.name)}</strong><br /><span>v${escapeHtml(prompt.version)} ${prompt.active ? "active" : "inactive"}</span></button>`).join("")}</div>
      </section>
      <section class="panel">
        <div class="panel-header"><h2>${escapeHtml(selected?.name || "Prompt")}</h2></div>
        <div class="panel-body">${selected ? renderPromptForm(selected) : ""}</div>
      </section>
    </div>`;
}
function renderPromptForm(prompt) {
  return `
    <form id="prompt-form" class="form-grid">
      <input type="hidden" name="prompt_key" value="${escapeHtml(prompt.prompt_key)}" />
      <label>Name<input name="name" value="${escapeHtml(prompt.name)}" /></label>
      <label>Description<input name="description" value="${escapeHtml(prompt.description)}" /></label>
      <label>System prompt<textarea name="system_prompt">${escapeHtml(prompt.system_prompt)}</textarea></label>
      <label>User prompt template<textarea name="user_prompt_template">${escapeHtml(prompt.user_prompt_template)}</textarea></label>
      <label class="inline-check"><input name="active" type="checkbox" ${prompt.active ? "checked" : ""} /> Active</label>
      <div class="form-actions"><button class="primary" type="submit">Save prompt</button></div>
    </form>`;
}
function renderDatasources() {
  const selected = getSelectedDatasource();
  return `
    <div class="split">
      <section class="panel">
        <div class="panel-header"><h2>Datasources</h2><button id="new-datasource">New</button></div>
        <div class="panel-body list datasource-list">${state.datasources.map((connector) => `<button data-datasource="${connector.id}" class="${selected?.id === connector.id ? "active" : ""}"><strong>${escapeHtml(connector.name)}</strong><br /><span>${escapeHtml(connector.database_type)} ${connector.active ? "active" : ""}</span></button>`).join("")}</div>
      </section>
      <section class="panel">
        <div class="panel-header"><h2>${selected ? escapeHtml(selected.name) : "New datasource"}</h2></div>
        <div class="panel-body">${renderDatasourceForm(selected)}</div>
      </section>
    </div>
    ${selected ? renderDatasourceSchema() : ""}`;
}
function getSelectedDatasource() {
  if (state.selectedDatasourceId === "new") return null;
  return state.datasources.find((item) => item.id === state.selectedDatasourceId) || state.datasources[0] || null;
}
function renderDatasourceForm(connector) {
  const systemManaged = connector?.system_managed === true;
  const selectedTypeKey = connector?.database_type || state.datasourceTypes[0]?.type_key || "";
  const selectedType = getDatasourceType(selectedTypeKey);
  const selectedSqlDialect = connector?.sql_dialect || selectedType?.default_sql_dialect || "";
  const unavailableType = Boolean(selectedTypeKey && !selectedType);
  const disabled = systemManaged || unavailableType || !selectedType ? "disabled" : "";
  const connectorDescription = selectedType?.description || (unavailableType ? `Connector type '${selectedTypeKey}' is unavailable. Install or enable its plugin before editing this datasource.` : "No connector types are available. Install or enable a connector plugin.");
  const connectionValues = datasourceConnectionConfigDefaults(selectedType, connector);
  return `
    <form id="datasource-form" class="form-grid">
      <input type="hidden" name="id" value="${escapeHtml(connector?.id || "")}" />
      ${systemManaged ? `<div class="badge">System managed</div>` : ""}
      <label>Connector key<input name="connector_key" ${connector || systemManaged ? "readonly" : ""} ${disabled} value="${escapeHtml(connector?.connector_key || "")}" /></label>
      <label>Name<input name="name" ${disabled} value="${escapeHtml(connector?.name || "")}" /></label>
      <div class="subgrid">
        <label>Connector type<select id="datasource-type" name="database_type" ${disabled}>${renderDatasourceTypeOptions(selectedTypeKey)}</select></label>
        <label>SQL dialect<input id="datasource-sql-dialect" readonly ${disabled} value="${escapeHtml(selectedSqlDialect)}" /></label>
      </div>
      <p id="datasource-type-description" class="muted">${escapeHtml(connectorDescription)}</p>
      <div id="datasource-connection-fields" class="subgrid">
        ${renderDatasourceConnectionFields(selectedType, connectionValues, disabled)}
      </div>
      <label class="inline-check"><input name="active" type="checkbox" ${connector?.active ? "checked" : ""} ${disabled} /> Active datasource</label>
      <div class="button-row">
        <button type="button" id="test-datasource" ${disabled}>Test</button>
        <button type="button" id="introspect-datasource" ${connector ? "" : "disabled"}>Schema introspection</button>
        <button type="button" id="activate-datasource" ${connector && !connector.active && !systemManaged ? "" : "disabled"}>Activate</button>
        ${connector && !systemManaged ? `<button type="button" class="danger" id="delete-datasource">Delete</button>` : ""}
        <button class="primary" type="submit" ${systemManaged ? "disabled" : ""}>${connector ? "Save" : "Create"}</button>
      </div>
    </form>`;
}
function getDatasourceType(typeKey) {
  return state.datasourceTypes.find((item) => item.type_key === typeKey) || null;
}
function renderDatasourceTypeOptions(selected) {
  const datasourceTypes = [...state.datasourceTypes];
  if (selected && !getDatasourceType(selected)) {
    datasourceTypes.unshift({
      type_key: selected,
      label: `${selected} (plugin unavailable)`,
      description: "",
      sql_dialects: [],
      default_sql_dialect: "",
      config_schema: {}
    });
  }
  if (!datasourceTypes.length) {
    return `<option value="" selected>No connector types available</option>`;
  }
  return datasourceTypes.map((item) => `<option value="${escapeHtml(item.type_key)}" ${item.type_key === selected ? "selected" : ""}>${escapeHtml(item.label)}</option>`).join("");
}
function renderSqlDialectOptions(datasourceType, selected) {
  const dialects = [...datasourceType?.sql_dialects || []];
  if (selected && !dialects.includes(selected)) {
    dialects.unshift(selected);
  }
  if (!dialects.length) {
    return `<option value="" selected>No SQL dialect available</option>`;
  }
  return dialects.map((value) => `<option value="${escapeHtml(value)}" ${selected === value ? "selected" : ""}>${escapeHtml(value)}</option>`).join("");
}
function datasourceConnectionConfigDefaults(datasourceType, connector = null) {
  const values = {};
  const properties = datasourceType?.config_schema?.properties || {};
  const usesGeneratedFields = datasourceUsesGeneratedConnectionFields(datasourceType);
  Object.entries(properties).forEach(([key, schema]) => {
    if (key === "database_url" && usesGeneratedFields) return;
    values[key] = schema?.default ?? "";
  });
  if (!connector?.database_url) return values;
  if (!usesGeneratedFields) {
    return { ...values, database_url: connector.database_url };
  }
  return { ...values, ...parseDatasourceUrl(connector.database_type, connector.database_url) };
}
function datasourceUsesGeneratedConnectionFields(datasourceType) {
  const required = datasourceType?.config_schema?.required || [];
  return required.some((key) => key !== "database_url");
}
function parseDatasourceUrl(databaseType, databaseUrl) {
  if (!databaseUrl) return {};
  if (databaseType === "sqlite") {
    return { database_path: databaseUrl.replace(/^sqlite:\/\/\/?/, "") };
  }
  try {
    const parsed = new URL(databaseUrl);
    const query = Object.fromEntries(parsed.searchParams.entries());
    return {
      host: parsed.hostname || "",
      port: parsed.port || "",
      database: decodeURIComponent(parsed.pathname.replace(/^\//, "")),
      username: decodeURIComponent(parsed.username || ""),
      password: decodeURIComponent(parsed.password || ""),
      ...query
    };
  } catch {
    return {};
  }
}
function renderDatasourceConnectionFields(datasourceType, values = {}, disabled = "") {
  const properties = datasourceType?.config_schema?.properties || {};
  const usesGeneratedFields = datasourceUsesGeneratedConnectionFields(datasourceType);
  return Object.entries(properties)
    .filter(([key]) => key !== "database_url" || !usesGeneratedFields)
    .map(([key, schema]) => {
      const type = schema?.format === "password" ? "password" : schema?.type === "integer" ? "number" : "text";
      const value = values[key] ?? schema?.default ?? "";
      const placeholder = schema?.description || "";
      const inputName = key === "database_url" ? "database_url" : `connection_${key}`;
      const dataAttribute = key === "database_url" ? "" : `data-connection-field="${escapeHtml(key)}"`;
      return `<label>${escapeHtml(schema?.title || key)}<input ${dataAttribute} name="${escapeHtml(inputName)}" type="${type}" ${disabled} placeholder="${escapeHtml(placeholder)}" value="${escapeHtml(value)}" /></label>`;
    })
    .join("");
}
function collectDatasourceConnectionConfig(form) {
  const config = {};
  form.querySelectorAll("[data-connection-field]").forEach((input) => {
    config[input.dataset.connectionField] = input.value;
  });
  return config;
}
function syncDatasourceTypeFields(event) {
  const typeKey = event.currentTarget.value;
  const datasourceType = getDatasourceType(typeKey);
  const sqlDialect = document.querySelector("#datasource-sql-dialect");
  const description = document.querySelector("#datasource-type-description");
  const connectionFields = document.querySelector("#datasource-connection-fields");
  if (sqlDialect) {
    sqlDialect.value = datasourceType?.default_sql_dialect || "";
  }
  if (description) {
    description.textContent = datasourceType?.description || "Connector type is unavailable.";
  }
  if (connectionFields) {
    connectionFields.innerHTML = renderDatasourceConnectionFields(
      datasourceType,
      datasourceConnectionConfigDefaults(datasourceType),
      ""
    );
  }
}
function renderModeOptions(selected, values) {
  return values.map((value) => `<option value="${value}" ${selected === value ? "selected" : ""}>${value}</option>`).join("");
}
function renderDatasourceSchema() {
  const schema = state.datasourceSchema?.item;
  const rawTables = schema?.raw_schema?.tables || [];
  const displayTables = [...rawTables].sort((a, b) => a.name.localeCompare(b.name));
  const tableSettings = schema?.table_settings?.tables || {};
  const draftTables = schema ? getDatasourceSchemaDraftTables(rawTables, tableSettings) : {};
  const visibleTables = state.datasourceSchemaShowEnabledOnly ? displayTables.filter((table) => draftTables[table.name]?.selected !== false) : displayTables;
  const selectedTable = schema ? getSelectedDatasourceSchemaObject(displayTables, visibleTables) : null;
  const selectedSettings = selectedTable ? draftTables[selectedTable.name] || {} : {};
  return `
    <section class="panel">
      <div class="panel-header"><h2>Schema introspection</h2><span class="badge">${escapeHtml(schema?.introspected_at || "not cached")}</span></div>
      <div class="panel-body">
        ${state.datasourceSchemaLoading ? `<p class="muted">loading schema</p>` : state.datasourceSchemaError ? `<p class="error">${escapeHtml(state.datasourceSchemaError)}</p>` : schema ? `
          <form id="datasource-schema-form" class="schema-editor">
            <section class="schema-object-list">
              <div class="schema-object-list-header">
                <label class="inline-check"><input id="schema-show-enabled-only" type="checkbox" ${state.datasourceSchemaShowEnabledOnly ? "checked" : ""} /> Show enabled objects only</label>
              </div>
              <div class="schema-object-list-body">
                ${visibleTables.length ? visibleTables.map((table) => renderDatasourceObjectListItem(table, draftTables[table.name] || {}, selectedTable?.name === table.name)).join("") : `<p class="muted schema-object-empty">No enabled objects.</p>`}
              </div>
            </section>
            <section class="schema-object-details">
              ${selectedTable ? renderDatasourceObjectDetails(selectedTable, selectedSettings) : `<p class="muted">Select a table or view to edit its guidance.</p>`}
              <div class="form-actions"><button class="primary" type="submit">Save schema settings</button></div>
            </section>
          </form>` : `<p class="muted">Run schema introspection to cache tables, views, keys and relationships.</p>`}
      </div>
    </section>`;
}
function getDatasourceSchemaDraftTables(rawTables, tableSettings) {
  if (!state.datasourceSchemaDraftTables) {
    state.datasourceSchemaDraftTables = {};
  }
  for (const table of rawTables) {
    if (state.datasourceSchemaDraftTables[table.name]) continue;
    const settings = tableSettings[table.name] || {};
    state.datasourceSchemaDraftTables[table.name] = {
      selected: settings.selected !== false,
      description: settings.description || "",
      primary_key_prompt: settings.primary_key_prompt || "",
      foreign_key_prompt: settings.foreign_key_prompt || "",
      join_logic: settings.join_logic || ""
    };
  }
  return state.datasourceSchemaDraftTables;
}
function getSelectedDatasourceSchemaObject(rawTables, visibleTables) {
  const current = rawTables.find((table) => table.name === state.datasourceSchemaSelectedObjectName);
  const currentIsVisible = visibleTables.some((table) => table.name === current?.name);
  if (current && currentIsVisible) return current;
  const fallback = visibleTables[0] || null;
  state.datasourceSchemaSelectedObjectName = fallback?.name || "";
  return fallback;
}
function renderBusinessLogicSuggestions() {
  const datasource = state.businessLogicDatasource;
  const datasources = state.businessLogicDatasources || (datasource ? [datasource] : []);
  const datasourceLabel = datasources.length
    ? datasources.map((item) => item.connector_key).join(", ")
    : "no active datasource";
  return `
    <section class="panel">
      <div class="panel-header">
        <h2>Business logic suggestions</h2>
        <span class="badge">${escapeHtml(datasourceLabel)}</span>
      </div>
      <div class="table-wrap"><table>
        <thead><tr><th>Use</th><th>Status</th><th>Safety</th><th>Rule</th><th>Error</th><th>Confidence</th><th>Actions</th></tr></thead>
        <tbody>${state.businessLogic.map((item) => `
          <tr>
            <td><input type="checkbox" data-business-logic-toggle="${escapeHtml(item.id)}" ${item.enabled ? "checked" : ""} /></td>
            <td>${escapeHtml(item.status)}</td>
            <td>${escapeHtml(item.safety)}</td>
            <td><strong>${escapeHtml(item.title)}</strong><br /><span class="muted">${escapeHtml(item.rule_text)}</span></td>
            <td>${escapeHtml(item.error_category)}</td>
            <td>${escapeHtml(Math.round(Number(item.confidence || 0) * 100))}%</td>
            <td>
              <div class="button-row">
                <button type="button" data-business-logic-edit="${escapeHtml(item.id)}">Edit</button>
                <button type="button" class="danger" data-business-logic-delete="${escapeHtml(item.id)}">Delete</button>
              </div>
            </td>
          </tr>`).join("")}</tbody>
      </table></div>
      ${state.businessLogic.length ? "" : `<div class="panel-body"><p class="muted">No suggestions for the active datasources.</p></div>`}
    </section>
    ${renderBusinessLogicEditorModal()}`;
}
function renderBusinessLogicEditorModal() {
  const suggestion = getBusinessLogicEditorSuggestion();
  if (!suggestion) return "";
  return `
    <div class="modal-backdrop" data-business-logic-backdrop>
      <section class="modal-panel modal-panel-small" role="dialog" aria-modal="true" aria-labelledby="business-logic-modal-title">
        <div class="modal-header">
          <div>
            <h2 id="business-logic-modal-title">Edit business logic</h2>
            <p>${escapeHtml(suggestion.error_category || "business logic suggestion")}</p>
          </div>
          <button type="button" data-close-business-logic-editor>Close</button>
        </div>
        <form class="form-grid" data-business-logic-form="${escapeHtml(suggestion.id)}">
          <label>Title<input name="title" value="${escapeHtml(suggestion.title || "")}" /></label>
          <label>Rule text<textarea class="textarea-small" name="rule_text">${escapeHtml(suggestion.rule_text || "")}</textarea></label>
          <div class="form-actions">
            <button type="button" data-close-business-logic-editor>Cancel</button>
            <button class="primary" type="submit">Save</button>
          </div>
        </form>
      </section>
    </div>`;
}
function getBusinessLogicEditorSuggestion() {
  if (state.businessLogicEditorId === null) return null;
  return state.businessLogic.find((item) => Number(item.id) === state.businessLogicEditorId) || null;
}
function renderLlmConfig() {
  const config = state.llmConfig || {};
  const apiKeyStatus = config.api_key_configured ? `Configured (${escapeHtml(config.api_key_preview || "hidden")})` : "Not configured";
  const apiKeyPlaceholder = config.api_key_configured ? "Leave blank to keep current key" : "Enter API key";
  return `
    <section class="panel">
      <div class="panel-header"><h2>LLM configuration</h2></div>
      <div class="panel-body">
        <form id="llm-config-form" class="form-grid">
          <label>Provider<input name="provider" value="${escapeHtml(config.provider || "openai-compatible")}" /></label>
          <label>Base URL<input name="base_url" value="${escapeHtml(config.base_url || "")}" /></label>
          <label>API key <span class="muted">${apiKeyStatus}</span><input name="api_key" type="password" value="" placeholder="${apiKeyPlaceholder}" autocomplete="new-password" /></label>
          <label>Model<input name="model" value="${escapeHtml(config.model || "")}" /></label>
          <label>LLM timeout seconds<input name="timeout_seconds" type="number" min="1" max="600" value="${escapeHtml(config.timeout_seconds || 60)}" /></label>
          <label>Extra body JSON<textarea name="extra_body">${escapeHtml(config.extra_body_json || "{}")}</textarea></label>
          <div class="form-actions">
            <button
              aria-label="Test LLM configuration"
              class="icon-button"
              id="test-llm-config"
              title="Test LLM configuration"
              type="button"
            >\u{1F9EA}</button>
            <button class="primary" type="submit">Save LLM configuration</button>
          </div>
        </form>
      </div>
    </section>`;
}
function renderReasoningConfig() {
  const config = state.reasoningConfig || {};
  return `
    <section class="panel">
      <div class="panel-header"><h2>Reasoning</h2></div>
      <div class="panel-body">
        <form id="reasoning-config-form" class="form-grid">
          <div class="subgrid">
            <label>Intent mode<select name="intent_classification_mode">${renderModeOptions(config.intent_classification_mode || "auto", ["auto", "llm"])}</select></label>
            <label>SQL generation<select name="sql_generation_mode">${renderModeOptions(config.sql_generation_mode || "llm", ["llm"])}</select></label>
          </div>
          <div class="subgrid">
            <label>Result interpretation<select name="result_interpretation_mode">${renderModeOptions(config.result_interpretation_mode || "llm", ["llm"])}</select></label>
            <label>Output classification<select name="output_classification_mode">${renderModeOptions(config.output_classification_mode || "auto", ["auto", "llm"])}</select></label>
          </div>
          <div class="subgrid">
            <label>Query max rows<input name="query_max_rows" type="number" min="1" max="100000" value="${escapeHtml(config.query_max_rows || 100)}" /></label>
            <label>Query timeout seconds<input name="query_timeout_seconds" type="number" min="1" max="3600" value="${escapeHtml(config.query_timeout_seconds || 30)}" /></label>
          </div>
          <div class="subgrid">
            <label>Analysis loop count<input name="analysis_loop_count" type="number" min="1" max="25" value="${escapeHtml(config.analysis_loop_count || 5)}" /></label>
            <label><input name="analysis_auto_enable_business_logic" type="checkbox" ${config.analysis_auto_enable_business_logic ? "checked" : ""} /> Auto-enable analysis findings</label>
          </div>
          <div class="mono muted">${escapeHtml(JSON.stringify(config.sources || {}, null, 2))}</div>
          <div class="form-actions"><button class="primary" type="submit">Save reasoning configuration</button></div>
        </form>
      </div>
    </section>`;
}
function renderGovernancePolicy() {
  const config = state.governancePolicy || {};
  const finalAnswer = config.final_answer || {};
  const sql = config.sql || {};
  const privacy = config.privacy || {};
  return `
    <section class="panel">
      <div class="panel-header"><h2>Governance policy</h2></div>
      <div class="panel-body">
        <form id="governance-policy-form" class="form-grid">
          <div class="subgrid">
            <label class="inline-check"><input name="record_level_pii_allowed" type="checkbox" ${finalAnswer.record_level_pii_allowed ? "checked" : ""} /> Record-level PII allowed</label>
            <label class="inline-check"><input name="prefer_aggregates_for_sensitive_domains" type="checkbox" ${finalAnswer.prefer_aggregates_for_sensitive_domains !== false ? "checked" : ""} /> Prefer aggregates for sensitive domains</label>
          </div>
          <div class="subgrid">
            <label class="inline-check"><input name="sql_read_only" type="checkbox" ${sql.read_only !== false ? "checked" : ""} /> Read-only SQL</label>
            <label class="inline-check"><input name="select_star_allowed" type="checkbox" ${sql.select_star_allowed ? "checked" : ""} /> SELECT * allowed</label>
          </div>
          <div class="subgrid">
            <label class="inline-check"><input name="tenant_filter_required" type="checkbox" ${sql.tenant_filter_required ? "checked" : ""} /> Tenant filter required</label>
            <label>Tenant column<input name="tenant_column" value="${escapeHtml(sql.tenant_column || "")}" /></label>
          </div>
          <label class="inline-check"><input name="record_level_forbidden" type="checkbox" ${privacy.record_level_forbidden ? "checked" : ""} /> Record-level output forbidden</label>
          <label>Explicit forbidden columns JSON<textarea name="forbidden_columns">${escapeHtml(JSON.stringify(privacy.forbidden_columns || {}, null, 2))}</textarea></label>
          <label>PII column names JSON<textarea name="pii_column_names">${escapeHtml(JSON.stringify(config.pii_column_names || {}, null, 2))}</textarea></label>
          <div class="mono muted">${escapeHtml(JSON.stringify(config.sources || {}, null, 2))}</div>
          <div class="form-actions"><button class="primary" type="submit">Save governance policy</button></div>
        </form>
      </div>
    </section>`;
}
function renderDatasourceObjectListItem(table, settings, active) {
  const selected = settings.selected !== false;
  const objectType = table.object_type || "table";
  return `
    <div class="schema-object-row ${active ? "active" : ""}" data-schema-object-row="${escapeHtml(table.name)}">
      <input name="${escapeHtml(table.name)}__selected" data-schema-object-enabled="${escapeHtml(table.name)}" aria-label="Use ${escapeHtml(table.name)}" type="checkbox" ${selected ? "checked" : ""} />
      <button type="button" data-schema-object="${escapeHtml(table.name)}">
        <span class="schema-object-name">${escapeHtml(table.name)}</span>
        <span class="badge">${escapeHtml(objectType)}</span>
      </button>
    </div>`;
}
function renderDatasourceObjectDetails(table, settings) {
  const objectType = table.object_type || "table";
  return `
    <div class="schema-object-detail-header">
      <div>
        <h3>${escapeHtml(table.name)}</h3>
        <span class="badge">${escapeHtml(objectType)}</span>
      </div>
    </div>
    <div class="schema-object-columns">${escapeHtml((table.columns || []).map((column) => `${column.name}:${column.type}${column.primary_key ? " pk" : ""}`).join(", ") || "No columns available.")}</div>
    <label>Description<input data-schema-detail="description" name="${escapeHtml(table.name)}__description" value="${escapeHtml(settings.description || "")}" /></label>
    <label>Primary key guidance<input data-schema-detail="primary_key_prompt" name="${escapeHtml(table.name)}__primary_key_prompt" value="${escapeHtml(settings.primary_key_prompt || "")}" /></label>
    <label>Foreign key guidance<input data-schema-detail="foreign_key_prompt" name="${escapeHtml(table.name)}__foreign_key_prompt" value="${escapeHtml(settings.foreign_key_prompt || "")}" /></label>
    <label>Join logic<textarea data-schema-detail="join_logic" class="textarea-small" name="${escapeHtml(table.name)}__join_logic">${escapeHtml(settings.join_logic || "")}</textarea></label>`;
}
function renderSchemaCache() {
  return `
    <section class="panel"><div class="panel-header"><h2>Schema cache</h2></div>
      <div class="panel-body"><form id="schema-cache-form" class="form-grid">
        <label>TTL seconds<input name="ttl_seconds" type="number" min="1" max="86400" value="${escapeHtml(state.schemaCache?.ttl_seconds ?? 300)}" /></label>
        <div><span class="badge">Runtime ${escapeHtml(state.schemaCache?.runtime_ttl_seconds ?? "-")}s</span></div>
        <div><code>${escapeHtml(state.schemaCache?.cache_key ?? "")}</code></div>
        <div class="form-actions"><button type="button" id="invalidate-schema-cache" class="danger">Invalidate cache</button><button class="primary" type="submit">Save TTL</button></div>
      </form></div>
    </section>`;
}
function renderStub(title, text) {
  return `<section class="panel"><div class="panel-header"><h2>${escapeHtml(title)}</h2><span class="badge planned">planned</span></div><div class="panel-body"><p class="muted">${escapeHtml(text)}</p></div></section>`;
}
function renderLicense() {
  const license = state.license || {};
  const statusClass = license.valid ? "ok" : license.status === "missing" ? "planned" : "danger";
  const canUpdatePackages = license.plan && license.plan !== "community";
  const packageUpdateRunning = state.licensePackageUpdate?.status === "running";
  return `
    <section class="panel">
      <div class="panel-header">
        <h2>License</h2>
        <span class="badge ${statusClass}">${escapeHtml(license.status || "missing")}</span>
      </div>
      <div class="panel-body">
        <div class="license-status-grid">
          ${renderLicenseStatusItem("Plan", license.plan || "community")}
          ${renderLicenseStatusItem("Valid", license.valid ? "yes" : "no")}
          ${renderLicenseStatusItem("Current period end", formatLicenseDate(license.current_period_end))}
          ${renderLicenseStatusItem("Grace until", formatLicenseDate(license.grace_until))}
          ${renderLicenseStatusItem("Last checked", formatLicenseDate(license.last_checked_at))}
          ${renderLicenseStatusItem("Next check", formatLicenseDate(license.next_check_at))}
        </div>
        ${formatLicenseMessage(license) ? `<p class="muted">${escapeHtml(formatLicenseMessage(license))}</p>` : ""}
        <form id="license-key-form" class="form-grid license-key-form">
          <label>License key<input name="license_key" type="password" autocomplete="off" placeholder="gaard_live_xxx" /></label>
          <div class="form-actions">
            <button type="button" class="danger" id="clear-license-key">Clear key</button>
            <button type="button" id="check-license-now">Check now</button>
            ${canUpdatePackages ? `<button type="button" id="update-license-packages"${packageUpdateRunning ? " disabled" : ""}>Update packages</button>` : ""}
            <button class="primary" type="submit">Save key</button>
          </div>
        </form>
        ${renderLicensePackageProgress()}
      </div>
    </section>`;
}
function renderLicenseStatusItem(label, value) {
  return `
    <div class="stat">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
    </div>`;
}
function renderLicensePackageProgress() {
  const job = state.licensePackageUpdate;
  if (!job) return "";
  const stage = job.stage || "queued";
  const percent = Math.max(0, Math.min(100, Number(job.percent || 0)));
  const activeIndex = packageUpdateStages.findIndex((item) => item.key === stage);
  const complete = job.status === "succeeded";
  const failed = job.status === "failed";
  const statusLabel = failed ? "Failed" : complete ? "Complete" : job.message || "Updating packages.";
  return `
    <div class="package-update-progress ${failed ? "failed" : complete ? "complete" : ""}">
      <div class="package-update-progress-header">
        <strong>Package update</strong>
        <span>${failed ? "Failed" : `${percent}%`}</span>
      </div>
      <div class="package-update-track" aria-label="Package update progress">
        <div class="package-update-fill" style="width: ${percent}%"></div>
      </div>
      <div class="package-update-steps">
        ${packageUpdateStages.map((item, index) => {
          const done = complete || (activeIndex >= 0 && index < activeIndex);
          const active = !complete && !failed && item.key === stage;
          return `<div class="package-update-step ${done ? "done" : ""} ${active ? "active" : ""}"><span></span>${escapeHtml(item.label)}</div>`;
        }).join("")}
      </div>
      <p class="muted">${escapeHtml(failed ? job.error?.message || job.message || "Package update failed." : statusLabel)}</p>
    </div>`;
}
function renderAdminAudit() {
  return `
    <section class="panel"><div class="panel-header"><h2>Admin audit</h2></div>
      <div class="table-wrap"><table>
        <thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Resource</th><th>Details</th></tr></thead>
        <tbody>${state.adminAudit.map((item) => `<tr><td>${escapeHtml(item.occurred_at)}</td><td>${escapeHtml(item.actor)}</td><td>${escapeHtml(item.action)}</td><td>${escapeHtml(item.resource_type)}:${escapeHtml(item.resource_id)}</td><td><code>${escapeHtml(JSON.stringify(item.details))}</code></td></tr>`).join("")}</tbody>
      </table></div>
    </section>`;
}
function attachSectionHandlers() {
  document.querySelector("#overview-refresh")?.addEventListener("click", refreshOverview);
  document.querySelector("#overview-add-slots")?.addEventListener("click", addOverviewSlots);
  attachOverviewDragAndDropHandlers();
  document.querySelectorAll("[data-overview-empty-slot]").forEach((button) => {
    button.addEventListener("click", async () => {
      const slot = Number(button.dataset.overviewEmptySlot || 0);
      state.overviewPlacementSlot = state.overviewPlacementSlot === slot ? null : slot;
      if (!state.overviewWidgetConfigs.length) {
        await loadOverviewWidgetConfigs(false);
      }
      render();
    });
  });
  document.querySelectorAll("[data-overview-remove-slot]").forEach((button) => {
    button.addEventListener("click", removeOverviewSlot);
  });
  document.querySelectorAll("[data-overview-place-widget]").forEach((button) => {
    button.addEventListener("click", placeOverviewWidget);
  });
  document.querySelectorAll("[data-overview-new-widget]").forEach((button) => {
    button.addEventListener("click", () => {
      state.overviewPlacementSlot = Number(button.dataset.overviewNewWidget || 0);
      state.selectedOverviewWidgetKey = "__new__";
      state.section = "widgets";
      render();
    });
  });
  document.querySelector("#new-overview-widget")?.addEventListener("click", () => {
    state.selectedOverviewWidgetKey = "__new__";
    state.overviewPlacementSlot = null;
    render();
  });
  document.querySelectorAll("[data-overview-widget-select]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedOverviewWidgetKey = button.dataset.overviewWidgetSelect || "";
      state.overviewPlacementSlot = null;
      render();
    });
  });
  document.querySelectorAll("[data-overview-widget-active]").forEach((input) => {
    input.addEventListener("change", updateOverviewWidgetActive);
  });
  document.querySelectorAll("[data-overview-widget-delete]").forEach((button) => {
    button.addEventListener("click", deleteOverviewWidget);
  });
  document.querySelectorAll("[data-overview-resize-handle]").forEach((button) => {
    button.addEventListener("mousedown", startOverviewResize);
  });
  document.querySelector("#overview-widget-settings-form")?.addEventListener("submit", saveOverviewWidgetSettings);
  document.querySelectorAll("[data-edit-overview-widget]").forEach((button) => {
    button.addEventListener("click", () => {
      state.overviewEditorWidgetKey = button.dataset.editOverviewWidget || null;
      render();
    });
  });
  document.querySelectorAll("[data-close-overview-widget]").forEach((button) => {
    button.addEventListener("click", () => {
      state.overviewEditorWidgetKey = null;
      render();
    });
  });
  document.querySelector("[data-overview-widget-backdrop]")?.addEventListener("click", (event) => {
    if (event.target === event.currentTarget) {
      state.overviewEditorWidgetKey = null;
      render();
    }
  });
  document.querySelector("[data-confirm-backdrop]")?.addEventListener("click", (event) => {
    if (event.target === event.currentTarget) {
      closeConfirmDialog(false);
    }
  });
  document.querySelector("[data-confirm-cancel]")?.addEventListener("click", () => {
    closeConfirmDialog(false);
  });
  document.querySelector("[data-confirm-accept]")?.addEventListener("click", () => {
    closeConfirmDialog(true);
  });
  document.querySelectorAll("[data-overview-widget-form]").forEach((form) => {
    form.addEventListener("submit", saveOverviewWidget);
  });
  document.querySelectorAll("[data-overview-table-page]").forEach((button) => {
    button.addEventListener("click", () => {
      const widgetKey = button.dataset.overviewTablePage;
      const page = Number(button.dataset.page || 0);
      if (!widgetKey || !Number.isFinite(page)) return;
      state.overviewTablePages[widgetKey] = page;
      render();
    });
  });
  document.querySelector("#data-audit-filter-form")?.addEventListener("submit", loadDataAuditForFilters);
  document.querySelector("#retention-form")?.addEventListener("submit", saveRetention);
  document.querySelector("#data-audit-type")?.addEventListener("change", loadDataAuditForFilters);
  document.querySelector("#data-audit-output-classification")?.addEventListener("change", loadDataAuditForFilters);
  document.querySelector("#prompt-form")?.addEventListener("submit", savePrompt);
  document.querySelector("#schema-cache-form")?.addEventListener("submit", saveSchemaCacheTtl);
  document.querySelector("#license-key-form")?.addEventListener("submit", saveLicenseKey);
  document.querySelector("#clear-license-key")?.addEventListener("click", clearLicenseKey);
  document.querySelector("#check-license-now")?.addEventListener("click", checkLicenseNow);
  document.querySelector("#update-license-packages")?.addEventListener("click", updateLicensePackages);
  document.querySelector("#llm-config-form")?.addEventListener("submit", saveLlmConfig);
  document.querySelector("#test-llm-config")?.addEventListener("click", testLlmConfig);
  document.querySelector("#reasoning-config-form")?.addEventListener("submit", saveReasoningConfig);
  document.querySelector("#governance-policy-form")?.addEventListener("submit", saveGovernancePolicy);
  document.querySelector("#datasource-form")?.addEventListener("submit", saveDatasource);
  document.querySelector("#datasource-type")?.addEventListener("change", syncDatasourceTypeFields);
  document.querySelector("#datasource-schema-form")?.addEventListener("submit", saveDatasourceSchema);
  document.querySelector("#schema-show-enabled-only")?.addEventListener("change", (event) => {
    syncDatasourceSchemaDraftFromForm();
    state.datasourceSchemaShowEnabledOnly = event.currentTarget.checked;
    render();
  });
  document.querySelectorAll("[data-schema-object-enabled]").forEach((input) => {
    input.addEventListener("change", () => {
      if (!state.datasourceSchemaShowEnabledOnly) return;
      syncDatasourceSchemaDraftFromForm();
      render();
    });
  });
  document.querySelectorAll("[data-schema-object]").forEach((button) => {
    button.addEventListener("click", () => {
      syncDatasourceSchemaDraftFromForm();
      state.datasourceSchemaSelectedObjectName = button.dataset.schemaObject || "";
      render();
    });
  });
  document.querySelector("#invalidate-schema-cache")?.addEventListener("click", invalidateSchemaCache);
  document.querySelector("#test-datasource")?.addEventListener("click", testDatasource);
  document.querySelector("#introspect-datasource")?.addEventListener("click", introspectDatasource);
  document.querySelector("#activate-datasource")?.addEventListener("click", activateDatasource);
  document.querySelector("#delete-datasource")?.addEventListener("click", deleteDatasource);
  document.querySelectorAll("[data-open-business-logic]").forEach((button) => {
    button.addEventListener("click", async () => {
      state.section = "business-logic";
      render();
      await loadBusinessLogicSuggestions();
    });
  });
  document.querySelectorAll("[data-business-logic-toggle]").forEach((input) => {
    input.addEventListener("change", updateBusinessLogicSuggestion);
  });
  document.querySelectorAll("[data-business-logic-edit]").forEach((button) => {
    button.addEventListener("click", () => {
      state.businessLogicEditorId = Number(button.dataset.businessLogicEdit);
      render();
    });
  });
  document.querySelectorAll("[data-close-business-logic-editor]").forEach((button) => {
    button.addEventListener("click", () => {
      state.businessLogicEditorId = null;
      render();
    });
  });
  document.querySelector("[data-business-logic-backdrop]")?.addEventListener("click", (event) => {
    if (event.target === event.currentTarget) {
      state.businessLogicEditorId = null;
      render();
    }
  });
  document.querySelectorAll("[data-business-logic-form]").forEach((form) => {
    form.addEventListener("submit", saveBusinessLogicSuggestion);
  });
  document.querySelectorAll("[data-business-logic-delete]").forEach((button) => {
    button.addEventListener("click", deleteBusinessLogicSuggestion);
  });
  document.querySelector("#new-datasource")?.addEventListener("click", () => {
    state.selectedDatasourceId = "new";
    state.datasourceSchema = null;
    state.datasourceSchemaLoading = false;
    state.datasourceSchemaError = "";
    state.datasourceSchemaSelectedObjectName = "";
    state.datasourceSchemaDraftTables = null;
    render();
  });
  document.querySelectorAll("[data-prompt]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedPromptKey = button.dataset.prompt || "";
      render();
    });
  });
  document.querySelectorAll("[data-datasource]").forEach((button) => {
    button.addEventListener("click", async () => {
      state.selectedDatasourceId = Number(button.dataset.datasource);
      await loadDatasourceSchema();
    });
  });
}
async function loadDataAuditForFilters(event) {
  event.preventDefault();
  state.dataAuditType = document.querySelector("#data-audit-type")?.value || "";
  state.dataAuditOutputClassification = document.querySelector("#data-audit-output-classification")?.value || "";
  state.dataAuditSqlContains = document.querySelector("#data-audit-sql-contains")?.value || "";
  await loadDataAudit();
}
async function saveOverviewWidget(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const widgetKey = formElement.dataset.overviewWidgetForm;
  if (!widgetKey) return;
  const form = new FormData(formElement);
  const widget = getOverviewEditorWidget();
  if (!widget) return;
  try {
    await api(`/api/v1/admin/overview/widgets/${encodeURIComponent(widgetKey)}`, {
      method: "PUT",
      body: JSON.stringify({
        label: form.get("label"),
        widget_type: form.get("widget_type"),
        datasource_key: form.get("datasource_key"),
        question: form.get("question"),
        result_mode: form.get("result_mode") || "data",
        position: widget.position,
        grid_width: getOverviewWidgetGridWidth(widget),
        active: form.get("active") !== "false"
      })
    });
    setMessage("success", "Overview widget saved.");
    state.overviewEditorWidgetKey = null;
    await loadOverview();
  } catch (error) {
    setMessage("error", error.message);
  }
}
async function placeOverviewWidget(event) {
  const button = event.currentTarget;
  const slot = Number(button.dataset.overviewPlaceWidget || 0);
  const select = document.querySelector(`[data-overview-placement-select="${slot}"]`);
  const widgetKey = select?.value || "";
  const widget = state.overviewWidgetConfigs.find((item) => item.widget_key === widgetKey);
  if (!widgetKey || !widget) return;
  try {
    const width = getOverviewWidgetGridWidth(widget);
    const layout = buildOverviewLayout(state.overview?.widgets || []);
    const actualSlot = findAvailableOverviewSlot(slot, width, layout.occupiedSlots);
    await updateOverviewWidgetState(widgetKey, true, overviewPositionFromSlot(actualSlot), width);
    state.overviewPlacementSlot = null;
    setMessage("success", "Widget added to overview.");
    await loadOverview();
  } catch (error) {
    setMessage("error", error.message);
    render();
  }
}
async function updateOverviewWidgetActive(event) {
  const input = event.currentTarget;
  const widgetKey = input.dataset.overviewWidgetActive || "";
  const widget = state.overviewWidgetConfigs.find((item) => item.widget_key === widgetKey);
  if (!widget) return;
  try {
    await updateOverviewWidgetState(widgetKey, input.checked, widget.position, getOverviewWidgetGridWidth(widget));
    setMessage("success", input.checked ? "Widget enabled." : "Widget disabled.");
    await loadOverviewWidgetConfigs(false);
    if (state.section === "overview") {
      await loadOverview();
    } else {
      render();
    }
  } catch (error) {
    setMessage("error", error.message);
    await loadOverviewWidgetConfigs();
  }
}
async function deleteOverviewWidget(event) {
  const button = event.currentTarget;
  const widgetKey = button.dataset.overviewWidgetDelete || "";
  const widget = state.overviewWidgetConfigs.find((item) => item.widget_key === widgetKey);
  if (!widgetKey || !widget) return;
  if (!await requestConfirmation({
    title: "Delete widget",
    message: `Delete widget "${widget.label}"?`,
    confirmLabel: "Delete"
  })) {
    return;
  }
  try {
    await api(`/api/v1/admin/overview/widgets/${encodeURIComponent(widgetKey)}`, {
      method: "DELETE"
    });
    setMessage("success", "Widget deleted.");
    if (state.selectedOverviewWidgetKey === widgetKey) {
      state.selectedOverviewWidgetKey = "";
    }
    if (state.overviewEditorWidgetKey === widgetKey) {
      state.overviewEditorWidgetKey = null;
    }
    await loadOverviewWidgetConfigs(false);
    await loadOverview();
    if (state.section !== "overview") {
      render();
    }
  } catch (error) {
    setMessage("error", error.message);
    render();
  }
}
async function updateOverviewWidgetState(widgetKey, active, position, gridWidth) {
  await api(`/api/v1/admin/overview/widgets/${encodeURIComponent(widgetKey)}/state`, {
    method: "PATCH",
    body: JSON.stringify({
      active,
      position,
      grid_width: gridWidth
    })
  });
}
function addOverviewSlots() {
  const widgets = state.overview?.widgets || [];
  const currentSlots = getOverviewSlotCount(widgets);
  const baseSlotCount = getOverviewBaseSlotCount(widgets);
  if (baseSlotCount >= OVERVIEW_MAX_GRID_SLOTS) {
    return;
  }
  state.overviewExtraSlots = Math.min(
    OVERVIEW_MAX_GRID_SLOTS,
    Math.max(baseSlotCount, state.overviewExtraSlots) + OVERVIEW_SLOT_INCREMENT
  );
  render();
}
function removeOverviewSlot(event) {
  event.stopPropagation();
  const button = event.currentTarget;
  const slot = Number(button.dataset.overviewRemoveSlot || -1);
  if (!Number.isFinite(slot) || slot < 0) {
    return;
  }
  state.overviewPlacementSlot = state.overviewPlacementSlot === slot ? null : state.overviewPlacementSlot;
  if (!state.overviewRemovedEmptySlots.includes(slot)) {
    state.overviewRemovedEmptySlots = [...state.overviewRemovedEmptySlots, slot].sort((left, right) => left - right);
  }
  render();
}
async function moveOverviewWidgetToSlot(widgetKey, requestedSlot) {
  const widget = getOverviewWidgetByKey(widgetKey);
  if (!widget) return;
  const width = getOverviewWidgetGridWidth(widget);
  const layout = getOverviewLayoutWithoutWidget(widgetKey);
  const actualSlot = findAvailableOverviewSlot(requestedSlot, width, layout.occupiedSlots);
  await updateOverviewWidgetState(widgetKey, true, overviewPositionFromSlot(actualSlot), width);
}
async function swapOverviewWidgets(sourceKey, targetKey) {
  if (!sourceKey || !targetKey || sourceKey === targetKey) return;
  const sourceWidget = getOverviewWidgetByKey(sourceKey);
  const targetWidget = getOverviewWidgetByKey(targetKey);
  if (!sourceWidget || !targetWidget) return;
  const sourceSlot = overviewSlotFromPosition(sourceWidget.position);
  const targetSlot = overviewSlotFromPosition(targetWidget.position);
  const sourceWidth = getOverviewWidgetGridWidth(sourceWidget);
  const targetWidth = getOverviewWidgetGridWidth(targetWidget);
  const remainingWidgets = getActiveOverviewWidgets().filter((widget) => widget.widget_key !== sourceKey && widget.widget_key !== targetKey);
  const occupiedSlots = getOverviewOccupiedSlots(remainingWidgets);
  if (!canPlaceOverviewWidget(targetSlot, sourceWidth, occupiedSlots)) {
    throw new Error("The dragged widget does not fit in that slot.");
  }
  const withSourcePlaced = new Set(occupiedSlots);
  for (let offset = 0; offset < sourceWidth; offset += 1) {
    withSourcePlaced.add(targetSlot + offset);
  }
  if (!canPlaceOverviewWidget(sourceSlot, targetWidth, withSourcePlaced)) {
    throw new Error("The target widget cannot be moved into the previous slot.");
  }
  await updateOverviewWidgetState(sourceKey, true, overviewPositionFromSlot(targetSlot), sourceWidth);
  await updateOverviewWidgetState(targetKey, true, overviewPositionFromSlot(sourceSlot), targetWidth);
}
async function resizeOverviewWidgetState(widgetKey, nextSlot, nextWidth) {
  const widget = getOverviewWidgetByKey(widgetKey);
  if (!widget) return;
  const currentSlot = overviewSlotFromPosition(widget.position);
  const currentWidth = getOverviewWidgetGridWidth(widget);
  if ((nextWidth === currentWidth && nextSlot === currentSlot) || nextWidth < 1 || nextWidth > OVERVIEW_GRID_COLUMNS) {
    return;
  }
  await updateOverviewWidgetState(widgetKey, true, overviewPositionFromSlot(nextSlot), nextWidth);
}
function startOverviewResize(event) {
  event.preventDefault();
  event.stopPropagation();
  const handle = event.currentTarget;
  const widgetKey = handle.dataset.overviewResizeHandle || "";
  const widget = getOverviewWidgetByKey(widgetKey);
  const card = handle.closest("[data-overview-widget-key]");
  const grid = handle.closest(".overview-grid");
  if (!widget || !card || !grid) return;
  const bounds = getOverviewResizeBounds(widgetKey);
  if (!bounds) return;
  const gridRect = grid.getBoundingClientRect();
  const gridStyle = window.getComputedStyle(grid);
  const gap = Number.parseFloat(gridStyle.columnGap || gridStyle.gap || "12") || 12;
  const columnWidth = (gridRect.width - gap * (OVERVIEW_GRID_COLUMNS - 1)) / OVERVIEW_GRID_COLUMNS;
  state.overviewResizeState = {
    widgetKey,
    edge: handle.dataset.edge === "left" ? "left" : "right",
    startX: event.clientX,
    startSlot: bounds.slot,
    startRight: bounds.startRight,
    startWidth: bounds.width,
    minLeftSlot: bounds.minLeftSlot,
    maxRightSlot: bounds.maxRightSlot,
    columnUnit: Math.max(1, columnWidth + gap),
    latestClientX: event.clientX
  };
  card.classList.add("is-resizing");
  window.addEventListener("mousemove", handleOverviewResizeMove);
  window.addEventListener("mouseup", finishOverviewResize, { once: true });
}
function handleOverviewResizeMove(event) {
  const resizeState = state.overviewResizeState;
  if (!resizeState) return;
  resizeState.latestClientX = event.clientX;
  const card = document.querySelector(`[data-overview-widget-key="${CSS.escape(resizeState.widgetKey)}"]`);
  if (!card) return;
  const proposal = getOverviewResizeProposal(resizeState, event.clientX);
  if (!proposal) return;
  const columnStart = proposal.slot % OVERVIEW_GRID_COLUMNS + 1;
  card.style.gridColumn = `${columnStart} / span ${proposal.width}`;
}
async function finishOverviewResize() {
  const resizeState = state.overviewResizeState;
  window.removeEventListener("mousemove", handleOverviewResizeMove);
  if (!resizeState) return;
  state.overviewResizeState = null;
  const card = document.querySelector(`[data-overview-widget-key="${CSS.escape(resizeState.widgetKey)}"]`);
  if (card) {
    card.classList.remove("is-resizing");
    card.style.gridColumn = "";
  }
  const proposal = getOverviewResizeProposal(resizeState, resizeState.latestClientX);
  if (!proposal) {
    render();
    return;
  }
  try {
    await resizeOverviewWidgetState(resizeState.widgetKey, proposal.slot, proposal.width);
    if (proposal.width !== resizeState.startWidth || proposal.slot !== resizeState.startSlot) {
      setMessage("success", "Widget size updated.");
      await loadOverview();
    } else {
      render();
    }
  } catch (error) {
    setMessage("error", error.message);
    render();
  }
}
function attachOverviewDragAndDropHandlers() {
  document.querySelectorAll("[data-overview-widget-key]").forEach((element) => {
    element.addEventListener("dragstart", handleOverviewWidgetDragStart);
    element.addEventListener("dragend", handleOverviewWidgetDragEnd);
    element.addEventListener("dragover", handleOverviewDragOver);
    element.addEventListener("drop", handleOverviewWidgetDrop);
  });
  document.querySelectorAll(".overview-empty-slot").forEach((element) => {
    element.addEventListener("dragover", handleOverviewDragOver);
    element.addEventListener("drop", handleOverviewEmptySlotDrop);
  });
}
function handleOverviewWidgetDragStart(event) {
  const element = event.currentTarget;
  const widgetKey = element.dataset.overviewWidgetKey || "";
  state.draggedOverviewWidgetKey = widgetKey;
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", widgetKey);
  }
  element.classList.add("is-dragging");
}
function handleOverviewWidgetDragEnd(event) {
  state.draggedOverviewWidgetKey = null;
  event.currentTarget.classList.remove("is-dragging");
  document.querySelectorAll(".overview-drop-target").forEach((element) => {
    element.classList.remove("overview-drop-target");
  });
}
function handleOverviewDragOver(event) {
  if (!state.draggedOverviewWidgetKey) return;
  event.preventDefault();
  if (event.dataTransfer) {
    event.dataTransfer.dropEffect = "move";
  }
  event.currentTarget.classList.add("overview-drop-target");
}
async function handleOverviewWidgetDrop(event) {
  event.preventDefault();
  event.currentTarget.classList.remove("overview-drop-target");
  const sourceKey = state.draggedOverviewWidgetKey;
  const targetKey = event.currentTarget.dataset.overviewWidgetKey || "";
  if (!sourceKey || !targetKey || sourceKey === targetKey) return;
  try {
    await swapOverviewWidgets(sourceKey, targetKey);
    setMessage("success", "Widgets swapped.");
    await loadOverview();
  } catch (error) {
    setMessage("error", error.message);
    render();
  }
}
async function handleOverviewEmptySlotDrop(event) {
  if (!state.draggedOverviewWidgetKey) return;
  event.preventDefault();
  event.currentTarget.classList.remove("overview-drop-target");
  const button = event.currentTarget.querySelector("[data-overview-empty-slot]");
  const slot = Number(button?.dataset.overviewEmptySlot || 0);
  if (!Number.isFinite(slot)) return;
  try {
    await moveOverviewWidgetToSlot(state.draggedOverviewWidgetKey, slot);
    setMessage("success", "Widget moved.");
    await loadOverview();
  } catch (error) {
    setMessage("error", error.message);
    render();
  }
}
async function saveOverviewWidgetSettings(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const mode = formElement.dataset.widgetMode || "update";
  const creating = mode === "create";
  const form = new FormData(formElement);
  const widgetKey = String(form.get("widget_key") || "").trim();
  if (!widgetKey) return;
  const selectedWidget = getSelectedOverviewWidgetConfig();
  const widgetType = String(form.get("widget_type") || "scalar");
  const payload = {
    widget_key: widgetKey,
    label: form.get("label"),
    widget_type: widgetType,
    datasource_key: form.get("datasource_key"),
    question: form.get("question"),
    result_mode: form.get("result_mode") || "data",
    position: creating ? getOverviewWidgetFormPosition(null) : selectedWidget?.position || 100,
    grid_width: creating ? getDefaultOverviewWidgetGridWidth(widgetType) : getOverviewWidgetGridWidth(selectedWidget || { widget_type: widgetType, grid_width: getDefaultOverviewWidgetGridWidth(widgetType) }),
    active: form.get("active") === "on"
  };
  try {
    if (mode === "create") {
      await api("/api/v1/admin/overview/widgets", {
        method: "POST",
        body: JSON.stringify(payload)
      });
      state.selectedOverviewWidgetKey = widgetKey;
      state.overviewPlacementSlot = null;
      setMessage("success", "Widget created.");
    } else {
      await api(`/api/v1/admin/overview/widgets/${encodeURIComponent(widgetKey)}`, {
        method: "PUT",
        body: JSON.stringify(payload)
      });
      setMessage("success", "Widget saved.");
    }
    await loadOverviewWidgetConfigs(false);
    await loadOverview();
    if (state.section !== "overview") {
      render();
    }
  } catch (error) {
    setMessage("error", error.message);
  }
}
async function updateBusinessLogicSuggestion(event) {
  const input = event.currentTarget;
  const id = input.dataset.businessLogicToggle;
  if (!id) return;
  try {
    await api(`/api/v1/admin/business-logic-suggestions/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify({ enabled: input.checked })
    });
    setMessage("success", input.checked ? "Business logic enabled." : "Business logic disabled.");
    await loadBusinessLogicSuggestions();
  } catch (error) {
    setMessage("error", error.message);
    await loadBusinessLogicSuggestions();
  }
}
async function saveBusinessLogicSuggestion(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const id = formElement.dataset.businessLogicForm;
  if (!id) return;
  const form = new FormData(formElement);
  try {
    await api(`/api/v1/admin/business-logic-suggestions/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify({
        title: form.get("title"),
        rule_text: form.get("rule_text")
      })
    });
    setMessage("success", "Business logic suggestion updated.");
    state.businessLogicEditorId = null;
    await loadBusinessLogicSuggestions();
  } catch (error) {
    setMessage("error", error.message);
    render();
  }
}
async function deleteBusinessLogicSuggestion(event) {
  const button = event.currentTarget;
  const id = button.dataset.businessLogicDelete;
  if (!id) return;
  try {
    await api(`/api/v1/admin/business-logic-suggestions/${encodeURIComponent(id)}`, {
      method: "DELETE"
    });
    setMessage("success", "Business logic suggestion deleted.");
    await loadBusinessLogicSuggestions();
  } catch (error) {
    setMessage("error", error.message);
    render();
  }
}
async function saveLicenseKey(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const licenseKey = String(form.get("license_key") || "").trim();
  if (!licenseKey) {
    setMessage("error", "License key is required.");
    return;
  }
  try {
    state.license = await api("/api/v1/admin/license/key", {
      method: "PUT",
      body: JSON.stringify({ license_key: licenseKey })
    });
    setMessage("success", "License key saved.");
    render();
  } catch (error) {
    setMessage("error", error.message);
    render();
  }
}
async function clearLicenseKey() {
  try {
    state.license = await api("/api/v1/admin/license/key", {
      method: "PUT",
      body: JSON.stringify({ clear_license_key: true })
    });
    setMessage("success", "License key cleared.");
    render();
  } catch (error) {
    setMessage("error", error.message);
    render();
  }
}
async function checkLicenseNow() {
  try {
    state.license = await api("/api/v1/admin/license/check", {
      method: "POST"
    });
    setMessage("success", "License status refreshed.");
    render();
  } catch (error) {
    setMessage("error", error.message);
    render();
  }
}
async function updateLicensePackages() {
  try {
    state.licensePackageUpdate = {
      status: "running",
      stage: "queued",
      percent: 0,
      message: "Starting package update."
    };
    render();
    const job = await api("/api/v1/admin/license/packages/update", {
      method: "POST"
    });
    state.licensePackageUpdate = job;
    render();
    pollLicensePackageUpdate(job.job_id);
  } catch (error) {
    setMessage("error", error.message);
    render();
  }
}
async function pollLicensePackageUpdate(jobId) {
  if (!jobId || state.licensePackageUpdate?.job_id !== jobId) return;
  if (licensePackageUpdatePollTimer) {
    clearTimeout(licensePackageUpdatePollTimer);
    licensePackageUpdatePollTimer = null;
  }
  try {
    const job = await api(`/api/v1/admin/license/packages/update/${encodeURIComponent(jobId)}`);
    state.licensePackageUpdate = job;
    if (job.status === "running") {
      render();
      licensePackageUpdatePollTimer = setTimeout(() => pollLicensePackageUpdate(jobId), 700);
      return;
    }
    if (job.status === "succeeded") {
      await loadExtensions(false);
      setMessage("success", job.result?.message || job.message || "Packages updated.");
    } else {
      setMessage("error", job.error?.message || job.message || "Package update failed.");
    }
    render();
  } catch (error) {
    setMessage("error", error.message);
    render();
  }
}
async function saveLlmConfig(event) {
  event.preventDefault();
  try {
    const result = await api("/api/v1/admin/llm-config", {
      method: "PUT",
      body: JSON.stringify(getLlmConfigPayload())
    });
    state.llmConfig = result.item;
    setMessage("success", "LLM configuration saved.");
    render();
  } catch (error) {
    setMessage("error", error.message);
    render();
  }
}
async function testLlmConfig() {
  try {
    setMessage("success", "Testing LLM configuration...");
    const result = await api("/api/v1/admin/llm-config/test", {
      method: "POST",
      body: JSON.stringify(getLlmConfigPayload())
    });
    setMessage("success", result.item?.model ? `OK \u2014 model: ${result.item.model}.` : "OK.");
    render();
  } catch (error) {
    setMessage("error", error.message);
    render();
  }
}
function getLlmConfigPayload() {
  const formElement = document.querySelector("#llm-config-form");
  if (!formElement) throw new Error("LLM configuration form is not available.");
  const form = new FormData(formElement);
  const extraBody = JSON.parse(String(form.get("extra_body") || "{}"));
  if (extraBody === null || Array.isArray(extraBody) || typeof extraBody !== "object") {
    throw new Error("Extra body JSON must be an object.");
  }
  return {
    provider: form.get("provider"),
    base_url: form.get("base_url"),
    api_key: form.get("api_key"),
    clear_api_key: false,
    model: form.get("model"),
    timeout_seconds: Number(form.get("timeout_seconds") || 60),
    extra_body: extraBody
  };
}
async function saveReasoningConfig(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  try {
    const result = await api("/api/v1/admin/reasoning-config", {
      method: "PUT",
      body: JSON.stringify({
        intent_classification_mode: form.get("intent_classification_mode"),
        sql_generation_mode: form.get("sql_generation_mode"),
        result_interpretation_mode: form.get("result_interpretation_mode"),
        output_classification_mode: form.get("output_classification_mode"),
        query_max_rows: Number(form.get("query_max_rows") || 100),
        query_timeout_seconds: Number(form.get("query_timeout_seconds") || 30),
        analysis_loop_count: Number(form.get("analysis_loop_count") || 5),
        analysis_auto_enable_business_logic: form.get("analysis_auto_enable_business_logic") === "on"
      })
    });
    state.reasoningConfig = result.item;
    setMessage("success", "Reasoning configuration saved.");
    render();
  } catch (error) {
    setMessage("error", error.message);
    render();
  }
}
async function saveGovernancePolicy(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  try {
    const forbiddenColumns = JSON.parse(String(form.get("forbidden_columns") || "{}"));
    const piiColumnNames = JSON.parse(String(form.get("pii_column_names") || "{}"));
    if (forbiddenColumns === null || Array.isArray(forbiddenColumns) || typeof forbiddenColumns !== "object") {
      throw new Error("Forbidden columns JSON must be an object.");
    }
    if (piiColumnNames === null || typeof piiColumnNames !== "object") {
      throw new Error("PII column names JSON must be an object or list.");
    }
    const result = await api("/api/v1/admin/governance-policy", {
      method: "PUT",
      body: JSON.stringify({
        final_answer: {
          record_level_pii_allowed: form.get("record_level_pii_allowed") === "on",
          prefer_aggregates_for_sensitive_domains: form.get("prefer_aggregates_for_sensitive_domains") === "on"
        },
        sql: {
          read_only: form.get("sql_read_only") === "on",
          select_star_allowed: form.get("select_star_allowed") === "on",
          tenant_filter_required: form.get("tenant_filter_required") === "on",
          tenant_column: String(form.get("tenant_column") || "").trim() || null
        },
        privacy: {
          record_level_forbidden: form.get("record_level_forbidden") === "on",
          forbidden_columns: forbiddenColumns
        },
        pii_column_names: piiColumnNames
      })
    });
    state.governancePolicy = result.item;
    setMessage("success", "Governance policy saved.");
    render();
  } catch (error) {
    setMessage("error", error.message);
    render();
  }
}
async function saveRetention(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  try {
    await api("/api/v1/admin/audit/settings", { method: "PUT", body: JSON.stringify({ data_query_retention_days: Number(form.get("retention")) }) });
    setMessage("success", "Audit retention saved.");
    await loadDataAudit();
  } catch (error) {
    setMessage("error", error.message);
    render();
  }
}
async function savePrompt(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const promptKey = String(form.get("prompt_key"));
  try {
    await api(`/api/v1/admin/prompts/${encodeURIComponent(promptKey)}`, {
      method: "PUT",
      body: JSON.stringify({
        name: form.get("name"),
        description: form.get("description"),
        system_prompt: form.get("system_prompt"),
        user_prompt_template: form.get("user_prompt_template"),
        active: form.get("active") === "on"
      })
    });
    setMessage("success", "Prompt saved.");
    await loadPrompts();
  } catch (error) {
    setMessage("error", error.message);
    render();
  }
}
async function saveDatasource(event) {
  event.preventDefault();
  const selected = getSelectedDatasource();
  if (selected?.system_managed) return;
  const form = new FormData(event.currentTarget);
  const id = String(form.get("id") || "");
  const payload = {
    connector_key: form.get("connector_key"),
    name: form.get("name"),
    database_type: form.get("database_type"),
    connection_config: collectDatasourceConnectionConfig(event.currentTarget),
    database_url: form.get("database_url") || null,
    active: form.get("active") === "on"
  };
  try {
    const result = await api(id ? `/api/v1/admin/datasources/${id}` : "/api/v1/admin/datasources", {
      method: id ? "PUT" : "POST",
      body: JSON.stringify(id ? { ...payload, connector_key: void 0 } : payload)
    });
    state.selectedDatasourceId = result.item.id;
    setMessage("success", "Datasource saved.");
    await loadDatasources();
  } catch (error) {
    setMessage("error", error.message);
  }
}
async function testDatasource() {
  const selected = getSelectedDatasource();
  const form = document.querySelector("#datasource-form");
  const formData = form ? new FormData(form) : null;
  try {
    if (selected) {
      await api(`/api/v1/admin/datasources/${selected.id}/test`, { method: "POST" });
    } else if (formData) {
      await api("/api/v1/admin/datasources/test", {
        method: "POST",
        body: JSON.stringify({
          database_type: formData.get("database_type"),
          connection_config: collectDatasourceConnectionConfig(form),
          database_url: formData.get("database_url") || null
        })
      });
    } else {
      return;
    }
    setMessage("success", "Connection test succeeded.");
  } catch (error) {
    setMessage("error", extractErrorMessage(error));
  }
}
async function introspectDatasource() {
  const selected = getSelectedDatasource();
  if (!selected) return;
  try {
    state.datasourceSchemaLoading = true;
    state.datasourceSchemaError = "";
    render();
    state.datasourceSchema = await api(`/api/v1/admin/datasources/${selected.id}/introspect`, { method: "POST" });
    state.datasourceSchemaSelectedObjectName = "";
    state.datasourceSchemaDraftTables = null;
    state.datasourceSchemaLoading = false;
    setMessage("success", "Schema introspection completed.");
    render();
  } catch (error) {
    state.datasourceSchemaLoading = false;
    state.datasourceSchemaError = error.message;
    setMessage("error", error.message);
    render();
  }
}
async function activateDatasource() {
  const selected = getSelectedDatasource();
  if (!selected) return;
  try {
    await api(`/api/v1/admin/datasources/${selected.id}/activate`, { method: "POST" });
    setMessage("success", "Datasource activated.");
    await loadDatasources();
  } catch (error) {
    setMessage("error", error.message);
    render();
  }
}
async function deleteDatasource() {
  const selected = getSelectedDatasource();
  if (!selected || selected.system_managed) return;
  if (!await requestConfirmation({
    title: "Delete datasource",
    message: `Delete datasource "${selected.name}"?`,
    confirmLabel: "Delete"
  })) return;
  try {
    await api(`/api/v1/admin/datasources/${selected.id}`, { method: "DELETE" });
    state.selectedDatasourceId = null;
    state.datasourceSchema = null;
    state.datasourceSchemaLoading = false;
    state.datasourceSchemaError = "";
    state.datasourceSchemaSelectedObjectName = "";
    state.datasourceSchemaDraftTables = null;
    setMessage("success", "Datasource deleted.");
    await loadDatasources();
  } catch (error) {
    setMessage("error", error.message);
    render();
  }
}
function syncDatasourceSchemaDraftFromForm() {
  const form = document.querySelector("#datasource-schema-form");
  const rawTables = state.datasourceSchema?.item?.raw_schema?.tables || [];
  const tableSettings = state.datasourceSchema?.item?.table_settings?.tables || {};
  if (!form || !rawTables.length) return;
  const draftTables = getDatasourceSchemaDraftTables(rawTables, tableSettings);
  form.querySelectorAll("[data-schema-object-enabled]").forEach((input) => {
    const name = input.dataset.schemaObjectEnabled;
    if (!name || !draftTables[name]) return;
    draftTables[name].selected = input.checked;
  });
  const selectedName = state.datasourceSchemaSelectedObjectName;
  const selectedSettings = selectedName ? draftTables[selectedName] : null;
  if (!selectedSettings) return;
  selectedSettings.description = form.querySelector("[data-schema-detail='description']")?.value || "";
  selectedSettings.primary_key_prompt = form.querySelector("[data-schema-detail='primary_key_prompt']")?.value || "";
  selectedSettings.foreign_key_prompt = form.querySelector("[data-schema-detail='foreign_key_prompt']")?.value || "";
  selectedSettings.join_logic = form.querySelector("[data-schema-detail='join_logic']")?.value || "";
}
async function saveDatasourceSchema(event) {
  event.preventDefault();
  const selected = getSelectedDatasource();
  if (!selected || !state.datasourceSchema?.item) return;
  const rawTables = state.datasourceSchema.item.raw_schema.tables || [];
  const tableSettings = state.datasourceSchema.item.table_settings?.tables || {};
  syncDatasourceSchemaDraftFromForm();
  const draftTables = getDatasourceSchemaDraftTables(rawTables, tableSettings);
  const tables = {};
  for (const table of rawTables) {
    const draft = draftTables[table.name] || {};
    tables[table.name] = {
      selected: draft.selected !== false,
      description: draft.description || "",
      primary_key_prompt: draft.primary_key_prompt || "",
      foreign_key_prompt: draft.foreign_key_prompt || "",
      join_logic: draft.join_logic || ""
    };
  }
  try {
    state.datasourceSchema = await api(`/api/v1/admin/datasources/${selected.id}/schema/tables`, {
      method: "PUT",
      body: JSON.stringify({ tables })
    });
    setMessage("success", "Schema settings saved.");
    state.datasourceSchemaSelectedObjectName = "";
    state.datasourceSchemaDraftTables = null;
    render();
  } catch (error) {
    setMessage("error", error.message);
    render();
  }
}
async function saveSchemaCacheTtl(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  try {
    await api("/api/v1/admin/schema-cache", { method: "PUT", body: JSON.stringify({ ttl_seconds: Number(form.get("ttl_seconds")) }) });
    setMessage("success", "Schema cache TTL saved.");
    await loadSchemaCache();
  } catch (error) {
    setMessage("error", error.message);
    render();
  }
}
async function invalidateSchemaCache() {
  try {
    await api("/api/v1/admin/schema-cache/invalidate", { method: "POST" });
    setMessage("success", "Schema cache invalidated.");
    await loadSchemaCache();
  } catch (error) {
    setMessage("error", error.message);
    render();
  }
}
async function loadCurrentSection() {
  if (!state.token || state.mustChangePassword) return;
  if (!state.extensionsLoaded) {
    await loadExtensions(false);
  }
  if (isExtensionSection(state.section)) {
    render();
    return;
  }
  if (state.section === "overview") await loadOverview();
  if (state.section === "widgets") await loadOverviewWidgetConfigs();
  if (state.section === "data-audit") await loadDataAudit();
  if (state.section === "prompts") await loadPrompts();
  if (state.section === "schema-cache") await loadSchemaCache();
  if (state.section === "business-logic") await loadBusinessLogicSuggestions();
  if (state.section === "llm-config") await loadLlmConfig();
  if (state.section === "reasoning") await loadReasoningConfig();
  if (state.section === "governance-policy") await loadGovernancePolicy();
  if (state.section === "datasources") await loadDatasources();
  if (state.section === "license") await loadLicense();
  if (state.section === "admin-audit") await loadAdminAudit();
}
async function loadExtensions(shouldRender = true) {
  const result = await api("/api/v1/admin/extensions");
  state.extensionSections = (result.admin_sections || []).sort((left, right) => {
    if (left.order !== right.order) return left.order - right.order;
    return left.label.localeCompare(right.label);
  });
  state.extensionsLoaded = true;
  if (isExtensionSection(state.section) && !getExtensionSection(state.section)) {
    state.section = "overview";
  }
  if (shouldRender) {
    render();
  }
}
async function loadOverview() {
  state.overviewLoading = true;
  if (state.section === "overview") {
    render();
  }
  try {
    const [overview, widgetConfig] = await Promise.all([
      api("/api/v1/admin/overview"),
      api("/api/v1/admin/overview/widgets")
    ]);
    state.overview = overview;
    state.overviewWidgetConfigs = widgetConfig.items || [];
    state.overviewWidgetDatasources = widgetConfig.datasources || [];
  } finally {
    state.overviewLoading = false;
    if (state.section === "overview") {
      render();
    }
  }
}
async function loadOverviewWidgetConfigs(shouldRender = true) {
  const payload = await api("/api/v1/admin/overview/widgets");
  state.overviewWidgetConfigs = payload.items || [];
  state.overviewWidgetDatasources = payload.datasources || [];
  if (state.selectedOverviewWidgetKey !== "__new__" && !state.overviewWidgetConfigs.some((widget) => widget.widget_key === state.selectedOverviewWidgetKey)) {
    state.selectedOverviewWidgetKey = state.overviewWidgetConfigs[0]?.widget_key || "";
  }
  if (shouldRender) {
    render();
  }
}
async function refreshOverview() {
  if (state.overviewRefreshing || state.overviewLoading) return;
  state.overviewRefreshing = true;
  setMessage("success", "");
  render();
  try {
    state.overview = await api("/api/v1/admin/overview");
  } catch (error) {
    setMessage("error", error.message);
  } finally {
    state.overviewRefreshing = false;
    render();
  }
}
async function loadDataAudit() {
  state.auditSettings = await api("/api/v1/admin/audit/settings");
  const params = new URLSearchParams();
  if (state.dataAuditType) params.set("audit_type", state.dataAuditType);
  if (state.dataAuditOutputClassification) {
    params.set("output_classification", state.dataAuditOutputClassification);
  }
  if (state.dataAuditSqlContains.trim()) {
    params.set("sql_contains", state.dataAuditSqlContains.trim());
  }
  const query = params.toString() ? `?${params.toString()}` : "";
  const logs = await api(`/api/v1/admin/audit/data-queries${query}`);
  state.dataAudit = logs.items || [];
  render();
}
async function loadPrompts() {
  const result = await api("/api/v1/admin/prompts");
  state.prompts = result.items || [];
  state.selectedPromptKey = state.selectedPromptKey || state.prompts[0]?.prompt_key || "";
  render();
}
async function loadDatasources() {
  const [datasources, datasourceTypes] = await Promise.all([
    api("/api/v1/admin/datasources"),
    api("/api/v1/admin/datasource-types")
  ]);
  state.datasources = datasources.items || [];
  state.datasourceTypes = datasourceTypes.items || [];
  if (!state.selectedDatasourceId || state.selectedDatasourceId === "new") {
    state.selectedDatasourceId = state.datasources[0]?.id || null;
  }
  render();
  void loadDatasourceSchema();
}
async function loadDatasourceSchema() {
  const selected = getSelectedDatasource();
  if (!selected) {
    state.datasourceSchema = null;
    state.datasourceSchemaSelectedObjectName = "";
    state.datasourceSchemaDraftTables = null;
    state.datasourceSchemaLoading = false;
    state.datasourceSchemaError = "";
    render();
    return;
  }
  state.datasourceSchemaLoading = true;
  state.datasourceSchemaError = "";
  render();
  try {
    state.datasourceSchema = await api(`/api/v1/admin/datasources/${selected.id}/schema`);
    state.datasourceSchemaSelectedObjectName = "";
    state.datasourceSchemaDraftTables = null;
  } catch (error) {
    state.datasourceSchema = null;
    state.datasourceSchemaError = error.message;
  } finally {
    state.datasourceSchemaLoading = false;
    render();
  }
}
async function loadSchemaCache() {
  state.schemaCache = await api("/api/v1/admin/schema-cache");
  render();
}
async function loadBusinessLogicSuggestions() {
  const result = await api("/api/v1/admin/business-logic-suggestions");
  state.businessLogic = result.items || [];
  state.businessLogicDatasource = result.datasource || null;
  state.businessLogicDatasources = result.datasources || [];
  render();
}
async function loadLlmConfig() {
  const result = await api("/api/v1/admin/llm-config");
  state.llmConfig = result.item || null;
  render();
}
async function loadReasoningConfig() {
  const result = await api("/api/v1/admin/reasoning-config");
  state.reasoningConfig = result.item || null;
  render();
}
async function loadGovernancePolicy() {
  const result = await api("/api/v1/admin/governance-policy");
  state.governancePolicy = result.item || null;
  render();
}
async function loadLicense(shouldRender = true) {
  state.license = await api("/api/v1/admin/license/status");
  if (shouldRender) {
    render();
  }
}
async function loadShellLicense() {
  if (!state.token || state.mustChangePassword) return;
  try {
    await loadLicense(false);
  } catch (error) {
    setMessage("error", error.message);
  }
}
async function loadAdminAudit() {
  const result = await api("/api/v1/admin/audit/admin-events");
  state.adminAudit = result.items || [];
  render();
}
async function bootstrap() {
  render();
  if (!state.token) return;
  try {
    const me = await api("/api/v1/admin/me");
    state.username = me.username;
    state.mustChangePassword = me.must_change_password;
    localStorage.setItem("gaard_admin_must_change", String(state.mustChangePassword));
    await loadShellLicense();
    render();
    await loadCurrentSection();
  } catch (error) {
    state.overviewLoading = false;
    setMessage("error", error.message);
    render();
  }
}
void bootstrap();
