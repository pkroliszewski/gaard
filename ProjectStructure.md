## Project Structure

- `packages/gaard-plugin-api/` - versioned extension contracts and discovery
- `packages/gaard-api/` - FastAPI HTTP API and bundled admin UI
- `packages/gaard-client/` - FastAPI-hosted community client UI
- `services/worker/` - background worker
- `services/scheduler/` - scheduled jobs
- `packages/gaard-core/` - query pipeline, prompts, policies, and validation
- `packages/gaard-connectors/` - SQLAlchemy-based database connectors
- `packages/gaard-llm/` - LLM provider adapters
- `config/` - prompts, policies, and semantic-layer examples
- `infra/` - deployment assets
- `docs/` - architecture, API, and operational documentation
- `examples/` - demo datasets and use cases
- `examples/extensions/` - copyable extension packages; not default GAARD distributions
- `tests/` - integration, end-to-end, and prompt evaluation tests
