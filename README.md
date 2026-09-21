# gh-ops

A reusable, deterministic GitHub operations and intelligence platform.

> "A personal GitHub operations layer running on GitHub Actions."

## Status

| Phase | Status | Tests |
|-------|--------|-------|
| Phase 0 — Architecture | Complete | — |
| Phase 1 — Core Runtime | Complete | 112/112 |
| Phase 2 — GitHub API Client | Complete | 73/73 |
| Phase 3 — State & Event Engine | Complete | 106/106 |
| Phase 4 — Repository Monitoring | ✅ Complete | 388/388 tests |
| Phase 5 — OSS Intelligence | ✅ Complete | 494/494 tests |
| Phase 6 — Developer Intelligence | ✅ Complete | 569/569 tests |
| Phase 7 — Notifications | Pending | — |

**Total: 569/569 tests passing.**

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

## State & Event Engine (Phase 3)

gh-ops implements a deterministic state engine that detects changes between collection runs.

### Four Distinct Concepts

| Concept | Description |
|---------|-------------|
| **Snapshot** | What was observed during a single collection run |
| **CurrentState** | The latest successfully accepted observation per resource type |
| **Event** | The deterministic transition between observations |
| **HistoryEntry** | What has previously been observed/emitted |

### Deterministic Diff Engine

`diff_resources()` produces NEW/CHANGED/REMOVED/UNCHANGED events with field-level change tracking:

```python
from src.core.events import diff_resources

events = diff_resources(
    resource_type="issues",
    previous_items={"#1": {"state": "open", "title": "bug"}},
    current_items={"#1": {"state": "closed", "title": "bug"}},
)
# events[0].event_type == EventType.CHANGED
# events[0].changes == (FieldChange(field="state", before="open", after="closed"),)
```

### Partial Failure Semantics

A failed collector MUST NOT cause valid previous state to disappear:

- **SUCCESS** — overwrite that collector's items
- **NOT_MODIFIED** — preserve existing items (304 response)
- **FAILURE** — preserve previous items, record the failure

### State Persistence

- Schema versioning with `STATE_SCHEMA_VERSION = 1`
- Atomic writes: temp file → flush → fsync → atomic replace
- Corruption handling: MISSING / VALID / CORRUPT / UNSUPPORTED_VERSION
- ETag persistence for conditional requests

### Usage

```python
from src.core.state import load_current_state, apply_snapshot
from src.core.models import Snapshot, CollectorResult, CollectorStatus

# Load previous state
previous = load_current_state()

# Apply a snapshot (partial failure safe)
snapshot = Snapshot(results={
    "repos": CollectorResult(collector="repos", status=CollectorStatus.SUCCESS, items={...}),
    "releases": CollectorResult(collector="releases", status=CollectorStatus.FAILURE, error="timeout"),
})
new_state = apply_snapshot(previous, snapshot)
```

## Developer Intelligence (Phase 6)

`src/developer/` turns data already collected by Phase 2/3 into **factual,
descriptive activity statistics**. It performs no network calls of its own,
introduces no LLM, and makes no judgement about the developer.

### Pipeline

```
CurrentState (resources) + Phase 3 events
        ↓
activity.py     — normalize into ActivityRecord
        ↓
statistics.py   — descriptive counts and breakdowns
        ↓
reports.py      — structured DeveloperReport
        ↓
Phase 7 notifications (not implemented)
```

### Activity Model

A record is emitted only when the corresponding resource field exists and
parses to a UTC datetime. Nothing is fabricated.

| `ActivityType` | Timestamp source | Emitted when |
|----------------|------------------|--------------|
| `issue_created` | `created_at` | NEW issue event |
| `issue_closed` | `closed_at` | `state` changed to `closed` |
| `issue_commented` | `updated_at` | `comments` count increased |
| `pr_opened` | `created_at` | NEW pull event |
| `pr_merged` | `merged_at` | `merged` changed to true |
| `pr_closed` | `closed_at` | `state` changed to `closed`, with no merge evidence |
| `release_published` | `published_at` | NEW release event |
| `workflow_run` | `run_started_at` | NEW or CHANGED workflow event |

### Semantics That Are Deliberately Not Simplified

- **Closed PR ≠ merged PR.** `pr_merged` requires the source data to establish a
  merge (`merged` true / `merged_at` present). A closed PR without merge
  evidence is `pr_closed`.
- **Current state ≠ historical event.** Timestamps come from the resource fields
  (`created_at`, `closed_at`, `merged_at`), never from collection time.
- **Missing ≠ zero.** A failed collector produces a `DataQuality` entry with
  `is_complete=False` and its error message; the report reports completeness
  rather than reporting zero activity.
- **First observation.** When an issue is already closed, or a PR is already
  merged, at first observation, both the create (or open) and the
  close/merge are emitted using the resource's own timestamps.

### Statistics

Pure aggregation over `ActivityRecord` lists — no I/O:

- `ActivityCounts` — per-type counts plus `total`
- `compute_by_repository()` — `RepoActivity` per repository
- `compute_by_period()` — fixed-width `PeriodActivity` buckets
- `compute_active_days()`, `compute_active_repositories()`, `compute_date_range()`
- `summarize_data_quality()` — collector completeness percentage

### Reports and Reporting Periods

`ReportPeriod` presets: `last(days)`, `this_week()` (Monday 00:00 UTC), and
`this_month()` (1st 00:00 UTC).

`generate_report()` deduplicates records, bounds them to the period
(`since` and `until` both inclusive), then computes counts, the per-repository
breakdown, `by_period` buckets, and data-quality context.

### Determinism

The aggregation is a pure function of its input:

- Frozen dataclasses; no randomness in aggregation
- Records sorted by timestamp; `by_repository` sorted by total descending then
  repository name; `by_period` chronological; `active_repositories` sorted
- Dedup key is `(activity_type, item_id, timestamp)`

Two values are generation-time by construction and differ between runs:
`generated_at`, and the trailing `by_period` bucket's `period_end` (clamped to
the reference time). Pass an explicit `reference_time` to `compute_by_period()`
to make bucket boundaries reproducible.

### Limitations

- Coverage is bounded by what Phase 2/3 collected; there is no backfill
- Workflow runs are not attributed to a developer, so per-user reports still
  include collected workflow runs
- Releases and workflow runs carry no repository name in the normalized record,
  so they group under `(unknown)` in per-repository breakdowns
- A PR already closed-without-merge at first observation yields only
  `pr_opened`, because that close transition was never observed
- A report describes one period from one snapshot; no history or trends

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
| `core/state.py` | 60 | Load/save, diff, prune, merge, validation, atomic writes, apply_snapshot, ETags, history |
| `core/events.py` | 49 | detect_events, diff_resources, field changes, determinism, normalization |
| `core/models.py` | 24 | State models, validation, serialization |
| `core/rate_limit.py` | 17 | Tracker, headers, backoff |
| `core/dispatcher.py` | 7 | Job registration, execution |
| `github/auth.py` | 15 | Token resolution, AuthConfig |
| `github/client.py` | 16 | GET-only, retry, pagination, ETags |
| `github/models.py` | 19 | 8 dataclasses, from_api, to_dict |
| `github/collectors.py` | 23 | 7 collectors, mocked responses |
| `monitors/repository.py` | 12 | Repository event evaluation, collection failures |
| `monitors/ci.py` | 12 | CI workflow event evaluation, failures, recovery |
| `monitors/release.py` | 12 | Release event evaluation, prerelease/draft filtering |
| `monitors/endpoint.py` | 26 | Security validation, config parsing, URL checks |
| `monitors/__init__.py` | 16 | Registry, event dispatch, summary |
| `monitors/monitor.py` | 14 | MonitorResult model, classification, properties |
| `monitors/*` (properties) | 5 | Property tests for MonitorResult invariants |
| `utils/text.py` | 20 | Markdown escaping, split, truncate |
| `utils/time.py` | 16 | Timestamps, relative time |
| `intelligence/oss/models.py` | 9 | Opportunity, ScoreBreakdown, from_search_result |
| `intelligence/oss/queries.py` | 16 | Query generation, dedup, config, custom queries |
| `intelligence/oss/filters.py` | 23 | 9 filter rules, bot detection, config parsing |
| `intelligence/oss/scorer.py` | 26 | 7 scoring signals, normalization, tie-breaking |
| `intelligence/oss/dedup.py` | 18 | Identity dedup, provenance merge, idempotency |
| `intelligence/oss/hunter.py` | 14 | Orchestrator, metadata enrichment, partial failure |
| `developer/activity.py` | 38 | Activity extraction, filtering, dedup, data quality |
| `developer/statistics.py` | 21 | Counts, repo breakdown, periods, active days |
| `developer/reports.py` | 16 | Report generation, periods, formatting |
| **Total** | **569** | |

## Project Structure

```
gh-ops/
├── src/
│   ├── core/           # Config, state, events, errors, rate limiting, monitor model
│   ├── github/         # API client, auth, collectors, models
│   │   ├── auth.py     # Token resolution (env vars only)
│   │   ├── client.py   # Single HTTP exit point (GET-only)
│   │   ├── models.py   # Frozen dataclasses for API responses
│   │   └── collectors/ # Thin data-fetching wrappers
│   ├── intelligence/   # OSS hunter, release/security radars
│   ├── developer/      # Personal activity, statistics, reports
│   ├── monitors/       # Repository, CI, release, endpoint monitors
│   │   ├── __init__.py # Monitor registry and evaluator
│   │   ├── repository.py # Repository metadata monitoring
│   │   ├── ci.py       # CI/workflow failure/recovery detection
│   │   ├── release.py  # Release event monitoring
│   │   └── endpoint.py # HTTP endpoint health checks (SSRF-protected)
│   ├── notifications/  # Telegram adapter
│   └── utils/          # Logging, text, time helpers
├── config/             # YAML configuration files (monitoring.yml, oss_hunter.yml)
├── data/               # Runtime state (gitignored)
├── tests/              # Test suite (569 tests)
└── scripts/            # Local development scripts
```

## Runtime Dependencies

- Python 3.10+
- `requests` — GitHub API HTTP client (client.py only)
- `pyyaml` — Configuration file parsing

## License

MIT
