### v0.2.20 - Conversation context and query context snapshots

- Replace restrictive follow-up validation and keyword rewrites with a binary
  LLM decision: follow-up or new topic. Use every turn since the last new topic,
  without the previous two/four-turn history limits.
- Summarize a follow-up's full context, including the current question, into one
  natural-language sentence before execution. Run classification and summary
  calls with temperature zero and let the LLM interpret free-form classifier
  responses instead of returning the legacy ambiguous-context refusal.
- Persist each question's context decision and execution-time context before
  running SQL or Analysis, including failed requests and resumed analyses.
  A new topic starts a new context without inheriting old analysis state.
- Add an authenticated, owner-scoped API endpoint for each query's saved context
  and a client context dialog next to query execution metadata. Opening the
  dialog does not regenerate the context or move the chat scroll position.
- Add regression tests for context boundaries, long conversations, execution
  snapshots, access control, analysis resumption, and desktop/mobile dialog use.
- Publish all six public packages as `0.2.20` with aligned dependencies.
  Private plugin versions and their compatible dependency constraints are unchanged.

### v0.2.19 API / Core / Connectors / LLM - SQL error learning and native row limits

- Preserve manual approval and reviewed content when a repeated SQL error matches
  an existing Business Logic rule. Identical rules also deduplicate when the LLM
  omits failed/repaired identifiers, and the audit reports an already active rule.
- Generate row-limit instructions for the configured SQL dialect, including
  `TOP` for MSSQL/TSQL. Upgrade legacy default LIMIT instructions in metadata
  while preserving other administrator prompt customizations.
- Apply row limits with dialect-aware SQL parsing instead of appending `LIMIT`
  to every query. MSSQL queries use `TOP` or `OFFSET/FETCH`, and result fetching
  remains bounded even for vendor-specific SQL that cannot be rewritten.
- Add regression coverage reproducing SQL error, manual approval, and repeated
  error through the query/admin APIs, plus prompt-upgrade and executor coverage.
- Publish `gaard-api`, `gaard-core`, `gaard-connectors`, and `gaard-llm` as
  `0.2.19` with aligned dependencies. Client, plugin API, and private plugins
  retain their existing versions.

### v0.2.18 API - Preserve reviewed business logic findings

- Reuse an existing finding when the same business logic suggestion is observed
  again in an investigation, preserving its identifier, review decision, and
  accumulated evidence instead of creating a new pending finding.
- Keep approved persistent Business Logic active when it is observed again with
  automatic approval disabled. The fix applies to all datasource connectors,
  including MSSQL/TSQL.
- Bumped only `gaard-api` to `0.2.18`; other public packages and private plugins
  retain their existing versions and compatible dependency requirements.

### v0.2.17 - Coordinated package compatibility and client usability release

- Bumped all public GAARD Python packages to `0.2.17` and aligned their
  internal dependency constraints so a clean PyPI installation resolves one
  compatible release set.
- Published the extended connector contract used by datasource preview,
  materialization finalization, and datasource cleanup hooks.
- Fixed chat navigation so opening Home or returning to the active chat scrolls
  to the latest message, and kept the chat toolbar and query composer visible
  within the viewport.
- Moved dashboard sharing, layout editing, and widget creation actions into the
  Analysis top bar before the account controls.
- Enabled allowlisted HTML rendering for table dashboard widgets, including
  safe links, while continuing to escape raw chat result tables.
- Added a clean-wheel installation smoke test to prevent releases from passing
  only against editable monorepo dependencies.

### v0.2.16 API - Investigation findings release

- Added the missing `openpyxl` development dependency required by the clean
  GitHub Actions test environment.
- Published the investigation-scoped Cognitive Runtime integration introduced
  in the unreleased `gaard-api` 0.2.15 build.

### v0.2.15 API - Investigation-scoped cognitive findings

- Added Radar Cognitive Runtime decision ingestion with durable, idempotent,
  session-scoped working knowledge and evidence/usage audit trails.
- Preserved the separate administrator-controlled persistent Business Logic flow.

### v0.2.14 API / v0.2.12 Client - Shared dashboards and collapsible client navigation
- Added API-backed dashboard sharing with `view` and `edit` access levels, owner-only deletion, shared dashboard listing, and metadata migrations for existing installations.
- Added the client dashboard sharing dialog, permission-aware dashboard editing controls, and view-only handling for shared dashboards.
- Added a collapsible client navigation rail matching the mobile icon-only layout and persisted the collapsed state locally.
- Bumped `gaard-api` to `0.2.14` and `gaard-client` to `0.2.12`; private package versions are unchanged.

### v0.2.11 - Admin extension compatibility fixes
- Added browser-compatible admin API request id fallback for extension panels when `crypto.randomUUID()` is unavailable.
- Prevented admin extension iframes from collapsing while empty, loading, or reporting their first content height.
- Bumped public packages, private extensions, package manifests, and local GAARD package references to `0.2.11`.

### v0.2.10 - Client conversation history and external API refresh management
- Added API-backed client conversation history so saved chat threads and queries survive page reloads without a local client database.
- Added client query copy actions and preserved chat scroll position during save, retry, show/hide, copy, and streaming updates.
- Improved client/admin labels and English result-mode translations.
- Improved external API configuration, endpoint selection, custom endpoints, request diagnostics, job controls, stale job handling, and refresh job layout.
- Bumped all public packages, private extensions, package manifests, and local GAARD package references to `0.2.10`.

### v0.2.9 - Package metadata and private bundle release
- Bumped all public packages, private extensions, package manifests, and local GAARD package references to `0.2.9`.
- Moved private package bundle membership into each extension's `pyproject.toml` under `[tool.gaard.package]`.
- Removed hardcoded private package inventory from the runtime package updater so installed packages are driven by downloaded bundle manifests.

### v0.2.8 - Identity privileges and client datasource access
- Fixed identity-privileges datasource filtering for built-in users so admin-granted datasource access is visible in the client and query execution uses the same identity key.
- Bumped all public packages and local public package references to `0.2.8` for the next PyPI release.

### v0.2.7 - Prompt visibility and client metric management
- Added the conversation-context classification prompt to the admin prompt configuration so follow-up/new-topic classification is visible and editable like the other LLM prompts.
- Reworked conversation-context classification to use explicit recent-turn context when deciding whether the current question is a logical continuation.
- Added saved metric deletion in the client Metrics view, including a confirmation warning that deleting a metric also removes it from all dashboards.
- Added backend cleanup for deleted saved metrics so dashboard widgets referencing the removed metric are deleted for the same owner.
- Improved the client Metrics view with datasource-grouped sections, wider metric cards, two-line metric names, and full-name title hints.
- Resolved `default` datasource labels in the Metrics view to the active datasource name, displayed as `Datasource Name (default)`.
- Bumped all public packages, the public example connector, and local public package references to `0.2.7` for the PyPI release.

### v.0.2.6 - Public package release
- Bumped all public packages and local public package references to `0.2.6` for the next PyPI release.

### v.0.2.5 - Medical POC example data refresh
- Added richer Medical POC demo data with specialty-specific volumes, seasonality, and trends.
- Seeded the bundled Healthcare Operations dashboard and saved metrics with English metric names when installing the Medical POC example database.
- Bumped `gaard-api` to `0.2.5` for the API-only PyPI release.

### v.0.2.4 - Client dashboards, saved metrics and conversation-aware queries
- Added the new client dashboard workflow: users can create dashboards, select the active dashboard, delete dashboards, and manage dashboard widgets from the client UI.
- Added GridStack-based dashboard widgets with draggable/resizable layouts, persisted widget positions, and support for number, bar, stacked bar, line, multi-line, pie, area, and table visualizations.
- Added saved metrics support for dashboards: successful query results can be saved as reusable metrics, listed in the Metrics view, and added to dashboards as widgets.
- Added dashboard API endpoints for listing dashboards, selecting the active dashboard, listing saved metrics, adding/removing widgets, deleting dashboards, and saving widget layouts.
- Reworked the client web UI with a fuller app shell, dedicated Home, Analysis, Metrics, Datasources, My Queries, and Alerts sections, improved loading/error states, saved active tab restore after refresh, and better dashboard menu layering.
- Added client-side datasource management improvements, including Excel workbook upload, active datasource toggling, and proxy endpoints for datasource state updates.
- Added persisted conversation handling and conversation-context classification so follow-up questions can reuse recent query context more safely.
- Improved query and analysis APIs with conversation metadata, clarification flow support, better error handling, and richer business-logic suggestion handling.
- Added authenticated-session helpers and expanded backend models for dashboards, dashboard user state, dashboard widgets, saved metrics, and conversations.
- Improved admin UI and API coverage for dashboard/metric-related data and added tests for dashboard, conversation, client proxy, error, license, and conversation-context flows.
- Cleaned the public package set so paid extension packages are no longer shipped under `public/packages`; paid packages remain in `private`.
- Bumped all public packages and local public package references to `0.2.4`.

### v.0.2.1 - Better handling of datasources
- Improvements related to adding data sources, 
- Introducing a datasource parameters form instead of URL, 
- Bug fixes, and package version updates to v0.2.1.

### v.0.2.0 - Big step for GAARD. Data closer to people
- The new Analysis mode is available. It has its own endpoint.
- The investigation mode is not available anymore. 
- The Analysis works better than I expected. Ask Medical POC Data: **why** Cardiology has so low profit?!

### v.0.1.1 - GAARD Modularization
- Main packages will be modularized so all can be easily extended by extrernal code
- Extensions are smoothly integarted into Admin UI and metadata

### v.0.1.0 - PIP packages era
- The Gaard is available as a PIP package

### 2026-06-18 Improved widgets handling and managing

* Widgets are now listed on separated view. Also available by the api endpoint. Admin can add widgets, put it on the Overview.
* There are two new modes of Widget: return raw data, return data interpretation. 
* You can save the query result from client as a widget. 
* Widgets allows to include html tags, so you can easily ask LLM to create links from your data :)

### 2026-06-17 Datasource connector imrovements

* Gaard admin reads views and tables now
* The AdminUI/Datasource connector is more convienient. The view is splited into two panels: list of data items and theirs details. Now you can also filter the view to see only enabled data items.

### Pre Changelog Era

* The first version of Gaard was published. 
