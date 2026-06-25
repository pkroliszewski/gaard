## Requirements

Core local development requirements:

- Python 3.11 or newer
- `pip`
- Python virtual environment support, usually available through `venv`

Optional Docker-based runtime requirements:

- Docker-compatible runtime, such as Docker Engine or Docker Desktop
- Docker Compose, either the `docker compose` plugin or standalone `docker-compose`

Runtime configuration requirements:

- An OpenAI-compatible LLM endpoint and API key when using `llm` modes
- A relational datasource reachable through SQLAlchemy
- Local SQLite storage for GAARD metadata

## Technology Stack
- Backend API: Python + FastAPI
- Core engine: Python package
- Database abstraction: SQLAlchemy
- Metadata database: SQLite
- Demo datasource: SQLite
- Deployment: local Uvicorn or Docker Compose