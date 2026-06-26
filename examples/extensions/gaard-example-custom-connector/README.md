# GAARD Example Custom Connector

This is a public, copyable example of a GAARD datasource connector extension.
It is intentionally located under `examples/extensions`, not `packages`: it is
a template and is not one of the default GAARD distributions.

The example registers the `example-custom-sqlite` datasource type. It accepts:

```text
example-custom-sqlite:///absolute/path/to/database.db
```

Internally it maps that URL to SQLite. The connector demonstrates the complete
public contract: package entry point, manifest, `ConnectorDefinition`, query
execution, schema introspection, connection testing, and dynamic Admin UI
metadata.

## Try it locally

From the public repository root:

```bash
python -m pip install -e examples/extensions/gaard-example-custom-connector
gaard-core start
```

Open `http://localhost:8000/admin`, then go to **Datasource connector**. The
form will list **Example Custom SQLite** without any extra Admin UI code.

See [Extensions](../../../Extensions.md) for the extension lifecycle, safety
rules, and implementation guidance.
