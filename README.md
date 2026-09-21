# gh-ops

A reusable, deterministic GitHub operations and intelligence platform.

> "A personal GitHub operations layer running on GitHub Actions."

## Status

| Phase | Status | Tests |
|-------|--------|-------|
| Phase 0 — Architecture | Complete | — |
| Phase 1 — Core Runtime | Complete | 112/112 |
| Phase 2 — GitHub API Client | Complete | 73/73 |
| Phase 3 — State & Event Engine | Pending | — |
| Phase 4 — Repository Monitoring | Pending | — |
| Phase 5 — OSS Intelligence | Pending | — |
| Phase 6 — Developer Intelligence | Pending | — |
| Phase 7 — Notifications | Pending | — |

**Total: 185/185 tests passing.**

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full system design.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Set GitHub token
export GITHUB_TOKEN="ghp_..."

# Run locally
python scripts/run_local.py daily
```

## GitHub Authentication

gh-ops resolves GitHub tokens from environment variables:

| Variable | Priority | Description |
|----------|----------|-------------|
| `GITHUB_TOKEN` | 1 (preferred) | Standard GitHub token env var |
| `GH_TOKEN` | 2 (fallback) | Alternative GitHub token env var |

- Token is **never** logged, printed, or serialized
- `AuthConfig.__repr__` shows only the source variable name, not the token
- If no token is found, `resolve_auth()` raises `GhOpsError`
- Whitespace-only tokens are treated as empty

## GitHub API Client

`src/github/client.py` is the **single HTTP exit point** for all GitHub API communication.

### GET-Only V1 Boundary

gh-ops V1 is **read-only** with respect to GitHub. The client enforces this:

- `client.execute("POST", ...)` raises `WriteDeniedError`
- `client.execute("PUT", ...)` raises `WriteDeniedError`
- `client.execute("PATCH", ...)` raises `WriteDeniedError`
- `client.execute("DELETE", ...)` raises `WriteDeniedError`

Only `GET` requests are permitted. This is a hard boundary — no collectors, handlers, or utilities can bypass it.

### Pagination

`client.get_paginated()` handles pagination automatically:

- Follows `Link` header `rel="next"` references
- Configurable `per_page` (max 100) and `max_pages` (safety limit, default 10)
- Returns `PaginatedResult` with all items collected across pages
- Supports ETag conditional requests (304 Not Modified)

### ETag / 304 Support

- Pass `etag` parameter to `get_paginated()` for conditional requests
- On 304 Not Modified, returns `PaginatedResult` with `not_modified=True`
- ETag values are passed through for the state layer to persist (Phase 3)

### Rate-Limit / Retry Behavior

- Reads `X-RateLimit-Remaining`, `X-RateLimit-Limit`, `X-RateLimit-Reset` headers
- Updates `RateLimitTracker` on every response
- On 429 (rate limited): parses `Retry-After` header, retries with exponential backoff
- On 5xx (server errors): retries with exponential backoff + jitter
- Exponential backoff: `min(1.0 * 2^attempt + random(0, 0.5), 30.0)` seconds
- Maximum 3 retries (configurable via `max_retries`)
- `on_rate_limit` callback for monitoring integration

### Error Mapping

| HTTP Status | Error Code | Description |
|-------------|------------|-------------|
| 401 | `API_AUTH_FAILED` | Invalid/missing token |
| 403 | `API_FORBIDDEN` | Insufficient permissions |
| 404 | `API_NOT_FOUND` | Resource not found |
| 429 | `API_RATE_LIMITED` | Rate limit exceeded |
| 500+ | `INTERNAL_ERROR` | Server error |

All errors are typed `GitHubClientError` instances with status code, endpoint, and rate-limit metadata.

## Collector Architecture

Collectors are thin wrappers that call `client.get()` or `client.get_paginated()` and return normalized models.

| Collector | Endpoint | Returns |
|-----------|----------|---------|
| `repos` | `/user/repos`, `/orgs/{org}/repos`, `/repos/{owner}/{repo}` | `Repository` |
| `issues` | `/repos/{owner}/{repo}/issues`, `/issues`, `/search/issues` | `Issue` |
| `pulls` | `/repos/{owner}/{repo}/pulls` | `PullRequest` |
| `releases` | `/repos/{owner}/{repo}/releases` | `Release` |
| `workflows` | `/repos/{owner}/{repo}/actions/runs` | `WorkflowRun` |
| `security` | `/repos/{owner}/{repo}/dependabot/alerts`, `/code-scanning/alerts` | `SecurityAlert` |
| `user` | `/user`, `/users/{username}`, `/rate_limit` | `User` |

### Collector Invariants

- **No `requests` import** — collectors use only `GitHubClient` and models
- **No credential access** — collectors never see the token
- **No HTTP logic** — pagination, retries, rate limits are in `client.py`
- **Deterministic parsing** — `Model.from_api()` handles all field extraction
- **Frozen dataclasses** — models are immutable after creation

## Normalized Models

All API responses are parsed into frozen dataclasses in `src/github/models.py`:

| Model | Key Fields |
|-------|------------|
| `Repository` | `id`, `full_name`, `owner`, `name`, `stars`, `forks`, `language`, `topics` |
| `Issue` | `id`, `number`, `title`, `state`, `labels`, `user`, `comments` |
| `PullRequest` | `id`, `number`, `title`, `state`, `head_branch`, `base_branch`, `merged` |
| `Release` | `id`, `tag_name`, `name`, `author`, `assets_count` |
| `WorkflowRun` | `id`, `name`, `status`, `conclusion`, `event`, `is_success`, `is_failure` |
| `SecurityAlert` | `id`, `package_name`, `severity`, `summary`, `state` |
| `User` | `id`, `login`, `name`, `public_repos`, `followers` |
| `PaginatedResult` | `items`, `total_count`, `has_next`, `etag`, `not_modified` |

All models provide `to_dict()` for serialization and `from_api()` for parsing.

## No GitHub Writes

**gh-ops V1 performs zero write operations against the GitHub API.**

- No issue creation, commenting, or labeling
- No pull request creation, merging, or reviewing
- No release creation or asset uploading
- No workflow triggering
- No repository starring, watching, or forking

The `WriteDeniedError` enforcement in `client.py` makes this a hard guarantee, not a convention.

## Local Testing

All tests use mocked HTTP responses — no live GitHub contact:

```bash
# Run full test suite
python -m pytest tests/ -v

# Run specific module tests
python -m pytest tests/github/ -v
python -m pytest tests/core/ -v
```

### Test Coverage

| Module | Tests | Coverage |
|--------|-------|----------|
| `core/errors.py` | 12 | Error types, collector, serialization |
| `core/config.py` | 13 | YAML loading, deep merge, validation |
| `core/state.py` | 17 | Load, save, diff, merge, atomic writes |
| `core/events.py` | 10 | Event detection, filtering, summarization |
| `core/rate_limit.py` | 17 | State tracking, backoff, retry-after parsing |
| `core/dispatcher.py` | 7 | Job registration, execution, error handling |
| `github/auth.py` | 15 | Token resolution, format validation, env vars |
| `github/client.py` | 16 | GET-only, pagination, ETag, rate limits, errors |
| `github/models.py` | 19 | All 8 models + PaginatedResult |
| `github/collectors/` | 23 | All 7 collectors with mocked client |
| `utils/text.py` | 15 | Markdown escaping, splitting, truncation |
| `utils/time.py` | 13 | Timestamp parsing, formatting, staleness |
| **Total** | **185** | |

## Project Structure

```
gh-ops/
├── src/
│   ├── core/           # Config, state, events, errors, rate limiting
│   ├── github/         # API client, auth, collectors, models
│   │   ├── auth.py     # Token resolution (env vars only)
│   │   ├── client.py   # Single HTTP exit point (GET-only)
│   │   ├── models.py   # Frozen dataclasses for API responses
│   │   └── collectors/ # Thin data-fetching wrappers
│   ├── intelligence/   # OSS hunter, release/security radars
│   ├── developer/      # Personal activity, statistics, reports
│   ├── monitors/       # Repository, CI, release monitoring
│   ├── notifications/  # Telegram adapter
│   └── utils/          # Logging, text, time helpers
├── config/             # YAML configuration files
├── data/               # Runtime state (gitignored)
├── tests/              # Test suite (185 tests)
└── scripts/            # Local development scripts
```

## Runtime Dependencies

- Python 3.10+
- `requests` — GitHub API HTTP client (client.py only)
- `pyyaml` — Configuration file parsing

## License

MIT
