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
