type Section =
  | "overview"
  | "widgets"
  | "data-audit"
  | "prompts"
  | "schema-cache"
  | "business-logic"
  | "llm-config"
  | "governance-policy"
  | "identity"
  | "datasources"
  | "license"
  | "admin-audit";

type PromptTemplate = {
  prompt_key: string;
  name: string;
  description: string;
  system_prompt: string;
  user_prompt_template: string;
  version: number;
  active: boolean;
};

type DatasourceConnector = {
  id: number;
  connector_key: string;
  name: string;
  database_type: string;
  database_url: string;
  masked_database_url: string;
  sql_dialect: string;
  active: boolean;
  system_managed: boolean;
};

type DatasourceTypeConfigSchema = {
  properties?: Record<string, {
    title?: string;
    description?: string;
    default?: unknown;
  }>;
};

type DatasourceType = {
  type_key: string;
  label: string;
  description: string;
  sql_dialects: string[];
  default_sql_dialect: string;
  config_schema: DatasourceTypeConfigSchema;
};

type OverviewDatasource = {
  connector_key: string;
  name: string;
  database_type: string;
  masked_database_url: string;
  active: boolean;
};

type OverviewWidgetResult = {
  status: "ok" | "error";
  value?: unknown;
  columns?: string[];
  rows?: Record<string, unknown>[];
  answer?: string;
  interpretation?: string;
  result_mode?: "data" | "interpretation";
  sql?: string;
  error?: string;
};

type OverviewWidget = {
  widget_key: string;
  label: string;
  widget_type: "scalar" | "timeseries" | "table";
  datasource_key: string;
  question: string;
  sql: string;
  result_mode: "data" | "interpretation";
  position: number;
  grid_width: number;
  active: boolean;
  result?: OverviewWidgetResult;
};

type OverviewState = {
  datasources: OverviewDatasource[];
  info_widgets: OverviewWidget[];
  runtime_widget: OverviewWidget | null;
  table_widgets: OverviewWidget[];
  widgets: OverviewWidget[];
};

type State = {
  token: string | null;
  username: string;
  mustChangePassword: boolean;
  mobileMenuOpen: boolean;
  section: Section;
  error: string;
  success: string;
  overview: OverviewState | null;
  overviewWidgetConfigs: OverviewWidget[];
  overviewWidgetDatasources: OverviewDatasource[];
  selectedOverviewWidgetKey: string;
  overviewEditorWidgetKey: string | null;
  overviewPlacementSlot: number | null;
  overviewLoading: boolean;
  overviewRefreshing: boolean;
  overviewTablePages: Record<string, number>;
  dataAudit: any[];
  dataAuditType: string;
  dataAuditOutputClassification: string;
  dataAuditSqlContains: string;
  adminAudit: any[];
  auditSettings: any | null;
  prompts: PromptTemplate[];
  selectedPromptKey: string;
  schemaCache: any | null;
  businessLogic: any[];
  businessLogicDatasource: any | null;
  businessLogicEditorId: number | null;
  llmConfig: any | null;
  governancePolicy: any | null;
  datasources: DatasourceConnector[];
  datasourceTypes: DatasourceType[];
  selectedDatasourceId: number | "new" | null;
  datasourceSchema: any | null;
  datasourceSchemaLoading: boolean;
  datasourceSchemaError: string;
  datasourceSchemaSelectedObjectName: string;
  datasourceSchemaShowEnabledOnly: boolean;
  datasourceSchemaDraftTables: Record<string, any> | null;
  license: any | null;
};

const app = document.querySelector<HTMLDivElement>("#app");

const sections: Array<{ key: Section; label: string }> = [
  { key: "overview", label: "Overview" },
  { key: "widgets", label: "Widgets" },
  { key: "data-audit", label: "Data audit" },
  { key: "prompts", label: "Prompts" },
  { key: "schema-cache", label: "Schema cache" },
  { key: "business-logic", label: "Business logic suggestions" },
  { key: "llm-config", label: "LLM configuration" },
  { key: "governance-policy", label: "Governance policy" },
  { key: "identity", label: "Identity connector" },
  { key: "datasources", label: "Datasource connector" },
  { key: "license", label: "License" },
  { key: "admin-audit", label: "Admin audit" },
];

const state: State = {
  token: localStorage.getItem("gaard_admin_token"),
  username: localStorage.getItem("gaard_admin_username") || "",
  mustChangePassword: localStorage.getItem("gaard_admin_must_change") === "true",
  mobileMenuOpen: false,
  section: "overview",
  error: "",
  success: "",
  overview: null,
  overviewWidgetConfigs: [],
  overviewWidgetDatasources: [],
  selectedOverviewWidgetKey: "",
  overviewEditorWidgetKey: null,
  overviewPlacementSlot: null,
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
  businessLogicEditorId: null,
  llmConfig: null,
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
  license: null,
};

const dataAuditTypes = [
  { value: "", label: "All types" },
  { value: "info", label: "Info" },
  { value: "sql_error", label: "SQL error" },
  { value: "access_error", label: "Access error" },
];

const outputClassifications = [
  { value: "", label: "All classifications" },
  { value: "personal_data", label: "Personal data" },
  { value: "sensitive_data", label: "Sensitive data" },
  { value: "technical_data", label: "Technical data" },
  { value: "neutral_data", label: "Neutral data" },
  { value: "unknown", label: "Unknown" },
];

const OVERVIEW_TABLE_PAGE_SIZE = 10;
const OVERVIEW_GRID_COLUMNS = 4;
const OVERVIEW_MIN_GRID_SLOTS = 8;
const ALLOWED_WIDGET_HTML_TAGS = new Set(["A", "B", "I", "UL", "LI"]);
const DROPPED_WIDGET_HTML_TAGS = new Set(["SCRIPT", "STYLE", "IFRAME", "OBJECT", "EMBED", "TEMPLATE", "SVG", "MATH"]);

function escapeHtml(value: unknown): string {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderWidgetContent(value: unknown): string {
  const documentFragment = new DOMParser().parseFromString(String(value ?? ""), "text/html");

  return Array.from(documentFragment.body.childNodes)
    .map(renderWidgetContentNode)
    .join("");
}

function renderWidgetContentNode(node: ChildNode): string {
  if (node.nodeType === Node.TEXT_NODE) {
    return escapeHtml(node.textContent || "");
  }

  if (node.nodeType !== Node.ELEMENT_NODE) {
    return "";
  }

  const element = node as HTMLElement;
  const tagName = element.tagName.toUpperCase();

  if (DROPPED_WIDGET_HTML_TAGS.has(tagName)) {
    return "";
  }

  const children = Array.from(element.childNodes)
    .map(renderWidgetContentNode)
    .join("");

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

function sanitizeWidgetHref(value: string | null): string {
  const href = String(value || "")
    .trim()
    .replace(/[\u0000-\u001F\u007F\s]+/g, "");

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

function formatAuditTime(value: unknown): string {
  const raw = String(value ?? "");
  const match = raw.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?/);

  if (match) {
    const millis = (match[5] || "000").slice(0, 3).padEnd(3, "0");
    return `${match[1]} ${match[2]}:${match[3]}:${match[4]}:${millis}`;
  }

  return raw;
}

function extractErrorMessage(value: unknown): string {
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
    const record = value as Record<string, unknown>;

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

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
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

  return payload as T;
}

function setMessage(type: "error" | "success", value: string): void {
  state.error = type === "error" ? value : "";
  state.success = type === "success" ? value : "";
  const region = document.querySelector<HTMLElement>("#message-region");

  if (region) {
    region.innerHTML = renderMessages();
  }
}

function renderMessages(): string {
  return `
          ${state.error ? `<div class="error">${escapeHtml(state.error)}</div>` : ""}
          ${state.success ? `<div class="success">${escapeHtml(state.success)}</div>` : ""}`;
}

function persistAuth(token: string, username: string, mustChangePassword: boolean): void {
  state.token = token;
  state.username = username;
  state.mustChangePassword = mustChangePassword;
  localStorage.setItem("gaard_admin_token", token);
  localStorage.setItem("gaard_admin_username", username);
  localStorage.setItem("gaard_admin_must_change", String(mustChangePassword));
}

function logout(): void {
  state.token = null;
  state.username = "";
  state.mustChangePassword = false;
  state.overviewLoading = false;
  state.overviewRefreshing = false;
  state.overviewEditorWidgetKey = null;
  localStorage.removeItem("gaard_admin_token");
  localStorage.removeItem("gaard_admin_username");
  localStorage.removeItem("gaard_admin_must_change");
  render();
}

function render(): void {
  if (!app) return;
  if (!state.token) return renderLogin();
  if (state.mustChangePassword) return renderPasswordChange();
  renderShell();
}

function renderLogin(): void {
  app!.innerHTML = `
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

  document.querySelector<HTMLFormElement>("#login-form")?.addEventListener("submit", async event => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const result = await api<any>("/api/v1/admin/auth/login", {
        method: "POST",
        body: JSON.stringify({ username: form.get("username"), password: form.get("password") }),
      });
      persistAuth(result.token, result.username, result.must_change_password);
      state.overviewLoading = !result.must_change_password && state.section === "overview";
      setMessage("success", "");
      render();
      if (!result.must_change_password) await loadCurrentSection();
    } catch (error) {
      setMessage("error", (error as Error).message);
      render();
    }
  });
}

function renderPasswordChange(): void {
  app!.innerHTML = `
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
  document.querySelector<HTMLFormElement>("#password-form")?.addEventListener("submit", async event => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const result = await api<any>("/api/v1/admin/auth/change-password", {
        method: "POST",
        body: JSON.stringify({
          current_password: form.get("current_password"),
          new_password: form.get("new_password"),
        }),
      });
      state.mustChangePassword = result.must_change_password;
      state.overviewLoading = !state.mustChangePassword && state.section === "overview";
      localStorage.setItem("gaard_admin_must_change", String(result.must_change_password));
      setMessage("success", "Password changed.");
      render();
      await loadCurrentSection();
    } catch (error) {
      setMessage("error", (error as Error).message);
      render();
    }
  });
}

function renderShell(): void {
  const active = sections.find(section => section.key === state.section);
  app!.innerHTML = `
    <div class="app-shell">
      <aside class="sidebar${state.mobileMenuOpen ? " menu-open" : ""}">
        <div class="sidebar-header">
          <div class="brand"><strong>GAARD Admin Console</strong><span>Community edition</span></div>
          <button class="menu-toggle" id="mobile-menu-button" type="button" aria-label="${state.mobileMenuOpen ? "Close navigation" : "Open navigation"}" aria-expanded="${state.mobileMenuOpen}" aria-controls="admin-navigation">
            <span></span><span></span><span></span>
          </button>
        </div>
        <nav class="nav" id="admin-navigation">
          ${sections.map(section => `<button data-section="${section.key}" class="${section.key === state.section ? "active" : ""}">${section.label}</button>`).join("")}
        </nav>
        <div class="sidebar-footer"><span>${escapeHtml(state.username)}</span><button id="logout-button">Sign out</button></div>
      </aside>
      <main class="main">
        <header class="topbar">
          <h1>${escapeHtml(active?.label || "Admin")}</h1>
          <div class="topbar-actions"><span>${escapeHtml(state.username)}</span><button id="top-logout-button">Sign out</button></div>
        </header>
        <section class="content">
          <div id="message-region">${renderMessages()}</div>
          ${renderSection()}
        </section>
      </main>
    </div>
    ${renderOverviewWidgetModal()}`;

  document.querySelectorAll<HTMLButtonElement>("[data-section]").forEach(button => {
    button.addEventListener("click", async () => {
      state.section = button.dataset.section as Section;
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
    render();
  });
  document.querySelector("#logout-button")?.addEventListener("click", logout);
  document.querySelector("#top-logout-button")?.addEventListener("click", logout);
  attachSectionHandlers();
}

function renderSection(): string {
  if (state.section === "overview") return renderOverview();
  if (state.section === "widgets") return renderWidgets();
  if (state.section === "data-audit") return renderDataAudit();
  if (state.section === "prompts") return renderPrompts();
  if (state.section === "schema-cache") return renderSchemaCache();
  if (state.section === "business-logic") return renderBusinessLogicSuggestions();
  if (state.section === "llm-config") return renderLlmConfig();
  if (state.section === "governance-policy") return renderGovernancePolicy();
  if (state.section === "identity") return renderStub("Identity connector", "FreeIPA connector configuration is planned.");
  if (state.section === "datasources") return renderDatasources();
  if (state.section === "license") return renderLicense();
  if (state.section === "admin-audit") return renderAdminAudit();
  return "";
}

function renderOverview(): string {
  const overview = state.overview;
  const widgets = overview?.widgets || [];
  const isLoading = state.overviewLoading || state.overviewRefreshing;
  const showInitialLoader = isLoading && !overview;

  return `
    <div class="toolbar overview-toolbar">
      <div class="refresh-status" aria-live="polite">
        ${isLoading ? `<span class="spinner" aria-hidden="true"></span><span>Refreshing</span>` : ""}
      </div>
      <button class="primary" type="button" id="overview-refresh" ${isLoading ? "disabled" : ""}>Refresh</button>
    </div>
    <div class="overview-grid">
      ${showInitialLoader ? renderOverviewLoading() : renderOverviewGrid(widgets)}
    </div>`;
}

function renderOverviewLoading(): string {
  return `
    <section class="overview-loading" aria-live="polite">
      <span class="spinner" aria-hidden="true"></span>
      <span>Refreshing</span>
    </section>`;
}

function renderOverviewGrid(widgets: OverviewWidget[]): string {
  const layout = buildOverviewLayout(widgets);
  const occupiedSlots = Array.from(layout.occupiedSlots);
  const slotCount = Math.max(
    OVERVIEW_MIN_GRID_SLOTS,
    occupiedSlots.length ? Math.max(...occupiedSlots) + 1 : 0
  );

  return Array.from({ length: slotCount }, (_, slot) => {
    const widget = layout.widgetSlots.get(slot);

    if (widget) {
      return renderOverviewGridWidget(widget);
    }

    if (layout.occupiedSlots.has(slot)) {
      return "";
    }

    return renderOverviewEmptySlot(slot);
  }).join("");
}

function buildOverviewLayout(widgets: OverviewWidget[]): {
  widgetSlots: Map<number, OverviewWidget>;
  occupiedSlots: Set<number>;
} {
  const widgetSlots = new Map<number, OverviewWidget>();
  const occupiedSlots = new Set<number>();

  widgets
    .filter(widget => widget.active !== false)
    .sort((left, right) => (left.position || 0) - (right.position || 0))
    .forEach(widget => {
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

function canPlaceOverviewWidget(slot: number, width: number, occupiedSlots: Set<number>): boolean {
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

function findAvailableOverviewSlot(slot: number, width: number, occupiedSlots: Set<number>): number {
  let candidate = Math.max(0, slot);

  while (!canPlaceOverviewWidget(candidate, width, occupiedSlots)) {
    candidate += 1;
  }

  return candidate;
}

function overviewSlotFromPosition(position: unknown): number {
  const numeric = Number(position);

  if (!Number.isFinite(numeric) || numeric < 10) {
    return 0;
  }

  return Math.max(0, Math.floor(numeric / 10) - 1);
}

function overviewPositionFromSlot(slot: number): number {
  return (slot + 1) * 10;
}

function getOverviewWidgetGridWidth(widget: Pick<OverviewWidget, "widget_type" | "grid_width">): number {
  const fallback = widget.widget_type === "scalar" ? 1 : OVERVIEW_GRID_COLUMNS;
  const width = Number(widget.grid_width || fallback);

  return Math.max(1, Math.min(OVERVIEW_GRID_COLUMNS, Number.isFinite(width) ? Math.floor(width) : fallback));
}

function renderOverviewGridWidget(widget: OverviewWidget): string {
  const result = widget.result;
  const width = getOverviewWidgetGridWidth(widget);

  return `
    <section class="widget-card overview-widget-slot overview-widget-${escapeHtml(widget.widget_type)}" style="grid-column: span ${escapeHtml(width)};">
      <div class="widget-card-header">
        <div>
          <span>${escapeHtml(widget.datasource_key)}</span>
          <strong>${escapeHtml(widget.label)}</strong>
        </div>
        ${renderEditWidgetButton(widget.widget_key)}
      </div>
      <div class="widget-card-main">
        ${renderOverviewWidgetBody(widget, result)}
      </div>
    </section>`;
}

function renderOverviewWidgetBody(widget: OverviewWidget, result?: OverviewWidgetResult): string {
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

function renderOverviewEmptySlot(slot: number): string {
  return `
    <section class="overview-empty-slot">
      <button type="button" data-overview-empty-slot="${escapeHtml(slot)}" aria-label="Add widget to slot ${escapeHtml(slot + 1)}">+</button>
      ${state.overviewPlacementSlot === slot ? renderOverviewPlacementPanel(slot) : ""}
    </section>`;
}

function renderOverviewPlacementPanel(slot: number): string {
  const availableWidgets = state.overviewWidgetConfigs.filter(widget => widget.active === false);

  return `
    <div class="overview-placement-panel">
      <label>Widget
        <select data-overview-placement-select="${escapeHtml(slot)}" ${availableWidgets.length ? "" : "disabled"}>
          ${availableWidgets.length
            ? availableWidgets.map(widget => `<option value="${escapeHtml(widget.widget_key)}">${escapeHtml(widget.label)} (${escapeHtml(widget.widget_key)}, ${escapeHtml(getOverviewWidgetGridWidth(widget))} cols)</option>`).join("")
            : `<option>No inactive widgets</option>`}
        </select>
      </label>
      <div class="button-row">
        <button type="button" data-overview-place-widget="${escapeHtml(slot)}" ${availableWidgets.length ? "" : "disabled"}>Add selected</button>
        <button type="button" class="primary" data-overview-new-widget="${escapeHtml(slot)}">New widget</button>
      </div>
    </div>`;
}

function renderEditWidgetButton(widgetKey: string): string {
  return `
    <button class="icon-button" type="button" data-edit-overview-widget="${escapeHtml(widgetKey)}" aria-label="Edit widget source" title="Edit source">
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d="M12 20h9" />
        <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
      </svg>
    </button>`;
}

function renderOverviewWidgetModal(): string {
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
        <input type="hidden" name="position" value="${escapeHtml(widget.position || 100)}" />
        <input type="hidden" name="grid_width" value="${escapeHtml(getOverviewWidgetGridWidth(widget))}" />
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

function getOverviewEditorWidget(): OverviewWidget | null {
  if (!state.overviewEditorWidgetKey) return null;

  return (state.overview?.widgets || []).find(
    widget => widget.widget_key === state.overviewEditorWidgetKey
  ) || null;
}

function renderWidgetTypeOptions(selected: string): string {
  return ["scalar", "timeseries", "table"]
    .map(value => `<option value="${value}" ${value === selected ? "selected" : ""}>${value}</option>`)
    .join("");
}

function renderOverviewDatasourceOptions(selected: string): string {
  const datasources = state.overviewWidgetDatasources.length
    ? state.overviewWidgetDatasources
    : state.overview?.datasources || [];

  return datasources
    .map(item => `<option value="${escapeHtml(item.connector_key)}" ${item.connector_key === selected ? "selected" : ""}>${escapeHtml(item.name)} (${escapeHtml(item.connector_key)})</option>`)
    .join("");
}

function renderTimeSeriesChart(result: OverviewWidgetResult): string {
  const points = normalizeChartPoints(result);

  if (!points.length) {
    return `<div class="empty-state">No data yet.</div>`;
  }

  const max = Math.max(...points.map(point => point.value), 1);
  const dates = Array.from(new Set(points.map(point => point.date)));
  const series = Array.from(new Set(points.map(point => point.series)));

  return `
    <div class="chart">
      ${dates.map(date => {
        const datePoints = points.filter(point => point.date === date);
        return `<div class="chart-row">
          <div class="chart-date">${escapeHtml(date)}</div>
          <div class="chart-bars">
            ${datePoints.map(point => `<div class="chart-bar" title="${escapeHtml(`${point.series}: ${point.value}`)}" style="width: ${Math.max(4, (point.value / max) * 100)}%"><span>${escapeHtml(point.series)}: ${escapeHtml(point.value)}</span></div>`).join("")}
          </div>
        </div>`;
      }).join("")}
    </div>
    <div class="chart-legend">${series.map(item => `<span>${escapeHtml(item)}</span>`).join("")}</div>`;
}

function normalizeChartPoints(result: OverviewWidgetResult): Array<{ date: string; series: string; value: number }> {
  const rows = result.rows || [];
  const columns = result.columns || Object.keys(rows[0] || {});

  if (!rows.length || columns.length < 2) {
    return [];
  }

  const dateColumn = columns[0];

  if (columns.length === 3 && rows.some(row => !isNumeric(row[columns[1]]) && isNumeric(row[columns[2]]))) {
    return rows
      .filter(row => isNumeric(row[columns[2]]))
      .map(row => ({
        date: formatChartDate(row[dateColumn]),
        series: String(row[columns[1]] ?? "series"),
        value: Number(row[columns[2]]),
      }));
  }

  return rows.flatMap(row =>
    columns.slice(1)
      .filter(column => isNumeric(row[column]))
      .map(column => ({
        date: formatChartDate(row[dateColumn]),
        series: column,
        value: Number(row[column]),
      }))
  );
}

function formatChartDate(value: unknown): string {
  return String(value ?? "").slice(0, 10);
}

function isNumeric(value: unknown): boolean {
  return value !== null && value !== "" && !Array.isArray(value) && Number.isFinite(Number(value));
}

function renderOverviewTable(widgetKey: string, result: OverviewWidgetResult): string {
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
        <thead><tr>${columns.map(column => `<th>${escapeHtml(column)}</th>`).join("")}</tr></thead>
        <tbody>
          ${pageRows.length ? pageRows.map(row => `<tr>${columns.map(column => `<td>${formatOverviewTableCell(row[column])}</td>`).join("")}</tr>`).join("") : `<tr><td colspan="${escapeHtml(columns.length)}" class="empty-state">No rows.</td></tr>`}
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

function formatOverviewTableCell(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return `<span class="muted">-</span>`;
  }

  if (typeof value === "object") {
    return `<code>${escapeHtml(JSON.stringify(value))}</code>`;
  }

  return renderWidgetContent(value);
}

function renderDataAudit(): string {
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
        <tbody>${state.dataAudit.map(item => `<tr><td>${escapeHtml(formatAuditTime(item.occurred_at))}</td><td>${escapeHtml(item.audit_type || "info")}</td><td>${escapeHtml(item.output_classification || "unknown")}</td><td>${renderAuditLearning(item)}</td><td>${escapeHtml(item.user_id)}</td><td>${escapeHtml(item.datasource_id)}</td><td>${escapeHtml(item.question)}</td><td>${escapeHtml(item.answer)}</td><td><code>${escapeHtml(item.sql)}</code></td><td>${renderAuditMetadata(item)}</td></tr>`).join("")}</tbody>
      </table></div>
    </section>`;
}

function renderAuditMetadata(item: any): string {
  const metadata = item.metadata || {};

  if (!Object.keys(metadata).length) return "";

  return `<pre class="metadata-json">${escapeHtml(JSON.stringify(metadata, null, 2))}</pre>`;
}

function renderAuditLearning(item: any): string {
  const learning = item.metadata?.business_logic_learning;

  if (!learning) return "";

  return `
    <span>${escapeHtml(learning.message || "")}</span>
    ${learning.suggestion_id ? `<button type="button" data-open-business-logic>Open suggestions</button>` : ""}`;
}

function renderDataAuditTypeOptions(): string {
  return dataAuditTypes
    .map(type => `<option value="${escapeHtml(type.value)}" ${state.dataAuditType === type.value ? "selected" : ""}>${escapeHtml(type.label)}</option>`)
    .join("");
}

function renderOutputClassificationOptions(): string {
  return outputClassifications
    .map(item => `<option value="${escapeHtml(item.value)}" ${state.dataAuditOutputClassification === item.value ? "selected" : ""}>${escapeHtml(item.label)}</option>`)
    .join("");
}

function renderWidgets(): string {
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
          ${state.overviewWidgetConfigs.length
            ? state.overviewWidgetConfigs.map(widget => renderWidgetConfigListItem(widget, selectedWidget?.widget_key === widget.widget_key && !creating)).join("")
            : `<p class="muted">No widgets defined.</p>`}
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

function renderWidgetConfigListItem(widget: OverviewWidget, active: boolean): string {
  return `
    <div class="widget-config-row ${active ? "active" : ""}">
      <input type="checkbox" data-overview-widget-active="${escapeHtml(widget.widget_key)}" aria-label="Enable ${escapeHtml(widget.label)}" ${widget.active ? "checked" : ""} />
      <button class="widget-config-select" type="button" data-overview-widget-select="${escapeHtml(widget.widget_key)}">
        <strong>${escapeHtml(widget.label)}</strong>
        <span>${escapeHtml(widget.widget_key)} · ${escapeHtml(widget.widget_type)} · ${escapeHtml(formatOverviewWidgetResultMode(widget.result_mode))} · ${escapeHtml(formatOverviewWidgetSize(widget))} · slot ${escapeHtml(overviewSlotFromPosition(widget.position) + 1)}</span>
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

function getSelectedOverviewWidgetConfig(): OverviewWidget | null {
  if (state.selectedOverviewWidgetKey === "__new__") {
    return null;
  }

  return state.overviewWidgetConfigs.find(widget => widget.widget_key === state.selectedOverviewWidgetKey)
    || state.overviewWidgetConfigs[0]
    || null;
}

function getOverviewWidgetFormPosition(widget: OverviewWidget | null): number {
  if (widget) {
    return widget.position || 100;
  }

  return overviewPositionFromSlot(state.overviewPlacementSlot ?? 0);
}

function formatOverviewWidgetSize(widget: Pick<OverviewWidget, "widget_type" | "grid_width">): string {
  return `${getOverviewWidgetGridWidth(widget)}/${OVERVIEW_GRID_COLUMNS}`;
}

function getDefaultOverviewWidgetGridWidth(widgetType: string): number {
  return widgetType === "scalar" ? 1 : OVERVIEW_GRID_COLUMNS;
}

function renderOverviewWidgetSizeOptions(selected: number): string {
  const options = [
    { value: 1, label: "Small (1 col)" },
    { value: 2, label: "Medium (2 cols)" },
    { value: 3, label: "Wide (3 cols)" },
    { value: 4, label: "Full (4 cols)" },
  ];

  return options
    .map(option => `<option value="${escapeHtml(option.value)}" ${option.value === selected ? "selected" : ""}>${escapeHtml(option.label)}</option>`)
    .join("");
}

function renderOverviewWidgetResultModeOptions(selected: string = "data"): string {
  const options = [
    { value: "data", label: "Zwróć dane" },
    { value: "interpretation", label: "Interpretuj dane" },
  ];

  return options
    .map(option => `<option value="${escapeHtml(option.value)}" ${option.value === selected ? "selected" : ""}>${escapeHtml(option.label)}</option>`)
    .join("");
}

function formatOverviewWidgetResultMode(value: string = "data"): string {
  return value === "interpretation" ? "interpretation" : "data";
}

function renderOverviewWidgetSettingsForm(widget: OverviewWidget | null): string {
  const creating = widget === null;
  const position = getOverviewWidgetFormPosition(widget);
  const widgetType = widget?.widget_type || "scalar";
  const gridWidth = widget ? getOverviewWidgetGridWidth(widget) : getDefaultOverviewWidgetGridWidth(widgetType);
  const resultMode = widget?.result_mode || "data";

  return `
    <form class="form-grid" id="overview-widget-settings-form" data-widget-mode="${creating ? "create" : "update"}" data-widget-key="${escapeHtml(widget?.widget_key || "")}">
      ${creating ? `<label>Widget key<input name="widget_key" value="" placeholder="custom_widget_key" /></label>` : `<input type="hidden" name="widget_key" value="${escapeHtml(widget?.widget_key || "")}" />`}
      <label>Label<input name="label" value="${escapeHtml(widget?.label || "")}" /></label>
      <div class="subgrid">
        <label>Type<select name="widget_type">${renderWidgetTypeOptions(widget?.widget_type || "scalar")}</select></label>
        <label>Datasource<select name="datasource_key">${renderOverviewDatasourceOptions(widget?.datasource_key || "metadata-db")}</select></label>
      </div>
      <div class="subgrid">
        <label>Position<input name="position" type="number" min="10" step="10" value="${escapeHtml(position)}" /></label>
        <label>Size<select name="grid_width">${renderOverviewWidgetSizeOptions(gridWidth)}</select></label>
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

function renderPrompts(): string {
  const selected = state.prompts.find(prompt => prompt.prompt_key === state.selectedPromptKey) || state.prompts[0];
  return `
    <div class="split">
      <section class="panel">
        <div class="panel-header"><h2>Prompt templates</h2></div>
        <div class="panel-body list">${state.prompts.map(prompt => `<button data-prompt="${prompt.prompt_key}" class="${selected?.prompt_key === prompt.prompt_key ? "active" : ""}"><strong>${escapeHtml(prompt.name)}</strong><br /><span>v${escapeHtml(prompt.version)} ${prompt.active ? "active" : "inactive"}</span></button>`).join("")}</div>
      </section>
      <section class="panel">
        <div class="panel-header"><h2>${escapeHtml(selected?.name || "Prompt")}</h2></div>
        <div class="panel-body">${selected ? renderPromptForm(selected) : ""}</div>
      </section>
    </div>`;
}

function renderPromptForm(prompt: PromptTemplate): string {
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

function renderDatasources(): string {
  const selected = getSelectedDatasource();
  return `
    <div class="split">
      <section class="panel">
        <div class="panel-header"><h2>Datasources</h2><button id="new-datasource">New</button></div>
        <div class="panel-body list">${state.datasources.map(connector => `<button data-datasource="${connector.id}" class="${selected?.id === connector.id ? "active" : ""}"><strong>${escapeHtml(connector.name)}</strong><br /><span>${escapeHtml(connector.database_type)} ${connector.active ? "active" : ""}</span></button>`).join("")}</div>
      </section>
      <section class="panel">
        <div class="panel-header"><h2>${selected ? escapeHtml(selected.name) : "New datasource"}</h2></div>
        <div class="panel-body">${renderDatasourceForm(selected)}</div>
      </section>
    </div>
    ${selected ? renderDatasourceSchema() : ""}`;
}

function getSelectedDatasource(): DatasourceConnector | null {
  if (state.selectedDatasourceId === "new") return null;
  return state.datasources.find(item => item.id === state.selectedDatasourceId) || state.datasources[0] || null;
}

function renderDatasourceForm(connector: DatasourceConnector | null): string {
  const systemManaged = connector?.system_managed === true;
  const selectedTypeKey = connector?.database_type || state.datasourceTypes[0]?.type_key || "";
  const selectedType = getDatasourceType(selectedTypeKey);
  const databaseUrlSchema = selectedType?.config_schema?.properties?.database_url;
  const selectedSqlDialect = connector?.sql_dialect || selectedType?.default_sql_dialect || "";
  const databaseUrl = connector?.database_url || String(databaseUrlSchema?.default || "");
  const unavailableType = Boolean(selectedTypeKey && !selectedType);
  const disabled = systemManaged || unavailableType || !selectedType ? "disabled" : "";
  const connectorDescription = selectedType?.description
    || (unavailableType
      ? `Connector type '${selectedTypeKey}' is unavailable. Install or enable its plugin before editing this datasource.`
      : "No connector types are available. Install or enable a connector plugin.");
  return `
    <form id="datasource-form" class="form-grid">
      <input type="hidden" name="id" value="${escapeHtml(connector?.id || "")}" />
      ${systemManaged ? `<div class="badge">System managed</div>` : ""}
      <label>Connector key<input name="connector_key" ${connector || systemManaged ? "readonly" : ""} ${disabled} value="${escapeHtml(connector?.connector_key || "")}" /></label>
      <label>Name<input name="name" ${disabled} value="${escapeHtml(connector?.name || "")}" /></label>
      <div class="subgrid">
        <label>Connector type<select id="datasource-type" name="database_type" ${disabled}>${renderDatasourceTypeOptions(selectedTypeKey)}</select></label>
        <label>SQL dialect<select id="datasource-sql-dialect" name="sql_dialect" ${disabled}>${renderSqlDialectOptions(selectedType, selectedSqlDialect)}</select></label>
      </div>
      <p id="datasource-type-description" class="muted">${escapeHtml(connectorDescription)}</p>
      <label><span id="datasource-url-label">${escapeHtml(databaseUrlSchema?.title || "Database URL")}</span><input id="datasource-url" name="database_url" ${disabled} placeholder="${escapeHtml(databaseUrlSchema?.description || "")}" value="${escapeHtml(databaseUrl)}" /></label>
      <label class="inline-check"><input name="active" type="checkbox" ${connector?.active ? "checked" : ""} ${disabled} /> Active datasource</label>
      <div class="button-row">
        <button type="button" id="test-datasource" ${disabled}>Test</button>
        <button type="button" id="introspect-datasource" ${connector ? "" : "disabled"}>Schema introspection</button>
        <button type="button" id="activate-datasource" ${connector && !connector.active && !systemManaged ? "" : "disabled"}>Activate</button>
        <button class="primary" type="submit" ${systemManaged ? "disabled" : ""}>${connector ? "Save" : "Create"}</button>
      </div>
    </form>`;
}

function getDatasourceType(typeKey: string): DatasourceType | null {
  return state.datasourceTypes.find(item => item.type_key === typeKey) || null;
}

function renderDatasourceTypeOptions(selected: string): string {
  const datasourceTypes = [...state.datasourceTypes];

  if (selected && !getDatasourceType(selected)) {
    datasourceTypes.unshift({
      type_key: selected,
      label: `${selected} (plugin unavailable)`,
      description: "",
      sql_dialects: [],
      default_sql_dialect: "",
      config_schema: {},
    });
  }

  if (!datasourceTypes.length) {
    return `<option value="" selected>No connector types available</option>`;
  }

  return datasourceTypes
    .map(item => `<option value="${escapeHtml(item.type_key)}" ${item.type_key === selected ? "selected" : ""}>${escapeHtml(item.label)}</option>`)
    .join("");
}

function renderSqlDialectOptions(datasourceType: DatasourceType | null, selected: string): string {
  const dialects = [...(datasourceType?.sql_dialects || [])];

  if (selected && !dialects.includes(selected)) {
    dialects.unshift(selected);
  }

  if (!dialects.length) {
    return `<option value="" selected>No SQL dialect available</option>`;
  }

  return dialects
    .map(value => `<option value="${escapeHtml(value)}" ${selected === value ? "selected" : ""}>${escapeHtml(value)}</option>`)
    .join("");
}

function syncDatasourceTypeFields(event: Event): void {
  const typeKey = (event.currentTarget as HTMLSelectElement).value;
  const datasourceType = getDatasourceType(typeKey);
  const sqlDialect = document.querySelector<HTMLSelectElement>("#datasource-sql-dialect");
  const description = document.querySelector<HTMLElement>("#datasource-type-description");
  const urlLabel = document.querySelector<HTMLElement>("#datasource-url-label");
  const urlInput = document.querySelector<HTMLInputElement>("#datasource-url");
  const databaseUrlSchema = datasourceType?.config_schema?.properties?.database_url;

  if (sqlDialect) {
    sqlDialect.innerHTML = renderSqlDialectOptions(
      datasourceType,
      datasourceType?.default_sql_dialect || "",
    );
  }

  if (description) {
    description.textContent = datasourceType?.description || "Connector type is unavailable.";
  }

  if (urlLabel) {
    urlLabel.textContent = databaseUrlSchema?.title || "Database URL";
  }

  if (urlInput) {
    urlInput.placeholder = databaseUrlSchema?.description || "";
  }
}

function renderModeOptions(selected: string, values: string[]): string {
  return values
    .map(value => `<option value="${value}" ${selected === value ? "selected" : ""}>${value}</option>`)
    .join("");
}

function renderDatasourceSchema(): string {
  const schema = state.datasourceSchema?.item;
  const rawTables = schema?.raw_schema?.tables || [];
  const tableSettings = schema?.table_settings?.tables || {};
  const draftTables = schema ? getDatasourceSchemaDraftTables(rawTables, tableSettings) : {};
  const visibleTables = state.datasourceSchemaShowEnabledOnly
    ? rawTables.filter((table: any) => draftTables[table.name]?.selected !== false)
    : rawTables;
  const selectedTable = schema ? getSelectedDatasourceSchemaObject(rawTables, visibleTables) : null;
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
                ${visibleTables.length ? visibleTables.map((table: any) => renderDatasourceObjectListItem(table, draftTables[table.name] || {}, selectedTable?.name === table.name)).join("") : `<p class="muted schema-object-empty">No enabled objects.</p>`}
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

function getDatasourceSchemaDraftTables(rawTables: any[], tableSettings: Record<string, any>): Record<string, any> {
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
      join_logic: settings.join_logic || "",
    };
  }

  return state.datasourceSchemaDraftTables;
}

function getSelectedDatasourceSchemaObject(rawTables: any[], visibleTables: any[]): any | null {
  const current = rawTables.find((table: any) => table.name === state.datasourceSchemaSelectedObjectName);
  const currentIsVisible = visibleTables.some((table: any) => table.name === current?.name);

  if (current && currentIsVisible) return current;

  const fallback = visibleTables[0] || null;
  state.datasourceSchemaSelectedObjectName = fallback?.name || "";
  return fallback;
}

function renderBusinessLogicSuggestions(): string {
  const datasource = state.businessLogicDatasource;
  return `
    <section class="panel">
      <div class="panel-header">
        <h2>Business logic suggestions</h2>
        <span class="badge">${escapeHtml(datasource?.connector_key || "no active datasource")}</span>
      </div>
      <div class="table-wrap"><table>
        <thead><tr><th>Use</th><th>Status</th><th>Safety</th><th>Rule</th><th>Error</th><th>Confidence</th><th>Actions</th></tr></thead>
        <tbody>${state.businessLogic.map(item => `
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
      ${state.businessLogic.length ? "" : `<div class="panel-body"><p class="muted">No suggestions for the active datasource.</p></div>`}
    </section>
    ${renderBusinessLogicEditorModal()}`;
}

function renderBusinessLogicEditorModal(): string {
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

function getBusinessLogicEditorSuggestion(): any | null {
  if (state.businessLogicEditorId === null) return null;

  return state.businessLogic.find(item => Number(item.id) === state.businessLogicEditorId) || null;
}

function renderLlmConfig(): string {
  const config = state.llmConfig || {};
  const apiKeyStatus = config.api_key_configured
    ? `Configured (${escapeHtml(config.api_key_preview || "hidden")})`
    : "Not configured";
  const apiKeyPlaceholder = config.api_key_configured
    ? "Leave blank to keep current key"
    : "Enter API key";
  return `
    <section class="panel">
      <div class="panel-header"><h2>LLM configuration</h2></div>
      <div class="panel-body">
        <form id="llm-config-form" class="form-grid">
          <label>Provider<input name="provider" value="${escapeHtml(config.provider || "openai-compatible")}" /></label>
          <label>Base URL<input name="base_url" value="${escapeHtml(config.base_url || "")}" /></label>
          <label>API key <span class="muted">${apiKeyStatus}</span><input name="api_key" type="password" value="" placeholder="${apiKeyPlaceholder}" autocomplete="new-password" /></label>
          <label class="checkbox-row"><input name="clear_api_key" type="checkbox" /> Clear API key</label>
          <label>Model<input name="model" value="${escapeHtml(config.model || "")}" /></label>
          <label>LLM timeout seconds<input name="timeout_seconds" type="number" min="1" max="600" value="${escapeHtml(config.timeout_seconds || 60)}" /></label>
          <div class="subgrid">
            <label>Intent mode<select name="intent_classification_mode">${renderModeOptions(config.intent_classification_mode || "auto", ["auto", "llm"])}</select></label>
            <label>SQL generation<select name="sql_generation_mode">${renderModeOptions(config.sql_generation_mode || "llm", ["llm"])}</select></label>
          </div>
          <div class="subgrid">
            <label>Result interpretation<select name="result_interpretation_mode">${renderModeOptions(config.result_interpretation_mode || "llm", ["llm"])}</select></label>
            <label>Output classification<select name="output_classification_mode">${renderModeOptions(config.output_classification_mode || "auto", ["auto", "llm"])}</select></label>
          </div>
          <div class="subgrid">
            <label>Investigation mode<select name="investigation_mode">${renderModeOptions(config.investigation_mode || "llm", ["llm"])}</select></label>
            <label>Ambiguity handling<select name="investigation_ambiguity_mode">${renderModeOptions(config.investigation_ambiguity_mode || "clarify", ["clarify", "safe_aggregate"])}</select></label>
          </div>
          <div class="subgrid">
            <label>Query max rows<input name="query_max_rows" type="number" min="1" max="100000" value="${escapeHtml(config.query_max_rows || 100)}" /></label>
            <label>Query timeout seconds<input name="query_timeout_seconds" type="number" min="1" max="3600" value="${escapeHtml(config.query_timeout_seconds || 30)}" /></label>
          </div>
          <label>Extra body JSON<textarea name="extra_body">${escapeHtml(config.extra_body_json || "{}")}</textarea></label>
          <div class="mono muted">${escapeHtml(JSON.stringify(config.sources || {}, null, 2))}</div>
          <div class="form-actions"><button class="primary" type="submit">Save LLM configuration</button></div>
        </form>
      </div>
    </section>`;
}

function renderGovernancePolicy(): string {
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

function renderDatasourceObjectListItem(table: any, settings: any, active: boolean): string {
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

function renderDatasourceObjectDetails(table: any, settings: any): string {
  const objectType = table.object_type || "table";
  return `
    <div class="schema-object-detail-header">
      <div>
        <h3>${escapeHtml(table.name)}</h3>
        <span class="badge">${escapeHtml(objectType)}</span>
      </div>
    </div>
    <div class="schema-object-columns">${escapeHtml((table.columns || []).map((column: any) => `${column.name}:${column.type}${column.primary_key ? " pk" : ""}`).join(", ") || "No columns available.")}</div>
    <label>Description<input data-schema-detail="description" name="${escapeHtml(table.name)}__description" value="${escapeHtml(settings.description || "")}" /></label>
    <label>Primary key guidance<input data-schema-detail="primary_key_prompt" name="${escapeHtml(table.name)}__primary_key_prompt" value="${escapeHtml(settings.primary_key_prompt || "")}" /></label>
    <label>Foreign key guidance<input data-schema-detail="foreign_key_prompt" name="${escapeHtml(table.name)}__foreign_key_prompt" value="${escapeHtml(settings.foreign_key_prompt || "")}" /></label>
    <label>Join logic<textarea data-schema-detail="join_logic" class="textarea-small" name="${escapeHtml(table.name)}__join_logic">${escapeHtml(settings.join_logic || "")}</textarea></label>`;
}

function renderSchemaCache(): string {
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

function renderStub(title: string, text: string): string {
  return `<section class="panel"><div class="panel-header"><h2>${escapeHtml(title)}</h2><span class="badge planned">planned</span></div><div class="panel-body"><p class="muted">${escapeHtml(text)}</p></div></section>`;
}

function renderLicense(): string {
  return `<section class="panel"><div class="panel-header"><h2>License</h2></div><div class="panel-body mono">${escapeHtml(JSON.stringify(state.license || {}, null, 2))}</div></section>`;
}

function renderAdminAudit(): string {
  return `
    <section class="panel"><div class="panel-header"><h2>Admin audit</h2></div>
      <div class="table-wrap"><table>
        <thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Resource</th><th>Details</th></tr></thead>
        <tbody>${state.adminAudit.map(item => `<tr><td>${escapeHtml(item.occurred_at)}</td><td>${escapeHtml(item.actor)}</td><td>${escapeHtml(item.action)}</td><td>${escapeHtml(item.resource_type)}:${escapeHtml(item.resource_id)}</td><td><code>${escapeHtml(JSON.stringify(item.details))}</code></td></tr>`).join("")}</tbody>
      </table></div>
    </section>`;
}

function attachSectionHandlers(): void {
  document.querySelector<HTMLButtonElement>("#overview-refresh")?.addEventListener("click", refreshOverview);
  document.querySelectorAll<HTMLButtonElement>("[data-overview-empty-slot]").forEach(button => {
    button.addEventListener("click", async () => {
      const slot = Number(button.dataset.overviewEmptySlot || 0);

      state.overviewPlacementSlot = state.overviewPlacementSlot === slot ? null : slot;

      if (!state.overviewWidgetConfigs.length) {
        await loadOverviewWidgetConfigs(false);
      }

      render();
    });
  });
  document.querySelectorAll<HTMLButtonElement>("[data-overview-place-widget]").forEach(button => {
    button.addEventListener("click", placeOverviewWidget);
  });
  document.querySelectorAll<HTMLButtonElement>("[data-overview-new-widget]").forEach(button => {
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
  document.querySelectorAll<HTMLButtonElement>("[data-overview-widget-select]").forEach(button => {
    button.addEventListener("click", () => {
      state.selectedOverviewWidgetKey = button.dataset.overviewWidgetSelect || "";
      state.overviewPlacementSlot = null;
      render();
    });
  });
  document.querySelectorAll<HTMLInputElement>("[data-overview-widget-active]").forEach(input => {
    input.addEventListener("change", updateOverviewWidgetActive);
  });
  document.querySelectorAll<HTMLButtonElement>("[data-overview-widget-delete]").forEach(button => {
    button.addEventListener("click", deleteOverviewWidget);
  });
  document.querySelector<HTMLFormElement>("#overview-widget-settings-form")?.addEventListener("submit", saveOverviewWidgetSettings);
  document.querySelectorAll<HTMLButtonElement>("[data-edit-overview-widget]").forEach(button => {
    button.addEventListener("click", () => {
      state.overviewEditorWidgetKey = button.dataset.editOverviewWidget || null;
      render();
    });
  });
  document.querySelectorAll<HTMLButtonElement>("[data-close-overview-widget]").forEach(button => {
    button.addEventListener("click", () => {
      state.overviewEditorWidgetKey = null;
      render();
    });
  });
  document.querySelector<HTMLElement>("[data-overview-widget-backdrop]")?.addEventListener("click", event => {
    if (event.target === event.currentTarget) {
      state.overviewEditorWidgetKey = null;
      render();
    }
  });
  document.querySelectorAll<HTMLFormElement>("[data-overview-widget-form]").forEach(form => {
    form.addEventListener("submit", saveOverviewWidget);
  });
  document.querySelectorAll<HTMLButtonElement>("[data-overview-table-page]").forEach(button => {
    button.addEventListener("click", () => {
      const widgetKey = button.dataset.overviewTablePage;
      const page = Number(button.dataset.page || 0);

      if (!widgetKey || !Number.isFinite(page)) return;

      state.overviewTablePages[widgetKey] = page;
      render();
    });
  });
  document.querySelector<HTMLFormElement>("#data-audit-filter-form")?.addEventListener("submit", loadDataAuditForFilters);
  document.querySelector<HTMLFormElement>("#retention-form")?.addEventListener("submit", saveRetention);
  document.querySelector<HTMLSelectElement>("#data-audit-type")?.addEventListener("change", loadDataAuditForFilters);
  document.querySelector<HTMLSelectElement>("#data-audit-output-classification")?.addEventListener("change", loadDataAuditForFilters);
  document.querySelector<HTMLFormElement>("#prompt-form")?.addEventListener("submit", savePrompt);
  document.querySelector<HTMLFormElement>("#schema-cache-form")?.addEventListener("submit", saveSchemaCacheTtl);
  document.querySelector<HTMLFormElement>("#llm-config-form")?.addEventListener("submit", saveLlmConfig);
  document.querySelector<HTMLFormElement>("#governance-policy-form")?.addEventListener("submit", saveGovernancePolicy);
  document.querySelector<HTMLFormElement>("#datasource-form")?.addEventListener("submit", saveDatasource);
  document.querySelector<HTMLSelectElement>("#datasource-type")?.addEventListener("change", syncDatasourceTypeFields);
  document.querySelector<HTMLFormElement>("#datasource-schema-form")?.addEventListener("submit", saveDatasourceSchema);
  document.querySelector<HTMLInputElement>("#schema-show-enabled-only")?.addEventListener("change", event => {
    syncDatasourceSchemaDraftFromForm();
    state.datasourceSchemaShowEnabledOnly = (event.currentTarget as HTMLInputElement).checked;
    render();
  });
  document.querySelectorAll<HTMLInputElement>("[data-schema-object-enabled]").forEach(input => {
    input.addEventListener("change", () => {
      if (!state.datasourceSchemaShowEnabledOnly) return;

      syncDatasourceSchemaDraftFromForm();
      render();
    });
  });
  document.querySelectorAll<HTMLButtonElement>("[data-schema-object]").forEach(button => {
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
  document.querySelectorAll<HTMLButtonElement>("[data-open-business-logic]").forEach(button => {
    button.addEventListener("click", async () => {
      state.section = "business-logic";
      render();
      await loadBusinessLogicSuggestions();
    });
  });
  document.querySelectorAll<HTMLInputElement>("[data-business-logic-toggle]").forEach(input => {
    input.addEventListener("change", updateBusinessLogicSuggestion);
  });
  document.querySelectorAll<HTMLButtonElement>("[data-business-logic-edit]").forEach(button => {
    button.addEventListener("click", () => {
      state.businessLogicEditorId = Number(button.dataset.businessLogicEdit);
      render();
    });
  });
  document.querySelectorAll<HTMLButtonElement>("[data-close-business-logic-editor]").forEach(button => {
    button.addEventListener("click", () => {
      state.businessLogicEditorId = null;
      render();
    });
  });
  document.querySelector<HTMLElement>("[data-business-logic-backdrop]")?.addEventListener("click", event => {
    if (event.target === event.currentTarget) {
      state.businessLogicEditorId = null;
      render();
    }
  });
  document.querySelectorAll<HTMLFormElement>("[data-business-logic-form]").forEach(form => {
    form.addEventListener("submit", saveBusinessLogicSuggestion);
  });
  document.querySelectorAll<HTMLButtonElement>("[data-business-logic-delete]").forEach(button => {
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
  document.querySelectorAll<HTMLButtonElement>("[data-prompt]").forEach(button => {
    button.addEventListener("click", () => {
      state.selectedPromptKey = button.dataset.prompt || "";
      render();
    });
  });
  document.querySelectorAll<HTMLButtonElement>("[data-datasource]").forEach(button => {
    button.addEventListener("click", async () => {
      state.selectedDatasourceId = Number(button.dataset.datasource);
      await loadDatasourceSchema();
    });
  });
}

async function loadDataAuditForFilters(event: Event): Promise<void> {
  event.preventDefault();
  state.dataAuditType = document.querySelector<HTMLSelectElement>("#data-audit-type")?.value || "";
  state.dataAuditOutputClassification = document.querySelector<HTMLSelectElement>("#data-audit-output-classification")?.value || "";
  state.dataAuditSqlContains = document.querySelector<HTMLInputElement>("#data-audit-sql-contains")?.value || "";
  await loadDataAudit();
}

async function saveOverviewWidget(event: Event): Promise<void> {
  event.preventDefault();
  const formElement = event.currentTarget as HTMLFormElement;
  const widgetKey = formElement.dataset.overviewWidgetForm;

  if (!widgetKey) return;

  const form = new FormData(formElement);

  try {
    await api(`/api/v1/admin/overview/widgets/${encodeURIComponent(widgetKey)}`, {
      method: "PUT",
      body: JSON.stringify({
        label: form.get("label"),
        widget_type: form.get("widget_type"),
        datasource_key: form.get("datasource_key"),
        question: form.get("question"),
        result_mode: form.get("result_mode") || "data",
        position: Number(form.get("position") || 100),
        grid_width: Number(form.get("grid_width") || 1),
        active: form.get("active") !== "false",
      }),
    });
    setMessage("success", "Overview widget saved.");
    state.overviewEditorWidgetKey = null;
    await loadOverview();
  } catch (error) {
    setMessage("error", (error as Error).message);
  }
}

async function placeOverviewWidget(event: Event): Promise<void> {
  const button = event.currentTarget as HTMLButtonElement;
  const slot = Number(button.dataset.overviewPlaceWidget || 0);
  const select = document.querySelector<HTMLSelectElement>(`[data-overview-placement-select="${slot}"]`);
  const widgetKey = select?.value || "";
  const widget = state.overviewWidgetConfigs.find(item => item.widget_key === widgetKey);

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
    setMessage("error", (error as Error).message);
    render();
  }
}

async function updateOverviewWidgetActive(event: Event): Promise<void> {
  const input = event.currentTarget as HTMLInputElement;
  const widgetKey = input.dataset.overviewWidgetActive || "";
  const widget = state.overviewWidgetConfigs.find(item => item.widget_key === widgetKey);

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
    setMessage("error", (error as Error).message);
    await loadOverviewWidgetConfigs();
  }
}

async function deleteOverviewWidget(event: Event): Promise<void> {
  const button = event.currentTarget as HTMLButtonElement;
  const widgetKey = button.dataset.overviewWidgetDelete || "";
  const widget = state.overviewWidgetConfigs.find(item => item.widget_key === widgetKey);

  if (!widgetKey || !widget) return;

  if (!window.confirm(`Delete widget "${widget.label}"?`)) {
    return;
  }

  try {
    await api(`/api/v1/admin/overview/widgets/${encodeURIComponent(widgetKey)}`, {
      method: "DELETE",
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
    setMessage("error", (error as Error).message);
    render();
  }
}

async function updateOverviewWidgetState(
  widgetKey: string,
  active: boolean,
  position: number,
  gridWidth?: number,
): Promise<void> {
  await api(`/api/v1/admin/overview/widgets/${encodeURIComponent(widgetKey)}/state`, {
    method: "PATCH",
    body: JSON.stringify({
      active,
      position,
      grid_width: gridWidth,
    }),
  });
}

async function saveOverviewWidgetSettings(event: Event): Promise<void> {
  event.preventDefault();
  const formElement = event.currentTarget as HTMLFormElement;
  const mode = formElement.dataset.widgetMode || "update";
  const form = new FormData(formElement);
  const widgetKey = String(form.get("widget_key") || "").trim();

  if (!widgetKey) return;

  const payload = {
    widget_key: widgetKey,
    label: form.get("label"),
    widget_type: form.get("widget_type"),
    datasource_key: form.get("datasource_key"),
    question: form.get("question"),
    result_mode: form.get("result_mode") || "data",
    position: Number(form.get("position") || 100),
    grid_width: Number(form.get("grid_width") || 1),
    active: form.get("active") === "on",
  };

  try {
    if (mode === "create") {
      await api("/api/v1/admin/overview/widgets", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      state.selectedOverviewWidgetKey = widgetKey;
      state.overviewPlacementSlot = null;
      setMessage("success", "Widget created.");
    } else {
      await api(`/api/v1/admin/overview/widgets/${encodeURIComponent(widgetKey)}`, {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      setMessage("success", "Widget saved.");
    }

    await loadOverviewWidgetConfigs(false);
    await loadOverview();

    if (state.section !== "overview") {
      render();
    }
  } catch (error) {
    setMessage("error", (error as Error).message);
  }
}

async function updateBusinessLogicSuggestion(event: Event): Promise<void> {
  const input = event.currentTarget as HTMLInputElement;
  const id = input.dataset.businessLogicToggle;

  if (!id) return;

  try {
    await api(`/api/v1/admin/business-logic-suggestions/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify({ enabled: input.checked }),
    });
    setMessage("success", input.checked ? "Business logic enabled." : "Business logic disabled.");
    await loadBusinessLogicSuggestions();
  } catch (error) {
    setMessage("error", (error as Error).message);
    await loadBusinessLogicSuggestions();
  }
}

async function saveBusinessLogicSuggestion(event: Event): Promise<void> {
  event.preventDefault();
  const formElement = event.currentTarget as HTMLFormElement;
  const id = formElement.dataset.businessLogicForm;

  if (!id) return;

  const form = new FormData(formElement);

  try {
    await api(`/api/v1/admin/business-logic-suggestions/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify({
        title: form.get("title"),
        rule_text: form.get("rule_text"),
      }),
    });
    setMessage("success", "Business logic suggestion updated.");
    state.businessLogicEditorId = null;
    await loadBusinessLogicSuggestions();
  } catch (error) {
    setMessage("error", (error as Error).message);
    render();
  }
}

async function deleteBusinessLogicSuggestion(event: Event): Promise<void> {
  const button = event.currentTarget as HTMLButtonElement;
  const id = button.dataset.businessLogicDelete;

  if (!id) return;

  try {
    await api(`/api/v1/admin/business-logic-suggestions/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
    setMessage("success", "Business logic suggestion deleted.");
    await loadBusinessLogicSuggestions();
  } catch (error) {
    setMessage("error", (error as Error).message);
    render();
  }
}

async function saveLlmConfig(event: Event): Promise<void> {
  event.preventDefault();
  const form = new FormData(event.currentTarget as HTMLFormElement);

  try {
    const extraBody = JSON.parse(String(form.get("extra_body") || "{}"));
    if (extraBody === null || Array.isArray(extraBody) || typeof extraBody !== "object") {
      throw new Error("Extra body JSON must be an object.");
    }

    const result = await api<any>("/api/v1/admin/llm-config", {
      method: "PUT",
      body: JSON.stringify({
        provider: form.get("provider"),
        base_url: form.get("base_url"),
        api_key: form.get("api_key"),
        clear_api_key: form.get("clear_api_key") === "on",
        model: form.get("model"),
        timeout_seconds: Number(form.get("timeout_seconds") || 60),
        intent_classification_mode: form.get("intent_classification_mode"),
        sql_generation_mode: form.get("sql_generation_mode"),
        result_interpretation_mode: form.get("result_interpretation_mode"),
        output_classification_mode: form.get("output_classification_mode"),
        investigation_mode: form.get("investigation_mode"),
        investigation_ambiguity_mode: form.get("investigation_ambiguity_mode"),
        query_max_rows: Number(form.get("query_max_rows") || 100),
        query_timeout_seconds: Number(form.get("query_timeout_seconds") || 30),
        extra_body: extraBody,
      }),
    });
    state.llmConfig = result.item;
    setMessage("success", "LLM configuration saved.");
    render();
  } catch (error) {
    setMessage("error", (error as Error).message);
    render();
  }
}

async function saveGovernancePolicy(event: Event): Promise<void> {
  event.preventDefault();
  const form = new FormData(event.currentTarget as HTMLFormElement);

  try {
    const forbiddenColumns = JSON.parse(String(form.get("forbidden_columns") || "{}"));
    const piiColumnNames = JSON.parse(String(form.get("pii_column_names") || "{}"));
    if (forbiddenColumns === null || Array.isArray(forbiddenColumns) || typeof forbiddenColumns !== "object") {
      throw new Error("Forbidden columns JSON must be an object.");
    }
    if (piiColumnNames === null || typeof piiColumnNames !== "object") {
      throw new Error("PII column names JSON must be an object or list.");
    }

    const result = await api<any>("/api/v1/admin/governance-policy", {
      method: "PUT",
      body: JSON.stringify({
        final_answer: {
          record_level_pii_allowed: form.get("record_level_pii_allowed") === "on",
          prefer_aggregates_for_sensitive_domains: form.get("prefer_aggregates_for_sensitive_domains") === "on",
        },
        sql: {
          read_only: form.get("sql_read_only") === "on",
          select_star_allowed: form.get("select_star_allowed") === "on",
          tenant_filter_required: form.get("tenant_filter_required") === "on",
          tenant_column: String(form.get("tenant_column") || "").trim() || null,
        },
        privacy: {
          record_level_forbidden: form.get("record_level_forbidden") === "on",
          forbidden_columns: forbiddenColumns,
        },
        pii_column_names: piiColumnNames,
      }),
    });
    state.governancePolicy = result.item;
    setMessage("success", "Governance policy saved.");
    render();
  } catch (error) {
    setMessage("error", (error as Error).message);
    render();
  }
}

async function saveRetention(event: Event): Promise<void> {
  event.preventDefault();
  const form = new FormData(event.currentTarget as HTMLFormElement);
  try {
    await api("/api/v1/admin/audit/settings", { method: "PUT", body: JSON.stringify({ data_query_retention_days: Number(form.get("retention")) }) });
    setMessage("success", "Audit retention saved.");
    await loadDataAudit();
  } catch (error) {
    setMessage("error", (error as Error).message);
    render();
  }
}

async function savePrompt(event: Event): Promise<void> {
  event.preventDefault();
  const form = new FormData(event.currentTarget as HTMLFormElement);
  const promptKey = String(form.get("prompt_key"));
  try {
    await api(`/api/v1/admin/prompts/${encodeURIComponent(promptKey)}`, {
      method: "PUT",
      body: JSON.stringify({
        name: form.get("name"),
        description: form.get("description"),
        system_prompt: form.get("system_prompt"),
        user_prompt_template: form.get("user_prompt_template"),
        active: form.get("active") === "on",
      }),
    });
    setMessage("success", "Prompt saved.");
    await loadPrompts();
  } catch (error) {
    setMessage("error", (error as Error).message);
    render();
  }
}

async function saveDatasource(event: Event): Promise<void> {
  event.preventDefault();
  const selected = getSelectedDatasource();
  if (selected?.system_managed) return;

  const form = new FormData(event.currentTarget as HTMLFormElement);
  const id = String(form.get("id") || "");
  const payload = {
    connector_key: form.get("connector_key"),
    name: form.get("name"),
    database_type: form.get("database_type"),
    database_url: form.get("database_url"),
    sql_dialect: form.get("sql_dialect"),
    active: form.get("active") === "on",
  };
  try {
    const result = await api<any>(id ? `/api/v1/admin/datasources/${id}` : "/api/v1/admin/datasources", {
      method: id ? "PUT" : "POST",
      body: JSON.stringify(id ? { ...payload, connector_key: undefined } : payload),
    });
    state.selectedDatasourceId = result.item.id;
    setMessage("success", "Datasource saved.");
    await loadDatasources();
  } catch (error) {
    setMessage("error", (error as Error).message);
  }
}

async function testDatasource(): Promise<void> {
  const selected = getSelectedDatasource();
  const form = document.querySelector<HTMLFormElement>("#datasource-form");
  const formData = form ? new FormData(form) : null;

  try {
    if (selected) {
      await api(`/api/v1/admin/datasources/${selected.id}/test`, { method: "POST" });
    } else if (formData) {
      await api("/api/v1/admin/datasources/test", {
        method: "POST",
        body: JSON.stringify({
          database_type: formData.get("database_type"),
          database_url: formData.get("database_url"),
        }),
      });
    } else {
      return;
    }
    setMessage("success", "Connection test succeeded.");
  } catch (error) {
    setMessage("error", extractErrorMessage(error));
  }
}

async function introspectDatasource(): Promise<void> {
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
    state.datasourceSchemaError = (error as Error).message;
    setMessage("error", (error as Error).message);
    render();
  }
}

async function activateDatasource(): Promise<void> {
  const selected = getSelectedDatasource();
  if (!selected) return;
  try {
    await api(`/api/v1/admin/datasources/${selected.id}/activate`, { method: "POST" });
    setMessage("success", "Datasource activated.");
    await loadDatasources();
  } catch (error) {
    setMessage("error", (error as Error).message);
    render();
  }
}

function syncDatasourceSchemaDraftFromForm(): void {
  const form = document.querySelector<HTMLFormElement>("#datasource-schema-form");
  const rawTables = state.datasourceSchema?.item?.raw_schema?.tables || [];
  const tableSettings = state.datasourceSchema?.item?.table_settings?.tables || {};

  if (!form || !rawTables.length) return;

  const draftTables = getDatasourceSchemaDraftTables(rawTables, tableSettings);

  form.querySelectorAll<HTMLInputElement>("[data-schema-object-enabled]").forEach(input => {
    const name = input.dataset.schemaObjectEnabled;
    if (!name || !draftTables[name]) return;

    draftTables[name].selected = input.checked;
  });

  const selectedName = state.datasourceSchemaSelectedObjectName;
  const selectedSettings = selectedName ? draftTables[selectedName] : null;

  if (!selectedSettings) return;

  selectedSettings.description = form.querySelector<HTMLInputElement>("[data-schema-detail='description']")?.value || "";
  selectedSettings.primary_key_prompt = form.querySelector<HTMLInputElement>("[data-schema-detail='primary_key_prompt']")?.value || "";
  selectedSettings.foreign_key_prompt = form.querySelector<HTMLInputElement>("[data-schema-detail='foreign_key_prompt']")?.value || "";
  selectedSettings.join_logic = form.querySelector<HTMLTextAreaElement>("[data-schema-detail='join_logic']")?.value || "";
}

async function saveDatasourceSchema(event: Event): Promise<void> {
  event.preventDefault();
  const selected = getSelectedDatasource();
  if (!selected || !state.datasourceSchema?.item) return;
  const rawTables = state.datasourceSchema.item.raw_schema.tables || [];
  const tableSettings = state.datasourceSchema.item.table_settings?.tables || {};
  syncDatasourceSchemaDraftFromForm();
  const draftTables = getDatasourceSchemaDraftTables(rawTables, tableSettings);
  const tables: Record<string, any> = {};
  for (const table of rawTables) {
    const draft = draftTables[table.name] || {};
    tables[table.name] = {
      selected: draft.selected !== false,
      description: draft.description || "",
      primary_key_prompt: draft.primary_key_prompt || "",
      foreign_key_prompt: draft.foreign_key_prompt || "",
      join_logic: draft.join_logic || "",
    };
  }
  try {
    state.datasourceSchema = await api(`/api/v1/admin/datasources/${selected.id}/schema/tables`, {
      method: "PUT",
      body: JSON.stringify({ tables }),
    });
    setMessage("success", "Schema settings saved.");
    state.datasourceSchemaSelectedObjectName = "";
    state.datasourceSchemaDraftTables = null;
    render();
  } catch (error) {
    setMessage("error", (error as Error).message);
    render();
  }
}

async function saveSchemaCacheTtl(event: Event): Promise<void> {
  event.preventDefault();
  const form = new FormData(event.currentTarget as HTMLFormElement);
  try {
    await api("/api/v1/admin/schema-cache", { method: "PUT", body: JSON.stringify({ ttl_seconds: Number(form.get("ttl_seconds")) }) });
    setMessage("success", "Schema cache TTL saved.");
    await loadSchemaCache();
  } catch (error) {
    setMessage("error", (error as Error).message);
    render();
  }
}

async function invalidateSchemaCache(): Promise<void> {
  try {
    await api("/api/v1/admin/schema-cache/invalidate", { method: "POST" });
    setMessage("success", "Schema cache invalidated.");
    await loadSchemaCache();
  } catch (error) {
    setMessage("error", (error as Error).message);
    render();
  }
}

async function loadCurrentSection(): Promise<void> {
  if (!state.token || state.mustChangePassword) return;
  if (state.section === "overview") await loadOverview();
  if (state.section === "widgets") await loadOverviewWidgetConfigs();
  if (state.section === "data-audit") await loadDataAudit();
  if (state.section === "prompts") await loadPrompts();
  if (state.section === "schema-cache") await loadSchemaCache();
  if (state.section === "business-logic") await loadBusinessLogicSuggestions();
  if (state.section === "llm-config") await loadLlmConfig();
  if (state.section === "governance-policy") await loadGovernancePolicy();
  if (state.section === "datasources") await loadDatasources();
  if (state.section === "license") await loadLicense();
  if (state.section === "admin-audit") await loadAdminAudit();
}

async function loadOverview(): Promise<void> {
  state.overviewLoading = true;
  if (state.section === "overview") {
    render();
  }

  try {
    const [overview, widgetConfig] = await Promise.all([
      api<OverviewState>("/api/v1/admin/overview"),
      api<{ items: OverviewWidget[]; datasources: OverviewDatasource[] }>("/api/v1/admin/overview/widgets"),
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

async function loadOverviewWidgetConfigs(shouldRender = true): Promise<void> {
  const payload = await api<{ items: OverviewWidget[]; datasources: OverviewDatasource[] }>("/api/v1/admin/overview/widgets");
  state.overviewWidgetConfigs = payload.items || [];
  state.overviewWidgetDatasources = payload.datasources || [];

  if (
    state.selectedOverviewWidgetKey !== "__new__" &&
    !state.overviewWidgetConfigs.some(widget => widget.widget_key === state.selectedOverviewWidgetKey)
  ) {
    state.selectedOverviewWidgetKey = state.overviewWidgetConfigs[0]?.widget_key || "";
  }

  if (shouldRender) {
    render();
  }
}

async function refreshOverview(): Promise<void> {
  if (state.overviewRefreshing || state.overviewLoading) return;

  state.overviewRefreshing = true;
  setMessage("success", "");
  render();

  try {
    state.overview = await api("/api/v1/admin/overview");
  } catch (error) {
    setMessage("error", (error as Error).message);
  } finally {
    state.overviewRefreshing = false;
    render();
  }
}

async function loadDataAudit(): Promise<void> {
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
  const logs = await api<any>(`/api/v1/admin/audit/data-queries${query}`);
  state.dataAudit = logs.items || [];
  render();
}

async function loadPrompts(): Promise<void> {
  const result = await api<any>("/api/v1/admin/prompts");
  state.prompts = result.items || [];
  state.selectedPromptKey = state.selectedPromptKey || state.prompts[0]?.prompt_key || "";
  render();
}

async function loadDatasources(): Promise<void> {
  const [datasources, datasourceTypes] = await Promise.all([
    api<{ items: DatasourceConnector[] }>("/api/v1/admin/datasources"),
    api<{ items: DatasourceType[] }>("/api/v1/admin/datasource-types"),
  ]);
  state.datasources = datasources.items || [];
  state.datasourceTypes = datasourceTypes.items || [];
  if (!state.selectedDatasourceId || state.selectedDatasourceId === "new") {
    state.selectedDatasourceId = state.datasources[0]?.id || null;
  }
  render();
  void loadDatasourceSchema();
}

async function loadDatasourceSchema(): Promise<void> {
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
    state.datasourceSchemaError = (error as Error).message;
  } finally {
    state.datasourceSchemaLoading = false;
    render();
  }
}

async function loadSchemaCache(): Promise<void> {
  state.schemaCache = await api("/api/v1/admin/schema-cache");
  render();
}

async function loadBusinessLogicSuggestions(): Promise<void> {
  const result = await api<any>("/api/v1/admin/business-logic-suggestions");
  state.businessLogic = result.items || [];
  state.businessLogicDatasource = result.datasource || null;
  render();
}

async function loadLlmConfig(): Promise<void> {
  const result = await api<any>("/api/v1/admin/llm-config");
  state.llmConfig = result.item || null;
  render();
}

async function loadGovernancePolicy(): Promise<void> {
  const result = await api<any>("/api/v1/admin/governance-policy");
  state.governancePolicy = result.item || null;
  render();
}

async function loadLicense(): Promise<void> {
  state.license = await api("/api/v1/admin/license");
  render();
}

async function loadAdminAudit(): Promise<void> {
  const result = await api<any>("/api/v1/admin/audit/admin-events");
  state.adminAudit = result.items || [];
  render();
}

async function bootstrap(): Promise<void> {
  render();
  if (!state.token) return;
  try {
    const me = await api<any>("/api/v1/admin/me");
    state.username = me.username;
    state.mustChangePassword = me.must_change_password;
    localStorage.setItem("gaard_admin_must_change", String(state.mustChangePassword));
    render();
    await loadCurrentSection();
  } catch (error) {
    state.overviewLoading = false;
    setMessage("error", (error as Error).message);
    render();
  }
}

void bootstrap();
