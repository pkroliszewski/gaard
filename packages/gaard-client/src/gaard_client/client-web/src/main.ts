type QueryMetadata = {
  duration_ms?: number;
  datasource_id?: string;
  query_mode?: string;
  analysis_mode?: string;
  analysis_session_id?: string;
  analysis_status?: string;
  analysis_supporting_data?: boolean;
  analysis_supporting_question?: string;
  analysis_supporting_step?: string;
  output_classification?: string;
  output_classification_mode?: string;
  result_interpretation_mode?: string;
  sql_generation_mode?: string;
};

type QueryMode = "sql" | "analysis";

type QueryResponse = {
  answer: string;
  sql?: string;
  rows?: Record<string, unknown>[];
  metadata?: QueryMetadata;
};

type ProgressUpdate = {
  event: string;
  title: string;
  detail: string;
  items: string[];
};

type Message = {
  id: number;
  question: string;
  mode: QueryMode;
  status: "pending" | "waiting" | "ok" | "error";
  response: QueryResponse | null;
  error: string;
  dataOpen: boolean;
  saveStatus: "idle" | "saving" | "saved" | "error";
  saveError: string;
  progress: ProgressUpdate[];
  progressOpen: boolean;
  analysisSessionId: string;
  userQuestion: string;
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
          <label class="${state.queryMode === "analysis" ? "active" : ""}">
            <input type="radio" name="mode" value="analysis" ${state.queryMode === "analysis" ? "checked" : ""}>
            <span>Analysis</span>
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
  document.querySelectorAll<HTMLButtonElement>("[data-save-widget]").forEach((button) => {
    button.addEventListener("click", saveWidgetFromMessage);
  });
  document.querySelectorAll<HTMLFormElement>("[data-analysis-reply-form]").forEach((form) => {
    form.addEventListener("submit", submitAnalysisReply);
  });
  document.querySelectorAll<HTMLDetailsElement>("[data-analysis-progress]").forEach((details) => {
    details.addEventListener("toggle", toggleAnalysisProgress);
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
    : message.status === "waiting"
      ? "Waiting for your answer."
    : message.status === "error"
      ? message.error
      : message.response?.answer || "";
  const dataTable = message.status === "ok" && message.dataOpen ? renderDataTable(rows) : "";
  const mockWarning = message.status === "ok" ? renderMockWarning(message.response?.metadata) : "";
  const saveNotice = renderSaveNotice(message);
  const progress = message.mode === "analysis"
    ? renderAnalysisProgress(message)
    : "";
  const analysisReply = message.status === "waiting" ? renderAnalysisReply(message) : "";

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
      ${analysisReply}
      ${mockWarning}
      ${saveNotice}
      ${meta}
      ${dataTable}
    </article>`;
}

function renderMessageActions(message: Message): string {
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

function canSaveWidget(message: Message): boolean {
  return message.status === "ok" && Boolean(message.response?.sql?.trim());
}

function renderSaveNotice(message: Message): string {
  if (message.saveStatus === "saved") {
    return `<div class="save-notice success" role="status">Saved as inactive widget.</div>`;
  }

  if (message.saveStatus === "error") {
    return `<div class="save-notice error" role="alert">${escapeHtml(message.saveError || "Widget could not be saved.")}</div>`;
  }

  return "";
}

function renderAnalysisProgress(message: Message): string {
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

function renderAnalysisReply(message: Message): string {
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

function formatDuration(value: unknown): string {
  const numeric = Number(value);

  if (!Number.isFinite(numeric)) {
    return "-";
  }

  return `${numeric} ms`;
}

function formatMode(value: unknown): string {
  return value === "analysis" ? "Analysis" : "SQL";
}

function normalizeQueryMode(value: unknown): QueryMode {
  return value === "analysis" ? "analysis" : "sql";
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

function toggleAnalysisProgress(event: Event): void {
  const details = event.currentTarget as HTMLDetailsElement;
  const id = Number(details.dataset.analysisProgress);
  const message = state.messages.find((item) => item.id === id);

  if (message) {
    message.progressOpen = details.open;
  }
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

async function saveWidgetFromMessage(event: Event): Promise<void> {
  const id = Number((event.currentTarget as HTMLButtonElement).dataset.saveWidget);
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
      },
      body: JSON.stringify({
        label: buildWidgetLabel(message.question),
        widget_type: inferWidgetType(getRows(message.response)),
        datasource_key: message.response?.metadata?.datasource_id || "default",
        question: message.question,
        sql,
        result_mode: "data",
        backend_url: state.backendUrl,
      }),
    });
    const payload = await response.json().catch(() => ({}));

    if (!response.ok) {
      throw new Error(extractErrorMessage(payload));
    }

    message.saveStatus = "saved";
  } catch (error) {
    message.saveStatus = "error";
    message.saveError = (error as Error).message || "Widget could not be saved.";
  } finally {
    render();
  }
}

function buildWidgetLabel(question: string): string {
  const compact = question.replace(/\s+/g, " ").trim();

  return compact.length > 64 ? `${compact.slice(0, 61)}...` : compact || "Saved query";
}

function inferWidgetType(rows: Record<string, unknown>[]): "scalar" | "table" {
  if (rows.length === 1 && Object.keys(rows[0] || {}).length === 1) {
    return "scalar";
  }

  return "table";
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
    saveStatus: "idle",
    saveError: "",
    progress: [],
    progressOpen: false,
    analysisSessionId: "",
    userQuestion: "",
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

async function submitAnalysisQuestion(
  message: Message,
  question: string,
): Promise<void> {
  const response = await fetch("/api/analysis/stream", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      question,
      backend_url: state.backendUrl,
    }),
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(extractErrorMessage(payload));
  }

  await readAnalysisStream(message, response);
}

async function submitAnalysisReply(event: Event): Promise<void> {
  event.preventDefault();

  if (state.pending) return;

  const form = event.currentTarget as HTMLFormElement;
  const id = Number(form.dataset.analysisReplyForm);
  const message = state.messages.find((item) => item.id === id);
  const input = form.elements.namedItem("reply") as HTMLTextAreaElement | null;
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
    message.error = (error as Error).message || "Request failed.";
  } finally {
    state.pending = false;
    render({ scrollToLatest: true });
  }
}

async function continueAnalysis(message: Message, reply: string): Promise<void> {
  const response = await fetch(`/api/analysis/${encodeURIComponent(message.analysisSessionId)}/messages/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      message: reply,
      backend_url: state.backendUrl,
    }),
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(extractErrorMessage(payload));
  }

  await readAnalysisStream(message, response);
}

async function readAnalysisStream(message: Message, response: Response): Promise<void> {
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

function handleAnalysisStreamLine(message: Message, line: string): boolean {
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
        items: [],
      },
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

function firstText(...values: unknown[]): string {
  for (const value of values) {
    const text = String(value || "").trim();
    if (text) return text;
  }

  return "";
}

function extractUserQuestion(payload: any): string {
  const userQuestion = payload?.user_question;

  return firstText(
    typeof userQuestion === "string" ? userQuestion : "",
    userQuestion?.question,
    userQuestion?.message,
    userQuestion?.visible_question,
    payload?.question,
    payload?.decision?.user_question,
    payload?.decision?.visible_question,
    "GAARD needs a clarification.",
  );
}

function progressFromAnalysisEvent(payload: any): ProgressUpdate | null {
  const event = String(payload?.event || "");

  if (event === "analysis_step") {
    const step = payload.analysis_step || {};
    return {
      event,
      title: step.visible_question || "GAARD is checking the next analysis step.",
      detail: step.visible_reasoning || "",
      items: [`Iteration ${step.iteration || payload.sequence || ""}`].filter(Boolean),
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
        decision.answer ? `Context answer prepared.` : "",
      ].filter(Boolean),
    };
  }

  if (event === "database_question") {
    const question = payload.database_question || {};
    return {
      event,
      title: question.final ? "GAARD asks the final database question" : "GAARD asks the database",
      detail: question.question || "",
      items: [],
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
        Array.isArray(result.rows) ? `Rows: ${result.rows.length}` : "",
      ].filter(Boolean),
    };
  }

  if (event === "business_logic_suggestion") {
    const suggestion = payload.business_logic_suggestion || {};
    return {
      event,
      title: suggestion.enabled
        ? "Business logic finding enabled"
        : "Business logic finding saved for review",
      detail: suggestion.title || suggestion.rule_text || "",
      items: [
        suggestion.error_category ? `Type: ${suggestion.error_category}` : "",
        suggestion.confidence !== undefined ? `Confidence: ${suggestion.confidence}` : "",
      ].filter(Boolean),
    };
  }

  if (event === "limit_reached") {
    return {
      event,
      title: "Analysis loop limit reached",
      detail: `Limit: ${payload.limit_reached?.analysis_loop_count || "-"}`,
      items: [],
    };
  }

  if (event === "session_started" || event === "session_resumed") {
    return null;
  }

  return null;
}

function formatAnalysisAction(value: unknown): string {
  return String(value || "unknown").replaceAll("_", " ");
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
