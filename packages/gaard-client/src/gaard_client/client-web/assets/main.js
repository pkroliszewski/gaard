// src/main.ts
var app = document.querySelector("#app");
var params = new URLSearchParams(window.location.search);
var configuredBackendUrl = (params.get("backendUrl") || params.get("apiUrl") || window.GAARD_CLIENT_CONFIG?.backendUrl || "http://localhost:8000").replace(/\/+$/, "");
var storedToken = localStorage.getItem("gaard_client_token") || "";
var storedUsername = localStorage.getItem("gaard_client_username") || "";
var state = {
  backendUrl: configuredBackendUrl,
  token: storedToken,
  username: storedUsername,
  activeView: normalizeView(params.get("view")),
  queryMode: normalizeQueryMode(params.get("mode")),
  messages: [],
  nextMessageId: 1,
  conversationId: "",
  pending: false,
  error: "",
  loginOpen: !storedToken,
  sourcesOpen: false,
  datasources: [],
  datasourcesLoaded: false,
  datasourcesLoading: false,
  datasourceError: "",
  datasourceUploadPending: false,
  datasourceStatePendingId: null,
  newDatasourceActive: false
};
function escapeHtml(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
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
      </svg>`
  };
  return icons[name] || "";
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
        <button class="source-add" type="button" data-add-source aria-label="Dodaj plik Excel" title="Dodaj plik Excel" ${state.datasourceUploadPending || !state.token ? "disabled" : ""}>
          <span aria-hidden="true">+</span>
        </button>
        <label class="new-source-state">
          <input type="checkbox" data-new-source-active ${state.newDatasourceActive ? "checked" : ""} ${state.datasourceUploadPending || !state.token ? "disabled" : ""} />
          <span>Dodaj jako aktywne źródło</span>
        </label>
        
      </div>
      <div class="sources-list" aria-live="polite">
        ${state.datasourcesLoading ? `<div class="source-muted">Ładowanie...</div>` : ""}
        ${!state.datasourcesLoading && !visibleSources.length ? `<div class="source-muted">Brak źródeł</div>` : ""}
        ${visibleSources.map((source) => `
          <div class="source-row" title="${escapeHtml(source.name)}">
            <label class="source-state">
              <input type="checkbox" data-source-active="${source.id}" ${source.active ? "checked" : ""} ${state.datasourceStatePendingId === source.id ? "disabled" : ""} />
              <span>${escapeHtml(source.name)}</span>
            </label>
            <small>${source.active ? "Aktywne" : "Nieaktywne"}</small>
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
        <h2>Ask your data</h2>
        <p>Log in to start a conversation.</p>
        <button class="primary" type="button" data-open-login>Log in</button>
      </div>`;
  }
  return `
    <div class="empty-state">
      <img class="empty-logo" src="/assets/getgaard.svg" alt="" />
      <h2>Ask your data</h2>
      <p>Ask about metrics, records, trends, or run step-by-step analysis.</p>
    </div>`;
}
function renderViewHeading() {
  const headings = {
    home: ["Home", "Ask your data"],
    analysis: ["Analysis", "Healthcare Operations overview"],
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
function renderActiveView() {
  if (state.activeView === "analysis") {
    return renderAnalysisView();
  }
  if (state.activeView === "metrics") {
    return renderPlaceholderView({
      title: "Metrics",
      description: "Defined dashboard widgets will appear here. For now, you can save a widget from a chat response with the save button.",
      items: ["Saved query widgets", "KPI cards", "Chart templates"]
    });
  }
  if (state.activeView === "datasources") {
    return renderPlaceholderView({
      title: "Datasources",
      description: "Uploaded files and connected data sources, such as Excel or CSV, will appear here. This module is marked as a future client feature.",
      items: ["Excel workbooks", "CSV uploads", "Connected databases"]
    });
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
function renderAnalysisView() {
  return `
    <section class="dashboard-view" aria-label="Dashboard Analysis">
      <div class="dashboard-toolbar">
        <div>
          <h1>Healthcare Operations overview</h1>
          <p>Weekly operational snapshot across capacity, flow and active issues.</p>
        </div>
        <button class="date-range" type="button" aria-disabled="true">
          ${renderIcon("calendar")}
          <span>May 12 - May 18, 2025</span>
        </button>
      </div>
      <div class="kpi-row" aria-label="Key metrics">
        ${renderKpiCard("Total patients", "45,781", "6.3%", "vs May 5 - May 11", "up")}
        ${renderKpiCard("Active encounters", "12,986", "4.8%", "vs May 5 - May 11", "up")}
        ${renderKpiCard("New episodes", "3,274", "7.1%", "vs May 5 - May 11", "up")}
        ${renderKpiCard("Average LOS", "4.2 days", "0.3", "vs May 5 - May 11", "down")}
      </div>
      <div class="grid-stack dashboard-grid">
        ${renderGridWidget("capacity", "Capacity by data domain", "chart chart-capacity", 0, 0, 6, 4)}
        ${renderGridWidget("flow", "Patients flow", "chart chart-flow", 6, 0, 6, 4)}
        ${renderGridWidget("episodes", "New episodes", "chart chart-episodes", 0, 4, 6, 3, renderEpisodeSummary())}
        ${renderGridWidget("issues", "Recent issues", "issues-table", 6, 4, 6, 3, renderIssuesTable())}
      </div>
      <div class="dashboard-fallback" data-dashboard-fallback hidden>
        Dashboard libraries are loading. Charts will appear when GridStack and ECharts are available.
      </div>
    </section>`;
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
        ${renderActiveView()}
      </section>
      <input id="excel-source-input" type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" hidden />
      ${state.loginOpen ? renderLoginDialog() : ""}
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
  document.querySelector("[data-toggle-sources]")?.addEventListener("click", toggleSources);
  document.querySelector("[data-add-source]")?.addEventListener("click", openSourcePicker);
  document.querySelector("[data-new-source-active]")?.addEventListener("change", toggleNewSourceActive);
  document.querySelectorAll("[data-source-active]").forEach((input2) => {
    input2.addEventListener("change", updateSourceActive);
  });
  document.querySelector("#excel-source-input")?.addEventListener("change", uploadSelectedSource);
  document.querySelector("#login-form")?.addEventListener("submit", login);
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
  if (options.scrollToLatest) {
    scrollToLatest();
  }
}
async function toggleSources() {
    state.sourcesOpen = !state.sourcesOpen;
    state.datasourceError = "";
    render();
    if (state.sourcesOpen && state.token && !state.datasourcesLoaded) {
        await loadDatasources();
    }
}
function openSourcePicker() {
    if (!state.token || state.datasourceUploadPending) return;
    const input = document.querySelector("#excel-source-input");
    if (!input) return;
    input.value = "";
    input.click();
}
function toggleNewSourceActive(event) {
    state.newDatasourceActive = event.currentTarget.checked;
}
async function loadDatasources() {
    state.datasourcesLoading = true;
    state.datasourceError = "";
    render();
    try {
        const response = await fetch(`/api/datasources?backend_url=${encodeURIComponent(state.backendUrl)}`, {
            headers: authHeaders()
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(extractErrorMessage(payload));
        }
        state.datasources = payload.items || [];
        state.datasourcesLoaded = true;
    } catch (error) {
        state.datasourceError = error.message || "Nie udało się pobrać źródeł danych.";
    } finally {
        state.datasourcesLoading = false;
        render();
    }
}
async function uploadSelectedSource(event) {
    const file = event.currentTarget.files?.[0];
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".xlsx")) {
        state.datasourceError = "Wybierz plik w formacie .xlsx.";
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
            active: state.newDatasourceActive ? "true" : "false"
        });
        const response = await fetch(`/api/datasources/excel?${params.toString()}`, {
            method: "POST",
            headers: authHeaders(),
            body: formData
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(friendlyDatasourceError(extractErrorMessage(payload)));
        }
        state.datasources = [
            payload.item,
            ...state.datasources.filter((item) => item.id !== payload.item?.id)
        ].filter(Boolean);
        state.datasourcesLoaded = true;
    } catch (error) {
        state.datasourceError = error.message || "Nie udało się dodać źródła danych.";
    } finally {
        state.datasourceUploadPending = false;
        render();
    }
}
async function updateSourceActive(event) {
    const input = event.currentTarget;
    const sourceId = Number(input.dataset.sourceActive);
    const active = input.checked;
    if (!sourceId || state.datasourceStatePendingId) return;
    state.datasourceStatePendingId = sourceId;
    state.datasourceError = "";
    render();
    try {
        const response = await fetch(`/api/datasources/${sourceId}/state`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...authHeaders()
            },
            body: JSON.stringify({
                active,
                backend_url: state.backendUrl
            })
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(friendlyDatasourceError(extractErrorMessage(payload)));
        }
        await loadDatasources();
    } catch (error) {
        state.datasourceError = error.message || "Nie udało się zmienić stanu źródła.";
    } finally {
        state.datasourceStatePendingId = null;
        render();
    }
}
function friendlyDatasourceError(message) {
    if (message.includes("non-SQL source support") || message.includes("LICENSE_ENTITLEMENT_REQUIRED")) {
        return "Ta licencja nie pozwala na używanie plików Excel jako źródeł danych.";
    }
    if (message.includes("multi-source access")) {
        return "Korzystanie z wielu aktywnych źródeł danych wymaga licencji z obsługą wielu źródeł.";
    }
    return message;
}
function changeView(event) {
  const view = normalizeView(event.currentTarget.dataset.view);
  if (state.activeView === view) return;
  state.activeView = view;
  state.error = "";
  render();
}
function initAnalysisDashboard() {
  if (state.activeView !== "analysis") return;
  const gridElement = document.querySelector(".dashboard-grid");
  if (!gridElement) return;
  if (window.GridStack) {
    window.GridStack.init(
      {
        cellHeight: 94,
        column: 12,
        float: false,
        margin: 12,
        resizable: { handles: "e,se,s,sw,w" }
      },
      gridElement
    );
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
  const charts = [
    ["capacity", capacityOptions()],
    ["flow", flowOptions()],
    ["episodes-line", episodesOptions()]
  ];
  charts.forEach(([id, options]) => {
    const element = document.querySelector(`[data-chart="${id}"]`);
    if (!element) return;
    const chart = window.echarts.init(element, null, { renderer: "canvas" });
    chart.setOption(options);
    window.addEventListener("resize", () => chart.resize(), { passive: true });
    const gridItem = element.closest(".grid-stack-item");
    if (gridItem && window.ResizeObserver) {
      new ResizeObserver(() => chart.resize()).observe(gridItem);
    }
  });
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
  render();
}
function renderMessage(message) {
  const rows = getRows(message.response);
  const meta = message.status === "ok" ? renderMeta(message, rows) : "";
  const answer = message.status === "pending" ? "Processing..." : message.status === "waiting" ? "Waiting for your answer." : message.status === "error" ? message.error : message.response?.answer || "";
  const dataTable = message.status === "ok" && message.dataOpen ? renderDataTable(rows) : "";
  const mockWarning = message.status === "ok" ? renderMockWarning(message.response?.metadata) : "";
  const saveNotice = renderSaveNotice(message);
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
        <p>${escapeHtml(answer)}</p>
      </div>
      ${progress}
      ${analysisReply}
      ${mockWarning}
      ${saveNotice}
      ${meta}
      ${dataTable}
    </article>`;
}
function renderMessageActions(message) {
  const saveDisabled = state.pending || message.saveStatus === "saving" || message.saveStatus === "saved";
  const saveTitle = message.saveStatus === "saved" ? "Saved as widget" : message.saveStatus === "saving" ? "Saving widget" : "Save as widget";
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
    </div>`;
}
function canSaveWidget(message) {
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
function renderAnalysisProgress(message) {
  if (!message.progress.length) {
    return "";
  }
  const latest = message.progress[message.progress.length - 1];
  return `
    <details class="analysis-log" data-analysis-progress="${message.id}" ${message.progressOpen ? "open" : ""}>
      <summary>
        <span>Analysis</span>
        <strong>${escapeHtml(latest.title)}</strong>
        ${latest.detail ? `<small>${escapeHtml(latest.detail)}</small>` : ""}
      </summary>
      <ol class="analysis-progress" aria-label="Analysis progress">
        ${message.progress.map((update, index) => `
          <li class="${index === message.progress.length - 1 ? "active" : "done"}">
            <div>
              <p>${escapeHtml(update.title)}</p>
              ${update.detail ? `<p class="progress-detail">${escapeHtml(update.detail)}</p>` : ""}
              ${renderProgressDecisions(update.items)}
            </div>
          </li>`).join("")}
      </ol>
    </details>`;
}
function renderAnalysisReply(message) {
  return `
    <form class="analysis-reply" data-analysis-reply-form="${message.id}">
      <div class="analysis-reply-question">${escapeHtml(message.userQuestion || "GAARD needs a clarification.")}</div>
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
  return `<ul>${visible.map((decision) => `<li>${escapeHtml(decision)}</li>`).join("")}</ul>`;
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
async function saveWidgetFromMessage(event) {
  const id = Number(event.currentTarget.dataset.saveWidget);
  const message = state.messages.find((item) => item.id === id);
  const sql = message?.response?.sql?.trim() || "";
  if (!message || !sql || message.saveStatus === "saving" || message.saveStatus === "saved") {
    return;
  }
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
        label: buildWidgetLabel(message.question),
        widget_type: inferWidgetType(getRows(message.response)),
        datasource_key: message.response?.metadata?.datasource_id || "default",
        question: message.question,
        sql,
        result_mode: "data",
        backend_url: state.backendUrl
      })
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(extractErrorMessage(payload));
    }
    message.saveStatus = "saved";
  } catch (error) {
    message.saveStatus = "error";
    message.saveError = error.message || "Widget could not be saved.";
  } finally {
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
      throw new Error(extractErrorMessage(payload));
    }
    state.token = payload.token || "";
    state.username = payload.username || "";
    state.loginOpen = false;
    localStorage.setItem("gaard_client_token", state.token);
    localStorage.setItem("gaard_client_username", state.username);
    render();
  } catch (error) {
    state.error = error.message || "Login failed.";
    renderLogin();
  }
}
function logout() {
  state.token = "";
  state.username = "";
  state.messages = [];
  state.conversationId = "";
  state.activeView = "home";
  state.loginOpen = false;
  localStorage.removeItem("gaard_client_token");
  localStorage.removeItem("gaard_client_username");
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
    progress: [],
    progressOpen: false,
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
      const response = await fetch("/api/query", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...authHeaders()
        },
        body: JSON.stringify({
          question,
          mode,
          ...conversationPayload(),
          backend_url: state.backendUrl
        })
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(extractErrorMessage(payload));
      }
      syncConversationFromResponse(payload);
      message.status = "ok";
      message.response = payload;
    }
  } catch (error) {
    message.status = "error";
    message.error = error.message || "Request failed.";
  } finally {
    state.pending = false;
    render({ scrollToLatest: true });
  }
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
      ...conversationPayload(),
      backend_url: state.backendUrl
    })
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(extractErrorMessage(payload));
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
    throw new Error(extractErrorMessage(payload));
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
  if (detail?.error?.message) {
    return detail.error.message;
  }
  if (payload?.error?.message) {
    return payload.error.message;
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
