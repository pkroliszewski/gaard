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
  queryMode: normalizeQueryMode(params.get("mode")),
  messages: [],
  nextMessageId: 1,
  pending: false,
  error: "",
  loginOpen: !storedToken
};
function escapeHtml(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}
function renderIcon(name) {
  const icons = {
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
      </svg>`
  };
  return icons[name] || "";
}
function renderSidebar() {
  const items = [
    ["dashboards", "Dashboards"],
    ["history", "Historia"],
    ["sources", "Źródła danych"]
  ];
  return `
    <aside class="sidebar" aria-label="Nawigacja">
      <div class="brand">
        <img class="brand-logo" src="/assets/getgaard.svg" alt="" />
        <div class="brand-copy">
          <strong>GAARD</strong>
          <span>Client</span>
        </div>
      </div>
      <nav class="nav-list" aria-label="Główne sekcje">
        ${items.map(([icon, label], index) => `
          <button class="nav-item ${index === 1 ? "active" : ""}" type="button" aria-disabled="true" title="${escapeHtml(label)}">
            ${renderIcon(icon)}
            <span>${escapeHtml(label)}</span>
          </button>`).join("")}
      </nav>
    </aside>`;
}
function renderAuthControls() {
  if (state.token) {
    return `
      <div class="signed-in">
        <span class="user-chip" title="${escapeHtml(state.username || "Zalogowany użytkownik")}">
          ${renderIcon("user")}
          <span>${escapeHtml(state.username || "Użytkownik")}</span>
        </span>
        <button class="ghost-button" type="button" data-logout>Wyloguj</button>
      </div>`;
  }
  return `<button class="primary auth-button" type="button" data-open-login>Zaloguj się</button>`;
}
function renderEmptyState() {
  if (!state.token) {
    return `
      <div class="empty-state locked">
        <div class="empty-icon">${renderIcon("lock")}</div>
        <h2>GAARD</h2>
        <p>Zaloguj się, aby rozpocząć rozmowę.</p>
        <button class="primary" type="button" data-open-login>Zaloguj się</button>
      </div>`;
  }
  return `
    <div class="empty-state">
      <img class="empty-logo" src="/assets/getgaard.svg" alt="" />
      <h2>Jak mogę pomóc z danymi?</h2>
    </div>`;
}
function render(options = {}) {
  if (!app) return;
  const inputDisabled = state.pending || !state.token;
  app.innerHTML = `
    <main class="app-shell">
      ${renderSidebar()}
      <section class="chat-shell" aria-label="Czat GAARD">
        <header class="topbar">
          <div class="conversation-heading">
            <span>Czat</span>
            <strong>GAARD</strong>
          </div>
          <div class="header-actions">
            ${renderAuthControls()}
          </div>
        </header>
        <section class="history" aria-live="polite">
          ${state.messages.length ? state.messages.map(renderMessage).join("") : renderEmptyState()}
        </section>
        <form id="query-form" class="query-bar">
          <fieldset class="mode-control" ${inputDisabled ? "disabled" : ""}>
            <legend>Tryb pracy</legend>
          <label class="${state.queryMode === "sql" ? "active" : ""}">
            <input type="radio" name="mode" value="sql" ${state.queryMode === "sql" ? "checked" : ""}>
            <span>SQL</span>
          </label>
          <label class="${state.queryMode === "analysis" ? "active" : ""}">
            <input type="radio" name="mode" value="analysis" ${state.queryMode === "analysis" ? "checked" : ""}>
            <span>Analiza</span>
          </label>
          </fieldset>
          <textarea id="question-input" name="question" placeholder="${state.token ? "Zadaj pytanie" : "Zaloguj się, aby zadać pytanie"}" rows="1" ${inputDisabled ? "disabled" : ""}></textarea>
          <button class="send-button" type="submit" aria-label="Wyślij pytanie" title="Wyślij" ${inputDisabled ? "disabled" : ""}>
            <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
              <path d="M22 2 11 13" />
              <path d="m22 2-7 20-4-9-9-4Z" />
            </svg>
          </button>
        </form>
      </section>
      ${state.loginOpen ? renderLoginDialog() : ""}
    </main>`;
  document.querySelector("#query-form")?.addEventListener("submit", submitQuestion);
  document.querySelector("[data-logout]")?.addEventListener("click", logout);
  document.querySelectorAll("[data-open-login]").forEach((button) => {
    button.addEventListener("click", openLogin);
  });
  document.querySelector("[data-close-login]")?.addEventListener("click", closeLogin);
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
  if (state.token) {
    input?.focus();
  }
  if (options.scrollToLatest) {
    scrollToLatest();
  }
}
function renderLoginDialog() {
  return `
    <div class="login-overlay" role="presentation">
      <section class="login-panel" role="dialog" aria-modal="true" aria-labelledby="login-title">
        <button class="icon-button close-login" type="button" data-close-login aria-label="Zamknij logowanie" title="Zamknij">
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            <path d="M18 6 6 18" />
            <path d="m6 6 12 12" />
          </svg>
        </button>
        <img class="login-logo" src="/assets/getgaard.svg" alt="" />
        <h1 id="login-title">GAARD Client</h1>
        <p>Zaloguj się kontem GAARD.</p>
        <form id="login-form" class="form-grid">
          <label>Login<input name="username" autocomplete="username" /></label>
          <label>Hasło<input name="password" type="password" autocomplete="current-password" /></label>
          ${state.error ? `<div class="error">${escapeHtml(state.error)}</div>` : ""}
          <div class="form-actions"><button class="primary" type="submit">Zaloguj się</button></div>
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
function renderMessage(message) {
  const rows = getRows(message.response);
  const meta = message.status === "ok" ? renderMeta(message, rows) : "";
  const answer = message.status === "pending" ? "Przetwarzam..." : message.status === "waiting" ? "Czekam na odpowiedź." : message.status === "error" ? message.error : message.response?.answer || "";
  const dataTable = message.status === "ok" && message.dataOpen ? renderDataTable(rows) : "";
  const mockWarning = message.status === "ok" ? renderMockWarning(message.response?.metadata) : "";
  const saveNotice = renderSaveNotice(message);
  const progress = message.mode === "analysis" ? renderAnalysisProgress(message) : "";
  const analysisReply = message.status === "waiting" ? renderAnalysisReply(message) : "";
  return `
    <article class="exchange ${message.status}">
      <div class="exchange-top">
        <div class="question">
          <span>Pytanie \xB7 ${escapeHtml(formatMode(message.mode))}</span>
          <p>${escapeHtml(message.question)}</p>
        </div>
        ${renderMessageActions(message)}
      </div>
      <div class="answer">
        <span>Odpowiedź</span>
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
  return value === "analysis" ? "Analiza" : "SQL";
}
function normalizeQueryMode(value) {
  return value === "analysis" ? "analysis" : "sql";
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
  state.loginOpen = true;
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
          backend_url: state.backendUrl
        })
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(extractErrorMessage(payload));
      }
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
