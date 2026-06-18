const app = document.querySelector("#app");

const params = new URLSearchParams(window.location.search);
const configuredBackendUrl = (
    params.get("backendUrl") ||
    params.get("apiUrl") ||
    window.GAARD_CLIENT_CONFIG?.backendUrl ||
    "http://localhost:8000"
).replace(/\/+$/, "");

const state = {
    backendUrl: configuredBackendUrl,
    queryMode: normalizeQueryMode(params.get("mode")),
    messages: [],
    nextMessageId: 1,
    pending: false,
    error: ""
};

function escapeHtml(value) {
    return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

function render(options = {}) {
    if (!app) return;

    app.innerHTML = `
    <main class="shell">
      <header class="header">
        <h1>GAARD - Governed AI Access to Relational Data</h1>
      </header>
      <section class="history" aria-live="polite">
        ${state.messages.length ? state.messages.map(renderMessage).join("") : `<div class="empty-state">Ask a governed data question.</div>`}
      </section>
      <form id="query-form" class="query-bar">
        <fieldset class="mode-control" ${state.pending ? "disabled" : ""}>
          <legend>Mode</legend>
          <label class="${state.queryMode === "sql" ? "active" : ""}">
            <input type="radio" name="mode" value="sql" ${state.queryMode === "sql" ? "checked" : ""}>
            <span>SQL</span>
          </label>
          <label class="${state.queryMode === "investigation" ? "active" : ""}">
            <input type="radio" name="mode" value="investigation" ${state.queryMode === "investigation" ? "checked" : ""}>
            <span>Investigation</span>
          </label>
        </fieldset>
        <textarea id="question-input" name="question" placeholder="Ask a question" rows="1" ${state.pending ? "disabled" : ""}></textarea>
        <button class="send-button" type="submit" aria-label="Send question" title="Send" ${state.pending ? "disabled" : ""}>
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            <path d="M22 2 11 13" />
            <path d="m22 2-7 20-4-9-9-4Z" />
          </svg>
        </button>
      </form>
    </main>`;

    document.querySelector("#query-form")?.addEventListener("submit", submitQuestion);
    document.querySelectorAll('input[name="mode"]').forEach((input) => {
        input.addEventListener("change", handleModeChange);
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
    const input = document.querySelector("#question-input");
    input?.addEventListener("keydown", handleQuestionKeydown);
    input?.focus();

    if (options.scrollToLatest) {
        scrollToLatest();
    }
}

function renderMessage(message) {
    const rows = getRows(message.response);
    const meta = message.status === "ok" ? renderMeta(message, rows) : "";
    const answer = message.status === "pending"
        ? "Processing..."
        : message.status === "error"
            ? message.error
            : message.response?.answer || "";
    const dataTable = message.status === "ok" && message.dataOpen ? renderDataTable(rows) : "";
    const mockWarning = message.status === "ok" ? renderMockWarning(message.response?.metadata) : "";
    const saveNotice = renderSaveNotice(message);
    const progress = message.status === "pending" && message.mode === "investigation" ? renderInvestigationProgress(message) : "";

    return `
    <article class="exchange ${message.status}">
      <div class="exchange-top">
        <div class="question">
          <span>Question · ${escapeHtml(formatMode(message.mode))}</span>
          <p>${escapeHtml(message.question)}</p>
        </div>
        ${renderMessageActions(message)}
      </div>
      <div class="answer">
        <span>Answer</span>
        <p>${escapeHtml(answer)}</p>
      </div>
      ${progress}
      ${mockWarning}
      ${saveNotice}
      ${meta}
      ${dataTable}
    </article>`;
}

function renderMessageActions(message) {
    const saveDisabled = state.pending || message.saveStatus === "saving" || message.saveStatus === "saved";
    const saveTitle = message.saveStatus === "saved"
        ? "Saved as widget"
        : message.saveStatus === "saving"
            ? "Saving widget"
            : "Save as widget";

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

function renderInvestigationProgress(message) {
    if (!message.progress.length) {
        return "";
    }

    return `
    <ol class="investigation-progress" aria-label="Investigation progress">
      ${message.progress.map((update, index) => `
        <li class="${index === message.progress.length - 1 ? "active" : "done"}">
          <div>
            <p>${escapeHtml(update.data_question)}</p>
            ${renderProgressDecisions(update.decisions)}
          </div>
        </li>`).join("")}
    </ol>`;
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

    return `
    <div class="meta-row">
      <dl class="meta">
        <div><dt>Time</dt><dd>${escapeHtml(formatDuration(metadata.duration_ms))}</dd></div>
        <div><dt>Datasource</dt><dd>${escapeHtml(metadata.datasource_id || "-")}</dd></div>
        <div><dt>Mode</dt><dd>${escapeHtml(formatMode(metadata.query_mode || message.mode))}</dd></div>
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
    return value === "investigation" ? "Investigation" : "SQL";
}

function normalizeQueryMode(value) {
    return value === "investigation" ? "investigation" : "sql";
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

function retryQuestion(event) {
    const id = Number(event.currentTarget.dataset.retryQuestion);
    const message = state.messages.find((item) => item.id === id);

    if (!message || state.pending) return;

    state.queryMode = message.mode;
    render();
    const input = document.querySelector("#question-input");

    if (!input || input.disabled) return;

    input.value = message.question;
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
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
                "Content-Type": "application/json"
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

    const input = event.currentTarget.elements.namedItem("question");
    const question = String(input?.value || "").trim();
    const mode = getSelectedMode(event.currentTarget);

    if (!question) return;

    input.value = "";
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
        progress: []
    };
    state.nextMessageId += 1;
    state.messages.push(message);
    render({ scrollToLatest: true });

    try {
        if (mode === "investigation") {
            await submitInvestigationQuestion(message, question, mode);
        } else {
            const response = await fetch("/api/query", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
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

async function submitInvestigationQuestion(message, question, mode) {
    const response = await fetch("/api/query/stream", {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify({
            question,
            mode,
            backend_url: state.backendUrl
        })
    });

    if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(extractErrorMessage(payload));
    }

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
            finalReceived = handleStreamLine(message, line) || finalReceived;
        }
    }

    buffer += decoder.decode();
    if (buffer.trim()) {
        finalReceived = handleStreamLine(message, buffer) || finalReceived;
    }

    if (!finalReceived) {
        throw new Error("Investigation stream ended without a final response.");
    }
}

function handleStreamLine(message, line) {
    const trimmed = line.trim();

    if (!trimmed) return false;

    const payload = JSON.parse(trimmed);

    if (payload?.error?.message) {
        throw new Error(payload.error.message);
    }

    if (payload?.final) {
        message.status = "ok";
        message.response = payload.final;
        render({ scrollToLatest: true });
        return true;
    }

    if (isProgressUpdate(payload)) {
        message.progress = [
            ...message.progress,
            payload
        ];
        render({ scrollToLatest: true });
    }

    return false;
}

function isProgressUpdate(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
        return false;
    }

    return (typeof value.data_question === "string" &&
        Array.isArray(value.decisions) &&
        value.decisions.every((decision) => typeof decision === "string"));
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
