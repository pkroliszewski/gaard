# GAARD Extensions

GAARD extensions are trusted Python distributions that add capabilities to a
running GAARD installation without requiring a fork of the public source tree.
An extension can be private, published independently, or developed from a
local checkout.

The public GAARD packages never import an extension directly. Instead, the
application discovers installed extensions through Python package entry points
and activates the contributions supported by the current host.

## Current scope

The extension runtime, datasource connector contributions, and API/Admin UI
host contributions are implemented today. The public connector example is
located at:

```text
examples/extensions/gaard-example-custom-connector/
```

The `core`, `llm`, and `client` contribution areas are reserved for the
extension platform but are **not activated by the current GAARD host yet**. Do
not rely on them as a public integration contract until their registries and
lifecycle hooks are implemented and documented here.

| Contribution | Status | Current integration |
| --- | --- | --- |
| `connectors` | Implemented | Adds datasource types to the Admin datasource page and query runtime. |
| `api` | Implemented | Adds namespaced FastAPI routers, static Admin assets, and Admin menu sections. |
| `core` | Reserved | No public registry or activation hook yet. |
| `llm` | Reserved | No public provider registry or activation hook yet. |
| `client` | Reserved | No custom navigation or frontend-module extension hook yet. |

## How discovery and activation work

1. Install the extension into the same Python environment as `gaard-api`.
2. The GAARD API host reads entry points in the `gaard.extensions` group.
3. It loads and validates every `ExtensionManifest`:
   - extension API version;
   - declared compatible GAARD package versions;
   - contribution import targets.
4. The API creates its built-in `ConnectorRegistry` and activates the
   `connectors` contribution of every compatible extension.
5. The API creates an `ApiRegistry` and activates the `api` contribution of
   every compatible extension. API routes are mounted below
   `/api/v1/extensions/<extension-id>/...`; Admin pages and assets are mounted
   below `/admin/extensions/<extension-id>/...`.
6. The Admin API exposes all active connector definitions at
   `GET /api/v1/admin/datasource-types`.
7. The Admin API exposes extension diagnostics and Admin menu declarations at
   `GET /api/v1/admin/extensions`.
8. The Admin UI loads that endpoint after sign-in and renders extension menu
   sections generically.
9. The Admin UI loads datasource types when the **Datasource connector** section
   opens. It renders the connector label, description, supported SQL dialects,
   and URL guidance dynamically.

An extension is considered trusted application code. Only install packages from
sources you trust. GAARD does not load executable code from database rows,
configuration files, or network URLs.

## Create a connector extension

### 1. Create a Python distribution

Use a regular `src/` layout. The example below is deliberately separate from
`packages/`: it is a template, not a GAARD core distribution or a default PyPI
release target.

```text
my-connector/
  pyproject.toml
  src/my_connector/
    __init__.py
    plugin.py
    connector.py
```

Declare dependencies and an entry point in `pyproject.toml`:

```toml
[project]
name = "my-gaard-connector"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "gaard-connectors>=0.2.11,<0.3.0",
  "gaard-plugin-api>=0.2.11,<0.3.0",
]

[project.entry-points."gaard.extensions"]
my-connector = "my_connector.plugin:extension"
```

Pin a compatible GAARD version range. This lets the host reject an extension
that was built for an incompatible contract instead of failing later during a
request.

### 2. Declare an extension manifest

The entry point resolves to an `ExtensionManifest`, or to a callable returning
one:

```python
from gaard_plugin_api import ExtensionManifest


def extension() -> ExtensionManifest:
    return ExtensionManifest(
        id="my-connector",
        version="0.1.0",
        requires={
            "gaard-connectors": ">=0.2.11,<0.3.0",
            "gaard-plugin-api": ">=0.2.11,<0.3.0",
        },
        contributions={
            "connectors": "my_connector.connector:register",
        },
    )
```

`id` is stable, lowercase, begins with a letter, and may contain digits and
hyphens. It identifies the extension in diagnostics and future configuration.

### 3. Register a `ConnectorDefinition`

The `connectors` contribution receives an `ExtensionContext`. Its `registry`
is a `ConnectorRegistry` in the GAARD API host.

```python
from gaard_connectors.registry import ConnectorDefinition, ConnectorRegistry
from gaard_plugin_api import ExtensionContext


def register(context: ExtensionContext) -> None:
    if not isinstance(context.registry, ConnectorRegistry):
        raise RuntimeError("This contribution requires ConnectorRegistry")

    context.registry.register(
        ConnectorDefinition(
            type_key="my-warehouse",
            label="My Warehouse",
            description="A connector supplied by my extension.",
            sql_dialects=("postgres",),
            url_prefixes=("my-warehouse://",),
            executor_factory=create_executor,
            introspector_factory=create_introspector,
            connection_tester=test_connection,
            config_schema={
                "type": "object",
                "properties": {
                    "database_url": {
                        "type": "string",
                        "format": "uri",
                        "title": "My Warehouse URL",
                        "description": "my-warehouse://…",
                    }
                },
                "required": ["database_url"],
            },
        )
    )
```

`type_key` must be unique across the running installation. Registering a
duplicate type is an activation error. A connector owns all three operational
functions:

- `executor_factory(database_url, max_rows)` creates an object that executes
  SQL and returns GAARD's `QueryResult`.
- `introspector_factory(database_url)` creates an object that returns
  `DatabaseSchema`.
- `connection_tester(database_url)` checks that the datasource is reachable.

Do not assume that every datasource is a SQLAlchemy URL or that every SQL
dialect uses `LIMIT`. A connector must adapt its own URL format, driver,
introspection, and query execution semantics.

## Admin UI integration

No plugin-specific Admin UI code is required for a datasource connector.
Registering a valid `ConnectorDefinition` makes it available in the existing
**Datasource connector** page after the API restarts.

The current UI uses this safe, generic subset of `config_schema`:

- `description` is shown below the connector selector;
- `sql_dialects` and `default_sql_dialect` populate the SQL dialect selector;
- `properties.database_url.title` labels the URL input;
- `properties.database_url.description` becomes URL guidance;
- `properties.database_url.default`, when supplied, pre-fills a new form.

The persisted datasource model currently stores `database_type`,
`database_url`, and `sql_dialect`. Arbitrary schema-defined settings are not
persisted or rendered yet. Design a connector around the URL field for now, or
wait for the versioned `connection_config` model before requiring structured
connection settings.

If a previously configured plugin is no longer installed or cannot be
activated, the Admin UI labels its connector type as unavailable and disables
editing instead of silently changing it to a built-in connector.

### Menu extensions

An extension can add an Admin navigation item through the `api` contribution.
The contribution receives an `ExtensionContext`; its `registry` is a
`gaard_api.api_registry.ApiRegistry`.

```python
from fastapi import APIRouter
from gaard_api.api_registry import ApiRegistry
from gaard_plugin_api import ExtensionContext


router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def register_api(context: ExtensionContext) -> None:
    if not isinstance(context.registry, ApiRegistry):
        raise RuntimeError("This contribution requires ApiRegistry")

    context.registry.register_router(
        extension_id=context.extension_id,
        router=router,
    )
    context.registry.register_admin_page(
        extension_id=context.extension_id,
        section_key="extract",
        label="Extract",
        html_path="/path/to/extension/admin/index.html",
        description="Private structured extraction workflow.",
    )
```

The router above is mounted at:

```text
/api/v1/extensions/<extension-id>/health
```

Extension routers registered through `ApiRegistry.register_router()` require
the standard GAARD Admin bearer token by default. Use `require_admin=False`
only for deliberately public endpoints such as a minimal health probe.

The Admin page above is mounted at:

```text
/admin/extensions/<extension-id>/extract
```

The public Admin UI obtains menu items from
`GET /api/v1/admin/extensions`, places them under the **Extensions** menu
group, and renders each extension page in a same-origin frame. The extension
page is trusted installed code, but it must still call GAARD APIs through
documented endpoints and include the standard Admin bearer token.

Admin paths must stay below `/admin/extensions/<extension-id>/...`. API routes
must stay below `/api/v1/extensions/<extension-id>/...`. This prevents route
collisions between GAARD core endpoints and independently installed
extensions.

## Install an extension

For local development from the GAARD repository, install the public packages
first and then the extension in editable mode:

```bash
cd public
python -m pip install -r requirements-dev.txt
python -m pip install -e examples/extensions/gaard-example-custom-connector
```

For a private extension stored beside the public tree, use its private path:

```bash
cd public
python -m pip install -e ../private/packages/my-connector
```

Restart the API process after installing or upgrading an extension. Discovery
happens during process startup. A production deployment should build an image
that installs the selected extension wheel(s) alongside `gaard-api`; it should
not copy private source into the public GAARD distribution.

## Verify an extension

After restart:

1. Sign in to `/admin`.
2. Open **Datasource connector**.
3. Create a datasource and choose the extension's connector label.
4. Confirm that its declared SQL dialects and URL guidance appear.
5. Use **Test** and **Schema introspection**.
6. Run a read-only query through GAARD.

An API-level check is also available to authenticated administrators:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/admin/datasource-types
```

## Public and private examples

`examples/extensions/gaard-example-custom-connector` is a public template that
uses the URL scheme `example-custom-sqlite://…`. Copy and rename it when
starting a connector of your own.

The private repository may contain a separate private example. It follows the
same public contract but is deliberately not referenced by the public build or
subtree publication workflow.

## Versioning, failures, and safety

- Use a new extension package version when changing its behavior.
- Keep the manifest requirement ranges narrow enough to protect compatibility.
- Treat extension startup failures as deployment failures. Inspect the API
  process logs; do not rely on a silently skipped connector.
- Never put credentials, API keys, or production URLs in a manifest, source
  tree, or frontend schema. Use the configured datasource URL or a supported
  secret-management integration.
- Test both an installation without your extension and one with it installed.

The extension contract is intentionally narrow. Use documented registries and
contexts only; do not monkey-patch GAARD classes or depend on private module
internals.
