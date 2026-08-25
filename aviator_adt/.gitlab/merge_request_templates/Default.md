## Summary

<!-- What does this MR do and why? -->

## Related Issue

<!-- Link to the issue(s) this MR addresses -->

## Type of Change

- [ ] Bug fix
- [ ] New feature
- [ ] Refactor / tech debt
- [ ] Breaking change
- [ ] Documentation update
- [ ] Configuration / infrastructure

## Impact Areas

<!-- Check all that apply -->
- [ ] API endpoints (`src/aviator/api/`)
- [ ] LangGraph agent / tools (`src/aviator/graph.py`, `src/aviator/tools/`)
- [ ] Celery worker / embeddings (`src/aviator/celery.py`)
- [ ] Plugin system (auth, tools, prompts, graph extensions)
- [ ] Settings / environment variables
- [ ] Database schema / migrations
- [ ] Helm chart / deployment
- [ ] Dependencies (`pyproject.toml` / `uv.lock`)

## How Was This Tested?

<!-- Describe the tests you ran or added. Include relevant details for reviewers to reproduce. -->

## Screenshots / Logs

<!-- If applicable, add screenshots or relevant log output. Remove section if not needed. -->

## Breaking Changes & Migration Notes

<!-- If this is a breaking change, describe the impact and migration path. Remove section if not applicable. -->

## Checklist

- [ ] `make ruff` passes
- [ ] `make test` passes
- [ ] Documentation updated (if applicable)
- [ ] Entry points updated in `pyproject.toml` (if adding a plugin)
- [ ] Settings documented in `settings.py` with defaults (if adding env vars)
- [ ] Helm values updated (if changing deployment config)
