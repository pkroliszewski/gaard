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
