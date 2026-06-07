type QueryMetadata = {
  duration_ms?: number;
  datasource_id?: string;
  query_mode?: QueryMode;
  output_classification?: string;
  output_classification_mode?: string;
  result_interpretation_mode?: string;
  sql_generation_mode?: string;
};

type QueryMode = "sql" | "investigation";

type QueryResponse = {
  answer: string;
  rows?: Record<string, unknown>[];
  metadata?: QueryMetadata;
};

type ProgressUpdate = {
  data_question: string;
  decisions: string[];
};

type Message = {
  id: number;
  question: string;
  mode: QueryMode;
  status: "pending" | "ok" | "error";
  response: QueryResponse | null;
  error: string;
  dataOpen: boolean;
  progress: ProgressUpdate[];
};

declare global {
  interface Window {
    GAARD_CLIENT_CONFIG?: {
      backendUrl?: string;
    };
  }
}

const app = document.querySelector<HTMLDivElement>("#app");

const params = new URLSearchParams(window.location.search);
const configuredBackendUrl = (
  params.get("backendUrl") ||
  params.get("apiUrl") ||
  window.GAARD_CLIENT_CONFIG?.backendUrl ||
  "http://localhost:8000"
).replace(/\/+$/, "");

const state: {
  backendUrl: string;
  queryMode: QueryMode;
  messages: Message[];
  nextMessageId: number;
  pending: boolean;
  error: string;
} = {
  backendUrl: configuredBackendUrl,
  queryMode: normalizeQueryMode(params.get("mode")),
  messages: [],
  nextMessageId: 1,
  pending: false,
  error: "",
};

function escapeHtml(value: unknown): string {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function render(options: { scrollToLatest?: boolean } = {}): void {
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

  document.querySelector<HTMLFormElement>("#query-form")?.addEventListener("submit", submitQuestion);
  document.querySelectorAll<HTMLInputElement>('input[name="mode"]').forEach((input) => {
    input.addEventListener("change", handleModeChange);
  });
  document.querySelectorAll<HTMLButtonElement>("[data-toggle-data]").forEach((button) => {
    button.addEventListener("click", toggleDataTable);
  });
  document.querySelectorAll<HTMLButtonElement>("[data-retry-question]").forEach((button) => {
    button.addEventListener("click", retryQuestion);
  });
  const input = document.querySelector<HTMLTextAreaElement>("#question-input");
  input?.addEventListener("keydown", handleQuestionKeydown);
  input?.focus();

  if (options.scrollToLatest) {
    scrollToLatest();
  }
}

function renderMessage(message: Message): string {
  const rows = getRows(message.response);
  const meta = message.status === "ok" ? renderMeta(message, rows) : "";
  const answer = message.status === "pending"
    ? "Processing..."
    : message.status === "error"
      ? message.error
      : message.response?.answer || "";
  const dataTable = message.status === "ok" && message.dataOpen ? renderDataTable(rows) : "";
  const mockWarning = message.status === "ok" ? renderMockWarning(message.response?.metadata) : "";
  const progress = message.status === "pending" && message.mode === "investigation"
    ? renderInvestigationProgress(message)
    : "";

  return `
    <article class="exchange ${message.status}">
      <div class="exchange-top">
        <div class="question">
          <span>Question · ${escapeHtml(formatMode(message.mode))}</span>
          <p>${escapeHtml(message.question)}</p>
        </div>
        <button class="retry-button" type="button" data-retry-question="${message.id}" aria-label="Copy question to input" title="Retry question" ${state.pending ? "disabled" : ""}>
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            <path d="M3 12a9 9 0 1 0 2.64-6.36L3 8" />
            <path d="M3 3v5h5" />
          </svg>
        </button>
      </div>
      <div class="answer">
        <span>Answer</span>
        <p>${escapeHtml(answer)}</p>
      </div>
      ${progress}
      ${mockWarning}
      ${meta}
      ${dataTable}
    </article>`;
}

function renderInvestigationProgress(message: Message): string {
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

function renderProgressDecisions(decisions: string[]): string {
  const visible = decisions.filter((decision) => decision.trim()).slice(0, 3);

  if (!visible.length) {
    return "";
  }

  return `<ul>${visible.map((decision) => `<li>${escapeHtml(decision)}</li>`).join("")}</ul>`;
}

function renderMockWarning(metadata?: QueryMetadata): string {
  const mockModes = [
    ["SQL generation", metadata?.sql_generation_mode],
    ["Result interpretation", metadata?.result_interpretation_mode],
    ["Output classification", metadata?.output_classification_mode],
  ]
    .filter(([, mode]) => mode === "mock")
    .map(([label]) => label);

  if (!mockModes.length) {
    return "";
  }

  return `
    <div class="mock-warning" role="status">
      This response used mock data processing: ${escapeHtml(mockModes.join(", "))}.
    </div>`;
}

function renderMeta(message: Message, rows: Record<string, unknown>[]): string {
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

function formatDuration(value: unknown): string {
  const numeric = Number(value);

  if (!Number.isFinite(numeric)) {
    return "-";
  }

  return `${numeric} ms`;
}

function formatMode(value: unknown): string {
  return value === "investigation" ? "Investigation" : "SQL";
}

function normalizeQueryMode(value: unknown): QueryMode {
  return value === "investigation" ? "investigation" : "sql";
}

function handleModeChange(event: Event): void {
  state.queryMode = normalizeQueryMode((event.currentTarget as HTMLInputElement).value);
  render();
}

function handleQuestionKeydown(event: KeyboardEvent): void {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    (event.currentTarget as HTMLTextAreaElement).form?.requestSubmit();
  }
}

function toggleDataTable(event: Event): void {
  const id = Number((event.currentTarget as HTMLButtonElement).dataset.toggleData);
  const message = state.messages.find((item) => item.id === id);

  if (!message) return;

  message.dataOpen = !message.dataOpen;
  const latestMessage = state.messages[state.messages.length - 1];

  render({
    scrollToLatest: message.dataOpen && latestMessage?.id === message.id,
  });
}

function retryQuestion(event: Event): void {
  const id = Number((event.currentTarget as HTMLButtonElement).dataset.retryQuestion);
  const message = state.messages.find((item) => item.id === id);

  if (!message || state.pending) return;

  state.queryMode = message.mode;
  render();
  const refreshedInput = document.querySelector<HTMLTextAreaElement>("#question-input");

  if (!refreshedInput || refreshedInput.disabled) return;

  refreshedInput.value = message.question;
  refreshedInput.focus();
  refreshedInput.setSelectionRange(refreshedInput.value.length, refreshedInput.value.length);
}

function getSelectedMode(form: HTMLFormElement): QueryMode {
  const value = new FormData(form).get("mode");

  return normalizeQueryMode(value);
}

async function submitQuestion(event: Event): Promise<void> {
  event.preventDefault();

  if (state.pending) return;

  const form = event.currentTarget as HTMLFormElement;
  const input = form.elements.namedItem("question") as HTMLTextAreaElement | null;
  const question = String(input?.value || "").trim();
  const mode = getSelectedMode(form);

  if (!question) return;

  if (input) input.value = "";

  state.error = "";
  state.pending = true;
  const message: Message = {
    id: state.nextMessageId,
    question,
    mode,
    status: "pending",
    response: null,
    error: "",
    dataOpen: false,
    progress: [],
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
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          question,
          mode,
          backend_url: state.backendUrl,
        }),
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
    message.error = (error as Error).message || "Request failed.";
  } finally {
    state.pending = false;
    render({ scrollToLatest: true });
  }
}

async function submitInvestigationQuestion(
  message: Message,
  question: string,
  mode: QueryMode,
): Promise<void> {
  const response = await fetch("/api/query/stream", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      question,
      mode,
      backend_url: state.backendUrl,
    }),
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

function handleStreamLine(message: Message, line: string): boolean {
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
    message.progress = [...message.progress, payload];
    render({ scrollToLatest: true });
  }

  return false;
}

function isProgressUpdate(value: unknown): value is ProgressUpdate {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }

  const payload = value as Record<string, unknown>;

  return (
    typeof payload.data_question === "string" &&
    Array.isArray(payload.decisions) &&
    payload.decisions.every((decision) => typeof decision === "string")
  );
}

function extractErrorMessage(payload: any): string {
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

function getRows(response: QueryResponse | null): Record<string, unknown>[] {
  return Array.isArray(response?.rows) ? response.rows : [];
}

function getColumns(rows: Record<string, unknown>[]): string[] {
  const columns: string[] = [];

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

function renderDataTable(rows: Record<string, unknown>[]): string {
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

function formatCellValue(value: unknown): string {
  if (value === null) {
    return "null";
  }

  if (typeof value === "object") {
    return JSON.stringify(value);
  }

  return String(value ?? "");
}

function scrollToLatest(): void {
  const scroll = () => {
    const history = document.querySelector<HTMLElement>(".history");

    if (!history) return;

    history.scrollTo({
      top: history.scrollHeight,
      behavior: "auto",
    });
  };

  requestAnimationFrame(() => {
    scroll();
    requestAnimationFrame(scroll);
  });
}

render();
