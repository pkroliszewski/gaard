# GAARD - Governed AI Access to Relational Data

GAARD is a self-hosted AI SQL Gateway for governed natural-language access to relational data.

GAARD allows applications and users to ask questions about relational databases using
natural language while keeping SQL generation, validation, execution, prompts,
connectors, and auditability under control.
## Quick Start - Gaard user
### 1. Create virtual python evironment (optional)
To have things clean, use virtual environment to install Gaard only in specified directory.
It keeps example databases and metadata in the same place.

```bash
mkdir gaard
cd gaard
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install Gaard
```bash
pip install --upgrade pip
pip install gaard-api gaard-client
```

### 2. Install example database (optional)

```bash
gaard-core install-example-database
```

### 3. Start the Gaard API
```bash
gaard-core start
```
The admin panel will be available at http://localhost:8000/admin

### 4. Start the Gaard Client
```bash
gaard-client start
```
The client will be available at http://localhost:8001/?backendUrl=http://localhost:8000

The `backendUrl` frontend parameter is the only client-side configuration
value. It points the client to the GAARD API backend.

## Upgrading Gaard

### 1. Upgrade pip packages
```bash
python -m pip install --upgrade gaard-api gaard-client
```

The fastest local path is to run the API with Uvicorn and use the bundled SQLite
demo database.


## Configure Gaard from the admin UI

GAARD does not read `.env` for API runtime configuration. On first start it
creates `metadata.db`, seeds default prompts, runtime settings, and the bundled
demo datasource, then lets the admin UI become the source of truth.

After the API starts, log in to `/admin` with `admin` / `admin`, change the
password, and configure the datasource, LLM connection, runtime modes, prompt
templates, audit retention, and schema cache settings there.

**Most important**

Put your LLM settings here:

![admin_llm_settings](docs/images/admin_llm_settings.png)


## Ask a question to your data

Use the shipped simple client UI:

![example_ask_a_question](docs/images/example_ask_a_question.png)

or use the API:

```bash
curl -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{"question":"How many active patients are there?"}'
```

The response includes the natural-language answer, generated SQL, returned rows,
and request metadata.

## Datasource Connectors

Datasource connectors are configured in the admin UI and persisted in the
metadata database. For required database permissions, MySQL, PostgreSQL and
SQLite connection examples, schema introspection, views, and business logic
settings for tables and views, see
[Datasource Configuration](DatasourceConfiguration.md).

## How to use GAARD in your Reports

You can integrate the GAARD using the REST API. When you run the GAARD locally you will find docs here:

```text
http://localhost:8000/docs
```

Community version currently doesn't have the mutli user authentication so you need to admin login by the endpoint, 
and then get the bearer token and use it in next requests. 
This will allow you to get widgets, and answer user queries. 

Details coming soon.

## More reading
- [Configuration Details](ConfigurationDetails.md)
- [Datasource Configuration](DatasourceConfiguration.md)
- [Compiling Portable Docker Images](CompilingDockerImages.md)
- [Run Gaard Using Docker](RunUsingDocker.md)
- [Gaard Development](GaardDevelopment.md)
- [Project Structure](ProjectStructure.md)
- [Requirements](Requirements.md)



