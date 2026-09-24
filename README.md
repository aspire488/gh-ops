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
| Phase 7 — Telegram Notifications | ✅ Complete | 766/766 tests |
| Phase 8 — GitHub Actions | ✅ Complete | 1084/1084 tests |
| Security Intelligence batch | ✅ Complete | 1150/1150 tests |
| Reporting + product-hardening batch | ✅ Complete | 1260/1260 tests |

**Total: 1260/1260 tests passing.**

Figures are cumulative as of the end of each phase. Security Intelligence, unified reporting, repository/event hardening, and Telegram contract hardening are post-Phase-8 batches.

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full system design.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Configure secrets locally (the file is gitignored)
cp .env.example .env
# then edit .env and fill in the values

# Run locally
python scripts/run_local.py daily

# Or run a job directly — the same entry point the workflows use
python -m src.jobs status        # read-only: report current state + config
python -m src.jobs daily

# Check Telegram delivery configuration (prints no secrets)
python scripts/check_telegram.py
```

### Running a Job

The dispatcher is the same code path in CI and locally:

```bash
python -m src.jobs <job_name>          # daily | monitoring | weekly-report
                                       # oss-hunt | security | status
GHOPS_JOB=weekly-report python -m src.jobs

python -m src.jobs                     # prints usage and exits 1
python -m src.jobs not-a-job           # prints available jobs, exits 1
```

Jobs read `GITHUB_TOKEN` for collection and `TELEGRAM_BOT_TOKEN` for delivery.
A job never exits 0 without having executed one.

Monitoring alerts remain event-driven. When `telegram.run_summary` is enabled, successful daily and weekly runs may send briefs built from the shared reporting ledger (only when meaningful events exist; a quiet run sends nothing). Completion summaries (run/oss/security) are no longer sent by jobs; their formatters remain exported for API compatibility. Repository counts come only from config/repositories.yml; accessible account repositories are never added automatically. OSS opportunities are deduplicated across runs via the `oss_opportunities` resource key in Phase 3 state (at-least-once: identities are marked only after successful delivery). Security findings are likewise deduplicated across runs via the `security_notified` resource key (findings live under `security`; only actionable severities are batch-delivered, marked only after successful delivery; recovery events clear the mark and may send a RESOLVED report).

## Local Secrets (`.env`)

Secrets live in environment variables, never in source or config. For local
development you can keep them in a gitignored `.env` file:

```bash
cp .env.example .env
```

`.env` is **gitignored** (`.gitignore`: `.env`, `.env.*`) — confirm with
`git check-ignore -v .env`. Only `.env.example`, which holds no values, is tracked.

| Variable | Used by | Notes |
|----------|---------|-------|
| `TELEGRAM_BOT_TOKEN` | Phase 7 Telegram delivery | The **only** variable consulted for the bot token |
| `GITHUB_TOKEN` / `GH_TOKEN` | Phases 2–6 GitHub collection | Either name works |

Loading rules (`src/utils/env.py`, standard library only — no new dependency):

- The process environment **always wins**. `.env` never overrides a variable that
  is already set, so a CI secret cannot be shadowed by a stale local file.
- Values are never logged or printed. `load_env_file()` returns variable *names*.
- A missing `.env` is a no-op, so production runs are unaffected.
- Comment lines, blank lines, an `export` prefix, quoted values, and ` #` inline
  comments are supported.

`python scripts/check_telegram.py` reports readiness using booleans, counts, and
the token length only — it never prints the token, so its output is safe to paste
into a terminal or a CI log. It exits non-zero when delivery is not ready.

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
Phase 7 Telegram delivery (escaped, split, per-chat isolation)
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

## Telegram Notifications (Phase 7)

`src/notifications/telegram.py` delivers already-computed results to Telegram.
It is a **thin outbound delivery layer**: it does not query GitHub, contains no
intelligence or scoring, does not mutate state, performs no GitHub writes,
accepts no inbound control, and calls no LLM/agent/MCP/UEA runtime.

### Pipeline

```
structured results (MonitorResult / Opportunity / DeveloperReport)
        ↓
formatters         — escaped MarkdownV2, split to fit Telegram's limit
        ↓
TelegramTransport  — finite timeout, bounded retries, 429 / Retry-After
        ↓
Telegram Bot API   — https://api.telegram.org (fixed module constant)
```

### Bot Token

- Read from `TELEGRAM_BOT_TOKEN` **only** — never from config, CLI arguments, or state
- Config that sets `bot_token_ref` to any other variable is rejected at load time
- The token is never logged, never placed in errors or error context, and never persisted
  - The token is embedded in the request URL, so raw URLs are excluded from logs and errors
  - Raw exception text is excluded too, since third-party exceptions can embed the URL
- `logging.py` additionally redacts Telegram token patterns as defence-in-depth

### Configuration (`config/telegram.yml`)

```yaml
telegram:
  enabled: false
  bot_token_ref: "TELEGRAM_BOT_TOKEN"
  chat_ids:
    - chat_id: 123456789
      topics: [alerts, oss_opportunities]
    - chat_id: 987654321      # no topics = receives everything
  message:
    max_length: 4096
    parse_mode: "MarkdownV2"
    retry_attempts: 3
    retry_delay_seconds: 5
    timeout_seconds: 10
```

### Topics

| Topic | Entry point |
|-------|-------------|
| `alerts` | `notify_monitor_alerts` — ALERT and ERROR monitor results only |
| `oss_opportunities` | `notify_oss_opportunities` |
| `developer_report` | `notify_developer_report` — a Phase 6 `DeveloperReport` |
| `run_summary` | `notify_report` — daily/weekly briefs built from the reporting ledger (`telegram.run_summary`) |
| `oss_summary` | reserved; jobs no longer send OSS completion summaries (formatter kept for API compatibility) |
| `security` | `notify_security_alerts` — open findings batch; recovery events via `notify_report` |

### Delivery Behavior

| Condition | Behavior |
|-----------|----------|
| 2xx `ok: true` | delivered |
| 2xx `ok: false` | structured error |
| 400 / 404 | structured error, no retry |
| 401 / 403 | structured error, no retry |
| 429 | retried after `Retry-After`, else exponential backoff |
| 5xx | retried with exponential backoff |
| timeout / network | retried, then structured error |

- Attempts are bounded at `retry_attempts + 1`; every request has a finite timeout
- Delivery is a safe no-op when disabled, when no chat matches the topic, or when
  no token is set — it never raises for a delivery problem
- Failures are isolated per chat: one failing chat does not block the others, and
  a failing chat receives no further messages in that run

### Escaping and Splitting

- All text — including the message title — goes through `escape_markdown_v2`, so
  a hostile repository name or issue title cannot open or close a MarkdownV2 entity
- Blocks are escaped independently and then packed, so packing never splits an
  escape sequence; oversized blocks are split on their escaped form with a
  dangling backslash carried onto the next chunk
- Every message is at most `max_length` characters, even when escaping doubles the text
- Output is byte-identical for identical input

### Limitations

- Outbound only: no commands, no polling, no webhooks
- URLs are sent as escaped plain text rather than inline links
- Digest scheduling (daily/weekly jobs) belongs to Phase 8; this phase provides
  formatting and delivery only

## GitHub Actions (Phase 8)

Actions is the scheduler; the application stays in Python.

```
trigger (cron / workflow_dispatch)
        ↓
python -m src.jobs          ← job name read from GHOPS_JOB
        ↓
core/dispatcher.py
        ↓
src/jobs/jobs.py            ← collect → state/events → monitors/intelligence/developer
        ↓
Telegram                    ← outbound only
```

The only substantive step in any workflow is `python -m src.jobs`. No expression
is interpolated into a shell command, and no business logic lives in YAML.

### Workflows

| Workflow | Schedule (UTC) | Schedule (IST) | Job | Purpose |
|----------|----------------|----------------|-----|---------|
| `security.yml` | `0 2 * * *` | 07:30 | `security` | Dependabot + code scanning radar, at-least-once alerts |
| `oss-hunter.yml` | `0 12 * * *` | 17:30 | `oss-hunt` | OSS opportunity discovery |
| `daily.yml` | `30 15 * * *` | 21:00 | `daily` | Collection, release/CI detection, alert digest, daily brief |
| `weekly-report.yml` | `0 16 * * 1` | Mon 21:30 | `weekly-report` | Personal activity report + weekly brief |
| `monitoring.yml` | `30 */2 * * *` | every 2h at :30 | `monitoring` | CI failures + security alerts |
| `manual.yml` | on demand | — | any | `workflow_dispatch` with a `choice` job input |

Schedules are staggered so no two state-writing jobs share a minute
(monitoring uses `:30`, others `:00`/`:30` on non-overlapping slots).
IST = UTC+5:30 (fixed offset; no tzdata dependency).

All six also support manual dispatch. Every job has a 25-minute timeout.

### Permissions

Every workflow declares explicit **read-only** scopes. Nothing has `write`.

| Workflow | `contents` | `issues` | `pull-requests` | `actions` | `security-events` |
|----------|-----------|----------|-----------------|-----------|-------------------|
| `daily.yml` | read | read | read | read | — |
| `monitoring.yml` | read | — | — | read | read |
| `weekly-report.yml` | read | read | read | — | — |
| `oss-hunter.yml` | read | read | — | — | — |
| `security.yml` | read | — | — | — | read |
| `manual.yml` | read | read | read | read | read |

Note the key is `security-events` (hyphen); `security_events` is not a valid
permission name.

### Secrets

| Secret | Used for |
|--------|----------|
| `GITHUB_TOKEN` | Repository data collection (read-only scopes above) |
| `TELEGRAM_BOT_TOKEN` | Outbound delivery |

Both are provided through the `env:` block only — never as CLI arguments, never
in a cache key, never in `run:` text. Add `TELEGRAM_BOT_TOKEN` under
*Settings → Secrets and variables → Actions*. `GITHUB_TOKEN` is automatic.

### State Persistence

Actions cache carries `data/state/` between runs. It is a rolling snapshot, not
a database:

- **Restore** uses the shared prefix `gh-ops-state-`, which resolves to the most
  recently created snapshot regardless of which workflow wrote it.
- **Save** uses a fresh immutable key
  (`gh-ops-state-<job>-${{ github.run_id }}-${{ github.run_attempt }}`), because
  cache entries cannot be overwritten.
- A `${{ github.run_id }}`-only key would be unusable as the restore path, since
the next run can never hit it exactly.
- All state-writing workflows share one `concurrency: gh-ops-state` group with
  `cancel-in-progress: false`, serializing the read-modify-write cycle against
  the single shared state file.

### Failure Behavior

| Condition | Result |
|-----------|--------|
| One repository fails to collect | Job succeeds; other repositories still collected, previous state preserved |
| Bad config, missing credentials, unreadable state | Job fails, exit 1, state not saved |
| Telegram delivery fails | Job still succeeds; state was already persisted |
| Unknown or missing job name | Exit 1 |

A job invocation never exits 0 without having executed a job.

### Limitations

- The state cache is evicted after **7 idle days** of no runs, and lives in a
  shared **10 GB LRU pool** that grows by one entry per successful run.
- GitHub runs at most one *running* plus one *pending* run per concurrency group;
  a third queued run replaces the pending one.
- `GITHUB_TOKEN` may be refused by the Dependabot alerts endpoint. When that
  happens the collector is recorded as a partial failure and previous state is
  preserved — the system never reports "no alerts" for a check it could not run.

## Local Testing

All tests use mocked HTTP responses — no live GitHub contact:

```bash
# Run full test suite
python -m pytest tests/ -v

# Run specific module tests
python -m pytest tests/github/ -v
python -m pytest tests/core/ -v

# Validate the workflow YAML, permissions, pins, and state strategy
python -m pytest tests/ci/ -v
```

Workflow validation is static: it parses `.github/workflows/*.yml` and asserts
the security and state invariants documented below. It never triggers a workflow
or contacts GitHub.

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
| `core/dispatcher.py` (CLI) | 24 | Exit codes, env-var job selection, module + `run_local.py` entry points |
| `github/auth.py` | 15 | Token resolution, AuthConfig |
| `github/client.py` | 16 | GET-only, retry, pagination, ETags |
| `github/models.py` | 19 | 8 dataclasses, from_api, to_dict |
| `github/collectors.py` | 23 | 7 collectors, mocked responses |
| `monitors/repository.py` | 12 | Repository event evaluation, collection failures |
| `monitors/ci.py` | 12 | CI workflow event evaluation, failures, recovery |
| `monitors/release.py` | 12 | Release event evaluation, prerelease/draft filtering |
| `monitors/endpoint.py` | 26 | Security validation, config parsing, URL checks |
| `monitors/__init__.py` | 16 | Registry, event dispatch, summary |
| `monitors/monitor.py` | 15 | MonitorResult model, classification, properties, SECURITY category |
| `monitors/*` (properties) | 5 | Property tests for MonitorResult invariants |
| `utils/text.py` | 23 | MarkdownV2 escaping, split, truncate |
| `utils/time.py` | 16 | Timestamps, relative time |
| `utils/env.py` | 31 | .env parsing, precedence, secret safety |
| `intelligence/oss/models.py` | 9 | Opportunity, ScoreBreakdown, from_search_result |
| `intelligence/oss/queries.py` | 16 | Query generation, dedup, config, custom queries |
| `intelligence/oss/filters.py` | 23 | 9 filter rules, bot detection, config parsing |
| `intelligence/oss/scorer.py` | 26 | 7 scoring signals, normalization, tie-breaking |
| `intelligence/oss/dedup.py` | 18 | Identity dedup, provenance merge, idempotency |
| `intelligence/oss/hunter.py` | 14 | Orchestrator, metadata enrichment, partial failure |
| `developer/activity.py` | 38 | Activity extraction, filtering, dedup, data quality |
| `developer/statistics.py` | 21 | Counts, repo breakdown, periods, active days |
| `developer/reports.py` | 16 | Report generation, periods, formatting |
| `notifications/telegram.py` (config) | 48 | Token resolution, config validation, chat routing, summary toggles |
| `notifications/telegram.py` (transport) | 40 | Send, 4xx/5xx, retries, Retry-After, token safety |
| `notifications/telegram.py` (formatters) | 47 | Escaping, splitting, long messages, determinism |
| `notifications/telegram.py` (notifier) | 33 | Routing, failure isolation, safe skipping |
| `notifications/telegram.py` (summaries) | 11 | Run/oss summary formatting, toggles, delivery isolation |
| `intelligence/security/*` | 24 | Severity ranks, findings, radar, report shape |
| `monitors/security.py` | 17 | Alert evaluation, registry wiring, disabled/error paths |
| `notifications/security report` | 15 | Security formatters, toggles, topic routing, isolation |
| `jobs/jobs.py` | 50 | Six jobs, dispatch, partial failure, delivery isolation, OSS + security dedup |
| `jobs/*` (architecture) | 17 | Dependency direction, no HTTP/LLM/eval in the job layer |
| `.github/workflows/*` | 220 | Static validation: YAML, permissions, SHA pins, schedules, secrets, state |
| **Total** | **1150** | |

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
│   ├── monitors/       # Repository, CI, release, security, endpoint monitors
│   │   ├── __init__.py # Monitor registry and evaluator
│   │   ├── repository.py # Repository metadata monitoring
│   │   ├── ci.py       # CI/workflow failure/recovery detection
│   │   ├── release.py  # Release event monitoring
│   │   ├── security.py # Security alert change evaluation
│   │   └── endpoint.py # HTTP endpoint health checks (SSRF-protected)
│   ├── notifications/  # Outbound Telegram delivery (Phase 7)
│   ├── jobs/           # Thin job orchestration for Actions (Phase 8)
│   │   ├── __main__.py # python -m src.jobs <job_name>
│   │   ├── pipeline.py # Shared plumbing: config, collection, state, diff
│   │   └── jobs.py     # The six jobs + registration
│   └── utils/          # Logging, text, time helpers
├── .github/workflows/  # Six Actions workflows (thin: they call python -m src.jobs)
├── config/             # YAML configuration files (monitoring.yml, oss_hunter.yml, telegram.yml)
├── .env.example        # Tracked template; real .env is gitignored
├── data/               # Runtime state (gitignored, carried by Actions cache)
├── tests/              # Test suite (1084 tests)
└── scripts/            # Local development scripts
```

## Runtime Dependencies

- Python 3.10+
- `requests` — GitHub API HTTP client (client.py only)
- `pyyaml` — Configuration file parsing

## License

MIT
