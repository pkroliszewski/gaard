const app = document.querySelector("#app");
const sections = [
    {
        key: "overview",
        label: "Overview"
    },
    {
        key: "data-audit",
        label: "Data audit"
    },
    {
        key: "prompts",
        label: "Prompts"
    },
    {
        key: "schema-cache",
        label: "Schema cache"
    },
    {
        key: "business-logic",
        label: "Business logic suggestions"
    },
    {
        key: "llm-config",
        label: "LLM configuration"
    },
    {
        key: "governance-policy",
        label: "Governance policy"
    },
    {
        key: "identity",
        label: "Identity connector"
    },
    {
        key: "datasources",
        label: "Datasource connector"
    },
    {
        key: "license",
        label: "License"
    },
    {
        key: "admin-audit",
        label: "Admin audit"
    }
];
const state = {
    token: localStorage.getItem("gaard_admin_token"),
    username: localStorage.getItem("gaard_admin_username") || "",
    mustChangePassword: localStorage.getItem("gaard_admin_must_change") === "true",
    section: "overview",
    error: "",
    success: "",
    overview: null,
    overviewEditorWidgetKey: null,
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
    selectedDatasourceId: null,
    datasourceSchema: null,
    license: null
};
const dataAuditTypes = [
    {
        value: "",
        label: "All types"
    },
    {
        value: "info",
        label: "Info"
    },
    {
        value: "sql_error",
        label: "SQL error"
    },
    {
        value: "access_error",
        label: "Access error"
    }
];
const outputClassifications = [
    {
        value: "",
        label: "All classifications"
    },
    {
        value: "personal_data",
        label: "Personal data"
    },
    {
        value: "sensitive_data",
        label: "Sensitive data"
    },
    {
        value: "technical_data",
        label: "Technical data"
    },
    {
        value: "neutral_data",
        label: "Neutral data"
    },
    {
        value: "unknown",
        label: "Unknown"
    }
];
function escapeHtml(value) {
    return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
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
async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("Content-Type", "application/json");
    if (state.token) headers.set("Authorization", `Bearer ${state.token}`);
    const response = await fetch(path, {
        ...options,
        headers
    });
    const payload = await response.json().catch(()=>({}));
    if (response.status === 401) {
        logout();
        throw new Error("Session expired.");
    }
    if (!response.ok) {
        throw new Error(payload.detail || payload.error?.message || "Request failed.");
    }
    return payload;
}
function setMessage(type, value) {
    state.error = type === "error" ? value : "";
    state.success = type === "success" ? value : "";
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
    state.overviewEditorWidgetKey = null;
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
    document.querySelector("#login-form")?.addEventListener("submit", async (event)=>{
        event.preventDefault();
        const form = new FormData(event.currentTarget);
        try {
            const result = await api("/api/v1/admin/auth/login", {
                method: "POST",
                body: JSON.stringify({
                    username: form.get("username"),
                    password: form.get("password")
                })
            });
            persistAuth(result.token, result.username, result.must_change_password);
            setMessage("success", "");
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
    document.querySelector("#password-form")?.addEventListener("submit", async (event)=>{
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
            localStorage.setItem("gaard_admin_must_change", String(result.must_change_password));
            setMessage("success", "Password changed.");
            render();
            await loadCurrentSection();
        } catch (error) {
            setMessage("error", error.message);
            render();
        }
    });
}
function renderShell() {
    const active = sections.find((section)=>section.key === state.section);
    app.innerHTML = `
    <div class="app-shell">
      <aside class="sidebar">
        <div class="brand"><strong>GAARD Admin Console</strong><span>Community edition</span></div>
        <nav class="nav">
          ${sections.map((section)=>`<button data-section="${section.key}" class="${section.key === state.section ? "active" : ""}">${section.label}</button>`).join("")}
        </nav>
        <div class="sidebar-footer"><span>${escapeHtml(state.username)}</span><button id="logout-button">Sign out</button></div>
      </aside>
      <main class="main">
        <header class="topbar">
          <h1>${escapeHtml(active?.label || "Admin")}</h1>
          <div class="topbar-actions"><span>${escapeHtml(state.username)}</span><button id="top-logout-button">Sign out</button></div>
        </header>
        <section class="content">
          ${state.error ? `<div class="error">${escapeHtml(state.error)}</div>` : ""}
          ${state.success ? `<div class="success">${escapeHtml(state.success)}</div>` : ""}
          ${renderSection()}
        </section>
      </main>
    </div>
    ${renderOverviewWidgetModal()}`;
    document.querySelectorAll("[data-section]").forEach((button)=>{
    button.addEventListener("click", async ()=>{
        state.section = button.dataset.section;
        state.overviewEditorWidgetKey = null;
        setMessage("success", "");
            render();
            await loadCurrentSection();
        });
    });
    document.querySelector("#logout-button")?.addEventListener("click", logout);
    document.querySelector("#top-logout-button")?.addEventListener("click", logout);
    attachSectionHandlers();
}
function renderSection() {
    if (state.section === "overview") return renderOverview();
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
function renderOverview() {
    const overview = state.overview;
    const infoWidgets = overview?.info_widgets || [];
    const runtimeWidget = overview?.runtime_widget || null;
    return `
    <div class="widget-grid">
      ${infoWidgets.map(renderInfoWidget).join("")}
    </div>
    ${runtimeWidget ? renderRuntimeWidget(runtimeWidget) : renderStub("Runtime", "No runtime widget configured.")}`;
}
function renderInfoWidget(widget) {
    const result = widget.result;
    const value = result.status === "ok" ? result.value ?? "-" : "Error";
    return `
    <section class="widget-card">
      <div class="widget-card-main">
        <span>${escapeHtml(widget.label)}</span>
        <strong>${escapeHtml(value)}</strong>
      </div>
      <div class="widget-card-actions">
        ${renderEditWidgetButton(widget.widget_key)}
      </div>
    </section>`;
}
function renderRuntimeWidget(widget) {
    const result = widget.result;
    return `
    <section class="panel runtime-widget">
      <div class="panel-header">
        <h2>${escapeHtml(widget.label)}</h2>
        <div class="panel-actions">
          <span class="badge">${escapeHtml(widget.datasource_key)}</span>
          ${renderEditWidgetButton(widget.widget_key)}
        </div>
      </div>
      <div class="panel-body">
        ${result.status === "ok" ? renderTimeSeriesChart(result) : `<div class="error">${escapeHtml(result.error || "Widget query failed.")}</div>`}
      </div>
    </section>`;
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
        <label>Label<input name="label" value="${escapeHtml(widget.label)}" /></label>
        <div class="subgrid">
          <label>Type<select name="widget_type">${renderWidgetTypeOptions(widget.widget_type)}</select></label>
          <label>Datasource<select name="datasource_key">${renderOverviewDatasourceOptions(widget.datasource_key)}</select></label>
        </div>
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
function getOverviewEditorWidget() {
    if (!state.overviewEditorWidgetKey) return null;
    return (state.overview?.widgets || []).find((widget)=>widget.widget_key === state.overviewEditorWidgetKey) || null;
}
function renderWidgetTypeOptions(selected) {
    return [
        "scalar",
        "timeseries"
    ].map((value)=>`<option value="${value}" ${value === selected ? "selected" : ""}>${value}</option>`).join("");
}
function renderOverviewDatasourceOptions(selected) {
    const datasources = state.overview?.datasources || [];
    return datasources.map((item)=>`<option value="${escapeHtml(item.connector_key)}" ${item.connector_key === selected ? "selected" : ""}>${escapeHtml(item.name)} (${escapeHtml(item.connector_key)})</option>`).join("");
}
function renderTimeSeriesChart(result) {
    const points = normalizeChartPoints(result);
    if (!points.length) {
        return `<div class="empty-state">No data yet.</div>`;
    }
    const max = Math.max(...points.map((point)=>point.value), 1);
    const dates = Array.from(new Set(points.map((point)=>point.date)));
    const series = Array.from(new Set(points.map((point)=>point.series)));
    return `
    <div class="chart">
      ${dates.map((date)=>{
        const datePoints = points.filter((point)=>point.date === date);
        return `<div class="chart-row">
          <div class="chart-date">${escapeHtml(date)}</div>
          <div class="chart-bars">
            ${datePoints.map((point)=>`<div class="chart-bar" title="${escapeHtml(`${point.series}: ${point.value}`)}" style="width: ${Math.max(4, point.value / max * 100)}%"><span>${escapeHtml(point.series)}: ${escapeHtml(point.value)}</span></div>`).join("")}
          </div>
        </div>`;
    }).join("")}
    </div>
    <div class="chart-legend">${series.map((item)=>`<span>${escapeHtml(item)}</span>`).join("")}</div>`;
}
function normalizeChartPoints(result) {
    const rows = result.rows || [];
    const columns = result.columns || Object.keys(rows[0] || {});
    if (!rows.length || columns.length < 2) {
        return [];
    }
    const dateColumn = columns[0];
    if (columns.length === 3 && rows.some((row)=>!isNumeric(row[columns[1]]) && isNumeric(row[columns[2]]))) {
        return rows.filter((row)=>isNumeric(row[columns[2]])).map((row)=>({
                date: formatChartDate(row[dateColumn]),
                series: String(row[columns[1]] ?? "series"),
                value: Number(row[columns[2]])
            }));
    }
    return rows.flatMap((row)=>columns.slice(1).filter((column)=>isNumeric(row[column])).map((column)=>({
                date: formatChartDate(row[dateColumn]),
                series: column,
                value: Number(row[column])
            })));
}
function formatChartDate(value) {
    return String(value ?? "").slice(0, 10);
}
function isNumeric(value) {
    return value !== null && value !== "" && !Array.isArray(value) && Number.isFinite(Number(value));
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
        <tbody>${state.dataAudit.map((item)=>`<tr><td>${escapeHtml(formatAuditTime(item.occurred_at))}</td><td>${escapeHtml(item.audit_type || "info")}</td><td>${escapeHtml(item.output_classification || "unknown")}</td><td>${renderAuditLearning(item)}</td><td>${escapeHtml(item.user_id)}</td><td>${escapeHtml(item.datasource_id)}</td><td>${escapeHtml(item.question)}</td><td>${escapeHtml(item.answer)}</td><td><code>${escapeHtml(item.sql)}</code></td><td>${renderAuditMetadata(item)}</td></tr>`).join("")}</tbody>
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
    return dataAuditTypes.map((type)=>`<option value="${escapeHtml(type.value)}" ${state.dataAuditType === type.value ? "selected" : ""}>${escapeHtml(type.label)}</option>`).join("");
}
function renderOutputClassificationOptions() {
    return outputClassifications.map((item)=>`<option value="${escapeHtml(item.value)}" ${state.dataAuditOutputClassification === item.value ? "selected" : ""}>${escapeHtml(item.label)}</option>`).join("");
}
function renderPrompts() {
    const selected = state.prompts.find((prompt)=>prompt.prompt_key === state.selectedPromptKey) || state.prompts[0];
    return `
    <div class="split">
      <section class="panel">
        <div class="panel-header"><h2>Prompt templates</h2></div>
        <div class="panel-body list">${state.prompts.map((prompt)=>`<button data-prompt="${prompt.prompt_key}" class="${selected?.prompt_key === prompt.prompt_key ? "active" : ""}"><strong>${escapeHtml(prompt.name)}</strong><br /><span>v${escapeHtml(prompt.version)} ${prompt.active ? "active" : "inactive"}</span></button>`).join("")}</div>
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
        <div class="panel-body list">${state.datasources.map((connector)=>`<button data-datasource="${connector.id}" class="${selected?.id === connector.id ? "active" : ""}"><strong>${escapeHtml(connector.name)}</strong><br /><span>${escapeHtml(connector.database_type)} ${connector.active ? "active" : ""}</span></button>`).join("")}</div>
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
    return state.datasources.find((item)=>item.id === state.selectedDatasourceId) || state.datasources[0] || null;
}
function renderDatasourceForm(connector) {
    const systemManaged = connector?.system_managed === true;
    const disabled = systemManaged ? "disabled" : "";
    return `
    <form id="datasource-form" class="form-grid">
      <input type="hidden" name="id" value="${escapeHtml(connector?.id || "")}" />
      ${systemManaged ? `<div class="badge">System managed</div>` : ""}
      <label>Connector key<input name="connector_key" ${connector || systemManaged ? "readonly" : ""} ${disabled} value="${escapeHtml(connector?.connector_key || "")}" /></label>
      <label>Name<input name="name" ${disabled} value="${escapeHtml(connector?.name || "")}" /></label>
      <div class="subgrid">
        <label>Database type<select name="database_type" ${disabled}>${renderTypeOptions(connector?.database_type || "sqlite")}</select></label>
        <label>SQL dialect<select name="sql_dialect" ${disabled}>${renderTypeOptions(connector?.sql_dialect || "sqlite")}</select></label>
      </div>
      <label>Database URL<input name="database_url" ${disabled} value="${escapeHtml(connector?.database_url || "sqlite:///./examples/medical-poc/demo.db")}" /></label>
      <label class="inline-check"><input name="active" type="checkbox" ${connector?.active ? "checked" : ""} ${disabled} /> Active datasource</label>
      <div class="button-row">
        <button type="button" id="test-datasource" ${connector ? "" : "disabled"}>Test</button>
        <button type="button" id="introspect-datasource" ${connector ? "" : "disabled"}>Schema introspection</button>
        <button type="button" id="activate-datasource" ${connector && !connector.active && !systemManaged ? "" : "disabled"}>Activate</button>
        <button class="primary" type="submit" ${systemManaged ? "disabled" : ""}>${connector ? "Save" : "Create"}</button>
      </div>
    </form>`;
}
function renderTypeOptions(selected) {
    return [
        "sqlite",
        "postgresql",
        "mysql"
    ].map((value)=>`<option value="${value}" ${selected === value ? "selected" : ""}>${value}</option>`).join("");
}
function renderModeOptions(selected, values) {
    return values.map((value)=>`<option value="${value}" ${selected === value ? "selected" : ""}>${value}</option>`).join("");
}
function renderDatasourceSchema() {
    const schema = state.datasourceSchema?.item;
    const rawTables = schema?.raw_schema?.tables || [];
    const tableSettings = schema?.table_settings?.tables || {};
    return `
    <section class="panel">
      <div class="panel-header"><h2>Schema introspection</h2><span class="badge">${escapeHtml(schema?.introspected_at || "not cached")}</span></div>
      <div class="panel-body">
        ${schema ? `<form id="datasource-schema-form" class="form-grid">${rawTables.map((table)=>renderDatasourceTable(table, tableSettings[table.name] || {})).join("")}<div class="form-actions"><button class="primary" type="submit">Save schema settings</button></div></form>` : `<p class="muted">Run schema introspection to cache tables, keys and relationships.</p>`}
      </div>
    </section>`;
}
function renderBusinessLogicSuggestions() {
    const datasource = state.businessLogicDatasource;
    return `
    <section class="panel">
      <div class="panel-header">
        <h2>Business logic suggestions</h2>
        <span class="badge">${escapeHtml(datasource?.connector_key || "no active datasource")}</span>
      </div>
      <div class="table-wrap"><table>
        <thead><tr><th>Use</th><th>Status</th><th>Safety</th><th>Rule</th><th>Error</th><th>Confidence</th><th>Actions</th></tr></thead>
        <tbody>${state.businessLogic.map((item)=>`
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
    return state.businessLogic.find((item)=>Number(item.id) === state.businessLogicEditorId) || null;
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
          <label class="checkbox-row"><input name="clear_api_key" type="checkbox" /> Clear API key</label>
          <label>Model<input name="model" value="${escapeHtml(config.model || "")}" /></label>
          <label>LLM timeout seconds<input name="timeout_seconds" type="number" min="1" max="600" value="${escapeHtml(config.timeout_seconds || 60)}" /></label>
          <div class="subgrid">
            <label>Intent mode<select name="intent_classification_mode">${renderModeOptions(config.intent_classification_mode || "auto", [
        "auto",
        "llm"
    ])}</select></label>
            <label>SQL generation<select name="sql_generation_mode">${renderModeOptions(config.sql_generation_mode || "llm", [
        "llm"
    ])}</select></label>
          </div>
          <div class="subgrid">
            <label>Result interpretation<select name="result_interpretation_mode">${renderModeOptions(config.result_interpretation_mode || "llm", [
        "llm"
    ])}</select></label>
            <label>Output classification<select name="output_classification_mode">${renderModeOptions(config.output_classification_mode || "auto", [
        "auto",
        "llm"
    ])}</select></label>
          </div>
          <div class="subgrid">
            <label>Investigation mode<select name="investigation_mode">${renderModeOptions(config.investigation_mode || "llm", [
        "llm"
    ])}</select></label>
            <label>Ambiguity handling<select name="investigation_ambiguity_mode">${renderModeOptions(config.investigation_ambiguity_mode || "clarify", [
        "clarify",
        "safe_aggregate"
    ])}</select></label>
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
function renderDatasourceTable(table, settings) {
    const selected = settings.selected !== false;
    return `
    <section class="table-settings" data-table="${escapeHtml(table.name)}">
      <label class="inline-check"><input name="${escapeHtml(table.name)}__selected" type="checkbox" ${selected ? "checked" : ""} /> ${escapeHtml(table.name)}</label>
      <div class="muted">${escapeHtml((table.columns || []).map((column)=>`${column.name}:${column.type}${column.primary_key ? " pk" : ""}`).join(", "))}</div>
      <label>Description<input name="${escapeHtml(table.name)}__description" value="${escapeHtml(settings.description || "")}" /></label>
      <label>Primary key guidance<input name="${escapeHtml(table.name)}__primary_key_prompt" value="${escapeHtml(settings.primary_key_prompt || "")}" /></label>
      <label>Foreign key guidance<input name="${escapeHtml(table.name)}__foreign_key_prompt" value="${escapeHtml(settings.foreign_key_prompt || "")}" /></label>
      <label>Join logic<textarea class="textarea-small" name="${escapeHtml(table.name)}__join_logic">${escapeHtml(settings.join_logic || "")}</textarea></label>
    </section>`;
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
    return `<section class="panel"><div class="panel-header"><h2>License</h2></div><div class="panel-body mono">${escapeHtml(JSON.stringify(state.license || {}, null, 2))}</div></section>`;
}
function renderAdminAudit() {
    return `
    <section class="panel"><div class="panel-header"><h2>Admin audit</h2></div>
      <div class="table-wrap"><table>
        <thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Resource</th><th>Details</th></tr></thead>
        <tbody>${state.adminAudit.map((item)=>`<tr><td>${escapeHtml(item.occurred_at)}</td><td>${escapeHtml(item.actor)}</td><td>${escapeHtml(item.action)}</td><td>${escapeHtml(item.resource_type)}:${escapeHtml(item.resource_id)}</td><td><code>${escapeHtml(JSON.stringify(item.details))}</code></td></tr>`).join("")}</tbody>
      </table></div>
    </section>`;
}
function attachSectionHandlers() {
    document.querySelectorAll("[data-edit-overview-widget]").forEach((button)=>{
        button.addEventListener("click", ()=>{
            state.overviewEditorWidgetKey = button.dataset.editOverviewWidget || null;
            render();
        });
    });
    document.querySelectorAll("[data-close-overview-widget]").forEach((button)=>{
        button.addEventListener("click", ()=>{
            state.overviewEditorWidgetKey = null;
            render();
        });
    });
    document.querySelector("[data-overview-widget-backdrop]")?.addEventListener("click", (event)=>{
        if (event.target === event.currentTarget) {
            state.overviewEditorWidgetKey = null;
            render();
        }
    });
    document.querySelectorAll("[data-overview-widget-form]").forEach((form)=>{
        form.addEventListener("submit", saveOverviewWidget);
    });
    document.querySelector("#data-audit-filter-form")?.addEventListener("submit", loadDataAuditForFilters);
    document.querySelector("#retention-form")?.addEventListener("submit", saveRetention);
    document.querySelector("#data-audit-type")?.addEventListener("change", loadDataAuditForFilters);
    document.querySelector("#data-audit-output-classification")?.addEventListener("change", loadDataAuditForFilters);
    document.querySelector("#prompt-form")?.addEventListener("submit", savePrompt);
    document.querySelector("#schema-cache-form")?.addEventListener("submit", saveSchemaCacheTtl);
    document.querySelector("#llm-config-form")?.addEventListener("submit", saveLlmConfig);
    document.querySelector("#governance-policy-form")?.addEventListener("submit", saveGovernancePolicy);
    document.querySelector("#datasource-form")?.addEventListener("submit", saveDatasource);
    document.querySelector("#datasource-schema-form")?.addEventListener("submit", saveDatasourceSchema);
    document.querySelector("#invalidate-schema-cache")?.addEventListener("click", invalidateSchemaCache);
    document.querySelector("#test-datasource")?.addEventListener("click", testDatasource);
    document.querySelector("#introspect-datasource")?.addEventListener("click", introspectDatasource);
    document.querySelector("#activate-datasource")?.addEventListener("click", activateDatasource);
    document.querySelectorAll("[data-open-business-logic]").forEach((button)=>{
        button.addEventListener("click", async ()=>{
            state.section = "business-logic";
            render();
            await loadBusinessLogicSuggestions();
        });
    });
    document.querySelectorAll("[data-business-logic-toggle]").forEach((input)=>{
        input.addEventListener("change", updateBusinessLogicSuggestion);
    });
    document.querySelectorAll("[data-business-logic-edit]").forEach((button)=>{
        button.addEventListener("click", ()=>{
            state.businessLogicEditorId = Number(button.dataset.businessLogicEdit);
            render();
        });
    });
    document.querySelectorAll("[data-close-business-logic-editor]").forEach((button)=>{
        button.addEventListener("click", ()=>{
            state.businessLogicEditorId = null;
            render();
        });
    });
    document.querySelector("[data-business-logic-backdrop]")?.addEventListener("click", (event)=>{
        if (event.target === event.currentTarget) {
            state.businessLogicEditorId = null;
            render();
        }
    });
    document.querySelectorAll("[data-business-logic-form]").forEach((form)=>{
        form.addEventListener("submit", saveBusinessLogicSuggestion);
    });
    document.querySelectorAll("[data-business-logic-delete]").forEach((button)=>{
        button.addEventListener("click", deleteBusinessLogicSuggestion);
    });
    document.querySelector("#new-datasource")?.addEventListener("click", ()=>{
        state.selectedDatasourceId = "new";
        state.datasourceSchema = null;
        render();
    });
    document.querySelectorAll("[data-prompt]").forEach((button)=>{
        button.addEventListener("click", ()=>{
            state.selectedPromptKey = button.dataset.prompt || "";
            render();
        });
    });
    document.querySelectorAll("[data-datasource]").forEach((button)=>{
        button.addEventListener("click", async ()=>{
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
    try {
        await api(`/api/v1/admin/overview/widgets/${encodeURIComponent(widgetKey)}`, {
            method: "PUT",
            body: JSON.stringify({
                label: form.get("label"),
                widget_type: form.get("widget_type"),
                datasource_key: form.get("datasource_key"),
                question: form.get("question")
            })
        });
        setMessage("success", "Overview widget saved.");
        state.overviewEditorWidgetKey = null;
        await loadOverview();
    } catch (error) {
        setMessage("error", error.message);
        render();
    }
}
async function updateBusinessLogicSuggestion(event) {
    const input = event.currentTarget;
    const id = input.dataset.businessLogicToggle;
    if (!id) return;
    try {
        await api(`/api/v1/admin/business-logic-suggestions/${encodeURIComponent(id)}`, {
            method: "PUT",
            body: JSON.stringify({
                enabled: input.checked
            })
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
async function saveLlmConfig(event) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
        const extraBody = JSON.parse(String(form.get("extra_body") || "{}"));
        if (extraBody === null || Array.isArray(extraBody) || typeof extraBody !== "object") {
            throw new Error("Extra body JSON must be an object.");
        }
        const result = await api("/api/v1/admin/llm-config", {
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
                extra_body: extraBody
            })
        });
        state.llmConfig = result.item;
        setMessage("success", "LLM configuration saved.");
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
        await api("/api/v1/admin/audit/settings", {
            method: "PUT",
            body: JSON.stringify({
                data_query_retention_days: Number(form.get("retention"))
            })
        });
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
        database_url: form.get("database_url"),
        sql_dialect: form.get("sql_dialect"),
        active: form.get("active") === "on"
    };
    try {
        const result = await api(id ? `/api/v1/admin/datasources/${id}` : "/api/v1/admin/datasources", {
            method: id ? "PUT" : "POST",
            body: JSON.stringify(id ? {
                ...payload,
                connector_key: undefined
            } : payload)
        });
        state.selectedDatasourceId = result.item.id;
        setMessage("success", "Datasource saved.");
        await loadDatasources();
    } catch (error) {
        setMessage("error", error.message);
        render();
    }
}
async function testDatasource() {
    const selected = getSelectedDatasource();
    if (!selected) return;
    try {
        await api(`/api/v1/admin/datasources/${selected.id}/test`, {
            method: "POST"
        });
        setMessage("success", "Connection test succeeded.");
        render();
    } catch (error) {
        setMessage("error", error.message);
        render();
    }
}
async function introspectDatasource() {
    const selected = getSelectedDatasource();
    if (!selected) return;
    try {
        state.datasourceSchema = await api(`/api/v1/admin/datasources/${selected.id}/introspect`, {
            method: "POST"
        });
        setMessage("success", "Schema introspection completed.");
        render();
    } catch (error) {
        setMessage("error", error.message);
        render();
    }
}
async function activateDatasource() {
    const selected = getSelectedDatasource();
    if (!selected) return;
    try {
        await api(`/api/v1/admin/datasources/${selected.id}/activate`, {
            method: "POST"
        });
        setMessage("success", "Datasource activated.");
        await loadDatasources();
    } catch (error) {
        setMessage("error", error.message);
        render();
    }
}
async function saveDatasourceSchema(event) {
    event.preventDefault();
    const selected = getSelectedDatasource();
    if (!selected || !state.datasourceSchema?.item) return;
    const form = new FormData(event.currentTarget);
    const rawTables = state.datasourceSchema.item.raw_schema.tables || [];
    const tables = {};
    for (const table of rawTables){
        tables[table.name] = {
            selected: form.get(`${table.name}__selected`) === "on",
            description: form.get(`${table.name}__description`) || "",
            primary_key_prompt: form.get(`${table.name}__primary_key_prompt`) || "",
            foreign_key_prompt: form.get(`${table.name}__foreign_key_prompt`) || "",
            join_logic: form.get(`${table.name}__join_logic`) || ""
        };
    }
    try {
        state.datasourceSchema = await api(`/api/v1/admin/datasources/${selected.id}/schema/tables`, {
            method: "PUT",
            body: JSON.stringify({
                tables
            })
        });
        setMessage("success", "Schema settings saved.");
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
        await api("/api/v1/admin/schema-cache", {
            method: "PUT",
            body: JSON.stringify({
                ttl_seconds: Number(form.get("ttl_seconds"))
            })
        });
        setMessage("success", "Schema cache TTL saved.");
        await loadSchemaCache();
    } catch (error) {
        setMessage("error", error.message);
        render();
    }
}
async function invalidateSchemaCache() {
    try {
        await api("/api/v1/admin/schema-cache/invalidate", {
            method: "POST"
        });
        setMessage("success", "Schema cache invalidated.");
        await loadSchemaCache();
    } catch (error) {
        setMessage("error", error.message);
        render();
    }
}
async function loadCurrentSection() {
    if (!state.token || state.mustChangePassword) return;
    if (state.section === "overview") await loadOverview();
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
async function loadOverview() {
    state.overview = await api("/api/v1/admin/overview");
    render();
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
    const result = await api("/api/v1/admin/datasources");
    state.datasources = result.items || [];
    if (!state.selectedDatasourceId || state.selectedDatasourceId === "new") {
        state.selectedDatasourceId = state.datasources[0]?.id || null;
    }
    await loadDatasourceSchema(false);
    render();
}
async function loadDatasourceSchema(shouldRender = true) {
    const selected = getSelectedDatasource();
    if (!selected) {
        state.datasourceSchema = null;
        render();
        return;
    }
    state.datasourceSchema = await api(`/api/v1/admin/datasources/${selected.id}/schema`);
    if (shouldRender) render();
}
async function loadSchemaCache() {
    state.schemaCache = await api("/api/v1/admin/schema-cache");
    render();
}
async function loadBusinessLogicSuggestions() {
    const result = await api("/api/v1/admin/business-logic-suggestions");
    state.businessLogic = result.items || [];
    state.businessLogicDatasource = result.datasource || null;
    render();
}
async function loadLlmConfig() {
    const result = await api("/api/v1/admin/llm-config");
    state.llmConfig = result.item || null;
    render();
}
async function loadGovernancePolicy() {
    const result = await api("/api/v1/admin/governance-policy");
    state.governancePolicy = result.item || null;
    render();
}
async function loadLicense() {
    state.license = await api("/api/v1/admin/license");
    render();
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
        render();
        await loadCurrentSection();
    } catch (error) {
        setMessage("error", error.message);
        render();
    }
}
void bootstrap();
