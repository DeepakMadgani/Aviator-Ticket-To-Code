# Contributing to Content Aviator ADT

Thank you for your interest in contributing! This guide covers the **contributor workflow** — for environment setup, architecture, and debugging, see the [full documentation](https://aviator-adt-f912d2.glpages.otxlab.net/).

## Getting Started

For prerequisites, installation, Docker Compose environments, and VS Code debugging, follow the **[Getting Started guide](https://aviator-adt-f912d2.glpages.otxlab.net/)**.

Quick reference:

```bash
uv sync --locked   # Install dependencies (always use uv, never pip)
make up            # Start full dev stack
make test          # Run tests with coverage
```

## Development Workflow

1. **Create a feature branch** from `main`
2. **Make your changes** — follow the [code standards](#code-standards) below
3. **Run linting and tests** before committing:
   ```bash
   make ruff       # Format and lint check
   make ruff-fix   # Format and auto-fix lint issues
   make test       # Run pytest with coverage
   ```
4. **Push and open a Merge Request** — the [MR template](/.gitlab/merge_request_templates/Default.md) will auto-populate

## Code Standards

- **Always use `uv`** for package management — never `pip install` directly
- **Ruff** handles linting and formatting (line length: 120, scope: `src/` and `tests/`)
- **Pydantic BaseModel v2** for all models and validation
- **`StateModel`** for LangGraph state — never raw dicts
- **Custom exceptions** from `src/aviator/exceptions.py` for error handling
- **`pydantic-settings`** for new configuration — add to `src/aviator/settings.py` with sensible defaults

## Testing

Tests use **pytest** with in-memory backends (`VECTOR_STORE=memory`, `CHECKPOINTER=memory`) — configured automatically in `tests/conftest.py`.

- Place tests in `tests/` mirroring the source structure
- Name test files `test_*.py`
- Use `pytest-mock` for mocking, `pytest-xdist` for parallel execution
- Auth headers required in tests: `{"auth-ticket": "some-valid-ticket"}`

## Submitting Changes

### Merge Request Guidelines

1. Fill in the [MR template](/.gitlab/merge_request_templates/Default.md) completely
2. Ensure CI passes (linting + tests)
3. Keep MRs focused — one feature or fix per MR
4. Update documentation if you're changing:
   - API endpoints
   - Settings / environment variables
   - Plugin interfaces
   - Helm chart values

### Commit Messages

Use [Conventional Commits](https://www.conventionalcommits.org/) style:
```
feat: add custom prompt modifier plugin support
fix: handle empty vector store results in RAG tool
docs: update plugin development guide
```

## Plugin Development

See the **[Plugin Development guide](https://aviator-adt-f912d2.glpages.otxlab.net/dev/plugins/)** for the full reference, including working examples and the plugin sample repo.

The system supports multiple plugin types via `pyproject.toml` entry points, including auth handlers, tool modifiers, graph extensions, prompt modifiers, and more.

## Documentation

Docs are built with MkDocs Material and served at [aviator-adt-f912d2.glpages.otxlab.net](https://aviator-adt-f912d2.glpages.otxlab.net/). To build and serve locally:

```bash
make docs
```

## Getting Help

- **Documentation:** [aviator-adt-f912d2.glpages.otxlab.net](https://aviator-adt-f912d2.glpages.otxlab.net/)
- **Plugin sample repo:** [aviator-plugin-sample](https://gitlab.otxlab.net/csai/aviator-plugin-sample)
- **Contacts:** Reach out to the team leads for credentials, repo access, or questions
