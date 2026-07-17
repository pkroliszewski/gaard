// src/main.ts
var app = document.querySelector("#app");
var params = new URLSearchParams(window.location.search);
var configuredBackendUrl = (params.get("backendUrl") || params.get("apiUrl") || window.GAARD_CLIENT_CONFIG?.backendUrl || "http://localhost:8000").replace(/\/+$/, "");
var storedToken = localStorage.getItem("gaard_client_token") || "";
var storedUsername = localStorage.getItem("gaard_client_username") || "";
var storedRole = localStorage.getItem("gaard_client_role") || "";
var storedMustChangePassword = localStorage.getItem("gaard_client_must_change_password") === "true";
var storedActiveView = localStorage.getItem("gaard_client_active_view") || "";
var state = {
  backendUrl: configuredBackendUrl,
  token: storedToken,
  username: storedUsername,
  role: storedRole,
  mustChangePassword: storedMustChangePassword,
  passwordChangeError: "",
  activeView: normalizeView(params.get("view") || storedActiveView),
  queryMode: normalizeQueryMode(params.get("mode")),
  messages: [],
  nextMessageId: 1,
  conversationId: "",
  pending: false,
  error: "",
  apiError: null,
  nextApiErrorId: 1,
  loginOpen: false,
  sourcesOpen: false,
  datasources: [],
  datasourcesLoaded: false,
  datasourcesLoading: false,
  datasourceError: "",
  datasourceUploadPending: false,
  datasourceSelectionPending: false,
  selectedDatasourceIds: [],
  multipleDatasourceSelectionAllowed: false,
  dashboards: [],
  dashboardsLoaded: false,
  dashboardsLoading: false,
  dashboardsError: "",
  activeDashboardId: "",
  dashboardMenuOpen: false,
  dashboardCreateOpen: false,
  dashboardEditId: "",
  dashboardCreatePending: false,
  dashboardDeletePendingId: "",
  savedMetrics: [],
  savedMetricsLoaded: false,
  savedMetricsResultsLoaded: false,
  savedMetricsLoading: false,
  savedMetricsError: "",
  dashboardWidgets: [],
  dashboardWidgetsDashboardId: "",
  dashboardWidgetsLoading: false,
  dashboardWidgetsError: "",
  dashboardWidgetDialogOpen: false,
  dashboardWidgetPending: false,
  dashboardWidgetMetricKey: "",
  dashboardWidgetType: "",
  saveWidgetDialogOpen: false,
  saveWidgetMessageId: null,
  saveWidgetDraftLabel: "",
  saveWidgetTitleEdited: false,
  saveWidgetSuggestionLoading: false,
  saveWidgetSuggestionError: "",
  saveWidgetPending: false,
  saveWidgetError: "",
  metricEditDialogOpen: false,
  metricEditWidgetKey: "",
  metricEditDraftLabel: "",
  metricEditPending: false,
  metricEditError: "",
  metricDeletePendingKey: "",
  dashboardEditMode: false,
  dashboardLayoutSaving: false,
  dashboardLayoutSaveTimer: null,
  dashboardLayoutSavePromise: null,
  dashboardLayoutSaveSequence: 0,
  dashboardGrid: null
};
rememberActiveView(state.activeView);
const WIDGET_TYPES = [
  { key: "number", label: "Number", icon: "metrics" },
  { key: "bar", label: "Bar chart", icon: "analysis" },
  { key: "stacked_bar", label: "Stacked bar", icon: "analysis" },
  { key: "line", label: "Line chart", icon: "metrics" },
  { key: "multi_line", label: "Multi-line", icon: "metrics" },
  { key: "pie", label: "Pie chart", icon: "dashboards" },
  { key: "area", label: "Area chart", icon: "metrics" },
  { key: "table", label: "Table", icon: "queries" }
];
function escapeHtml(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}
function renderMarkdown(value) {
  const text = String(value ?? "").replace(/\r\n?/g, "\n").trim();
  if (!text) return "";
  const lines = text.split("\n");
  const blocks = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }
    const fenceMatch = line.match(/^\s*```([A-Za-z0-9_-]*)\s*$/);
    if (fenceMatch) {
      index += 1;
      const codeLines = [];
      while (index < lines.length && !lines[index].match(/^\s*```\s*$/)) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      blocks.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
      continue;
    }
    const headingMatch = line.match(/^\s{0,3}(#{1,4})\s+(.+?)\s*#*\s*$/);
    if (headingMatch) {
      const level = Math.min(headingMatch[1].length + 2, 5);
      blocks.push(`<h${level}>${renderInlineMarkdown(headingMatch[2])}</h${level}>`);
      index += 1;
      continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      const items = [];
      while (index < lines.length && /^\s*[-*]\s+/.test(lines[index])) {
        items.push(lines[index].replace(/^\s*[-*]\s+/, ""));
        index += 1;
      }
      blocks.push(`<ul>${items.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</ul>`);
      continue;
    }
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items = [];
      while (index < lines.length && /^\s*\d+[.)]\s+/.test(lines[index])) {
        items.push(lines[index].replace(/^\s*\d+[.)]\s+/, ""));
        index += 1;
      }
      blocks.push(`<ol>${items.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</ol>`);
      continue;
    }
    if (/^\s*>\s?/.test(line)) {
      const quoteLines = [];
      while (index < lines.length && /^\s*>\s?/.test(lines[index])) {
        quoteLines.push(lines[index].replace(/^\s*>\s?/, ""));
        index += 1;
      }
      blocks.push(`<blockquote>${renderMarkdown(quoteLines.join("\n"))}</blockquote>`);
      continue;
    }
    const paragraph = [];
    while (
      index < lines.length &&
      lines[index].trim() &&
      !/^\s*```/.test(lines[index]) &&
      !/^\s{0,3}#{1,4}\s+/.test(lines[index]) &&
      !/^\s*[-*]\s+/.test(lines[index]) &&
      !/^\s*\d+[.)]\s+/.test(lines[index]) &&
      !/^\s*>\s?/.test(lines[index])
    ) {
      paragraph.push(lines[index]);
      index += 1;
    }
    blocks.push(`<p>${renderInlineMarkdown(paragraph.join("\n"))}</p>`);
  }
  return `<div class="markdown-content">${blocks.join("")}</div>`;
}
function renderInlineMarkdown(value) {
  const placeholders = [];
  const reserve = (html) => {
    const token = `@@GAARD_MD_${placeholders.length}@@`;
    placeholders.push([token, html]);
    return token;
  };
  let text = String(value ?? "");
  text = text.replace(/`([^`\n]+)`/g, (_match, code) => reserve(`<code>${escapeHtml(code)}</code>`));
  text = text.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, (_match, label, url) => {
    const safeUrl = escapeHtml(url);
    return reserve(`<a href="${safeUrl}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>`);
  });
  let html = escapeHtml(text);
  html = html.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/__([^_\n]+)__/g, "<strong>$1</strong>");
  html = html.replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  html = html.replace(/(^|[\s(])_([^_\n]+)_/g, "$1<em>$2</em>");
  html = html.replace(/\n/g, "<br />");
  placeholders.forEach(([token, replacement]) => {
    html = html.replaceAll(token, replacement);
  });
  return html;
}
function renderIcon(name) {
  const icons = {
    home: `
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d="m3 10 9-7 9 7" />
        <path d="M5 9v11h14V9" />
        <path d="M9 20v-6h6v6" />
      </svg>`,
    analysis: `
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d="M4 19V5" />
        <path d="M9 19V9" />
        <path d="M14 19V3" />
        <path d="M19 19v-7" />
      </svg>`,
    metrics: `
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d="M4 19 10 8l4 7 6-11" />
        <path d="M4 5v14h16" />
      </svg>`,
    datasources: `
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <ellipse cx="12" cy="5" rx="7" ry="3" />
        <path d="M5 5v6c0 1.66 3.13 3 7 3s7-1.34 7-3V5" />
        <path d="M5 11v6c0 1.66 3.13 3 7 3s7-1.34 7-3v-6" />
      </svg>`,
    queries: `
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <circle cx="11" cy="11" r="7" />
        <path d="m20 20-3.5-3.5" />
      </svg>`,
    alerts: `
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9" />
        <path d="M10 21h4" />
      </svg>`,
    calendar: `
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <rect x="3" y="4" width="18" height="18" rx="2" />
        <path d="M16 2v4" />
        <path d="M8 2v4" />
        <path d="M3 10h18" />
      </svg>`,
    plus: `
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d="M12 5v14" />
        <path d="M5 12h14" />
      </svg>`,
    dashboards: `
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <rect x="3" y="3" width="7" height="8" rx="1.5" />
        <rect x="14" y="3" width="7" height="5" rx="1.5" />
        <rect x="14" y="12" width="7" height="9" rx="1.5" />
        <rect x="3" y="15" width="7" height="6" rx="1.5" />
      </svg>`,
    history: `
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d="M3 12a9 9 0 1 0 2.64-6.36L3 8" />
        <path d="M3 3v5h5" />
        <path d="M12 7v5l3 2" />
      </svg>`,
    sources: `
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <ellipse cx="12" cy="5" rx="7" ry="3" />
        <path d="M5 5v6c0 1.66 3.13 3 7 3s7-1.34 7-3V5" />
        <path d="M5 11v6c0 1.66 3.13 3 7 3s7-1.34 7-3v-6" />
      </svg>`,
    user: `
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d="M20 21a8 8 0 0 0-16 0" />
        <circle cx="12" cy="7" r="4" />
      </svg>`,
    lock: `
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <rect x="4" y="10" width="16" height="10" rx="2" />
        <path d="M8 10V7a4 4 0 0 1 8 0v3" />
      </svg>`,
    arrowUp: `
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d="m12 19 0-14" />
        <path d="m5 12 7-7 7 7" />
      </svg>`,
    trash: `
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d="M3 6h18" />
        <path d="M8 6V4h8v2" />
        <path d="M19 6l-1 15H6L5 6" />
        <path d="M10 11v6" />
        <path d="M14 11v6" />
      </svg>`,
    edit: `
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d="M12 20h9" />
        <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
      </svg>`,
    chevronDown: `
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d="m6 9 6 6 6-6" />
      </svg>`
  };
  return icons[name] || "";
}
function renderErrorIcon() {
  return `
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v6" />
      <path d="M12 17h.01" />
    </svg>`;
}
function renderSidebar() {
  const items = [
    ["home", "Home", "Ask your data"],
    ["analysis", "Analysis", "Dashboards"],
    ["metrics", "Metrics", "Dashboard widgets"],
    ["datasources", "Datasources", "Files and sources"],
    ["queries", "My Queries", "Chat history"],
    ["alerts", "Alerts", "Alert definitions"]
  ];
  return `
    <aside class="sidebar" aria-label="Navigation">
      <div class="brand">
        <img class="brand-logo" src="/assets/getgaard.svg" alt="" />
        <div class="brand-copy">
          <strong>GAARD</strong>
          <span>Data workspace</span>
        </div>
      </div>
      <nav class="nav-list" aria-label="Main sections">
        ${items.map(([view, label, description]) => `
          <button class="nav-item ${state.activeView === view ? "active" : ""}" type="button" data-view="${escapeHtml(view)}" title="${escapeHtml(label)}">
            ${renderIcon(view)}
            <span>
              <strong>${escapeHtml(label)}</strong>
              <small>${escapeHtml(description)}</small>
            </span>
          </button>`).join("")}
      </nav>
      <div class="data-status" role="status">
        <span class="status-dot"></span>
        <div>
          <strong>Data status</strong>
          <small>All systems operational</small>
        </div>
      </div>
    </aside>`;
}
function renderSourcesNavItem(icon, label) {
  return `
    <div class="sources-nav">
      <button class="nav-item ${state.sourcesOpen ? "active" : ""}" type="button" data-toggle-sources aria-expanded="${state.sourcesOpen ? "true" : "false"}" title="${escapeHtml(label)}">
        ${renderIcon(icon)}
        <span>${escapeHtml(label)}</span>
      </button>
      ${state.sourcesOpen ? renderSourcesPanel() : ""}
    </div>`;
}
function renderSourcesPanel() {
  const visibleSources = state.datasources.filter((item) => item.connector_key !== "metadata-db");
  return `
    <div class="sources-panel">
      <div class="source-actions">
        <button class="source-add" type="button" data-add-source aria-label="Add Excel file" title="Add Excel file" ${state.datasourceUploadPending || !state.token ? "disabled" : ""}>
          <span aria-hidden="true">+</span>
        </button>
      </div>
      <div class="sources-list" aria-live="polite">
        ${state.datasourcesLoading ? `<div class="source-muted">Loading...</div>` : ""}
        ${!state.datasourcesLoading && !visibleSources.length ? `<div class="source-muted">No sources</div>` : ""}
        ${visibleSources.map((source) => `
          <div class="source-row" title="${escapeHtml(source.name)}">
            <label class="source-state">
              <input type="checkbox" data-source-selected="${escapeHtml(source.connector_key)}" ${state.selectedDatasourceIds.includes(source.connector_key) ? "checked" : ""} ${state.datasourceSelectionPending ? "disabled" : ""} />
              <span>${escapeHtml(source.name)}</span>
            </label>
            <small>${state.selectedDatasourceIds.includes(source.connector_key) ? "Selected" : "Available"}</small>
          </div>`).join("")}
      </div>
      
      ${state.datasourceError ? `<div class="source-error" role="alert">${escapeHtml(state.datasourceError)}</div>` : ""}
    </div>`;
}
function renderAuthControls() {
  if (state.token) {
    return `
      <div class="signed-in">
        <button class="ghost-button" type="button" data-new-chat ${state.pending ? "disabled" : ""}>New chat</button>
        <span class="user-chip" title="${escapeHtml(state.username || "Signed-in user")}">
          ${renderIcon("user")}
          <span>${escapeHtml(state.username || "User")}</span>
        </span>
        <button class="ghost-button" type="button" data-logout>Log out</button>
      </div>`;
  }
  return `<button class="primary auth-button" type="button" data-open-login>Log in</button>`;
}
function renderEmptyState() {
  if (!state.token) {
    return `
      <div class="empty-state locked">
        <div class="empty-icon">${renderIcon("lock")}</div>
        <h2>ask your data</h2>
        <p>Log in to start a conversation.</p>
        <button class="primary" type="button" data-open-login>Log in</button>
      </div>`;
  }
  return `
    <div class="empty-state">
      <img class="empty-logo" src="/assets/getgaard.svg" alt="" />
      <h2>ask your data</h2>
      <p>Ask about metrics, records, trends, or run step-by-step analysis.</p>
    </div>`;
}
function renderViewHeading() {
  if (state.activeView === "analysis") {
    return renderAnalysisHeading();
  }
  const headings = {
    home: ["Home", "Ask your data"],
    metrics: ["Metrics", "Defined dashboard widgets"],
    datasources: ["Datasources", "Files and database sources"],
    queries: ["My Queries", "Conversation history"],
    alerts: ["Alerts", "Alert definitions"]
  };
  const [eyebrow, title] = headings[state.activeView] || headings.home;
  return `
    <div class="conversation-heading">
      <span>${escapeHtml(eyebrow)}</span>
      <strong>${escapeHtml(title)}</strong>
    </div>`;
}
function renderAnalysisHeading() {
  const dashboard = getActiveDashboard();
  if (state.token && state.dashboardsLoaded && !state.dashboardsLoading && !dashboard) {
    return `
    <div class="conversation-heading analysis-heading analysis-heading-empty">
      <div class="dashboard-title-row">
        ${renderAddDashboardButton("dashboard-picker-button dashboard-add-heading")}
      </div>
    </div>`;
  }
  const title = dashboard?.name || "Analysis";
  const description = dashboard?.description || (dashboard ? "No description yet." : "Create a dashboard to organize saved query widgets.");
  return `
    <div class="conversation-heading analysis-heading">
      <span>Analysis</span>
      <div class="dashboard-title-row">
        ${dashboard ? `
        <button class="dashboard-title-button" type="button" data-edit-active-dashboard aria-label="Edit dashboard details" title="Edit dashboard details">
          ${escapeHtml(title)}
        </button>` : `<strong>${escapeHtml(title)}</strong>`}
        ${renderDashboardPicker()}
      </div>
      <p>${escapeHtml(description)}</p>
    </div>`;
}
function renderActiveView() {
  if (state.activeView === "analysis") {
    return renderAnalysisView();
  }
  if (state.activeView === "metrics") {
    return renderMetricsView();
  }
  if (state.activeView === "datasources") {
    return renderDatasourcesView();
  }
  if (state.activeView === "queries") {
    return renderQueriesView();
  }
  if (state.activeView === "alerts") {
    return renderPlaceholderView({
      title: "Alerts",
      description: "Alert definitions and data monitoring rules will appear here once alert support is added.",
      items: ["Threshold alerts", "Scheduled checks", "Notification channels"]
    });
  }
  return renderHomeView();
}
function renderApiErrorBanner() {
  if (!state.apiError) {
    return "";
  }
  return `
    <div class="api-error-banner" role="alert">
      <div class="api-error-icon">${renderErrorIcon()}</div>
      <p>${escapeHtml(state.apiError.message)}</p>
      <button class="api-error-close" type="button" data-dismiss-api-error aria-label="Dismiss error" title="Dismiss">
        <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <path d="M18 6 6 18" />
          <path d="m6 6 12 12" />
        </svg>
      </button>
    </div>`;
}
function renderHomeView() {
  return `
    <section class="chat-shell" aria-label="GAARD chat">
      <section class="history" aria-live="polite">
        ${state.messages.length ? state.messages.map(renderMessage).join("") : renderEmptyState()}
      </section>
      ${renderQueryForm()}
    </section>`;
}
function renderQueryForm() {
  const inputDisabled = state.pending || !state.token;
  return `
    <form id="query-form" class="query-bar">
      ${state.error ? `<div class="query-error" role="alert">${escapeHtml(state.error)}</div>` : ""}
      <fieldset class="mode-control" ${inputDisabled ? "disabled" : ""}>
        <legend>Work mode</legend>
        <label class="${state.queryMode === "sql" ? "active" : ""}">
          <input type="radio" name="mode" value="sql" ${state.queryMode === "sql" ? "checked" : ""}>
          <span>SQL</span>
        </label>
        <label class="${state.queryMode === "analysis" ? "active" : ""}">
          <input type="radio" name="mode" value="analysis" ${state.queryMode === "analysis" ? "checked" : ""}>
          <span>Analysis</span>
        </label>
      </fieldset>
      <textarea id="question-input" name="question" placeholder="${state.token ? "Ask your data" : "Log in to ask a question"}" rows="1" ${inputDisabled ? "disabled" : ""}></textarea>
      <button class="send-button" type="submit" aria-label="Send question" title="Send" ${inputDisabled ? "disabled" : ""}>
        ${renderIcon("arrowUp")}
      </button>
    </form>`;
}
function getActiveDashboard() {
  if (!state.activeDashboardId && state.dashboards.length) {
    state.activeDashboardId = state.dashboards[0].id || "";
  }
  return state.dashboards.find((item) => item.id === state.activeDashboardId) || state.dashboards[0] || null;
}
function renderAnalysisView() {
  const dashboard = getActiveDashboard();
  return `
    <section class="dashboard-view" aria-label="Dashboard Analysis">
      <div class="dashboard-toolbar dashboard-toolbar-compact">
        <div class="dashboard-toolbar-actions">
          ${dashboard && state.token ? `
          <button
            class="dashboard-edit-mode-button ${state.dashboardEditMode ? "active" : ""} ${state.dashboardLayoutSaving ? "saving" : ""}"
            type="button"
            data-toggle-dashboard-edit
            aria-pressed="${state.dashboardEditMode ? "true" : "false"}"
            aria-label="${state.dashboardLayoutSaving ? "Saving dashboard layout" : state.dashboardEditMode ? "Finish editing dashboard layout" : "Edit dashboard layout"}"
            title="${state.dashboardLayoutSaving ? "Saving..." : state.dashboardEditMode ? "Finish editing" : "Edit layout"}"
            ${state.dashboardLayoutSaving ? "disabled" : ""}
          >
            ${state.dashboardLayoutSaving ? `<span class="dashboard-edit-saving-spinner" aria-hidden="true"></span>` : renderIcon("edit")}
            ${state.dashboardLayoutSaving ? `<span>Saving...</span>` : ""}
          </button>` : ""}
          ${dashboard && state.token && state.dashboardEditMode ? `
          <button class="dashboard-add-widget-button" type="button" data-open-widget-dialog aria-label="Add widget" title="Add widget">
            ${renderIcon("plus")}
          </button>` : ""}
        </div>
      </div>
      ${state.dashboardsError ? `<div class="source-error datasource-error" role="alert">${escapeHtml(state.dashboardsError)}</div>` : ""}
      ${state.dashboardWidgetsError ? `<div class="source-error datasource-error" role="alert">${escapeHtml(state.dashboardWidgetsError)}</div>` : ""}
      ${renderAnalysisDashboardBody(dashboard)}
    </section>`;
}
function renderAnalysisDashboardBody(dashboard) {
  if (!state.token) {
    return `
      <div class="dashboard-empty">
        <h2>Log in to manage dashboards.</h2>
        <p>Your dashboards are scoped to your authenticated GAARD account.</p>
        <button class="primary" type="button" data-open-login>Log in</button>
      </div>`;
  }
  if (state.dashboardsLoading && !state.dashboards.length) {
    return renderDashboardLoadingState(
      "Loading dashboards...",
      "Please wait while GAARD prepares your dashboard list."
    );
  }
  if (!dashboard) {
    return `
      <div class="dashboard-empty">
        <h2>No dashboards yet.</h2>
        <div class="dashboard-empty-copy">
          <span>Create your first dashboard by clicking</span>
          ${renderAddDashboardButton("dashboard-inline-add")}
        </div>
      </div>`;
  }
  return `
      ${renderDashboardWidgetGrid(dashboard)}
      <div class="dashboard-fallback" data-dashboard-fallback hidden>
        Dashboard libraries are loading. Charts will appear when GridStack and ECharts are available.
      </div>`;
}
function renderDashboardWidgetGrid(dashboard) {
  if (state.dashboardWidgetsLoading && state.dashboardWidgetsDashboardId !== dashboard.id) {
    return renderDashboardLoadingState(
      "Loading widgets...",
      "Please wait while GAARD prepares this dashboard."
    );
  }
  if (!state.dashboardWidgets.length) {
    return `
      <div class="dashboard-empty">
        <h2>No widgets yet.</h2>
        <div class="dashboard-empty-copy">
          <span>Add a saved metric to start building this dashboard.</span>
          ${state.dashboardEditMode ? `
          <button class="dashboard-inline-add" type="button" data-open-widget-dialog>
            ${renderIcon("plus")}
            <span>Add widget</span>
          </button>` : `<p>Choose "Edit layout" to add the first widget.</p>`}
        </div>
      </div>`;
  }
  return `
      <div class="grid-stack dashboard-grid ${
        state.dashboardEditMode
          ? "dashboard-grid-editing"
          : "dashboard-grid-readonly"
      }">
        ${state.dashboardWidgets.map(renderDashboardWidget).join("")}
      </div>`;
}
function renderDashboardLoadingState(title, description) {
  return `
      <div class="dashboard-empty dashboard-loading">
        <span class="dashboard-spinner" aria-hidden="true"></span>
        <h2>${escapeHtml(title)}</h2>
        <p>${escapeHtml(description)}</p>
      </div>`;
}
function renderDashboardPicker() {
  const label = state.dashboardsLoading ? "Loading dashboards" : "Choose dashboard";
  return `
    <div class="dashboard-picker">
      <button class="dashboard-picker-button dashboard-picker-icon-button" type="button" data-toggle-dashboard-menu aria-expanded="${state.dashboardMenuOpen ? "true" : "false"}" aria-label="${escapeHtml(label)}" title="${escapeHtml(label)}">
        ${renderIcon("dashboards")}
      </button>
      ${state.dashboardMenuOpen ? renderDashboardMenu() : ""}
    </div>`;
}
function renderAddDashboardButton(className = "dashboard-add-button") {
  return `
      <button class="${className}" type="button" data-toggle-dashboard-create>
        ${renderIcon("plus")}
        <span>Add new dashboard</span>
      </button>`;
}
function renderDashboardMenu() {
  return `
    <div class="dashboard-menu" role="menu">
      ${renderAddDashboardButton()}
      <div class="dashboard-menu-list">
        ${state.dashboardsLoading ? `<div class="dashboard-menu-empty">Loading...</div>` : ""}
        ${!state.dashboardsLoading && !state.dashboards.length ? `<div class="dashboard-menu-empty">No dashboards yet.</div>` : ""}
        ${state.dashboards.map((dashboard) => renderDashboardMenuItem(dashboard)).join("")}
      </div>
    </div>`;
}
function renderDashboardCreateDialog() {
  const editing = Boolean(state.dashboardEditId);
  const editingDashboard = state.dashboardEditId
    ? state.dashboards.find((dashboard) => dashboard.id === state.dashboardEditId)
    : null;
  const title = editing ? "Edit dashboard" : "Add new dashboard";
  const description = editing
    ? "Update the dashboard name and description."
    : "Name the workspace where saved query widgets will be organized.";
  return `
    <div class="dashboard-dialog-overlay" role="presentation">
      <section class="dashboard-dialog" role="dialog" aria-modal="true" aria-labelledby="dashboard-create-title">
        <button class="icon-button dashboard-dialog-close" type="button" data-close-dashboard-create aria-label="Close dialog" title="Close">
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            <path d="M18 6 6 18" />
            <path d="m6 6 12 12" />
          </svg>
        </button>
        <div class="dashboard-dialog-heading">
          <span>Analysis</span>
          <h2 id="dashboard-create-title">${escapeHtml(title)}</h2>
          <p>${escapeHtml(description)}</p>
        </div>
        ${renderDashboardCreateForm(editingDashboard, editing)}
      </section>
    </div>`;
}
function renderDashboardCreateForm(editingDashboard = null, editing = false) {
  return `
    <form class="dashboard-create-form" data-dashboard-create-form>
      <label>
        <span>Name</span>
        <input name="name" maxlength="255" required placeholder="Operations overview" value="${escapeHtml(editingDashboard?.name || "")}" ${state.dashboardCreatePending ? "disabled" : ""} />
      </label>
      <label>
        <span>Description</span>
        <textarea name="description" maxlength="2000" rows="2" placeholder="Weekly operational snapshot" ${state.dashboardCreatePending ? "disabled" : ""}>${escapeHtml(editingDashboard?.description || "")}</textarea>
      </label>
      ${state.dashboardsError ? `<div class="source-error datasource-error" role="alert">${escapeHtml(state.dashboardsError)}</div>` : ""}
      <div class="dashboard-create-actions">
        <button type="button" data-close-dashboard-create ${state.dashboardCreatePending ? "disabled" : ""}>Cancel</button>
        <button class="primary" type="submit" ${state.dashboardCreatePending ? "disabled" : ""}>
          ${state.dashboardCreatePending ? "Saving..." : editing ? "Save changes" : "Create dashboard"}
        </button>
      </div>
    </form>`;
}
function renderDashboardWidgetDialog() {
  const metrics = state.savedMetrics;
  const selectedMetric = getSelectedSavedMetric();
  const availableTypes = getAvailableWidgetTypes(selectedMetric);
  const selectedType = normalizeDashboardWidgetType(state.dashboardWidgetType, availableTypes);
  return `
    <div class="dashboard-dialog-overlay" role="presentation">
      <section class="dashboard-dialog dashboard-widget-dialog" role="dialog" aria-modal="true" aria-labelledby="dashboard-widget-title">
        <button class="icon-button dashboard-dialog-close" type="button" data-close-widget-dialog aria-label="Close dialog" title="Close">
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            <path d="M18 6 6 18" />
            <path d="m6 6 12 12" />
          </svg>
        </button>
        <div class="dashboard-dialog-heading">
          <span>Analysis</span>
          <h2 id="dashboard-widget-title">Add widget</h2>
          <p>Choose a saved metric and how it should be displayed on this dashboard.</p>
        </div>
        ${state.savedMetricsError ? `<div class="source-error datasource-error" role="alert">${escapeHtml(state.savedMetricsError)}</div>` : ""}
        ${state.savedMetricsLoading ? renderSavedMetricsLoadingState("dashboard-menu-empty") : ""}
        ${!state.savedMetricsLoading && !metrics.length ? renderNoSavedMetricsState() : ""}
        ${!state.savedMetricsLoading && metrics.length ? renderDashboardWidgetForm(metrics, selectedMetric, availableTypes, selectedType) : ""}
      </section>
    </div>`;
}
function renderNoSavedMetricsState() {
  return `
    <div class="dashboard-widget-empty">
      <strong>No saved metrics yet.</strong>
      <p>Run a query on Home and use the save button on a successful answer.</p>
    </div>`;
}
function renderSavedMetricsLoadingState(className) {
  return `
    <div class="${escapeHtml(className)} saved-metrics-loading" role="status" aria-live="polite">
      <span class="saved-metrics-spinner" aria-hidden="true"></span>
      <span>Loading saved metrics...</span>
    </div>`;
}
function renderDashboardWidgetForm(metrics, selectedMetric, availableTypes, selectedType) {
  const defaultTitle = selectedMetric?.label || "Dashboard widget";
  return `
    <form class="dashboard-widget-form" data-dashboard-widget-form>
      <label>
        <span>Saved metric</span>
        <select name="metric_widget_key" data-widget-metric-select ${state.dashboardWidgetPending ? "disabled" : ""}>
          ${metrics.map((metric) => `
            <option value="${escapeHtml(metric.widget_key)}" ${metric.widget_key === selectedMetric?.widget_key ? "selected" : ""}>${escapeHtml(metric.label || metric.widget_key)}</option>
          `).join("")}
        </select>
      </label>
      <label>
        <span>Widget name</span>
        <input name="title" maxlength="255" required value="${escapeHtml(defaultTitle)}" ${state.dashboardWidgetPending ? "disabled" : ""} />
      </label>
      <fieldset class="widget-type-grid">
        <legend>Widget type</legend>
        ${WIDGET_TYPES.map((type) => renderWidgetTypeChoice(type, availableTypes, selectedType)).join("")}
      </fieldset>
      ${state.dashboardWidgetsError ? `<div class="source-error datasource-error" role="alert">${escapeHtml(state.dashboardWidgetsError)}</div>` : ""}
      <div class="dashboard-create-actions">
        <button type="button" data-close-widget-dialog ${state.dashboardWidgetPending ? "disabled" : ""}>Cancel</button>
        <button class="primary" type="submit" ${state.dashboardWidgetPending ? "disabled" : ""}>
          ${state.dashboardWidgetPending ? "Adding..." : "Add widget"}
        </button>
      </div>
    </form>`;
}
function renderWidgetTypeChoice(type, availableTypes, selectedType) {
  const disabled = !availableTypes.includes(type.key) || state.dashboardWidgetPending;
  return `
    <label class="widget-type-choice ${selectedType === type.key ? "active" : ""} ${disabled ? "disabled" : ""}">
      <input type="radio" name="visualization_type" value="${escapeHtml(type.key)}" ${selectedType === type.key ? "checked" : ""} ${disabled ? "disabled" : ""} data-widget-type-select />
      <span class="widget-type-preview ${escapeHtml(type.key)}">${renderWidgetTypePreview(type.key)}</span>
      <strong>${escapeHtml(type.label)}</strong>
    </label>`;
}
function renderSaveWidgetDialog() {
  const message = state.messages.find((item) => item.id === state.saveWidgetMessageId);
  const pending = state.saveWidgetPending;
  const suggestionStatus = state.saveWidgetSuggestionLoading ? `
    <div class="save-title-status" role="status" aria-live="polite">
      <span class="saved-metrics-spinner" aria-hidden="true"></span>
      <span>LLM is suggesting a name...</span>
    </div>` : state.saveWidgetSuggestionError ? `
    <div class="save-title-status warning" role="status">${escapeHtml(state.saveWidgetSuggestionError)}</div>` : "";
  return `
    <div class="dashboard-dialog-overlay" role="presentation">
      <section class="dashboard-dialog save-metric-dialog" role="dialog" aria-modal="true" aria-labelledby="save-metric-title">
        <button class="icon-button dashboard-dialog-close" type="button" data-close-save-widget-dialog aria-label="Close dialog" title="Close" ${pending ? "disabled" : ""}>
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            <path d="M18 6 6 18" />
            <path d="m6 6 12 12" />
          </svg>
        </button>
        <div class="dashboard-dialog-heading">
          <span>Metric</span>
          <h2 id="save-metric-title">Save metric</h2>
          <p>LLM suggests a short name from the query. You can edit it before saving.</p>
        </div>
        <form class="dashboard-create-form" data-save-widget-form>
          <label>
            <span>Metric name</span>
            <input name="label" maxlength="255" required value="${escapeHtml(state.saveWidgetDraftLabel)}" ${pending ? "disabled" : ""} data-save-widget-label />
          </label>
          ${message ? `<div class="save-metric-question">${escapeHtml(message.question)}</div>` : ""}
          ${suggestionStatus}
          ${state.saveWidgetError ? `<div class="source-error datasource-error" role="alert">${escapeHtml(state.saveWidgetError)}</div>` : ""}
          <div class="dashboard-create-actions">
            <button type="button" data-close-save-widget-dialog ${pending ? "disabled" : ""}>Cancel</button>
            <button class="primary" type="submit" ${pending ? "disabled" : ""}>
              ${pending ? "Saving..." : "Save"}
            </button>
          </div>
        </form>
      </section>
    </div>`;
}
function renderMetricEditDialog() {
  const metric = state.savedMetrics.find((item) => item.widget_key === state.metricEditWidgetKey);
  const pending = state.metricEditPending;
  return `
    <div class="dashboard-dialog-overlay" role="presentation">
      <section class="dashboard-dialog metric-edit-dialog" role="dialog" aria-modal="true" aria-labelledby="metric-edit-title">
        <button class="icon-button dashboard-dialog-close" type="button" data-close-metric-edit aria-label="Close dialog" title="Close" ${pending ? "disabled" : ""}>
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            <path d="M18 6 6 18" />
            <path d="m6 6 12 12" />
          </svg>
        </button>
        <div class="dashboard-dialog-heading">
          <span>Metric</span>
          <h2 id="metric-edit-title">Edit metric name</h2>
          <p>Change the name shown in the saved metrics list.</p>
        </div>
        <form class="dashboard-create-form" data-metric-edit-form>
          <label>
            <span>Metric name</span>
            <input name="label" maxlength="255" required value="${escapeHtml(state.metricEditDraftLabel || metric?.label || "")}" ${pending ? "disabled" : ""} data-metric-edit-label />
          </label>
          ${state.metricEditError ? `<div class="source-error datasource-error" role="alert">${escapeHtml(state.metricEditError)}</div>` : ""}
          <div class="dashboard-create-actions">
            <button type="button" data-close-metric-edit ${pending ? "disabled" : ""}>Cancel</button>
            <button class="primary" type="submit" ${pending ? "disabled" : ""}>
              ${pending ? "Saving..." : "Save"}
            </button>
          </div>
        </form>
      </section>
    </div>`;
}
function renderWidgetTypePreview(type) {
  if (type === "table") {
    return `<i></i><i></i><i></i><i></i>`;
  }
  if (type === "number") {
    return `<b>42</b>`;
  }
  if (type === "pie") {
    return `<i></i>`;
  }
  return `<i></i><i></i><i></i>`;
}
function renderDashboardMenuItem(dashboard) {
  const selected = dashboard.id === state.activeDashboardId;
  const deleting = state.dashboardDeletePendingId === dashboard.id;
  return `
    <div class="dashboard-menu-item ${selected ? "active" : ""}">
      <button type="button" data-select-dashboard="${escapeHtml(dashboard.id)}">
        <strong>${escapeHtml(dashboard.name || "Untitled dashboard")}</strong>
        <small>${escapeHtml(dashboard.description || "No description")}</small>
      </button>
      <button class="dashboard-delete-button" type="button" data-delete-dashboard="${escapeHtml(dashboard.id)}" aria-label="Delete ${escapeHtml(dashboard.name || "dashboard")}" title="Delete dashboard" ${deleting ? "disabled" : ""}>
        ${renderIcon("trash")}
      </button>
    </div>`;
}
function renderDashboardWidget(widget) {
  const layout = widget.layout || {};
  const content = renderDashboardWidgetContent(widget);
  return `
    <div class="grid-stack-item" gs-id="${escapeHtml(widget.id)}" gs-x="${Number(layout.x) || 0}" gs-y="${Number(layout.y) || 0}" gs-w="${Number(layout.w) || 6}" gs-h="${Number(layout.h) || 4}">
      <article class="grid-stack-item-content dashboard-card dashboard-user-widget">
        <header>
          <h2>${escapeHtml(widget.title || widget.metric?.label || "Dashboard widget")}</h2>
          ${state.dashboardEditMode ? `
          <button type="button" data-delete-dashboard-widget="${escapeHtml(widget.id)}" aria-label="Remove widget" title="Remove widget">
            ${renderIcon("trash")}
          </button>` : ""}
        </header>
        ${content}
      </article>
    </div>`;
}
function renderDashboardWidgetContent(widget) {
  const result = widget.result || widget.metric?.result || {};
  if (result.status && result.status !== "ok") {
    return `<div class="dashboard-widget-error">${escapeHtml(result.message || result.error || "Widget data could not be loaded.")}</div>`;
  }
  if (widget.visualization_type === "table") {
    return `<div class="dashboard-table-widget">${renderDataTable(getRowsFromResult(result))}</div>`;
  }
  if (widget.visualization_type === "number") {
    return renderDashboardNumberWidget(result);
  }
  return `<div class="chart dashboard-widget-chart" data-dashboard-widget-chart="${escapeHtml(widget.id)}"></div>`;
}
function renderDashboardNumberWidget(result) {
  const rows = getRowsFromResult(result);
  const columns = Array.isArray(result?.columns) && result.columns.length ? result.columns : getColumns(rows);
  const value = result?.value ?? rows[0]?.[columns[0]] ?? "";
  const label = columns[0] || "Value";
  return `
    <div class="dashboard-number-widget">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(formatCellValue(value))}</strong>
    </div>`;
}
function renderKpiCard(label, value, delta, helper, direction) {
  return `
    <article class="kpi-card">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      <div class="kpi-delta ${direction}">
        <b>${direction === "up" ? "↗" : "↘"} ${escapeHtml(delta)}</b>
        <small>${escapeHtml(helper)}</small>
      </div>
    </article>`;
}
function renderGridWidget(id, title, bodyClass, x, y, w, h, content = "") {
  return `
    <div class="grid-stack-item" gs-id="${escapeHtml(id)}" gs-x="${x}" gs-y="${y}" gs-w="${w}" gs-h="${h}">
      <article class="grid-stack-item-content dashboard-card">
        <header>
          <h2>${escapeHtml(title)}</h2>
          <button type="button" aria-disabled="true" title="Widget settings">⌄</button>
        </header>
        <div class="${escapeHtml(bodyClass)}" data-chart="${escapeHtml(id)}">${content}</div>
      </article>
    </div>`;
}
function renderEpisodeSummary() {
  return `
    <div class="episode-summary">
      <span>New episodes</span>
      <strong>3,274</strong>
      <small>↗ 7.1% vs May 5 - May 11</small>
    </div>
    <div class="chart-mini" data-chart="episodes-line"></div>`;
}
function renderIssuesTable() {
  const issues = [
    ["Lab analyzer downtime - Unit 3", "High", "Open", "May 12, 08:15"],
    ["Radiology report backlog", "Medium", "In progress", "May 12, 07:42"],
    ["Cardiology devices offline", "Medium", "Open", "May 12, 06:31"],
    ["OR scheduling conflict", "Low", "Open", "May 11, 16:08"]
  ];
  return `
    <table>
      <thead><tr><th>Issue</th><th>Severity</th><th>Status</th><th>Opened</th></tr></thead>
      <tbody>
        ${issues.map(([issue, severity, status, opened]) => `
          <tr>
            <td>${escapeHtml(issue)}</td>
            <td><span class="severity ${severity.toLowerCase()}">${escapeHtml(severity)}</span></td>
            <td>${escapeHtml(status)}</td>
            <td>${escapeHtml(opened)}</td>
          </tr>`).join("")}
      </tbody>
    </table>
    <button class="link-button" type="button" aria-disabled="true">View all issues</button>`;
}
function renderQueriesView() {
  const recent = state.messages.slice(-6).reverse();
  return `
    <section class="placeholder-view">
      <div class="placeholder-intro">
        <span>Marked feature</span>
        <h1>My Queries</h1>
        <p>Full chat history will appear here. For now, this shows recent questions from the current browser session.</p>
      </div>
      <div class="placeholder-list query-list">
        ${recent.length ? recent.map((message) => `
          <button type="button" data-view="home" class="placeholder-item">
            ${renderIcon(message.mode === "analysis" ? "analysis" : "queries")}
            <span>
              <strong>${escapeHtml(message.question)}</strong>
              <small>${escapeHtml(formatMode(message.mode))} · ${escapeHtml(message.status)}</small>
            </span>
          </button>`).join("") : `
          <div class="placeholder-item muted">
            ${renderIcon("queries")}
            <span><strong>No local queries yet</strong><small>Ask your data on Home to start a session.</small></span>
          </div>`}
      </div>
    </section>`;
}
function renderMetricsView() {
  const groups = groupSavedMetricsByDatasource(state.savedMetrics);
  const waitingForDatasourceNames = (
    state.token &&
    state.savedMetrics.some((metric) => normalizeMetricDatasourceKey(metric) === "default") &&
    !state.datasourcesLoaded
  );
  return `
    <section class="placeholder-view metrics-view">
      ${state.savedMetricsError ? `<div class="source-error datasource-error" role="alert">${escapeHtml(state.savedMetricsError)}</div>` : ""}
      ${state.savedMetricsLoading ? renderDashboardLoadingState("Loading saved metrics...", "Please wait while GAARD prepares your saved metrics.") : ""}
      ${!state.savedMetricsLoading && waitingForDatasourceNames ? renderDashboardLoadingState("Loading data sources...", "Please wait while GAARD resolves saved metric sources.") : ""}
      ${!state.savedMetricsLoading && !state.token ? `<div class="datasource-empty">Log in to manage saved metrics.</div>` : ""}
      ${!state.savedMetricsLoading && state.token && !state.savedMetrics.length ? `<div class="datasource-empty">No saved metrics yet. Save a successful query from Home first.</div>` : ""}
      ${!state.savedMetricsLoading && !waitingForDatasourceNames && state.savedMetrics.length ? `
      <div class="metrics-groups">
        ${groups.map(renderMetricDatasourceGroup).join("")}
      </div>` : ""}
    </section>`;
}
function normalizeMetricDatasourceKey(metric) {
  return String(metric.datasource_key || "default").trim() || "default";
}
function formatMetricDatasourceName(datasourceKey) {
  const normalizedKey = String(datasourceKey || "default").trim() || "default";
  if (normalizedKey === "default") {
    return formatDefaultMetricDatasourceName();
  }
  const source = state.datasources.find((item) => (
    String(item.connector_key || "") === normalizedKey
    || String(item.id || "") === normalizedKey
    || String(item.name || "") === normalizedKey
  ));
  if (source) {
    return source.name || source.connector_key || normalizedKey;
  }
  return normalizedKey
    .replace(/[-_](db|database)$/i, "")
    .replace(/[-_]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}
function formatDefaultMetricDatasourceName() {
  const activeSource = state.datasources.find((item) => (
    item.active && item.connector_key !== "metadata-db"
  ));
  const defaultSource = state.datasources.find((item) => item.connector_key === "default");
  const source = activeSource || defaultSource;
  const name = source?.name || source?.connector_key || "Default source";
  return `${name} (default)`;
}
function groupSavedMetricsByDatasource(metrics) {
  const groups = new Map();
  metrics.forEach((metric) => {
    const key = normalizeMetricDatasourceKey(metric);
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        label: formatMetricDatasourceName(key),
        items: []
      });
    }
    groups.get(key).items.push(metric);
  });
  return Array.from(groups.values()).sort((left, right) => {
    if (left.key === "default") return -1;
    if (right.key === "default") return 1;
    return left.label.localeCompare(right.label);
  });
}
function renderMetricDatasourceGroup(group) {
  return `
    <section class="metric-source-group" aria-label="Data Source: ${escapeHtml(group.label)}">
      <header class="metric-source-header">
        <h2>Data Source: ${escapeHtml(group.label)}</h2>
        <span>${group.items.length} ${group.items.length === 1 ? "metric" : "metrics"}</span>
      </header>
      <div class="metrics-list">
        ${group.items.map(renderSavedMetricCard).join("")}
      </div>
    </section>`;
}
function renderSavedMetricCard(metric) {
  const result = metric.result || {};
  const rows = getRowsFromResult(result);
  const typeLabel = metric.widget_type === "scalar" ? "Number-ready" : metric.widget_type === "timeseries" ? "Time series" : "Table";
  const widgetKey = metric.widget_key || "";
  const deleting = state.metricDeletePendingKey === widgetKey;
  const label = metric.label || metric.widget_key || "Saved metric";
  const details = [typeLabel];
  if (metric.result) {
    details.push(`${rows.length} rows`);
  }
  return `
    <article class="placeholder-item metric-card">
      ${renderIcon(metric.widget_type === "scalar" ? "metrics" : "dashboards")}
      <span>
        <strong class="metric-title" title="${escapeHtml(label)}">${escapeHtml(label)}</strong>
        <small>${escapeHtml(details.join(" · "))}</small>
      </span>
      <div class="metric-card-actions">
        <button class="metric-action-button" type="button" data-edit-metric="${escapeHtml(widgetKey)}" aria-label="Edit metric name" title="Edit metric name" ${!widgetKey || deleting ? "disabled" : ""}>
          ${renderIcon("edit")}
        </button>
        <button class="metric-action-button metric-delete-button" type="button" data-delete-metric="${escapeHtml(widgetKey)}" aria-label="Delete metric" title="Delete metric" ${!widgetKey || deleting ? "disabled" : ""}>
          ${renderIcon("trash")}
        </button>
      </div>
    </article>`;
}
function renderDatasourcesView() {
  const visibleSources = state.datasources.filter((item) => item.connector_key !== "metadata-db");
  const uploadLabel = state.datasourceUploadPending
    ? "Uploading..."
    : state.token
      ? "Upload .xlsx workbook"
      : "Log in to upload .xlsx workbook";
  return `
    <section class="datasources-view placeholder-view">
      <div class="datasource-actions-grid">
        <button class="placeholder-item datasource-upload-card" type="button" data-add-source ${state.datasourceUploadPending ? "disabled" : ""}>
          ${renderIcon("plus")}
          <span><strong>Excel workbooks</strong><small>${escapeHtml(uploadLabel)}</small></span>
        </button>
        <div class="placeholder-item muted">
          ${renderIcon("plus")}
          <span><strong>CSV uploads</strong><small>Coming soon</small></span>
        </div>
        <div class="placeholder-item muted">
          ${renderIcon("plus")}
          <span><strong>Connected databases</strong><small>Coming soon</small></span>
        </div>
      </div>
      <section class="datasource-list-panel" aria-live="polite">
        <header>
          <div>
            <span>Available sources</span>
            <h2>Select sources to ask questions about.</h2>
          </div>
          <button class="ghost-button" type="button" data-refresh-sources ${state.datasourcesLoading || !state.token ? "disabled" : ""}>
            Refresh
          </button>
        </header>
        ${state.datasourceError ? `<div class="source-error datasource-error" role="alert">${escapeHtml(state.datasourceError)}</div>` : ""}
        ${state.datasourcesLoading ? `<div class="datasource-empty">Loading data sources...</div>` : ""}
        ${!state.datasourcesLoading && !state.token ? `<div class="datasource-empty">Log in to manage datasources.</div>` : ""}
        ${!state.datasourcesLoading && state.token && !visibleSources.length ? `<div class="datasource-empty">No Datasources have been enabled.</div>` : ""}
        ${!state.datasourcesLoading && visibleSources.length ? `
          <div class="datasource-table">
            ${visibleSources.map(renderDatasourceRow).join("")}
          </div>` : ""}
      </section>
    </section>`;
}
function renderDatasourceRow(source) {
  const selected = state.selectedDatasourceIds.includes(source.connector_key);
  const typeLabel = source.database_type || source.connector_key || "datasource";
  return `
    <article class="datasource-row">
      <label class="datasource-row-main">
        <input type="checkbox" data-source-selected="${escapeHtml(source.connector_key)}" ${selected ? "checked" : ""} ${state.datasourceSelectionPending ? "disabled" : ""} />
        <span>
          <strong>${escapeHtml(source.name || source.connector_key || "Untitled source")}</strong>
          <small>${escapeHtml(typeLabel)}</small>
        </span>
      </label>
      <span class="datasource-status ${selected ? "active" : ""}">${selected ? "Selected" : "Available"}</span>
    </article>`;
}
function renderPlaceholderView({ title, description, items }) {
  return `
    <section class="placeholder-view">
      <div class="placeholder-intro">
        <span>Marked feature</span>
        <h1>${escapeHtml(title)}</h1>
        <p>${escapeHtml(description)}</p>
      </div>
      <div class="placeholder-list">
        ${items.map((item) => `
          <div class="placeholder-item">
            ${renderIcon("plus")}
            <span><strong>${escapeHtml(item)}</strong><small>Coming soon</small></span>
          </div>`).join("")}
      </div>
    </section>`;
}
function render(options = {}) {
  if (!app) return;
  app.innerHTML = `
    <main class="app-shell">
      ${renderSidebar()}
      <section class="workspace-shell" aria-label="GAARD workspace">
        <header class="topbar">
          ${renderViewHeading()}
          <div class="header-actions">
            ${renderAuthControls()}
          </div>
        </header>
        <div class="api-error-slot">${renderApiErrorBanner()}</div>
        ${renderActiveView()}
      </section>
      <input id="excel-source-input" type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" hidden />
      ${state.loginOpen ? renderLoginDialog() : ""}
      ${state.mustChangePassword ? renderPasswordChangeDialog() : ""}
      ${state.dashboardCreateOpen ? renderDashboardCreateDialog() : ""}
      ${state.dashboardWidgetDialogOpen ? renderDashboardWidgetDialog() : ""}
      ${state.saveWidgetDialogOpen ? renderSaveWidgetDialog() : ""}
      ${state.metricEditDialogOpen ? renderMetricEditDialog() : ""}
    </main>`;
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", changeView);
  });
  document.querySelector("#query-form")?.addEventListener("submit", submitQuestion);
  document.querySelector("[data-logout]")?.addEventListener("click", logout);
  document.querySelector("[data-new-chat]")?.addEventListener("click", newChat);
  document.querySelectorAll("[data-open-login]").forEach((button) => {
    button.addEventListener("click", openLogin);
  });
  document.querySelector("[data-close-login]")?.addEventListener("click", closeLogin);
  document.querySelector("[data-dismiss-api-error]")?.addEventListener("click", dismissApiError);
  document.querySelector("[data-toggle-sources]")?.addEventListener("click", toggleSources);
  document.querySelector("[data-add-source]")?.addEventListener("click", openSourcePicker);
  document.querySelector("[data-refresh-sources]")?.addEventListener("click", () => loadDatasources());
  document.querySelectorAll("[data-source-selected]").forEach((input2) => {
    input2.addEventListener("change", updateSelectedSources);
  });
  document.querySelector("[data-toggle-dashboard-menu]")?.addEventListener("click", toggleDashboardMenu);
  document.querySelectorAll("[data-toggle-dashboard-create]").forEach((button) => {
    button.addEventListener("click", openDashboardCreate);
  });
  document.querySelector("[data-edit-active-dashboard]")?.addEventListener("click", openActiveDashboardEdit);
  document.querySelectorAll("[data-close-dashboard-create]").forEach((button) => {
    button.addEventListener("click", closeDashboardCreate);
  });
  document.querySelectorAll("[data-dashboard-create-form]").forEach((form) => {
    form.addEventListener("submit", createDashboard);
  });
  document.querySelectorAll("[data-select-dashboard]").forEach((button) => {
    button.addEventListener("click", selectDashboard);
  });
  document.querySelectorAll("[data-delete-dashboard]").forEach((button) => {
    button.addEventListener("click", deleteDashboard);
  });
  document.querySelectorAll("[data-open-widget-dialog]").forEach((button) => {
    button.addEventListener("click", openDashboardWidgetDialog);
  });
  document
    .querySelector("[data-toggle-dashboard-edit]")
    ?.addEventListener("click", toggleDashboardEditMode);
  document.querySelectorAll("[data-close-widget-dialog]").forEach((button) => {
    button.addEventListener("click", closeDashboardWidgetDialog);
  });
  document.querySelector("[data-dashboard-widget-form]")?.addEventListener("submit", addDashboardWidget);
  document.querySelector("[data-widget-metric-select]")?.addEventListener("change", changeDashboardWidgetMetric);
  document.querySelectorAll("[data-widget-type-select]").forEach((input2) => {
    input2.addEventListener("change", changeDashboardWidgetType);
  });
  document.querySelectorAll("[data-delete-dashboard-widget]").forEach((button) => {
    button.addEventListener("click", deleteDashboardWidget);
  });
  document.querySelector("#excel-source-input")?.addEventListener("change", uploadSelectedSource);
  document.querySelector("#login-form")?.addEventListener("submit", login);
  document.querySelector("#password-change-form")?.addEventListener("submit", changePassword);
  document.querySelectorAll('input[name="mode"]').forEach((input2) => {
    input2.addEventListener("change", handleModeChange);
  });
  document.querySelectorAll("[data-toggle-data]").forEach((button) => {
    button.addEventListener("click", toggleDataTable);
  });
  document.querySelectorAll("[data-retry-question]").forEach((button) => {
    button.addEventListener("click", retryQuestion);
  });
  document.querySelectorAll("[data-save-widget]").forEach((button) => {
    button.addEventListener("click", saveWidgetFromMessage);
  });
  document.querySelectorAll("[data-explain-answer]").forEach((button) => {
    button.addEventListener("click", explainAnswer);
  });
  document.querySelectorAll("[data-close-save-widget-dialog]").forEach((button) => {
    button.addEventListener("click", closeSaveWidgetDialog);
  });
  document.querySelector("[data-save-widget-form]")?.addEventListener("submit", confirmSaveWidgetFromDialog);
  document.querySelector("[data-save-widget-label]")?.addEventListener("input", changeSaveWidgetLabel);
  document.querySelectorAll("[data-edit-metric]").forEach((button) => {
    button.addEventListener("click", openMetricEditDialog);
  });
  document.querySelectorAll("[data-delete-metric]").forEach((button) => {
    button.addEventListener("click", deleteSavedMetric);
  });
  document.querySelectorAll("[data-close-metric-edit]").forEach((button) => {
    button.addEventListener("click", closeMetricEditDialog);
  });
  document.querySelector("[data-metric-edit-form]")?.addEventListener("submit", saveMetricLabel);
  document.querySelector("[data-metric-edit-label]")?.addEventListener("input", changeMetricEditLabel);
  document.querySelectorAll("[data-analysis-reply-form]").forEach((form) => {
    form.addEventListener("submit", submitAnalysisReply);
  });
  document.querySelectorAll("[data-analysis-progress]").forEach((details) => {
    details.addEventListener("toggle", toggleAnalysisProgress);
  });
  const input = document.querySelector("#question-input");
  input?.addEventListener("keydown", handleQuestionKeydown);
  if (state.token && state.activeView === "home") {
    input?.focus();
  }
  initAnalysisDashboard();
  maybeLoadDatasourcesForActiveView();
  maybeLoadDashboardsForActiveView();
  maybeLoadSavedMetricsForActiveView();
  if (options.scrollToLatest) {
    scrollToLatest();
  }
}
var apiErrorTimer = null;
function apiErrorMessage(error, fallback = "Request failed.") {
  const raw = typeof error === "string" ? error : error?.message;
  return String(raw || fallback).trim() || fallback;
}
function reportApiError(error, fallback) {
  const id = state.nextApiErrorId++;
  state.apiError = {
    id,
    message: apiErrorMessage(error, fallback)
  };
  if (apiErrorTimer) {
    clearTimeout(apiErrorTimer);
  }
  apiErrorTimer = setTimeout(() => {
    if (state.apiError?.id === id) {
      state.apiError = null;
      render();
    }
  }, 8000);
}
function dismissApiError() {
  if (apiErrorTimer) {
    clearTimeout(apiErrorTimer);
    apiErrorTimer = null;
  }
  state.apiError = null;
  render();
}
function formatApiResponseError(response, message) {
  const detail = apiErrorMessage(message);
  const status = response?.status;
  if (!status) {
    return detail;
  }
  const statusText = response.statusText ? ` ${response.statusText}` : "";
  const prefix = `HTTP ${status}${statusText}`;
  return detail === "Request failed." ? `${prefix}: Request failed.` : `${prefix}: ${detail}`;
}
function maybeLoadDatasourcesForActiveView() {
    if (["home", "datasources", "metrics"].includes(state.activeView) && state.token && !state.datasourcesLoaded && !state.datasourcesLoading) {
        void loadDatasources();
    }
}
function maybeLoadDashboardsForActiveView() {
    if (state.activeView === "analysis" && state.token && !state.dashboardsLoaded && !state.dashboardsLoading) {
        void loadDashboards();
        return;
    }
    if (
        state.activeView === "analysis" &&
        state.token &&
        state.activeDashboardId &&
        state.dashboardWidgetsDashboardId !== state.activeDashboardId &&
        !state.dashboardWidgetsLoading
    ) {
        void loadDashboardWidgets(state.activeDashboardId);
    }
}
function maybeLoadSavedMetricsForActiveView() {
    if (state.activeView === "metrics" && state.token && !state.savedMetricsLoaded && !state.savedMetricsLoading) {
        void loadSavedMetrics({ includeResult: false });
    }
}
async function toggleSources() {
    state.sourcesOpen = !state.sourcesOpen;
    state.datasourceError = "";
    render();
    if (state.sourcesOpen && state.token && !state.datasourcesLoaded) {
        await loadDatasources({ preserveOrder: true });
    }
}
async function loadDashboards() {
    if (!state.token) {
        state.dashboards = [];
        state.dashboardsLoaded = false;
        state.activeDashboardId = "";
        state.dashboardEditMode = false;
        return;
    }
    state.dashboardEditMode = false;
    state.dashboardsLoading = true;
    state.dashboardsError = "";
    render();
    try {
        const response = await fetch(`/api/dashboards?backend_url=${encodeURIComponent(state.backendUrl)}`, {
            headers: authHeaders()
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(formatApiResponseError(response, extractErrorMessage(payload)));
        }
        state.dashboards = payload.items || [];
        state.dashboardsLoaded = true;
        state.activeDashboardId = payload.active_dashboard_id || payload.active_dashboard?.id || state.dashboards[0]?.id || "";
        if (state.activeDashboardId) {
            await loadDashboardWidgets(state.activeDashboardId, { silent: true });
        }
    } catch (error) {
        state.dashboardsError = error.message || "Could not load dashboards.";
        reportApiError(error, "Could not load dashboards.");
        state.dashboardsLoaded = true;
    } finally {
        state.dashboardsLoading = false;
        render();
    }
}
function toggleDashboardMenu() {
    state.dashboardMenuOpen = !state.dashboardMenuOpen;
    state.dashboardsError = "";
    render();
    if (state.dashboardMenuOpen && state.token && !state.dashboardsLoaded) {
        void loadDashboards();
    }
}
function openDashboardCreate() {
    state.dashboardCreateOpen = true;
    state.dashboardEditId = "";
    state.dashboardMenuOpen = false;
    state.dashboardsError = "";
    render();
}
function openActiveDashboardEdit() {
    const dashboard = getActiveDashboard();
    if (!dashboard?.id || !state.token) return;
    state.dashboardCreateOpen = true;
    state.dashboardEditId = dashboard.id;
    state.dashboardMenuOpen = false;
    state.dashboardsError = "";
    render();
}
function closeDashboardCreate() {
    if (state.dashboardCreatePending) return;
    state.dashboardCreateOpen = false;
    state.dashboardEditId = "";
    state.dashboardsError = "";
    render();
}
async function selectDashboard(event) {
    const dashboardId = event.currentTarget.dataset.selectDashboard || "";
    if (state.dashboardEditMode) {
        await flushPendingDashboardLayoutSave();
    }
    state.dashboardEditMode = false;
    if (!dashboardId || dashboardId === state.activeDashboardId) {
        state.dashboardMenuOpen = false;
        state.dashboardCreateOpen = false;
        state.dashboardEditId = "";
        render();
        return;
    }
    const previousDashboardId = state.activeDashboardId;
    const previousWidgets = state.dashboardWidgets;
    const previousWidgetsDashboardId = state.dashboardWidgetsDashboardId;
    state.activeDashboardId = dashboardId;
    state.dashboardWidgets = [];
    state.dashboardWidgetsDashboardId = "";
    state.dashboardWidgetsLoading = true;
    state.dashboardMenuOpen = false;
    state.dashboardCreateOpen = false;
    state.dashboardEditId = "";
    render();
    try {
        const response = await fetch("/api/dashboards/active", {
            method: "PUT",
            headers: {
                "Content-Type": "application/json",
                ...authHeaders()
            },
            body: JSON.stringify({
                dashboard_id: dashboardId,
                backend_url: state.backendUrl
            })
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(formatApiResponseError(response, extractErrorMessage(payload)));
        }
        state.activeDashboardId = payload.active_dashboard_id || payload.active_dashboard?.id || dashboardId;
        await loadDashboardWidgets(state.activeDashboardId, { silent: true });
    } catch (error) {
        state.activeDashboardId = previousDashboardId;
        state.dashboardWidgets = previousWidgets;
        state.dashboardWidgetsDashboardId = previousWidgetsDashboardId;
        state.dashboardsError = error.message || "Could not select dashboard.";
        reportApiError(error, "Could not select dashboard.");
    } finally {
        state.dashboardWidgetsLoading = false;
        render();
    }
}
async function createDashboard(event) {
    event.preventDefault();
    if (!state.token || state.dashboardCreatePending) return;
    const editingDashboardId = state.dashboardEditId;
    const form = new FormData(event.currentTarget);
    const name = String(form.get("name") || "").trim();
    const description = String(form.get("description") || "").trim();
    if (!name) {
        state.dashboardsError = "Dashboard name is required.";
        render();
        return;
    }
    state.dashboardCreatePending = true;
    state.dashboardsError = "";
    render();
    try {
        const response = await fetch(
            editingDashboardId
                ? `/api/dashboards/${encodeURIComponent(editingDashboardId)}`
                : "/api/dashboards",
            {
                method: editingDashboardId ? "PUT" : "POST",
                headers: {
                    "Content-Type": "application/json",
                    ...authHeaders()
                },
                body: JSON.stringify({
                    name,
                    description,
                    backend_url: state.backendUrl
                })
            }
        );
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(formatApiResponseError(response, extractErrorMessage(payload)));
        }
        if (payload.item) {
            if (editingDashboardId) {
                state.dashboards = state.dashboards.map((dashboard) => (
                    dashboard.id === payload.item.id ? payload.item : dashboard
                ));
            } else {
                state.dashboards = [
                    payload.item,
                    ...state.dashboards.filter((dashboard) => dashboard.id !== payload.item.id)
                ];
                state.activeDashboardId = payload.active_dashboard_id || payload.active_dashboard?.id || payload.item.id || "";
                state.dashboardEditMode = false;
                state.dashboardWidgets = [];
                state.dashboardWidgetsDashboardId = state.activeDashboardId;
            }
        }
        state.dashboardsLoaded = true;
        state.dashboardCreateOpen = false;
        state.dashboardEditId = "";
        state.dashboardMenuOpen = false;
    } catch (error) {
        state.dashboardsError = error.message || (editingDashboardId ? "Could not update dashboard." : "Could not create dashboard.");
        reportApiError(error, editingDashboardId ? "Could not update dashboard." : "Could not create dashboard.");
    } finally {
        state.dashboardCreatePending = false;
        render();
    }
}
async function deleteDashboard(event) {
    event.stopPropagation();
    const dashboardId = event.currentTarget.dataset.deleteDashboard || "";
    const dashboard = state.dashboards.find((item) => item.id === dashboardId);
    if (!dashboardId || state.dashboardDeletePendingId) return;
    if (!window.confirm(`Delete dashboard "${dashboard?.name || "Untitled dashboard"}"?`)) {
        return;
    }
    if (state.activeDashboardId === dashboardId) {
        state.dashboardEditMode = false;
        state.dashboardLayoutSaving = false;
        if (state.dashboardLayoutSaveTimer) {
            clearTimeout(state.dashboardLayoutSaveTimer);
            state.dashboardLayoutSaveTimer = null;
        }
        state.dashboardLayoutSavePromise = null;
        state.dashboardLayoutSaveSequence += 1;
    }
    state.dashboardDeletePendingId = dashboardId;
    state.dashboardsError = "";
    render();
    try {
        const params = new URLSearchParams({ backend_url: state.backendUrl });
        const response = await fetch(`/api/dashboards/${encodeURIComponent(dashboardId)}?${params.toString()}`, {
            method: "DELETE",
            headers: authHeaders()
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(formatApiResponseError(response, extractErrorMessage(payload)));
        }
        state.dashboards = state.dashboards.filter((item) => item.id !== dashboardId);
        if (state.activeDashboardId === dashboardId) {
            state.activeDashboardId = payload.active_dashboard_id || state.dashboards[0]?.id || "";
            state.dashboardWidgets = [];
            state.dashboardWidgetsDashboardId = "";
            if (state.activeDashboardId) {
                await loadDashboardWidgets(state.activeDashboardId, { silent: true });
            }
        }
    } catch (error) {
        state.dashboardsError = error.message || "Could not delete dashboard.";
        reportApiError(error, "Could not delete dashboard.");
    } finally {
        state.dashboardDeletePendingId = "";
        render();
    }
}
async function loadDashboardWidgets(dashboardId, options = {}) {
    if (!state.token || !dashboardId) {
        state.dashboardWidgets = [];
        state.dashboardWidgetsDashboardId = "";
        return;
    }
    state.dashboardWidgetsLoading = true;
    state.dashboardWidgetsError = "";
    if (!options.silent) {
        render();
    }
    try {
        const params = new URLSearchParams({ backend_url: state.backendUrl });
        const response = await fetch(`/api/dashboards/${encodeURIComponent(dashboardId)}/widgets?${params.toString()}`, {
            headers: authHeaders()
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(formatApiResponseError(response, extractErrorMessage(payload)));
        }
        if (dashboardId === state.activeDashboardId) {
            state.dashboardWidgets = payload.items || [];
            state.dashboardWidgetsDashboardId = dashboardId;
        }
    } catch (error) {
        state.dashboardWidgetsError = error.message || "Could not load dashboard widgets.";
        reportApiError(error, "Could not load dashboard widgets.");
        if (dashboardId === state.activeDashboardId) {
            state.dashboardWidgetsDashboardId = dashboardId;
        }
    } finally {
        state.dashboardWidgetsLoading = false;
        render();
    }
}
async function loadSavedMetrics(options = {}) {
    const includeResult = options.includeResult !== false;
    if (!state.token) {
        state.savedMetrics = [];
        state.savedMetricsLoaded = false;
        state.savedMetricsResultsLoaded = false;
        return;
    }
    state.savedMetricsLoading = true;
    state.savedMetricsError = "";
    if (!options.silent) {
        render();
    }
    try {
        const params = new URLSearchParams({
            backend_url: state.backendUrl,
            include_result: includeResult ? "true" : "false"
        });
        const response = await fetch(`/api/dashboard-metrics?${params.toString()}`, {
            headers: authHeaders()
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(formatApiResponseError(response, extractErrorMessage(payload)));
        }
        const items = payload.items || [];
        state.savedMetrics = includeResult ? items : mergeSavedMetricsPreservingResults(items);
        state.savedMetricsLoaded = true;
        state.savedMetricsResultsLoaded = includeResult || state.savedMetrics.every((metric) => Boolean(metric.result));
        if (!state.dashboardWidgetMetricKey && state.savedMetrics.length) {
            state.dashboardWidgetMetricKey = state.savedMetrics[0].widget_key || "";
            state.dashboardWidgetType = recommendWidgetType(state.savedMetrics[0]);
        }
    } catch (error) {
        state.savedMetricsError = error.message || "Could not load saved metrics.";
        reportApiError(error, "Could not load saved metrics.");
        state.savedMetricsLoaded = true;
    } finally {
        state.savedMetricsLoading = false;
        render();
        if (state.dashboardWidgetDialogOpen && !includeResult && !state.savedMetricsResultsLoaded && state.token) {
            void loadSavedMetrics({ includeResult: true });
        }
    }
}
function mergeSavedMetricsPreservingResults(items) {
    const existingByKey = new Map(state.savedMetrics.map((metric) => [metric.widget_key, metric]));
    return items.map((item) => {
        const existing = existingByKey.get(item.widget_key);
        if (!existing?.result) return item;
        return {
            ...existing,
            ...item,
            result: existing.result
        };
    });
}
function openMetricEditDialog(event) {
    const widgetKey = event.currentTarget.dataset.editMetric || "";
    const metric = state.savedMetrics.find((item) => item.widget_key === widgetKey);
    if (!metric || !widgetKey) return;
    state.metricEditDialogOpen = true;
    state.metricEditWidgetKey = widgetKey;
    state.metricEditDraftLabel = metric.label || metric.widget_key || "";
    state.metricEditPending = false;
    state.metricEditError = "";
    render();
}
function closeMetricEditDialog() {
    if (state.metricEditPending) return;
    state.metricEditDialogOpen = false;
    state.metricEditWidgetKey = "";
    state.metricEditDraftLabel = "";
    state.metricEditError = "";
    render();
}
function changeMetricEditLabel(event) {
    state.metricEditDraftLabel = event.currentTarget.value || "";
}
function applySavedMetricDelete(widgetKey) {
    state.savedMetrics = state.savedMetrics.filter((metric) => metric.widget_key !== widgetKey);
    state.dashboardWidgets = state.dashboardWidgets.filter(
        (widget) => widget.metric_widget_key !== widgetKey
    );
    if (state.dashboardWidgetMetricKey === widgetKey) {
        state.dashboardWidgetMetricKey = state.savedMetrics[0]?.widget_key || "";
        state.dashboardWidgetType = state.savedMetrics[0]
            ? recommendWidgetType(state.savedMetrics[0])
            : "";
    }
    if (state.metricEditWidgetKey === widgetKey) {
        state.metricEditDialogOpen = false;
        state.metricEditWidgetKey = "";
        state.metricEditDraftLabel = "";
        state.metricEditError = "";
    }
}
function applySavedMetricUpdate(metric) {
    if (!metric?.widget_key) return;
    state.savedMetrics = state.savedMetrics.map((item) => (
        item.widget_key === metric.widget_key
            ? { ...item, ...metric, result: metric.result || item.result }
            : item
    ));
    state.dashboardWidgets = state.dashboardWidgets.map((widget) => {
        if (widget.metric_widget_key !== metric.widget_key || !widget.metric) {
            return widget;
        }
        const nextMetric = {
            ...widget.metric,
            ...metric,
            result: metric.result || widget.metric.result
        };
        return {
            ...widget,
            metric: nextMetric,
            result: nextMetric.result || widget.result
        };
    });
}
async function deleteSavedMetric(event) {
    const widgetKey = event.currentTarget.dataset.deleteMetric || "";
    if (!state.token || !widgetKey || state.metricDeletePendingKey) return;
    if (!window.confirm("Deleting this metric will also remove it from all dashboards.")) {
        return;
    }
    state.metricDeletePendingKey = widgetKey;
    state.savedMetricsError = "";
    render();
    const params = new URLSearchParams({ backend_url: state.backendUrl });
    try {
        const response = await fetch(
            `/api/dashboard-metrics/${encodeURIComponent(widgetKey)}?${params.toString()}`,
            {
                method: "DELETE",
                headers: authHeaders()
            }
        );
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(formatApiResponseError(response, extractErrorMessage(payload)));
        }
        applySavedMetricDelete(widgetKey);
    } catch (error) {
        state.savedMetricsError = error.message || "Metric could not be deleted.";
        reportApiError(error, "Metric could not be deleted.");
    } finally {
        state.metricDeletePendingKey = "";
        render();
    }
}
async function saveMetricLabel(event) {
    event.preventDefault();
    if (!state.token || state.metricEditPending) return;
    const form = new FormData(event.currentTarget);
    const label = String(form.get("label") || "").trim();
    const widgetKey = state.metricEditWidgetKey;
    if (!widgetKey || !label) {
        state.metricEditError = "Enter a metric name.";
        render();
        return;
    }
    state.metricEditPending = true;
    state.metricEditError = "";
    state.metricEditDraftLabel = label;
    render();
    try {
        const response = await fetch(`/api/dashboard-metrics/${encodeURIComponent(widgetKey)}`, {
            method: "PATCH",
            headers: {
                "Content-Type": "application/json",
                ...authHeaders()
            },
            body: JSON.stringify({
                label,
                backend_url: state.backendUrl
            })
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(formatApiResponseError(response, extractErrorMessage(payload)));
        }
        if (payload.item) {
            applySavedMetricUpdate(payload.item);
        }
        state.metricEditDialogOpen = false;
        state.metricEditWidgetKey = "";
        state.metricEditDraftLabel = "";
        state.metricEditError = "";
    } catch (error) {
        state.metricEditError = error.message || "Metric name could not be saved.";
        reportApiError(error, "Metric name could not be saved.");
    } finally {
        state.metricEditPending = false;
        render();
    }
}
function openDashboardWidgetDialog() {
    if (!state.dashboardEditMode) return;
    if (!state.token) {
        openLogin();
        return;
    }
    if (!state.activeDashboardId) {
        state.dashboardWidgetsError = "Create a dashboard before adding widgets.";
        render();
        return;
    }
    state.dashboardWidgetDialogOpen = true;
    state.dashboardWidgetsError = "";
    render();
    if ((!state.savedMetricsLoaded || !state.savedMetricsResultsLoaded) && !state.savedMetricsLoading) {
        void loadSavedMetrics({ includeResult: true });
    } else if (state.savedMetrics.length && !state.dashboardWidgetMetricKey) {
        state.dashboardWidgetMetricKey = state.savedMetrics[0].widget_key || "";
        state.dashboardWidgetType = recommendWidgetType(state.savedMetrics[0]);
        render();
    }
}
function closeDashboardWidgetDialog() {
    if (state.dashboardWidgetPending) return;
    state.dashboardWidgetDialogOpen = false;
    state.dashboardWidgetsError = "";
    render();
}
function changeDashboardWidgetMetric(event) {
    state.dashboardWidgetMetricKey = event.currentTarget.value || "";
    const metric = getSelectedSavedMetric();
    state.dashboardWidgetType = recommendWidgetType(metric);
    render();
}
function changeDashboardWidgetType(event) {
    state.dashboardWidgetType = event.currentTarget.value || "";
    render();
}
async function addDashboardWidget(event) {
    event.preventDefault();
    if (!state.dashboardEditMode) return;
    if (!state.token || state.dashboardWidgetPending || !state.activeDashboardId) return;
    const form = new FormData(event.currentTarget);
    const metricWidgetKey = String(form.get("metric_widget_key") || "").trim();
    const title = String(form.get("title") || "").trim();
    const selectedMetric = state.savedMetrics.find((metric) => metric.widget_key === metricWidgetKey);
    const availableTypes = getAvailableWidgetTypes(selectedMetric);
    const visualizationType = normalizeDashboardWidgetType(String(form.get("visualization_type") || ""), availableTypes);
    if (!metricWidgetKey || !title) {
        state.dashboardWidgetsError = "Choose a saved metric and name the widget.";
        render();
        return;
    }
    state.dashboardWidgetPending = true;
    state.dashboardWidgetsError = "";
    render();
    try {
        const response = await fetch(`/api/dashboards/${encodeURIComponent(state.activeDashboardId)}/widgets`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...authHeaders()
            },
            body: JSON.stringify({
                metric_widget_key: metricWidgetKey,
                title,
                visualization_type: visualizationType,
                backend_url: state.backendUrl
            })
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(formatApiResponseError(response, extractErrorMessage(payload)));
        }
        if (payload.item) {
            state.dashboardWidgets = [...state.dashboardWidgets, payload.item];
            state.dashboardWidgetsDashboardId = state.activeDashboardId;
        }
        await loadDashboardWidgets(state.activeDashboardId, { silent: true });
        state.dashboardWidgetDialogOpen = false;
    } catch (error) {
        state.dashboardWidgetsError = error.message || "Could not add widget.";
        reportApiError(error, "Could not add widget.");
    } finally {
        state.dashboardWidgetPending = false;
        render();
    }
}
async function deleteDashboardWidget(event) {
    if (!state.dashboardEditMode) return;
    const widgetId = event.currentTarget.dataset.deleteDashboardWidget || "";
    if (!widgetId || !state.activeDashboardId) return;
    if (!window.confirm("Remove this widget from the dashboard?")) {
        return;
    }
    try {
        const params = new URLSearchParams({ backend_url: state.backendUrl });
        const response = await fetch(`/api/dashboards/${encodeURIComponent(state.activeDashboardId)}/widgets/${encodeURIComponent(widgetId)}?${params.toString()}`, {
            method: "DELETE",
            headers: authHeaders()
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(formatApiResponseError(response, extractErrorMessage(payload)));
        }
        state.dashboardWidgets = state.dashboardWidgets.filter((widget) => widget.id !== widgetId);
    } catch (error) {
        state.dashboardWidgetsError = error.message || "Could not remove widget.";
        reportApiError(error, "Could not remove widget.");
    } finally {
        render();
    }
}
function scheduleDashboardLayoutSave() {
    if (!state.dashboardEditMode) return;
    if (!canPersistDashboardLayout()) return;
    const dashboardId = state.activeDashboardId;
    if (state.dashboardLayoutSaveTimer) {
        clearTimeout(state.dashboardLayoutSaveTimer);
    }
    state.dashboardLayoutSaveTimer = setTimeout(() => {
        void saveDashboardLayout(dashboardId);
    }, 600);
}
async function saveDashboardLayout(dashboardId = state.activeDashboardId) {
    if (!dashboardId || dashboardId !== state.activeDashboardId || !state.token) return;
    if (!canPersistDashboardLayout()) return;
    const items = collectDashboardLayout();
    if (!items.length) return;
    state.dashboardLayoutSaveTimer = null;
    const saveSequence = ++state.dashboardLayoutSaveSequence;
    const savePromise = (async () => {
        const response = await fetch(`/api/dashboards/${encodeURIComponent(dashboardId)}/widgets/layout`, {
            method: "PATCH",
            headers: {
                "Content-Type": "application/json",
                ...authHeaders()
            },
            body: JSON.stringify({
                items,
                backend_url: state.backendUrl
            })
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(formatApiResponseError(response, extractErrorMessage(payload)));
        }
        if (saveSequence !== state.dashboardLayoutSaveSequence) return;
        if (dashboardId !== state.activeDashboardId) return;
        const layoutById = new Map(items.map((item) => [item.widget_id, item]));
        (payload.items || []).forEach((updatedWidget) => {
            if (updatedWidget?.id && updatedWidget.layout) {
                layoutById.set(updatedWidget.id, updatedWidget.layout);
            }
        });
        state.dashboardWidgets = state.dashboardWidgets.map((widget) => {
            const layout = layoutById.get(widget.id);
            if (!layout) {
                return widget;
            }
            return {
                ...widget,
                layout: {
                    x: layout.x,
                    y: layout.y,
                    w: layout.w,
                    h: layout.h
                }
            };
        });
    })();
    state.dashboardLayoutSavePromise = savePromise;
    try {
        await savePromise;
    } catch (error) {
        state.dashboardWidgetsError = error.message || "Could not save widget layout.";
        reportApiError(error, "Could not save widget layout.");
        render();
    } finally {
        if (state.dashboardLayoutSavePromise === savePromise) {
            state.dashboardLayoutSavePromise = null;
        }
    }
}
async function flushPendingDashboardLayoutSave() {
    if (state.dashboardLayoutSaveTimer) {
        clearTimeout(state.dashboardLayoutSaveTimer);
        state.dashboardLayoutSaveTimer = null;
        await saveDashboardLayout(state.activeDashboardId);
    }
    if (state.dashboardLayoutSavePromise) {
        try {
            await state.dashboardLayoutSavePromise;
        } catch {
            // saveDashboardLayout reports the error; this keeps UI transitions from hanging.
        }
    }
}
async function toggleDashboardEditMode() {
    if (!state.token || !getActiveDashboard()?.id || state.dashboardLayoutSaving) return;
    if (state.dashboardEditMode) {
        setDashboardLayoutSaving(true);
        await flushPendingDashboardLayoutSave();
        state.dashboardEditMode = false;
        state.dashboardLayoutSaving = false;
    } else {
        state.dashboardEditMode = true;
    }
    render();
}
function setDashboardLayoutSaving(saving) {
    state.dashboardLayoutSaving = saving;
    const button = document.querySelector("[data-toggle-dashboard-edit]");
    if (!button) return;
    button.disabled = saving;
    button.classList.toggle("saving", saving);
    button.setAttribute("aria-label", saving ? "Saving dashboard layout" : "Finish editing dashboard layout");
    button.setAttribute("title", saving ? "Saving..." : "Finish editing");
    button.innerHTML = saving
        ? `<span class="dashboard-edit-saving-spinner" aria-hidden="true"></span><span>Saving...</span>`
        : renderIcon("edit");
}
function collectDashboardLayout() {
    return Array.from(document.querySelectorAll(".dashboard-grid .grid-stack-item")).map((element) => {
        const node = element.gridstackNode || {};
        return {
            widget_id: element.getAttribute("gs-id") || node.id || "",
            x: Number.isFinite(node.x) ? node.x : Number(element.getAttribute("gs-x") || 0),
            y: Number.isFinite(node.y) ? node.y : Number(element.getAttribute("gs-y") || 0),
            w: Number.isFinite(node.w) ? node.w : Number(element.getAttribute("gs-w") || 6),
            h: Number.isFinite(node.h) ? node.h : Number(element.getAttribute("gs-h") || 4)
        };
    }).filter((item) => item.widget_id);
}
function canPersistDashboardLayout(grid = state.dashboardGrid) {
    return Boolean(grid) && Number(grid.getColumn?.()) === 12;
}
function openSourcePicker() {
    if (state.datasourceUploadPending) return;
    if (!state.token) {
        openLogin();
        return;
    }
    const input = document.querySelector("#excel-source-input");
    if (!input) return;
    input.value = "";
    input.click();
}
function mergeDatasourcesPreservingOrder(currentItems, nextItems) {
    const nextById = new Map(nextItems.map((item) => [String(item.id), item]));
    const seen = new Set();
    const merged = currentItems.reduce((items, item) => {
        const key = String(item.id);
        const next = nextById.get(key);
        if (!next) return items;
        seen.add(key);
        items.push(next);
        return items;
    }, []);
    nextItems.forEach((item) => {
        const key = String(item.id);
        if (!seen.has(key)) {
            merged.push(item);
        }
    });
    return merged;
}
async function loadDatasources(options = {}) {
    state.datasourcesLoading = true;
    state.datasourceError = "";
    render();
    try {
        const response = await fetch(`/api/datasources?backend_url=${encodeURIComponent(state.backendUrl)}`, {
            headers: authHeaders()
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(formatApiResponseError(response, extractErrorMessage(payload)));
        }
        const items = payload.items || [];
        state.datasources = options.preserveOrder ? mergeDatasourcesPreservingOrder(state.datasources, items) : items;
        const availableDatasourceIds = new Set(items.map((item) => item.connector_key));
        state.selectedDatasourceIds = (payload.selected_datasource_ids || []).filter((id) => availableDatasourceIds.has(id));
        state.multipleDatasourceSelectionAllowed = Boolean(payload.multiple_selection_allowed);
        state.datasourcesLoaded = true;
    } catch (error) {
        state.datasourceError = error.message || "Could not load data sources.";
        reportApiError(error, "Could not load data sources.");
        state.datasourcesLoaded = true;
    } finally {
        state.datasourcesLoading = false;
        render();
    }
}
async function uploadSelectedSource(event) {
    const input = event.currentTarget;
    const file = input.files?.[0];
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".xlsx")) {
        state.datasourceError = "Choose an .xlsx file.";
        render();
        return;
    }
    const formData = new FormData();
    formData.append("file", file);
    state.datasourceUploadPending = true;
    state.datasourceError = "";
    render();
    try {
        const params = new URLSearchParams({
            backend_url: state.backendUrl,
            active: "false"
        });
        const response = await fetch(`/api/datasources/excel?${params.toString()}`, {
            method: "POST",
            headers: authHeaders(),
            body: formData
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(friendlyDatasourceError(formatApiResponseError(response, extractErrorMessage(payload))));
        }
        if (payload.item) {
            state.datasources = mergeDatasourcesPreservingOrder(state.datasources, [
                ...state.datasources,
                payload.item
            ]);
        }
        state.datasourcesLoaded = false;
        await loadDatasources({ preserveOrder: true });
    } catch (error) {
        state.datasourceError = error.message || "Could not add the data source.";
        reportApiError(error, "Could not add the data source.");
    } finally {
        state.datasourceUploadPending = false;
        input.value = "";
        render();
    }
}
async function updateSelectedSources(event) {
    const input = event.currentTarget;
    const datasourceId = input.dataset.sourceSelected;
    if (!datasourceId || state.datasourceSelectionPending) return;
    const availableDatasourceIds = new Set(state.datasources.map((item) => item.connector_key));
    const currentSelectedIds = state.selectedDatasourceIds.filter((id) => availableDatasourceIds.has(id));
    let selectedIds = input.checked
      ? [...currentSelectedIds, datasourceId]
      : currentSelectedIds.filter((item) => item !== datasourceId);
    selectedIds = [...new Set(selectedIds)];
    if (selectedIds.length > 1 && !state.multipleDatasourceSelectionAllowed) {
      input.checked = false;
      state.datasourceError = "Select one datasource unless multi-datasource access is enabled.";
      render();
      return;
    }
    state.datasourceSelectionPending = true;
    state.datasourceError = "";
    render();
    try {
        const response = await fetch("/api/datasources/selection", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...authHeaders()
            },
            body: JSON.stringify({
                datasource_ids: selectedIds,
                backend_url: state.backendUrl
            })
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(friendlyDatasourceError(formatApiResponseError(response, extractErrorMessage(payload))));
        }
        state.selectedDatasourceIds = payload.selected_datasource_ids || selectedIds;
        state.multipleDatasourceSelectionAllowed = Boolean(payload.multiple_selection_allowed);
    } catch (error) {
        state.datasourceError = error.message || "Could not update selected sources.";
        reportApiError(error, "Could not update selected sources.");
    } finally {
        state.datasourceSelectionPending = false;
        render();
    }
}
function friendlyDatasourceError(message) {
    if (message.includes("non-SQL source support") || message.includes("LICENSE_ENTITLEMENT_REQUIRED")) {
        return "This license does not allow Excel files to be used as data sources.";
    }
    if (message.includes("multi-source access")) {
        return "Using multiple active data sources requires a license with multi-source support.";
    }
    return message;
}
async function changeView(event) {
  const view = normalizeView(event.currentTarget.dataset.view);
  if (state.activeView === view) return;
  if (state.activeView === "analysis" && state.dashboardEditMode) {
    await flushPendingDashboardLayoutSave();
  }
  state.dashboardEditMode = false;
  state.dashboardGrid = null;
  state.activeView = view;
  rememberActiveView(view);
  state.error = "";
  state.dashboardMenuOpen = false;
  state.dashboardCreateOpen = false;
  state.dashboardEditId = "";
  render();
}
function initAnalysisDashboard() {
  if (state.activeView !== "analysis") return;
  state.dashboardGrid = null;
  const gridElement = document.querySelector(".dashboard-grid");
  if (!gridElement) return;
  if (window.GridStack) {
    const grid = window.GridStack.init(
      {
        cellHeight: 94,
        column: 12,
        columnOpts: {
          breakpointForWindow: false,
          breakpoints: [
            {
              w: 700,
              c: 1,
              layout: "list"
            },
            {
              w: 1100,
              c: 6,
              layout: "moveScale"
            }
          ]
        },
        float: false,
        margin: 12,
        alwaysShowResizeHandle: state.dashboardEditMode,
        staticGrid: false,
        resizable: { handles: "e,se,s,sw,w" }
      },
      gridElement
    );
    state.dashboardGrid = grid;
    if (state.dashboardEditMode) {
      grid.enable?.();
    } else {
      grid.disable?.();
    }
    grid.off?.("dragstop resizestop");
    grid.on?.("dragstop resizestop", () => {
      if (state.dashboardEditMode) {
        scheduleDashboardLayoutSave();
      }
    });
  } else {
    document.querySelector("[data-dashboard-fallback]")?.removeAttribute("hidden");
  }
  if (window.echarts) {
    renderDashboardCharts();
  } else {
    document.querySelector("[data-dashboard-fallback]")?.removeAttribute("hidden");
  }
}
function renderDashboardCharts() {
  state.dashboardWidgets.forEach((widget) => {
    if (["number", "table"].includes(widget.visualization_type)) return;
    const element = document.querySelector(`[data-dashboard-widget-chart="${selectorAttributeValue(widget.id)}"]`);
    if (!element) return;
    const options = dashboardWidgetChartOptions(widget);
    if (!options) {
      element.innerHTML = `<div class="dashboard-widget-error">This data cannot be displayed as ${escapeHtml(widget.visualization_type)}.</div>`;
      return;
    }
    const chart = window.echarts.init(element, null, { renderer: "canvas" });
    chart.setOption(options);
    window.addEventListener("resize", () => chart.resize(), { passive: true });
    const gridItem = element.closest(".grid-stack-item");
    if (gridItem && window.ResizeObserver) {
      new ResizeObserver(() => chart.resize()).observe(gridItem);
    }
  });
}
function selectorAttributeValue(value) {
  return String(value ?? "").replaceAll("\\", "\\\\").replaceAll('"', '\\"');
}
function dashboardWidgetChartOptions(widget) {
  const result = widget.result || widget.metric?.result || {};
  const profile = getTabularProfile(result);
  const { rows, numericColumns, dimensionColumns } = profile;
  const categoryColumn = dimensionColumns[0] || profile.columns[0];
  if (!rows.length || !categoryColumn) return null;
  if (widget.visualization_type === "pie") {
    const valueColumn = numericColumns[0];
    if (!valueColumn) return null;
    return pieWidgetOptions(aggregateRowsByCategory(rows, categoryColumn, valueColumn), "category", "value");
  }
  const seriesColumns = numericColumns.length ? numericColumns : profile.columns.filter((column) => column !== categoryColumn);
  if (!seriesColumns.length) return null;
  if (widget.visualization_type === "bar") {
    const axisRows = profile.hasLongSeries
      ? aggregateRowsByCategory(rows, categoryColumn, numericColumns[0])
      : rows;
    return axisWidgetOptions(axisRows, profile.hasLongSeries ? "category" : categoryColumn, profile.hasLongSeries ? ["value"] : seriesColumns, "bar");
  }
  if (widget.visualization_type === "stacked_bar") {
    if (profile.hasLongSeries) {
      return longSeriesWidgetOptions(rows, dimensionColumns[0], dimensionColumns[1], numericColumns[0], "bar", { stacked: true });
    }
    return axisWidgetOptions(rows, categoryColumn, seriesColumns, "bar", { stacked: true });
  }
  if (widget.visualization_type === "line") {
    const axisRows = profile.hasLongSeries
      ? aggregateRowsByCategory(rows, categoryColumn, numericColumns[0])
      : rows;
    return axisWidgetOptions(axisRows, profile.hasLongSeries ? "category" : categoryColumn, profile.hasLongSeries ? ["value"] : seriesColumns.slice(0, 1), "line");
  }
  if (widget.visualization_type === "multi_line") {
    if (profile.hasLongSeries) {
      return longSeriesWidgetOptions(rows, dimensionColumns[0], dimensionColumns[1], numericColumns[0], "line");
    }
    return axisWidgetOptions(rows, categoryColumn, seriesColumns, "line");
  }
  if (widget.visualization_type === "area") {
    const axisRows = profile.hasLongSeries
      ? aggregateRowsByCategory(rows, categoryColumn, numericColumns[0])
      : rows;
    return axisWidgetOptions(axisRows, profile.hasLongSeries ? "category" : categoryColumn, profile.hasLongSeries ? ["value"] : seriesColumns.slice(0, 1), "line", { area: true });
  }
  return null;
}
function longSeriesWidgetOptions(rows, categoryColumn, seriesColumn, valueColumn, type, options = {}) {
  const categories = uniqueValues(rows.map((row) => formatCellValue(row?.[categoryColumn])));
  const seriesNames = uniqueValues(rows.map((row) => formatCellValue(row?.[seriesColumn])));
  const sums = new Map();
  rows.forEach((row) => {
    const category = formatCellValue(row?.[categoryColumn]);
    const series = formatCellValue(row?.[seriesColumn]);
    const key = `${category}\u0000${series}`;
    sums.set(key, (sums.get(key) || 0) + (Number(row?.[valueColumn]) || 0));
  });
  return {
    animationDuration: 650,
    color: ["#2368d9", "#19a7a8", "#7aaeea", "#5b8ee8", "#6cc6d8"],
    tooltip: { trigger: "axis", axisPointer: { type: type === "bar" ? "shadow" : "line" } },
    legend: { right: 8, top: 6, textStyle: chartTextStyle() },
    grid: { left: 42, right: 20, top: 42, bottom: 32 },
    xAxis: {
      type: "category",
      boundaryGap: type === "bar",
      data: categories,
      axisLabel: chartTextStyle(),
      axisTick: { show: false }
    },
    yAxis: { type: "value", axisLabel: chartTextStyle(), splitLine: { lineStyle: { color: "#edf1f4" } } },
    series: seriesNames.map((series) => ({
      name: series,
      type,
      smooth: type === "line",
      stack: options.stacked ? "total" : void 0,
      data: categories.map((category) => sums.get(`${category}\u0000${series}`) || 0)
    }))
  };
}
function axisWidgetOptions(rows, categoryColumn, seriesColumns, type, options = {}) {
  return {
    animationDuration: 650,
    color: ["#2368d9", "#19a7a8", "#7aaeea", "#5b8ee8", "#6cc6d8"],
    tooltip: { trigger: "axis", axisPointer: { type: type === "bar" ? "shadow" : "line" } },
    legend: { right: 8, top: 6, textStyle: chartTextStyle() },
    grid: { left: 42, right: 20, top: 42, bottom: 32 },
    xAxis: {
      type: "category",
      boundaryGap: type === "bar",
      data: rows.map((row) => formatCellValue(row?.[categoryColumn])),
      axisLabel: chartTextStyle(),
      axisTick: { show: false }
    },
    yAxis: { type: "value", axisLabel: chartTextStyle(), splitLine: { lineStyle: { color: "#edf1f4" } } },
    series: seriesColumns.map((column) => ({
      name: column,
      type,
      smooth: type === "line",
      stack: options.stacked ? "total" : void 0,
      areaStyle: options.area ? { color: "rgba(35, 104, 217, 0.1)" } : void 0,
      data: rows.map((row) => Number(row?.[column]) || 0)
    }))
  };
}
function aggregateRowsByCategory(rows, categoryColumn, valueColumn) {
  const sums = new Map();
  rows.forEach((row) => {
    const category = formatCellValue(row?.[categoryColumn]);
    sums.set(category, (sums.get(category) || 0) + (Number(row?.[valueColumn]) || 0));
  });
  return Array.from(sums, ([category, value]) => ({ category, value }));
}
function uniqueValues(values) {
  const seen = new Set();
  return values.filter((value) => {
    if (seen.has(value)) return false;
    seen.add(value);
    return true;
  });
}
function pieWidgetOptions(rows, labelColumn, valueColumn) {
  return {
    animationDuration: 650,
    color: ["#2368d9", "#19a7a8", "#7aaeea", "#5b8ee8", "#6cc6d8"],
    tooltip: { trigger: "item" },
    legend: { bottom: 0, textStyle: chartTextStyle() },
    series: [
      {
        type: "pie",
        radius: ["42%", "70%"],
        center: ["50%", "44%"],
        data: rows.map((row) => ({
          name: formatCellValue(row?.[labelColumn]),
          value: Number(row?.[valueColumn]) || 0
        }))
      }
    ]
  };
}
function chartTextStyle() {
  return {
    color: "#64707d",
    fontFamily: "Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
  };
}
function capacityOptions() {
  const days = ["May 6", "May 7", "May 8", "May 9", "May 10", "May 11", "May 12"];
  return {
    animationDuration: 700,
    color: ["#19b2b4", "#2d6cdf", "#5b8ee8", "#7aaeea", "#6cc6d8"],
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    legend: {
      right: 2,
      top: 8,
      orient: "vertical",
      textStyle: chartTextStyle()
    },
    grid: { left: 44, right: 112, top: 26, bottom: 28 },
    xAxis: { type: "category", data: days, axisLabel: chartTextStyle(), axisTick: { show: false } },
    yAxis: { type: "value", axisLabel: chartTextStyle(), splitLine: { lineStyle: { color: "#edf1f4" } } },
    series: [
      { name: "Imaging", type: "bar", stack: "total", data: [430, 510, 470, 520, 500, 480, 430] },
      { name: "Lab tests", type: "bar", stack: "total", data: [280, 320, 260, 350, 330, 300, 250] },
      { name: "Surgery", type: "bar", stack: "total", data: [270, 245, 265, 260, 280, 240, 220] },
      { name: "Cardiology", type: "bar", stack: "total", data: [310, 330, 340, 360, 370, 350, 320] },
      { name: "Other", type: "bar", stack: "total", data: [120, 130, 110, 130, 140, 125, 105] }
    ]
  };
}
function flowOptions() {
  const days = ["May 6", "May 7", "May 8", "May 9", "May 10", "May 11", "May 12"];
  return {
    animationDuration: 700,
    color: ["#2d6cdf", "#6a93d5"],
    tooltip: { trigger: "axis" },
    legend: { right: 8, top: 6, textStyle: chartTextStyle() },
    grid: { left: 42, right: 18, top: 40, bottom: 30 },
    xAxis: { type: "category", boundaryGap: false, data: days, axisLabel: chartTextStyle(), axisTick: { show: false } },
    yAxis: { type: "value", axisLabel: chartTextStyle(), splitLine: { lineStyle: { color: "#edf1f4" } } },
    series: [
      { name: "Arrivals", type: "line", smooth: true, symbolSize: 7, data: [500, 650, 590, 760, 620, 700, 640] },
      { name: "Discharges", type: "line", smooth: true, symbolSize: 6, lineStyle: { type: "dashed" }, data: [360, 380, 420, 450, 390, 470, 430] }
    ]
  };
}
function episodesOptions() {
  return {
    animationDuration: 700,
    color: ["#2d6cdf"],
    grid: { left: 34, right: 18, top: 10, bottom: 24 },
    xAxis: { type: "category", boundaryGap: false, data: ["May 6", "May 7", "May 8", "May 9", "May 10", "May 11", "May 12"], axisLabel: chartTextStyle(), axisTick: { show: false } },
    yAxis: { type: "value", axisLabel: chartTextStyle(), splitLine: { lineStyle: { color: "#edf1f4" } } },
    series: [
      {
        type: "line",
        smooth: true,
        symbolSize: 7,
        data: [220, 430, 360, 500, 390, 610, 470],
        areaStyle: { color: "rgba(45, 108, 223, 0.08)" }
      }
    ]
  };
}
function renderLoginDialog() {
  return `
    <div class="login-overlay" role="presentation">
      <section class="login-panel" role="dialog" aria-modal="true" aria-labelledby="login-title">
        <button class="icon-button close-login" type="button" data-close-login aria-label="Close login" title="Close">
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            <path d="M18 6 6 18" />
            <path d="m6 6 12 12" />
          </svg>
        </button>
        <img class="login-logo" src="/assets/getgaard.svg" alt="" />
        <h1 id="login-title">GAARD Client</h1>
        <p>Log in with your GAARD account.</p>
        <form id="login-form" class="form-grid">
          <label>Username<input name="username" autocomplete="username" /></label>
          <label>Password<input name="password" type="password" autocomplete="current-password" /></label>
          ${state.error ? `<div class="error">${escapeHtml(state.error)}</div>` : ""}
          <div class="form-actions"><button class="primary" type="submit">Log in</button></div>
        </form>
      </section>
    </div>`;
}
function renderPasswordChangeDialog() {
  return `
    <div class="login-overlay" role="presentation">
      <section class="login-panel" role="dialog" aria-modal="true" aria-labelledby="password-change-title">
        <img class="login-logo" src="/assets/getgaard.svg" alt="" />
        <h1 id="password-change-title">Set a new password</h1>
        <p>Your temporary password can only be used to sign in once.</p>
        <form id="password-change-form" class="form-grid">
          <label>Temporary password<input name="current_password" type="password" autocomplete="current-password" required /></label>
          <label>New password<input name="new_password" type="password" minlength="8" autocomplete="new-password" required /></label>
          <label>Confirm new password<input name="confirm_password" type="password" minlength="8" autocomplete="new-password" required /></label>
          ${state.passwordChangeError ? `<div class="error" role="alert">${escapeHtml(state.passwordChangeError)}</div>` : ""}
          <div class="form-actions"><button class="primary" type="submit">Save password</button></div>
        </form>
      </section>
    </div>`;
}
function renderLogin() {
  state.loginOpen = true;
  render();
}
function openLogin() {
  state.error = "";
  state.loginOpen = true;
  render();
}
function closeLogin() {
  state.error = "";
  state.loginOpen = false;
  render();
}
function newChat() {
  if (state.pending) return;
  state.messages = [];
  state.conversationId = "";
  state.error = "";
  state.activeView = "home";
  rememberActiveView(state.activeView);
  render();
}
function renderMessage(message) {
  const rows = getRows(message.response);
  const meta = message.status === "ok" ? renderMeta(message, rows) : "";
  const answer = message.status === "pending" ? (message.processingStage || "Processing query..") : message.status === "waiting" ? "Waiting for your answer." : message.status === "error" ? message.error : message.response?.answer || "";
  const dataTable = message.status === "ok" && message.dataOpen ? renderDataTable(rows) : "";
  const mockWarning = message.status === "ok" ? renderMockWarning(message.response?.metadata) : "";
  const saveNotice = renderSaveNotice(message);
  const explanation = renderAnswerExplanation(message);
  const progress = message.mode === "analysis" ? renderAnalysisProgress(message) : "";
  const analysisReply = message.status === "waiting" ? renderAnalysisReply(message) : "";
  return `
    <article class="exchange ${message.status}">
      <div class="exchange-top">
        <div class="question">
          <span>Question \xB7 ${escapeHtml(formatMode(message.mode))}</span>
          <p>${escapeHtml(message.question)}</p>
        </div>
        ${renderMessageActions(message)}
      </div>
      <div class="answer">
        <span>Answer</span>
        ${renderMarkdown(answer)}
      </div>
      ${progress}
      ${analysisReply}
      ${mockWarning}
      ${saveNotice}
      ${explanation}
      ${meta}
      ${dataTable}
    </article>`;
}
function renderMessageActions(message) {
  const saveDialogOpen = state.saveWidgetDialogOpen && state.saveWidgetMessageId === message.id;
  const saveDisabled = state.pending || saveDialogOpen || message.saveStatus === "saving" || message.saveStatus === "saved";
  const saveTitle = message.saveStatus === "saved" ? "Saved as widget" : saveDialogOpen || message.saveStatus === "saving" ? "Saving widget" : "Save as widget";
  const explainDisabled = state.pending || message.explanationStatus === "loading";
  const explainTitle = message.explanationStatus === "ok" ? "Refresh explanation" : message.explanationStatus === "loading" ? "Explaining answer" : "Explain answer";
  return `
    <div class="message-actions">
      <button class="retry-button" type="button" data-retry-question="${message.id}" aria-label="Copy question to input" title="Retry question" ${state.pending ? "disabled" : ""}>
        <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <path d="M3 12a9 9 0 1 0 2.64-6.36L3 8" />
          <path d="M3 3v5h5" />
        </svg>
      </button>
      ${canSaveWidget(message) ? `
        <button class="save-widget-button" type="button" data-save-widget="${message.id}" aria-label="Save question as widget" title="${escapeHtml(saveTitle)}" ${saveDisabled ? "disabled" : ""}>
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z" />
            <path d="M17 21v-8H7v8" />
            <path d="M7 3v5h8" />
          </svg>
        </button>` : ""}
      ${canExplainAnswer(message) ? `
        <button class="explain-answer-button" type="button" data-explain-answer="${message.id}" aria-label="Explain answer" title="${escapeHtml(explainTitle)}" ${explainDisabled ? "disabled" : ""}>
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            <circle cx="12" cy="12" r="9" />
            <path d="M9.6 9a2.7 2.7 0 0 1 4.8 1.7c0 1.8-2.4 2.2-2.4 3.8" />
            <path d="M12 18h.01" />
          </svg>
        </button>` : ""}
    </div>`;
}
function canSaveWidget(message) {
  return message.status === "ok" && Boolean(message.response?.sql?.trim());
}
function canExplainAnswer(message) {
  return message.status === "ok" && Boolean(message.response?.sql?.trim());
}
function renderSaveNotice(message) {
  if (message.saveStatus === "saved") {
    return `<div class="save-notice success" role="status">Saved as inactive widget.</div>`;
  }
  if (message.saveStatus === "error") {
    return `<div class="save-notice error" role="alert">${escapeHtml(message.saveError || "Widget could not be saved.")}</div>`;
  }
  return "";
}
function renderAnswerExplanation(message) {
  if (message.explanationStatus === "loading") {
    return `<div class="answer-explanation loading" role="status">Preparing explanation...</div>`;
  }
  if (message.explanationStatus === "error") {
    return `<div class="answer-explanation error" role="alert">${escapeHtml(message.explanationError || "Explanation could not be prepared.")}</div>`;
  }
  if (message.explanationStatus === "ok" && message.explanation) {
    return `
      <section class="answer-explanation" aria-label="Answer explanation">
        <span>Explanation</span>
        ${renderMarkdown(message.explanation)}
      </section>`;
  }
  return "";
}
function renderAnalysisProgress(message) {
  if (!message.progress.length) {
    return "";
  }
  const latest = message.progress[message.progress.length - 1];
  return `
    <details class="analysis-log" data-analysis-progress="${message.id}" ${message.progressOpen ? "open" : ""}>
      <summary>
        <span>Analysis</span>
        <strong>${renderInlineMarkdown(latest.title)}</strong>
        ${latest.detail ? `<small>${renderInlineMarkdown(latest.detail)}</small>` : ""}
      </summary>
      <ol class="analysis-progress" aria-label="Analysis progress">
        ${message.progress.map((update, index) => `
          <li class="${index === message.progress.length - 1 ? "active" : "done"}">
            <div>
              <p>${renderInlineMarkdown(update.title)}</p>
              ${update.detail ? `<div class="progress-detail">${renderMarkdown(update.detail)}</div>` : ""}
              ${renderProgressDecisions(update.items)}
            </div>
          </li>`).join("")}
      </ol>
    </details>`;
}
function renderAnalysisReply(message) {
  return `
    <form class="analysis-reply" data-analysis-reply-form="${message.id}">
      <div class="analysis-reply-question">${renderMarkdown(message.userQuestion || "GAARD needs a clarification.")}</div>
      <label>
        <span>Your answer</span>
        <textarea name="reply" rows="2" placeholder="Answer GAARD" ${state.pending ? "disabled" : ""}></textarea>
      </label>
      <button type="submit" ${state.pending ? "disabled" : ""}>Continue analysis</button>
    </form>`;
}
function renderProgressDecisions(decisions) {
  const visible = decisions.filter((decision) => decision.trim()).slice(0, 3);
  if (!visible.length) {
    return "";
  }
  return `<ul>${visible.map((decision) => `<li>${renderInlineMarkdown(decision)}</li>`).join("")}</ul>`;
}
function renderMockWarning(metadata) {
  const mockModes = [
    ["SQL generation", metadata?.sql_generation_mode],
    ["Result interpretation", metadata?.result_interpretation_mode],
    ["Output classification", metadata?.output_classification_mode]
  ].filter(([, mode]) => mode === "mock").map(([label]) => label);
  if (!mockModes.length) {
    return "";
  }
  return `
    <div class="mock-warning" role="status">
      This response used mock data processing: ${escapeHtml(mockModes.join(", "))}.
    </div>`;
}
function renderMeta(message, rows) {
  const metadata = message.response?.metadata || {};
  const buttonText = message.dataOpen ? "Hide data" : `Data (${rows.length})`;
  const mode = metadata.analysis_mode === "analysis" ? "analysis" : metadata.query_mode || message.mode;
  return `
    <div class="meta-row">
      <dl class="meta">
        <div><dt>Time</dt><dd>${escapeHtml(formatDuration(metadata.duration_ms))}</dd></div>
        <div><dt>Datasource</dt><dd>${escapeHtml(metadata.datasource_id || "-")}</dd></div>
        <div><dt>Mode</dt><dd>${escapeHtml(formatMode(mode))}</dd></div>
        <div><dt>Output</dt><dd>${escapeHtml(metadata.output_classification || "unknown")}</dd></div>
      </dl>
      <button class="data-toggle" type="button" data-toggle-data="${message.id}" aria-expanded="${message.dataOpen ? "true" : "false"}">
        ${escapeHtml(buttonText)}
      </button>
    </div>`;
}
function formatDuration(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return "-";
  }
  return `${numeric} ms`;
}
function formatMode(value) {
  return value === "analysis" ? "Analysis" : "SQL";
}
function normalizeQueryMode(value) {
  return value === "analysis" ? "analysis" : "sql";
}
function normalizeView(value) {
  const allowed = new Set(["home", "analysis", "metrics", "datasources", "queries", "alerts"]);
  return allowed.has(value) ? value : "home";
}
function rememberActiveView(value) {
  localStorage.setItem("gaard_client_active_view", normalizeView(value));
}
function handleModeChange(event) {
  state.queryMode = normalizeQueryMode(event.currentTarget.value);
  render();
}
function handleQuestionKeydown(event) {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    event.currentTarget.form?.requestSubmit();
  }
}
function toggleDataTable(event) {
  const id = Number(event.currentTarget.dataset.toggleData);
  const message = state.messages.find((item) => item.id === id);
  if (!message) return;
  message.dataOpen = !message.dataOpen;
  const latestMessage = state.messages[state.messages.length - 1];
  render({
    scrollToLatest: message.dataOpen && latestMessage?.id === message.id
  });
}
function toggleAnalysisProgress(event) {
  const details = event.currentTarget;
  const id = Number(details.dataset.analysisProgress);
  const message = state.messages.find((item) => item.id === id);
  if (message) {
    message.progressOpen = details.open;
  }
}
function retryQuestion(event) {
  const id = Number(event.currentTarget.dataset.retryQuestion);
  const message = state.messages.find((item) => item.id === id);
  if (!message || state.pending) return;
  state.queryMode = message.mode;
  render();
  const refreshedInput = document.querySelector("#question-input");
  if (!refreshedInput || refreshedInput.disabled) return;
  refreshedInput.value = message.question;
  refreshedInput.focus();
  refreshedInput.setSelectionRange(refreshedInput.value.length, refreshedInput.value.length);
}
async function explainAnswer(event) {
  const id = Number(event.currentTarget.dataset.explainAnswer);
  const message = state.messages.find((item) => item.id === id);
  const responsePayload = message?.response || {};
  const sql = responsePayload.sql?.trim() || "";
  if (!message || !sql || message.explanationStatus === "loading") {
    return;
  }
  const rows = getRows(responsePayload);
  message.explanationStatus = "loading";
  message.explanationError = "";
  render();
  try {
    const response = await fetch("/api/query/explain", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...authHeaders()
      },
      body: JSON.stringify({
        question: message.question,
        sql,
        answer: responsePayload.answer || "",
        rows,
        columns: getColumns(rows),
        metadata: responsePayload.metadata || {},
        backend_url: state.backendUrl
      })
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(formatApiResponseError(response, extractErrorMessage(payload)));
    }
    message.explanationStatus = "ok";
    message.explanation = String(payload.explanation || "").trim();
    message.explanationMetadata = payload.metadata || {};
  } catch (error) {
    message.explanationStatus = "error";
    message.explanationError = error.message || "Explanation could not be prepared.";
    reportApiError(error, "Explanation could not be prepared.");
  } finally {
    const latestMessage = state.messages[state.messages.length - 1];
    render({ scrollToLatest: latestMessage?.id === message.id });
  }
}
async function saveWidgetFromMessage(event) {
  const id = Number(event.currentTarget.dataset.saveWidget);
  const message = state.messages.find((item) => item.id === id);
  const sql = message?.response?.sql?.trim() || "";
  if (!message || !sql || message.saveStatus === "saving" || message.saveStatus === "saved") {
    return;
  }
  state.saveWidgetDialogOpen = true;
  state.saveWidgetMessageId = message.id;
  state.saveWidgetDraftLabel = buildWidgetLabel(message.question);
  state.saveWidgetTitleEdited = false;
  state.saveWidgetSuggestionLoading = true;
  state.saveWidgetSuggestionError = "";
  state.saveWidgetPending = false;
  state.saveWidgetError = "";
  render();
  void requestMetricTitleSuggestion(message);
}
function closeSaveWidgetDialog() {
  if (state.saveWidgetPending) return;
  state.saveWidgetDialogOpen = false;
  state.saveWidgetMessageId = null;
  state.saveWidgetDraftLabel = "";
  state.saveWidgetTitleEdited = false;
  state.saveWidgetSuggestionLoading = false;
  state.saveWidgetSuggestionError = "";
  state.saveWidgetError = "";
  render();
}
function changeSaveWidgetLabel(event) {
  state.saveWidgetDraftLabel = event.currentTarget.value || "";
  state.saveWidgetTitleEdited = true;
}
async function requestMetricTitleSuggestion(message) {
  const dialogMessageId = message.id;
  const sql = message.response?.sql?.trim() || "";
  try {
    const response = await fetch("/api/widgets/title-suggestion", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...authHeaders()
      },
      body: JSON.stringify({
        question: message.question,
        sql,
        backend_url: state.backendUrl
      })
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(formatApiResponseError(response, extractErrorMessage(payload)));
    }
    const title = String(payload.title || "").trim();
    if (state.saveWidgetDialogOpen && state.saveWidgetMessageId === dialogMessageId && title && !state.saveWidgetTitleEdited) {
      state.saveWidgetDraftLabel = title;
    }
    if (state.saveWidgetDialogOpen && state.saveWidgetMessageId === dialogMessageId) {
      state.saveWidgetSuggestionError = "";
    }
  } catch (_error) {
    if (state.saveWidgetDialogOpen && state.saveWidgetMessageId === dialogMessageId) {
      state.saveWidgetSuggestionError = "Could not fetch the LLM suggestion. You can enter a name manually.";
    }
  } finally {
    if (state.saveWidgetDialogOpen && state.saveWidgetMessageId === dialogMessageId) {
      state.saveWidgetSuggestionLoading = false;
      render();
    }
  }
}
async function confirmSaveWidgetFromDialog(event) {
  event.preventDefault();
  const message = state.messages.find((item) => item.id === state.saveWidgetMessageId);
  const sql = message?.response?.sql?.trim() || "";
  const form = new FormData(event.currentTarget);
  const label = String(form.get("label") || "").trim();
  if (!message || !sql || !label || state.saveWidgetPending) {
    state.saveWidgetError = "Enter a metric name.";
    render();
    return;
  }
  state.saveWidgetPending = true;
  state.saveWidgetError = "";
  state.saveWidgetDraftLabel = label;
  message.saveStatus = "saving";
  message.saveError = "";
  render();
  try {
    const response = await fetch("/api/widgets/from-query", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...authHeaders()
      },
      body: JSON.stringify({
        label,
        widget_type: inferWidgetType(getRows(message.response)),
        datasource_key: message.response?.metadata?.datasource_id || "default",
        question: message.question,
        sql,
        rows: getRows(message.response),
        result_mode: "data",
        backend_url: state.backendUrl
      })
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(formatApiResponseError(response, extractErrorMessage(payload)));
    }
    message.saveStatus = "saved";
    state.saveWidgetDialogOpen = false;
    state.saveWidgetMessageId = null;
    state.saveWidgetDraftLabel = "";
    state.saveWidgetTitleEdited = false;
    state.saveWidgetSuggestionLoading = false;
    state.saveWidgetSuggestionError = "";
    state.saveWidgetError = "";
    if (payload.item) {
      state.savedMetrics = [payload.item, ...state.savedMetrics.filter((metric) => metric.widget_key !== payload.item.widget_key)];
      state.savedMetricsLoaded = true;
      state.savedMetricsResultsLoaded = state.savedMetrics.every((metric) => Boolean(metric.result));
    } else {
      state.savedMetricsLoaded = false;
      state.savedMetricsResultsLoaded = false;
    }
  } catch (error) {
    message.saveStatus = "error";
    message.saveError = error.message || "Widget could not be saved.";
    state.saveWidgetError = message.saveError;
    reportApiError(error, "Widget could not be saved.");
  } finally {
    state.saveWidgetPending = false;
    render();
  }
}
function authHeaders() {
  return state.token ? { Authorization: `Bearer ${state.token}` } : {};
}
async function login(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  state.error = "";
  try {
    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        username: String(data.get("username") || ""),
        password: String(data.get("password") || ""),
        backend_url: state.backendUrl
      })
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(formatApiResponseError(response, extractErrorMessage(payload)));
    }
    state.token = payload.token || "";
    state.username = payload.username || "";
    state.role = payload.role || "";
    state.mustChangePassword = payload.must_change_password === true;
    state.passwordChangeError = "";
    state.loginOpen = false;
    state.dashboards = [];
    state.dashboardsLoaded = false;
    state.activeDashboardId = "";
    state.dashboardEditMode = false;
    localStorage.setItem("gaard_client_token", state.token);
    localStorage.setItem("gaard_client_username", state.username);
    localStorage.setItem("gaard_client_role", state.role);
    localStorage.setItem("gaard_client_must_change_password", String(state.mustChangePassword));
    render();
  } catch (error) {
    state.error = error.message || "Login failed.";
    reportApiError(error, "Login failed.");
    renderLogin();
  }
}
async function changePassword(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const newPassword = String(form.get("new_password") || "");
  if (newPassword !== String(form.get("confirm_password") || "")) {
    state.passwordChangeError = "New passwords do not match.";
    render();
    return;
  }
  try {
    const response = await fetch("/api/auth/change-password", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({
        current_password: String(form.get("current_password") || ""),
        new_password: newPassword,
        backend_url: state.backendUrl
      })
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(formatApiResponseError(response, extractErrorMessage(payload)));
    state.mustChangePassword = false;
    state.passwordChangeError = "";
    localStorage.setItem("gaard_client_must_change_password", "false");
    render();
  } catch (error) {
    state.passwordChangeError = error.message || "Password could not be changed.";
    render();
  }
}
async function refreshPasswordChangeRequirement() {
  if (!state.token) return;
  try {
    const response = await fetch(`/api/auth/me?backend_url=${encodeURIComponent(state.backendUrl)}`, {
      headers: authHeaders()
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(formatApiResponseError(response, extractErrorMessage(payload)));
    state.mustChangePassword = payload.must_change_password === true;
    localStorage.setItem("gaard_client_must_change_password", String(state.mustChangePassword));
    render();
  } catch (error) {
    if (error.message?.includes("401")) await logout();
  }
}
async function logout() {
  if (state.dashboardEditMode) {
    await flushPendingDashboardLayoutSave();
  }
  const token = state.token;
  if (token) {
    void fetch(`/api/auth/logout?backend_url=${encodeURIComponent(state.backendUrl)}`, {
      method: "POST",
      headers: { "Authorization": `Bearer ${token}` },
      keepalive: true,
    });
  }
  state.token = "";
  state.username = "";
  state.role = "";
  state.mustChangePassword = false;
  state.passwordChangeError = "";
  state.messages = [];
  state.conversationId = "";
  state.datasources = [];
  state.datasourcesLoaded = false;
  state.datasourceError = "";
  state.selectedDatasourceIds = [];
  state.multipleDatasourceSelectionAllowed = false;
  state.dashboards = [];
  state.dashboardsLoaded = false;
  state.dashboardsError = "";
  state.activeDashboardId = "";
  state.dashboardMenuOpen = false;
  state.dashboardCreateOpen = false;
  state.dashboardEditId = "";
  state.dashboardWidgetDialogOpen = false;
  state.dashboardEditMode = false;
  state.savedMetrics = [];
  state.savedMetricsLoaded = false;
  state.savedMetricsResultsLoaded = false;
  state.savedMetricsError = "";
  state.dashboardWidgets = [];
  state.dashboardWidgetsDashboardId = "";
  state.dashboardWidgetsError = "";
  state.metricEditDialogOpen = false;
  state.metricEditWidgetKey = "";
  state.metricEditDraftLabel = "";
  state.metricEditPending = false;
  state.metricEditError = "";
  state.metricDeletePendingKey = "";
  state.activeView = "home";
  rememberActiveView(state.activeView);
  state.loginOpen = false;
  localStorage.removeItem("gaard_client_token");
  localStorage.removeItem("gaard_client_username");
  localStorage.removeItem("gaard_client_role");
  localStorage.removeItem("gaard_client_must_change_password");
  render();
}
function buildWidgetLabel(question) {
  const compact = question.replace(/\s+/g, " ").trim();
  return compact.length > 64 ? `${compact.slice(0, 61)}...` : compact || "Saved query";
}
function inferWidgetType(rows) {
  if (rows.length === 1 && Object.keys(rows[0] || {}).length === 1) {
    return "scalar";
  }
  return "table";
}
function getSelectedSavedMetric() {
  return state.savedMetrics.find((metric) => metric.widget_key === state.dashboardWidgetMetricKey) || state.savedMetrics[0] || null;
}
function getAvailableWidgetTypes(metric) {
  if (!metric) return ["table"];
  const profile = getTabularProfile(metric.result || {});
  if (metric.widget_type === "scalar" || (profile.rows.length === 1 && profile.columns.length === 1)) {
    return ["number", "table"];
  }
  const types = ["table"];
  if (profile.rows.length && profile.numericColumns.length) {
    types.unshift("bar");
    if (profile.dimensionColumns.length) {
      types.push("pie");
    }
    if (profile.hasWideSeries || profile.hasLongSeries) {
      types.push("stacked_bar", "multi_line");
    }
    types.push("line", "area");
  }
  return [...new Set(types)];
}
function recommendWidgetType(metric) {
  const availableTypes = getAvailableWidgetTypes(metric);
  if (availableTypes.includes("number")) return "number";
  if (metric?.widget_type === "timeseries" && availableTypes.includes("line")) return "line";
  if (availableTypes.includes("bar")) return "bar";
  return availableTypes[0] || "table";
}
function normalizeDashboardWidgetType(value, availableTypes) {
  return availableTypes.includes(value) ? value : availableTypes[0] || "table";
}
function getRowsFromResult(result) {
  return Array.isArray(result?.rows) ? result.rows : [];
}
function getTabularProfile(result) {
  const rows = getRowsFromResult(result);
  const columns = Array.isArray(result?.columns) && result.columns.length ? result.columns : getColumns(rows);
  const numericColumns = columns.filter((column) => rows.some((row) => isNumericValue(row?.[column])));
  const dimensionColumns = columns.filter((column) => !numericColumns.includes(column));
  return {
    rows,
    columns,
    numericColumns,
    dimensionColumns,
    hasWideSeries: numericColumns.length > 1,
    hasLongSeries: dimensionColumns.length >= 2 && numericColumns.length >= 1
  };
}
function isNumericValue(value) {
  if (typeof value === "number") {
    return Number.isFinite(value);
  }
  if (typeof value !== "string" || !value.trim()) {
    return false;
  }
  return Number.isFinite(Number(value));
}
function getSelectedMode(form) {
  const value = new FormData(form).get("mode");
  return normalizeQueryMode(value);
}
function conversationPayload() {
  return state.conversationId ? { conversation_id: state.conversationId } : {};
}
function syncConversationFromResponse(response) {
  const conversationId = response?.metadata?.conversation?.id || response?.conversation_id || response?.session_started?.conversation_id || response?.session_resumed?.conversation_id || "";
  if (conversationId) {
    state.conversationId = String(conversationId);
  }
}
async function submitQuestion(event) {
  event.preventDefault();
  if (state.pending) return;
  if (!state.token) {
    state.loginOpen = true;
    render();
    return;
  }
  if (!state.selectedDatasourceIds.length) {
    state.error = "Select at least one available datasource before asking a question.";
    render();
    return;
  }
  const form = event.currentTarget;
  const input = form.elements.namedItem("question");
  const question = String(input?.value || "").trim();
  const mode = getSelectedMode(form);
  if (!question) return;
  if (input) input.value = "";
  state.error = "";
  state.pending = true;
  const message = {
    id: state.nextMessageId,
    question,
    mode,
    status: "pending",
    response: null,
    error: "",
    dataOpen: false,
    saveStatus: "idle",
    saveError: "",
    explanationStatus: "idle",
    explanation: "",
    explanationError: "",
    explanationMetadata: {},
    progress: [],
    progressOpen: false,
    processingStage: "Processing query..",
    analysisSessionId: "",
    userQuestion: ""
  };
  state.nextMessageId += 1;
  state.messages.push(message);
  render({ scrollToLatest: true });
  try {
    if (mode === "analysis") {
      await submitAnalysisQuestion(message, question);
    } else {
      const response = await fetch("/api/query/stream", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...authHeaders()
        },
        body: JSON.stringify({
          question,
          mode,
          datasource_ids: state.selectedDatasourceIds,
          ...conversationPayload(),
          backend_url: state.backendUrl
        })
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(formatApiResponseError(response, extractErrorMessage(payload)));
      }
      await readQueryStream(message, response);
    }
  } catch (error) {
    message.status = "error";
    message.error = error.message || "Request failed.";
    reportApiError(error, "Request failed.");
  } finally {
    state.pending = false;
    render({ scrollToLatest: true });
  }
}
async function readQueryStream(message, response) {
  if (!response.body) {
    throw new Error("Streaming response is not available.");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalReceived = false;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      finalReceived = handleQueryStreamLine(message, line) || finalReceived;
    }
  }
  buffer += decoder.decode();
  if (buffer.trim()) {
    finalReceived = handleQueryStreamLine(message, buffer) || finalReceived;
  }
  if (!finalReceived) {
    throw new Error("Query stream ended without a final response.");
  }
}
function handleQueryStreamLine(message, line) {
  const trimmed = line.trim();
  if (!trimmed) return false;
  const payload = JSON.parse(trimmed);
  if (payload?.error?.message) {
    throw new Error(payload.error.message);
  }
  if (payload?.stage) {
    message.processingStage = payload.stage === "waiting_on_data_server"
      ? "Waiting on data server..."
      : "Processing query..";
    render({ scrollToLatest: true });
  }
  if (payload?.final) {
    syncConversationFromResponse(payload.final);
    message.status = "ok";
    message.response = payload.final;
    message.processingStage = "";
    render({ scrollToLatest: true });
    return true;
  }
  return false;
}
async function submitAnalysisQuestion(message, question) {
  const response = await fetch("/api/analysis/stream", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders()
    },
    body: JSON.stringify({
      question,
      datasource_ids: state.selectedDatasourceIds,
      ...conversationPayload(),
      backend_url: state.backendUrl
    })
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(formatApiResponseError(response, extractErrorMessage(payload)));
  }
  await readAnalysisStream(message, response);
}
async function submitAnalysisReply(event) {
  event.preventDefault();
  if (state.pending) return;
  const form = event.currentTarget;
  const id = Number(form.dataset.analysisReplyForm);
  const message = state.messages.find((item) => item.id === id);
  const input = form.elements.namedItem("reply");
  const reply = String(input?.value || "").trim();
  if (!message || !message.analysisSessionId || !reply) return;
  state.pending = true;
  message.status = "pending";
  message.userQuestion = "";
  render({ scrollToLatest: true });
  try {
    await continueAnalysis(message, reply);
  } catch (error) {
    message.status = "error";
    message.error = error.message || "Request failed.";
    reportApiError(error, "Request failed.");
  } finally {
    state.pending = false;
    render({ scrollToLatest: true });
  }
}
async function continueAnalysis(message, reply) {
  const response = await fetch(`/api/analysis/${encodeURIComponent(message.analysisSessionId)}/messages/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders()
    },
    body: JSON.stringify({
      message: reply,
      backend_url: state.backendUrl
    })
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(formatApiResponseError(response, extractErrorMessage(payload)));
  }
  await readAnalysisStream(message, response);
}
async function readAnalysisStream(message, response) {
  if (!response.body) {
    throw new Error("Streaming response is not available.");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalReceived = false;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      finalReceived = handleAnalysisStreamLine(message, line) || finalReceived;
    }
  }
  buffer += decoder.decode();
  if (buffer.trim()) {
    finalReceived = handleAnalysisStreamLine(message, buffer) || finalReceived;
  }
  if (!finalReceived && message.status !== "waiting") {
    throw new Error("Analysis stream ended without a final response.");
  }
}
function handleAnalysisStreamLine(message, line) {
  const trimmed = line.trim();
  if (!trimmed) return false;
  const payload = JSON.parse(trimmed);
  if (payload?.error?.message) {
    throw new Error(payload.error.message);
  }
  if (payload?.final) {
    syncConversationFromResponse(payload.final);
    message.status = "ok";
    message.response = payload.final;
    message.dataOpen = message.mode === "analysis" && getRows(payload.final).length > 0;
    message.userQuestion = "";
    render({ scrollToLatest: true });
    return true;
  }
  if (payload?.session_id && !message.analysisSessionId) {
    message.analysisSessionId = String(payload.session_id);
  }
  syncConversationFromResponse(payload);
  if (payload?.event === "user_question") {
    const question = extractUserQuestion(payload);
    message.status = "waiting";
    message.userQuestion = question;
    message.progress = [
      ...message.progress,
      {
        event: "user_question",
        title: "GAARD needs your clarification",
        detail: question,
        items: []
      }
    ];
    render({ scrollToLatest: true });
    return false;
  }
  const progress = progressFromAnalysisEvent(payload);
  if (progress) {
    message.progress = [...message.progress, progress];
    render({ scrollToLatest: true });
  }
  return false;
}
function firstText(...values) {
  for (const value of values) {
    const text = String(value || "").trim();
    if (text) return text;
  }
  return "";
}
function extractUserQuestion(payload) {
  const userQuestion = payload?.user_question;
  return firstText(
    typeof userQuestion === "string" ? userQuestion : "",
    userQuestion?.question,
    userQuestion?.message,
    userQuestion?.visible_question,
    payload?.question,
    payload?.decision?.user_question,
    payload?.decision?.visible_question,
    "GAARD needs a clarification."
  );
}
function progressFromAnalysisEvent(payload) {
  const event = String(payload?.event || "");
  if (event === "analysis_step") {
    const step = payload.analysis_step || {};
    return {
      event,
      title: step.visible_question || "GAARD is checking the next analysis step.",
      detail: step.visible_reasoning || "",
      items: [`Iteration ${step.iteration || payload.sequence || ""}`].filter(Boolean)
    };
  }
  if (event === "decision") {
    const decision = payload.decision || {};
    return {
      event,
      title: `Decision: ${formatAnalysisAction(decision.action)}`,
      detail: decision.visible_reasoning || decision.visible_question || "",
      items: [
        decision.user_question ? `Question for you: ${decision.user_question}` : "",
        decision.database_question ? `Database question: ${decision.database_question}` : "",
        decision.final_question ? `Final query question: ${decision.final_question}` : "",
        decision.answer ? `Context answer prepared.` : ""
      ].filter(Boolean)
    };
  }
  if (event === "database_question") {
    const question = payload.database_question || {};
    return {
      event,
      title: question.final ? "GAARD asks the final database question" : "GAARD asks the database",
      detail: question.question || "",
      items: []
    };
  }
  if (event === "database_result") {
    const result = payload.database_result || {};
    return {
      event,
      title: "Database result received",
      detail: result.answer || "",
      items: [
        result.sql ? `SQL: ${result.sql}` : "",
        Array.isArray(result.rows) ? `Rows: ${result.rows.length}` : ""
      ].filter(Boolean)
    };
  }
  if (event === "business_logic_suggestion") {
    const suggestion = payload.business_logic_suggestion || {};
    return {
      event,
      title: suggestion.enabled ? "Business logic finding enabled" : "Business logic finding saved for review",
      detail: suggestion.title || suggestion.rule_text || "",
      items: [
        suggestion.error_category ? `Type: ${suggestion.error_category}` : "",
        suggestion.confidence !== void 0 ? `Confidence: ${suggestion.confidence}` : ""
      ].filter(Boolean)
    };
  }
  if (event === "limit_reached") {
    return {
      event,
      title: "Analysis loop limit reached",
      detail: `Limit: ${payload.limit_reached?.analysis_loop_count || "-"}`,
      items: []
    };
  }
  if (event === "session_started" || event === "session_resumed") {
    return null;
  }
  return null;
}
function formatAnalysisAction(value) {
  return String(value || "unknown").replaceAll("_", " ");
}
function extractErrorMessage(payload) {
  const detail = payload?.detail;
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail)) {
    return detail.map((item) => item?.msg || item?.message || String(item)).join("; ");
  }
  if (detail?.message) {
    return detail.message;
  }
  if (detail?.detail) {
    return typeof detail.detail === "string" ? detail.detail : JSON.stringify(detail.detail);
  }
  if (detail?.error?.message) {
    return detail.error.message;
  }
  if (payload?.error?.message) {
    return payload.error.message;
  }
  if (payload?.message) {
    return payload.message;
  }
  return "Request failed.";
}
function getRows(response) {
  return Array.isArray(response?.rows) ? response.rows : [];
}
function getColumns(rows) {
  const columns = [];
  rows.forEach((row) => {
    if (!row || typeof row !== "object" || Array.isArray(row)) return;
    Object.keys(row).forEach((column) => {
      if (!columns.includes(column)) {
        columns.push(column);
      }
    });
  });
  return columns;
}
function renderDataTable(rows) {
  const columns = getColumns(rows);
  if (!rows.length) {
    return `<div class="data-table-empty">No rows returned.</div>`;
  }
  if (!columns.length) {
    return `<div class="data-table-empty">Rows are not table-shaped.</div>`;
  }
  return `
    <div class="data-table-wrap" tabindex="0">
      <table class="data-table">
        <thead>
          <tr>${columns.map((column) => `<th scope="col">${escapeHtml(column)}</th>`).join("")}</tr>
        </thead>
        <tbody>
          ${rows.map((row) => `
          <tr>
            ${columns.map((column) => `<td>${escapeHtml(formatCellValue(row?.[column]))}</td>`).join("")}
          </tr>`).join("")}
        </tbody>
      </table>
    </div>`;
}
function formatCellValue(value) {
  if (value === null) {
    return "null";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value ?? "");
}
function scrollToLatest() {
  const scroll = () => {
    const history = document.querySelector(".history");
    if (!history) return;
    history.scrollTo({
      top: history.scrollHeight,
      behavior: "auto"
    });
  };
  requestAnimationFrame(() => {
    scroll();
    requestAnimationFrame(scroll);
  });
}
render();
void refreshPasswordChangeRequirement();
