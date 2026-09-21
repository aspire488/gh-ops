# gh-ops Architecture v2

A reusable, deterministic GitHub operations and intelligence platform.

> "A personal GitHub operations layer running on GitHub Actions."

---

## A. Revised Architecture

### Three-Layer Model

```
┌─────────────────────────────────────────────────────────────┐
│                    DEVELOPMENT PLANE                        │
│                                                             │
│ OpenCode + MCPs                                             │
│ UEA (deterministic engineering capabilities)                │
│ Specialist Agents (architect, security, test, etc.)         │
│ Skills (verify-change, impact-analysis, code-quality, etc.) │
│ Tree-sitter · GitNexus · Hypothesis · Mutation Testing      │
│                                                             │
│ Boundary: These tools BUILD and MAINTAIN gh-ops.            │
│ They are NOT runtime dependencies.                          │
└───────────────────────────┬─────────────────────────────────┘
                            │ develops / audits / verifies
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                       GH-OPS                                 │
│                                                             │
│ Core Runtime (config, state, events, errors, rate_limit)    │
│ GitHub Client (single API exit point)                       │
│ Collectors (repos, issues, PRs, releases, workflows, user)  │
│ OSS Intelligence (hunter, filters, scorer, discovery)       │
│ Repository Intelligence (monitor, CI, release, security)    │
│ Developer Intelligence (activity, statistics, reports)      │
│ Notifications (Telegram adapter)                            │
│                                                             │
│ Boundary: Deterministic Python. No LLM. Read-mostly.        │
│ State is JSON. Workflows are thin. Logic is in Python.      │
└───────────────────────────┬─────────────────────────────────┘
                            │ executes in
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    EXECUTION PLANE                          │
│                                                             │
│ GitHub Actions (scheduler + runner)                         │
│ GitHub REST / GraphQL APIs (data source)                    │
│ Telegram Bot API (notification output)                      │
│                                                             │
│ Boundary: gh-ops runs here via GitHub Actions.              │
│ No OpenCode. No UEA. No MCPs. No LLMs.                     │
└─────────────────────────────────────────────────────────────┘
```

### Core Execution Pipeline

```
TRIGGER (schedule / workflow_dispatch / event)
    ↓
COLLECT (GitHub API → normalized models)
    ↓
COMPARE (current data vs. stored state)
    ↓
DETECT (new items, changes, conditions)
    ↓
ANALYZE (score, rank, filter — deterministic)
    ↓
DEDUPLICATE (suppress already-reported items)
    ↓
REPORT (format structured output)
    ↓
NOTIFY (Telegram delivery)
```

### Key Principle

Every module is independent. GitHub Actions orchestrates Python entry points. Python modules contain all logic. YAML workflows stay thin.

---

## B. Revised Directory Tree

```
gh-ops/
│
├── .github/
│   └── workflows/
│       ├── daily.yml              # Daily collection + digest
│       ├── monitoring.yml         # Frequent monitoring checks
│       ├── weekly-report.yml      # Weekly summary generation
│       ├── oss-hunter.yml         # OSS opportunity discovery
│       ├── security.yml           # Security/dependency scan
│       └── manual.yml             # workflow_dispatch operations
│
├── src/
│   ├── __init__.py
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py             # YAML config loader + validation
│   │   ├── state.py              # JSON state persistence (atomic writes)
│   │   ├── events.py             # Event detection (diff state)
│   │   ├── errors.py             # Structured error types, partial-failure
│   │   ├── rate_limit.py         # Rate-limit tracking, backoff
│   │   └── dispatcher.py         # Lightweight job dispatcher (replaces scheduler.py)
│   │
│   ├── github/
│   │   ├── __init__.py
│   │   ├── client.py             # Central GitHub API client (SINGLE exit point)
│   │   ├── auth.py               # Token resolution (GITHUB_TOKEN env var)
│   │   ├── models.py             # Dataclasses for GitHub API responses
│   │   └── collectors/
│   │       ├── __init__.py
│   │       ├── repos.py          # Repository metadata
│   │       ├── issues.py         # Issue listing + search
│   │       ├── pulls.py          # PR listing
│   │       ├── releases.py       # Release listing
│   │       ├── workflows.py      # Workflow runs
│   │       ├── security.py       # Dependabot alerts, code scanning
│   │       └── user.py           # Authenticated user activity
│   │
│   ├── intelligence/
│   │   ├── __init__.py
│   │   ├── oss/
│   │   │   ├── __init__.py
│   │   │   ├── hunter.py         # Opportunity orchestration
│   │   │   ├── filters.py        # Noise filtering (deterministic)
│   │   │   ├── scorer.py         # Deterministic scoring engine
│   │   │   └── queries.py        # Search query construction + management
│   │   ├── releases/
│   │   │   ├── __init__.py
│   │   │   └── radar.py          # Release tracking + alerts
│   │   ├── security/
│   │   │   ├── __init__.py
│   │   │   └── radar.py          # Security advisory monitoring
│   │   └── discovery/
│   │       ├── __init__.py
│   │       └── repos.py          # Repository discovery by criteria
│   │
│   ├── developer/
│   │   ├── __init__.py
│   │   ├── activity.py           # Personal GitHub activity collection
│   │   ├── statistics.py         # Aggregate stats (weekly/monthly)
│   │   └── reports.py            # Report formatting for Telegram
│   │
│   ├── monitors/
│   │   ├── __init__.py
│   │   ├── repository.py         # Stars, forks, issues, PRs, activity
│   │   ├── ci.py                 # Workflow health, repeated failures
│   │   ├── release.py            # New release detection
│   │   └── endpoint.py           # Generic URL/API health checks
│   │
│   ├── notifications/
│   │   ├── __init__.py
│   │   └── telegram.py           # Telegram Bot API adapter
│   │
│   └── utils/
│       ├── __init__.py
│       ├── logging.py            # Structured collector logging
│       ├── text.py               # Text escaping, formatting, splitting
│       └── time.py               # Timezone, date helpers
│
├── config/
│   ├── repositories.yml          # Monitored repository list
│   ├── oss_hunter.yml            # OSS opportunity criteria + scoring weights
│   ├── monitoring.yml            # Monitor feature toggles
│   ├── telegram.yml              # Bot token ref, chat IDs
│   └── settings.yml              # Global settings (timezone, rate limits)
│
├── data/
│   ├── state/                    # Runtime state (gitignored, Actions cache)
│   │   └── .gitkeep
│   └── history/                  # Historical data for trend reports
│       └── .gitkeep
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py               # Shared fixtures, mock factories
│   ├── fixtures/                 # Recorded API response fixtures
│   │   ├── github/
│   │   └── state/
│   ├── core/
│   │   ├── __init__.py
│   │   ├── test_config.py
│   │   ├── test_state.py
│   │   ├── test_events.py
│   │   └── test_rate_limit.py
│   ├── github/
│   │   ├── __init__.py
│   │   ├── test_client.py
│   │   ├── test_auth.py
│   │   └── test_collectors.py
│   ├── intelligence/
│   │   ├── __init__.py
│   │   ├── test_oss_hunter.py
│   │   ├── test_scorer.py
│   │   └── test_filters.py
│   ├── developer/
│   │   ├── __init__.py
│   │   └── test_statistics.py
│   ├── monitors/
│   │   ├── __init__.py
│   │   ├── test_repository.py
│   │   ├── test_ci.py
│   │   └── test_release.py
│   └── notifications/
│       ├── __init__.py
│       └── test_telegram.py
│
├── scripts/
│   └── run_local.py              # Local dev runner
│
├── pyproject.toml                # Project metadata, dependencies
├── requirements.txt              # Pinned runtime dependencies
├── .gitignore
├── ARCHITECTURE.md               # This file
└── README.md
```

### Changes From v1

| v1 | v2 | Reason |
|----|-----|--------|
| `scheduler.py` | `dispatcher.py` | Not a scheduler — GitHub Actions schedules. This is a job dispatcher. |
| `oss/` only in `intelligence/` | Separate `oss-hunter.yml` workflow | OSS is first-class, deserves its own workflow |
| `data/state/*.json` in git | `data/state/` gitignored + Actions cache | Runtime state should not be committed |
| No `queries.py` | `intelligence/oss/queries.py` | Search query management is complex enough to warrant its own module |
| 5 workflows | 6 workflows | Added dedicated `oss-hunter.yml` |

---

## C. UEA Integration Boundary

### UEA Inventory

UEA provides the following core modules:

| Module | Type | Dependencies | gh-ops Relevance |
|--------|------|-------------|-----------------|
| `tree_sitter_engine.py` | Structural code analysis | tree-sitter, language grammars | KEEP OUTSIDE — development-time only |
| `impact_model.py` | Import graph, blast radius | Pure Python | KEEP OUTSIDE — development-time only |
| `verification.py` | Tiered verification | subprocess (pytest, semgrep, mypy) | KEEP OUTSIDE — development-time only |
| `scoring.py` | Deterministic scoring framework | Pure Python | ADAPT — scoring patterns reusable for OSS scorer |
| `event_log.py` | SQLite event tracking | sqlite3 | KEEP OUTSIDE — development-time analytics |
| `analytics.py` | DuckDB analytics | duckdb | KEEP OUTSIDE — development-time analytics |
| `specialist_registry.py` | Specialist metadata store | Pure Python | KEEP OUTSIDE — development-time agent routing |
| `specialist_router.py` | Task → specialist routing | specialist_registry | KEEP OUTSIDE — development-time agent routing |
| `property_testing.py` | Hypothesis integration | hypothesis | KEEP OUTSIDE — development-time testing |
| `mutation_testing.py` | Mutation testing | subprocess | KEEP OUTSIDE — development-time testing |
| `sat_engine.py` | Z3 SAT/SMT | z3-solver | KEEP OUTSIDE — development-time formal verification |
| `project_detector.py` | Project type detection | Pure Python | KEEP OUTSIDE — development-time |
| `candidate_engine.py` | Git worktree management | subprocess (git) | KEEP OUTSIDE — development-time |
| `worktree_engine.py` | Worktree operations | subprocess (git) | KEEP OUTSIDE — development-time |
| `scale_decision.py` | Task decomposition | Pure Python | KEEP OUTSIDE — development-time |
| `provenance.py` | Evidence lineage | Pure Python | KEEP OUTSIDE — development-time |
| `cli.py` | CLI entry point | argparse | KEEP OUTSIDE — development-time |

### Classification

```
REUSE (patterns, not code):
  - scoring.py dimensions/weights pattern → adapt for OSS scorer
  - event_log.py structured logging pattern → adapt for collector logging
  - verification.py tier model → adopt for gh-ops verification

ADAPT (modify for gh-ops):
  - scoring.py weight system → OSS opportunity scoring
  - project_detector.py → detect gh-ops project for development

KEEP OUTSIDE GH-OPS (development-time only):
  - tree_sitter_engine.py — code analysis during development
  - impact_model.py — blast radius before editing gh-ops
  - verification.py — pre-commit verification of gh-ops code
  - property_testing.py — testing gh-ops logic
  - mutation_testing.py — testing gh-ops test quality
  - sat_engine.py — formal verification of constraints
  - specialist_registry.py — agent routing during development
  - specialist_router.py — task routing during development
  - candidate_engine.py — parallel experiment worktrees
  - worktree_engine.py — git worktree management
  - provenance.py — evidence tracking during development
  - analytics.py — DuckDB queries on development events
  - event_log.py — development event tracking
  - scale_decision.py — task decomposition
  - cli.py — UEA CLI

DUPLICATE (do not import):
  - None. UEA modules are not copied into gh-ops.

IGNORE (not relevant):
  - adapters/ — agent-specific formatting not needed
  - benchmarks/ — UEA benchmarks, not gh-ops
  - examples/ — UEA examples, not gh-ops
```

### Development Plane Dependency

```
OpenCode + UEA + Skills + MCPs
        │
        │ uses
        ▼
┌───────────────────────────────┐
│ Development Dependencies      │
│                               │
│ UEA core/ (installed -e)      │
│ tree-sitter + grammars        │
│ hypothesis                    │
│ z3-solver                     │
│ duckdb                        │
│ pytest, mypy, semgrep         │
│                               │
│ These are for BUILDING        │
│ gh-ops, not for RUNNING it.   │
└───────────────────────────────┘
        │
        │ develops
        ▼
┌───────────────────────────────┐
│ gh-ops Runtime Dependencies   │
│                               │
│ requests (GitHub API)         │
│ pyyaml (config)               │
│ pytest (testing)              │
│                               │
│ That's it. Keep it minimal.   │
└───────────────────────────────┘
```

---

## D. Specialist-Agent Model

### Purpose

Specialist agents help BUILD and MAINTAIN `gh-ops`. They do NOT become runtime dependencies.

### Available Specialists (from UEA registry)

| Specialist | Domain | gh-ops Use |
|-----------|--------|------------|
| `systematic-debugging` | debugging | Debugging gh-ops failures |
| `verification-before-completion` | code_quality | Pre-commit verification |
| `research-workflow` | research | Architecture decisions |
| `security-audit` | security | Workflow security review |
| `architecture-review` | architecture | Module boundary review |
| `performance-analysis` | performance | API client performance |
| `test-generation` | testing | Test creation for collectors |
| `code-review` | code_quality | Code quality checks |
| `documentation-generation` | documentation | README/doc generation |

### How Specialists Are Used

```text
DEVELOPMENT TIME (not runtime):

When modifying gh-ops code:
  1. OpenCode detects task type
  2. Specialist router recommends specialist
  3. Specialist + deterministic tools work together
  4. UEA verification runs
  5. Changes are validated
  6. gh-ops code is committed

RUNTIME (gh-ops in GitHub Actions):
  1. GitHub Actions triggers workflow
  2. gh-ops Python modules execute
  3. No specialists. No UEA. No LLM.
  4. Deterministic logic only.
  5. Results sent to Telegram.
```

### Specialist Boundary Rule

```
NEVER in gh-ops runtime:
  - specialist_registry
  - specialist_router
  - candidate_engine
  - worktree_engine
  - provenance
  - event_log (SQLite)
  - analytics (DuckDB)

ALWAYS in gh-ops runtime:
  - config
  - state (JSON)
  - github client
  - collectors
  - intelligence
  - monitors
  - notifications
```

---

## E. Skill Integration Model

### Skills Relevant to gh-ops Development

| Skill | Category | gh-ops Use | Classification |
|-------|----------|------------|----------------|
| `verify-change` | Verification | Pre-commit verification of gh-ops code | REUSE |
| `impact-analysis` | Analysis | Blast radius before editing shared modules | REUSE |
| `code-quality` | Quality | Code quality checks on gh-ops modules | REUSE |
| `property-mutation-testing` | Testing | Property + mutation testing of gh-ops logic | REUSE |
| `event-log` | Analytics | Log development events to UEA database | REUSE |
| `tree-sitter-structure` | Analysis | Parse gh-ops code structure | REUSE |
| `gitnexus-impact-analysis` | Analysis | Deep impact analysis via GitNexus | OPTIONAL |
| `gitnexus-refactoring` | Refactoring | Safe refactoring of gh-ops modules | OPTIONAL |
| `gitnexus-pr-review` | Review | PR review of gh-ops changes | OPTIONAL |
| `gitnexus-debugging` | Debugging | Debug complex gh-ops failures | OPTIONAL |
| `sat-smt-solver` | Formal | Verify constraint logic in scorer | OPTIONAL |
| `candidate-lifecycle` | Architecture | Isolate architectural experiments | OPTIONAL |
| `worktree-candidate` | Architecture | Create worktrees for parallel approaches | OPTIONAL |
| `duckdb-analytics` | Analytics | Query gh-ops development metrics | OPTIONAL |
| `karpathy-guidelines` | Quality | Reduce common LLM coding mistakes | REUSE |

### Skills NOT Relevant to gh-ops

| Skill | Reason |
|-------|--------|
| `customize-opencode` | OpenCode configuration, not gh-ops |
| `kio-event-flow` | KIO-specific event architecture |
| `kio-llm-boundary` | KIO-specific LLM boundary analysis |
| `kio-regression` | KIO-specific regression detection |
| `afl-fuzz-trigger` | Binary fuzzing, not applicable |
| `gitnexus-guide` | GitNexus documentation |
| `gitnexus-pdg-query` | Program dependence graph (KIO) |
| `gitnexus-taint-analysis` | Taint analysis (KIO) |

### Skill Usage Rule

```
Skills are used:
  ✓ During development of gh-ops
  ✓ During code review of gh-ops
  ✓ During verification of gh-ops
  ✓ During debugging of gh-ops

Skills are NOT:
  ✗ Installed in gh-ops runtime
  ✗ Required by gh-ops GitHub Actions
  ✗ Dependencies of gh-ops Python code
```

---

## F. MCP Integration Model

### MCP Inventory

| MCP | Category | gh-ops Use | Classification |
|-----|----------|------------|----------------|
| `github` | GitHub API | Inspect gh-ops repo, issues, PRs | REQUIRED (dev) |
| `git` | Git operations | Branch, commit, diff analysis | REQUIRED (dev) |
| `filesystem` | File access | Read/write gh-ops source files | REQUIRED (dev) |
| `context7` | Documentation | Library docs during development | USEFUL (dev) |
| `serena` | Code navigation | Symbol lookup, find references | USEFUL (dev) |
| `firecrawl` | Web research | Research GitHub API docs, patterns | USEFUL (dev) |
| `sequential-thinking` | Reasoning | Complex architecture decisions | OPTIONAL (dev) |
| `memory` | Knowledge graph | Track architecture decisions | OPTIONAL (dev) |
| `dbhub` | Database | Query UEA SQLite event database | OPTIONAL (dev) |
| `chrome-devtools` | Browser | Inspect web UIs (not needed) | UNNECESSARY |
| `playwright` | Browser | Web automation (not needed) | UNNECESSARY |

### MCP Boundary Rule

```
MCPs are DEVELOPMENT-TIME tools.

They are available:
  ✓ In OpenCode sessions
  ✓ During architecture work
  ✓ During code review
  ✓ During debugging

They are NOT:
  ✗ Part of gh-ops runtime
  ✗ Required by GitHub Actions
  ✗ Dependencies of gh-ops
  ✗ Available in production execution
```

### Development vs. Runtime

```
DEVELOPMENT ENVIRONMENT:
  OpenCode + UEA + Skills + MCPs
  → Full tool access
  → Agent-assisted development
  → Rich analysis capabilities

GH-OPS PRODUCTION RUNTIME:
  GitHub Actions + Python + GitHub API + Telegram
  → Deterministic only
  → No external dependencies beyond runtime requirements
  → No UEA, no MCPs, no agents, no LLM
```

---

## G. OSS Subsystem Architecture

### OSS Intelligence Pipeline

```
config/oss_hunter.yml
    ↓
src/intelligence/oss/queries.py
    → Build search queries from config
    → Execute GitHub Search API queries
    → Merge results from multiple queries
    ↓
src/github/collectors/issues.py
    → Fetch candidate issues
    → Normalize to Issue model
    ↓
src/intelligence/oss/filters.py
    → Remove bot-authored issues
    → Remove locked issues
    → Remove issues with excluded labels
    → Remove issues older than max_age
    → Remove issues already reported
    → Remove issues from excluded repos
    ↓
src/intelligence/oss/scorer.py
    → Score each candidate:
        repo_activity (stars, recent commits, contributor count)
        issue_age (newer = higher)
        label_signal (good-first-issue, help-wanted, etc.)
        discussion_activity (comments, reactions)
        assignment_status (unassigned = higher)
        repo_language (preferred languages)
        issue_scope (labeled scope signals)
        maintainer_activity (recent maintainer responses)
    → Apply configurable weights
    → Stable tie-breaking (by issue number)
    → Return ranked list
    ↓
src/core/state.py
    → Load oss_opportunities.json
    → Deduplicate against already-reported
    → Save new state
    ↓
src/notifications/telegram.py
    → Format opportunity report
    → Send to configured chat
```

### OSS Scoring Model

```yaml
# config/oss_hunter.yml
oss_hunter:
  enabled: true
  max_results: 10

  queries:
    - "label:\"good first issue\" language:python is:open"
    - "label:\"help wanted\" language:python is:open"
    - "label:\"good first issue\" language:typescript is:open"
    - "label:\"beginner\" is:open stars:>50"
    - "label:\"easy\" is:open stars:>100"
    - "is:issue is:open label:\"bug\" language:python no:assignee"

  filters:
    exclude_labels:
      - "wontfix"
      - "duplicate"
      - "invalid"
      - "wip"
    exclude_bot_authors: true
    exclude_locked: true
    min_repo_stars: 5
    max_issue_age_days: 90
    exclude_repos: []

  scoring:
    weights:
      repo_activity: 0.25
      issue_freshness: 0.20
      label_signal: 0.15
      discussion_activity: 0.15
      assignment_status: 0.10
      maintainer_activity: 0.10
      scope_signal: 0.05

    repo_activity:
      stars_weight: 0.4
      recent_commits_weight: 0.3
      contributors_weight: 0.3

    issue_freshness:
      decay_function: "linear"
      max_days: 90
      fresh_bonus: 0.2

    label_signals:
      "good first issue": 1.0
      "help wanted": 0.8
      "beginner": 0.9
      "easy": 0.7
      "bug": 0.5
      "documentation": 0.6

  tie_breaking:
    method: "issue_number_asc"
```

### OSS Scoring Algorithm (Deterministic)

```python
def score_opportunity(issue, repo_metadata, config):
    """
    Same input → same score → same ranking.
    No randomness. No LLM. Pure arithmetic.
    """
    weights = config["scoring"]["weights"]

    # Repo activity score (0.0 - 1.0)
    repo_score = (
        normalize_stars(repo_metadata.stars) * 0.4 +
        normalize_commits(repo_metadata.recent_commits) * 0.3 +
        normalize_contributors(repo_metadata.contributors) * 0.3
    )

    # Issue freshness score (0.0 - 1.0)
    age_days = (now - issue.created_at).days
    freshness = max(0, 1.0 - (age_days / 90))

    # Label signal score (0.0 - 1.0)
    label_score = max(
        config["scoring"]["label_signals"].get(label, 0.0)
        for label in issue.labels
    ) if issue.labels else 0.0

    # Discussion activity (0.0 - 1.0)
    discussion = min(1.0, issue.comments / 10)

    # Assignment status (1.0 if unassigned, 0.0 if assigned)
    assignment = 0.0 if issue.assignee else 1.0

    # Maintainer activity (0.0 - 1.0)
    maintainer = min(1.0, repo_metadata.recent_maintainer_responses / 5)

    # Scope signal (0.0 - 1.0)
    scope = compute_scope_signal(issue)

    # Weighted sum
    score = (
        repo_score * weights["repo_activity"] +
        freshness * weights["issue_freshness"] +
        label_score * weights["label_signal"] +
        discussion * weights["discussion_activity"] +
        assignment * weights["assignment_status"] +
        maintainer * weights["maintainer_activity"] +
        scope * weights["scope_signal"]
    )

    # Tie-breaking: use issue number as secondary sort
    return RoundScore(score, tie_breaker=issue.number)
```

### OSS Search Strategy

The system does NOT limit itself to `good first issue` / `help wanted`. Those are signals, not the entire strategy.

**Multi-query approach:**

1. Label-based queries (`good first issue`, `help wanted`, `beginner`, `easy`)
2. Language-filtered queries (per configured language)
3. Activity-based queries (recently active repos with open bugs)
4. Scope-filtered queries (labeled bugs, documentation, tests)
5. Freshness-filtered queries (recently opened issues)
6. Repo-size queries (small/medium repos with fewer maintainers)

**Deduplication:**

- Track issue URLs in `data/state/oss_opportunities.json`
- Hash of (repo, issue_number) for dedup
- Prune entries older than configurable threshold

---

## H. GitHub Actions Architecture

### Workflow Map

| Workflow | Trigger | Purpose | Permissions |
|----------|---------|---------|-------------|
| `daily.yml` | `cron '0 8 * * *'` + `workflow_dispatch` | Repository collection, release detection, CI status, daily digest | `contents:read`, `issues:read`, `pull-requests:read`, `actions:read` |
| `monitoring.yml` | `cron '0 */2 * * *'` + `workflow_dispatch` | CI failure detection, security alert checks | `contents:read`, `actions:read`, `security_events:read` |
| `weekly-report.yml` | `cron '0 9 * * 1'` + `workflow_dispatch` | Personal activity, statistics, weekly report | `contents:read`, `issues:read`, `pull-requests:read` |
| `oss-hunter.yml` | `cron '0 10 * * *'` + `workflow_dispatch` | OSS opportunity discovery + scoring + Telegram delivery | `contents:read`, `issues:read` |
| `security.yml` | `cron '0 6 * * *'` + `workflow_dispatch` | Dependabot alerts, code scanning | `contents:read`, `security_events:read` |
| `manual.yml` | `workflow_dispatch` only | On-demand operations | Read-only (varies) |

### Workflow Design Principles

1. **Thin workflows.** All logic in Python modules.
2. **Explicit permissions.** No speculative write permissions.
3. **Pinned actions.** Use SHA-pinned third-party actions.
4. **Secrets in env vars.** Never in CLI arguments.
5. **No untrusted interpolation.** Never `${{ github.event.* }}` in `run:` steps.
6. **Timeout limits.** Every job has a timeout.
7. **Cache state.** Use GitHub Actions cache for `data/state/`.

### State Persistence in Actions

```yaml
# In each workflow:
- name: Restore state cache
  uses: actions/cache@v4
  with:
    path: data/state
    key: gh-ops-state-${{ github.run_id }}
    restore-keys: |
      gh-ops-state-

- name: Run operation
  run: python -m src.core.dispatcher run daily

- name: Save state cache
  uses: actions/cache@v4
  with:
    path: data/state
    key: gh-ops-state-${{ github.run_id }}
```

---

## I. Security Model

### Preventing Accidental GitHub Writes

1. **V1 is read-only.** Every collector uses only `GET` requests.
2. `client.py` enforces a method whitelist: only `GET` is allowed.
3. Workflows declare minimal permissions (read-only).
4. Future write capability requires explicit `workflow_dispatch` + human approval.

### Preventing Excessive API Usage

1. All API calls go through `client.py`.
2. `client.py` reads `X-RateLimit-Remaining` headers.
3. Degraded mode when remaining quota < 100.
4. `Retry-After` respect on 429 responses.
5. Per-collector timeout: 30 seconds.
6. Total workflow timeout: 25 minutes.

### Preventing Credential Leakage

1. Tokens in `auth.py`, never logged or printed.
2. `logging.py` redacts token patterns.
3. Secrets in env vars only, never CLI args.
4. `.env` files gitignored.

### Preventing Workflow Injection

1. No `${{ github.event.* }}` in `run:` steps.
2. Untrusted data via env vars, not shell interpolation.
3. No `eval()`, no `exec()`, no shell string interpolation of payloads.
4. Python entry points use env vars, not parsed event JSON.

### Preventing Unsafe Fork Execution

1. No `pull_request_target` workflows with fork code checkout.
2. No execution of code from untrusted sources.
3. `manual.yml` runs only on `workflow_dispatch` from maintainers.

### Workflow Permission Audit

| Workflow | `contents` | `issues` | `pull-requests` | `actions` | `security_events` |
|----------|-----------|----------|-----------------|-----------|-------------------|
| `daily.yml` | read | read | read | read | — |
| `monitoring.yml` | read | — | — | read | read |
| `weekly-report.yml` | read | read | read | — | — |
| `oss-hunter.yml` | read | read | — | — | — |
| `security.yml` | read | — | — | — | read |
| `manual.yml` | read | read | read | read | read |

No workflow has `write` permissions in V1.

---

## J. State Model

### State Strategy

**Runtime state is NOT committed to Git.**

Instead:
- GitHub Actions cache persists state between runs
- `data/state/` is gitignored
- State is restored at workflow start, saved at workflow end

### State Files

```
data/state/
├── repos.json              # Repository metadata snapshots
├── issues.json             # Last-seen issue states per repo
├── pulls.json              # Last-seen PR states per repo
├── releases.json           # Last-seen release per repo
├── workflows.json          # Last-seen workflow run states
├── security_alerts.json    # Last-seen security alerts
├── oss_opportunities.json  # Already-reported opportunities
├── user_activity.json      # Last-seen personal activity
└── etags.json              # ETag cache for conditional requests
```

### State Schema

```json
{
  "last_updated": "2026-09-21T08:00:00Z",
  "version": 1,
  "items": {
    "microsoft/vscode": {
      "last_release": "1.92.0",
      "last_release_date": "2026-09-15T00:00:00Z",
      "open_issues_count": 8432,
      "stars": 162000,
      "last_checked": "2026-09-21T08:00:00Z"
    }
  }
}
```

### State Operations

| Operation | Description |
|-----------|-------------|
| `load(filename)` | Read JSON state file. Return empty dict if missing. |
| `save(filename, data)` | Write to temp file, then atomic rename. |
| `diff(previous, current)` | Return `{added, removed, changed}` sets. |
| `prune(state, max_age_days)` | Remove entries older than threshold. |

### What Does NOT Persist

- API responses (ephemeral, re-fetched each run)
- GitHub tokens (environment variables only)
- Rate-limit counters (reconstructed from headers each run)

---

## K. Testing / Verification Model

### Test Layers

| Layer | Scope | Mocking | Speed |
|-------|-------|---------|-------|
| **Unit** | Individual functions (scoring, filtering, state, formatting) | Minimal | Fast |
| **Integration** | Collector → client → response parsing | Mock HTTP | Fast |
| **Contract** | GitHub API response format validation | Recorded fixtures | Fast |
| **E2E** | Full pipeline (collect → state → detect → format) | All APIs mocked | Medium |
| **Security** | Input sanitization, injection resistance, secret handling | N/A | Fast |

### Development-Time Verification (Using UEA)

When developing/modifying gh-ops:

```
1. Impact Analysis (impact-analysis skill)
   → What breaks if I change this module?

2. Structural Analysis (tree-sitter)
   → Parse modified files, check structure

3. Unit Tests (pytest)
   → Run existing tests

4. Property Testing (Hypothesis via UEA)
   → Verify invariants hold with random inputs

5. Mutation Testing (UEA)
   → Verify test quality

6. Security Review (security-audit specialist)
   → Check for injection, credential leakage

7. Verification (verify-change skill)
   → Run tiered verification

8. Code Quality (code-quality skill)
   → Complexity, duplication, naming
```

### Runtime Verification (GitHub Actions)

```
Every workflow run:
  1. Python syntax check (py_compile)
  2. Import validation
  3. Smoke test (import all modules)
  4. State integrity check
  5. Collector execution with error isolation
```

### Mock Strategy

```python
# All GitHub API responses are mocked via recorded fixtures
# No test makes a real API call

tests/fixtures/
├── github/
│   ├── repos_response.json
│   ├── issues_response.json
│   ├── releases_response.json
│   └── workflow_runs_response.json
└── state/
    ├── empty_state.json
    └── populated_state.json
```

### Key Test Cases

**Core:**
- State load/save round-trip
- Atomic write (no corruption on crash)
- Event detection: new items found, no false positives
- Config validation: missing required fields
- Rate-limit parsing from headers

**GitHub Client:**
- Auth header injection
- Retry on 5xx with exponential backoff
- Retry-After respect
- Timeout handling
- Malformed JSON handling
- Rate-limit triggered degradation
- GET-only enforcement (WriteDeniedError for POST/PUT/PATCH/DELETE)

**Intelligence:**
- OSS scoring produces expected rankings
- Filters correctly exclude noise
- Deduplication suppresses already-reported items
- Scoring weights are configurable
- Same input → same output (deterministic)

**Monitors:**
- Release detection: new release found, no false positive
- CI failure detection and recovery detection

**Telegram:**
- Message escaping (markdown special chars)
- Long message splitting
- Failed delivery retry

**Security:**
- Shell injection in untrusted input
- Token redaction in logs
- Env var only (no CLI args for secrets)

---

## L. Dependency Graph

### Runtime Dependencies (gh-ops in production)

```
gh-ops runtime
├── Python 3.10+
├── requests          # HTTP client for GitHub API
├── pyyaml            # Config file parsing
└── pytest            # Testing (dev dependency, not runtime)

That's it. No UEA. No tree-sitter. No hypothesis. No duckdb.
```

### Development Dependencies (building gh-ops)

```
gh-ops development
├── Python 3.10+
├── requests
├── pyyaml
├── pytest
├── pytest-cov
├── mypy
├── ruff               # Linting
├── hypothesis          # Property testing (via UEA)
├── tree-sitter          # Structural analysis (via UEA)
├── tree-sitter-python
├── z3-solver            # Formal verification (optional)
├── duckdb               # Analytics (optional)
└── UEA (installed -e)   # Development capabilities
```

### Module Dependency Graph

```
src/core/config.py
    → (reads) config/*.yml

src/core/state.py
    → (reads/writes) data/state/*.json

src/core/events.py
    → (uses) state.py

src/core/errors.py
    → (used by) all modules

src/core/rate_limit.py
    → (used by) github/client.py

src/core/dispatcher.py
    → (calls) intelligence/*, monitors/*, developer/*

src/github/auth.py
    → (reads) GITHUB_TOKEN env var

src/github/client.py
    → (uses) auth.py, rate_limit.py, errors.py
    → (calls) GitHub REST API

src/github/models.py
    → (used by) collectors/*, intelligence/*, monitors/*

src/github/collectors/*
    → (uses) client.py, models.py

src/intelligence/oss/hunter.py
    → (uses) collectors/issues.py, filters.py, scorer.py, queries.py

src/intelligence/oss/filters.py
    → (pure logic, no external deps)

src/intelligence/oss/scorer.py
    → (pure logic, no external deps)

src/intelligence/oss/queries.py
    → (uses) config.py, client.py

src/monitors/*
    → (uses) client.py, state.py, events.py

src/developer/*
    → (uses) client.py, state.py

src/notifications/telegram.py
    → (calls) Telegram Bot API
```

---

## M. Implementation Phases

### Phase 0 — Architecture + Migration (current)

- [x] Inspect existing project
- [x] Copy to gh-ops/
- [x] Initialize Git
- [x] Write .gitignore
- [x] Produce ARCHITECTURE.md v1
- [x] Inspect UEA repository
- [x] Classify UEA capabilities
- [x] Identify architecture corrections
- [x] Produce ARCHITECTURE.md v2 (this document)
- [ ] Delete old oss-command-center/
- [ ] Initial commit

### Phase 1 — Core Runtime

**Goal:** Foundation that everything else depends on.

- [ ] `src/core/config.py` — YAML config loading + validation
- [ ] `src/core/state.py` — JSON state persistence (atomic writes)
- [ ] `src/core/errors.py` — Structured error types
- [ ] `src/core/rate_limit.py` — Rate-limit header parsing + backoff
- [ ] `src/core/events.py` — Event detection (diff state)
- [ ] `src/core/dispatcher.py` — Lightweight job dispatcher
- [ ] `src/utils/logging.py` — Structured log output
- [ ] `src/utils/text.py` — Text formatting utilities
- [ ] `src/utils/time.py` — Timezone/date helpers
- [ ] `config/settings.yml` — Global settings
- [ ] Tests for all core modules

**Verification:** `verify-change` at `standard` tier.

### Phase 2 — GitHub API Client

**Goal:** Central, secure, resilient API layer.

- [ ] `src/github/auth.py` — Token resolution
- [ ] `src/github/client.py` — Central HTTP client with retry, backoff, ETag, timeout, GET-only enforcement
- [ ] `src/github/models.py` — Dataclasses for API responses
- [ ] `src/github/collectors/repos.py` — Repository metadata collector
- [ ] `src/github/collectors/issues.py` — Issue collector + search
- [ ] `src/github/collectors/pulls.py` — PR collector
- [ ] `src/github/collectors/releases.py` — Release collector
- [ ] `src/github/collectors/workflows.py` — Workflow run collector
- [ ] `src/github/collectors/user.py` — Authenticated user collector
- [ ] `config/repositories.yml` — Monitored repository list
- [ ] Tests with mock HTTP responses

**Verification:** `verify-change` at `strict` tier (shared code).

### Phase 3 — State & Event Engine

**Goal:** Detect changes between collection runs.

- [ ] Event detection logic in `src/core/events.py`
- [ ] State diff algorithms (added/removed/changed)
- [ ] ETag/conditional request support
- [ ] State pruning for old entries
- [ ] Tests for event detection edge cases

**Verification:** `verify-change` at `standard` tier.

### Phase 4 — Repository Monitoring

**Goal:** Track repository health and changes.

- [ ] `src/monitors/repository.py` — Stars, forks, issues, PRs tracking
- [ ] `src/monitors/ci.py` — Workflow failure/recovery detection
- [ ] `src/monitors/release.py` — New release detection
- [ ] `config/monitoring.yml` — Monitor toggles
- [ ] Tests

**Verification:** `verify-change` at `standard` tier.

### Phase 5 — OSS Intelligence

**Goal:** Discover contribution opportunities.

- [ ] `src/intelligence/oss/queries.py` — Search query construction
- [ ] `src/intelligence/oss/hunter.py` — Opportunity orchestration
- [ ] `src/intelligence/oss/filters.py` — Noise filtering (deterministic)
- [ ] `src/intelligence/oss/scorer.py` — Deterministic scoring (adapted from UEA scoring patterns)
- [ ] `config/oss_hunter.yml` — Hunter configuration
- [ ] OSS opportunity state tracking
- [ ] Tests (property-based + unit)

**Verification:** `verify-change` at `strict` tier + property testing.

### Phase 6 — Developer Intelligence

**Goal:** Personal activity reports.

- [ ] `src/developer/activity.py` — Activity collection
- [ ] `src/developer/statistics.py` — Stats aggregation
- [ ] `src/developer/reports.py` — Report formatting
- [ ] `data/history/` — Historical data for trend analysis
- [ ] Tests

**Verification:** `verify-change` at `standard` tier.

### Phase 7 — Telegram Integration

**Goal:** Complete notification layer.

- [ ] `src/notifications/telegram.py` — Full adapter (split, retry, escape)
- [ ] Daily digest formatting
- [ ] Weekly report formatting
- [ ] Alert formatting
- [ ] Security notification formatting
- [ ] OSS opportunity formatting
- [ ] Tests

**Verification:** `verify-change` at `standard` tier.

### Phase 8 — GitHub Actions Workflows

**Goal:** Production automation.

- [ ] `.github/workflows/daily.yml`
- [ ] `.github/workflows/monitoring.yml`
- [ ] `.github/workflows/weekly-report.yml`
- [ ] `.github/workflows/oss-hunter.yml`
- [ ] `.github/workflows/security.yml`
- [ ] `.github/workflows/manual.yml`
- [ ] Workflow permission lockdown
- [ ] Pinned action versions
- [ ] State caching strategy
- [ ] Secret handling audit

**Verification:** `verify-change` at `security` tier.

### Phase 9 — Security Hardening

**Goal:** Verify all security constraints.

- [ ] Verify no write operations in codebase
- [ ] Verify GET-only enforcement in client.py
- [ ] Verify all input sanitization
- [ ] Verify token redaction in logs
- [ ] Verify workflow permissions are minimal
- [ ] Verify no shell injection vectors
- [ ] Verify no secrets in CLI arguments
- [ ] Security-focused test additions
- [ ] Security audit using UEA security-audit specialist

**Verification:** `verify-change` at `full` tier.

### Phase 10 — Integration Testing

**Goal:** End-to-end validation.

- [ ] Full pipeline tests (collect → state → detect → format → deliver)
- [ ] Mock Telegram delivery verification
- [ ] State persistence across simulated runs
- [ ] Partial failure scenarios
- [ ] Rate-limit simulation
- [ ] Documentation finalization
- [ ] Mutation testing of core logic

**Verification:** `verify-change` at `full` tier + mutation testing.

---

## Architectural Invariants

1. **Read-only by default.** No GitHub writes without explicit human approval via `workflow_dispatch`.
2. **Single API exit point.** All GitHub HTTP calls go through `src/github/client.py`.
3. **Deterministic.** Same input → same output. No randomness, no LLM calls.
4. **Partial failure is normal.** One bad repo does not kill the run.
5. **State is JSON.** No external databases in runtime. Repository-backed via Actions cache.
6. **Workflows are thin.** Logic lives in Python, not YAML.
7. **Configs are YAML.** Logic lives in Python, not configs.
8. **Secrets in env vars only.** Never in CLI args, never in logs.
9. **Every collector is isolated.** Failures propagate as structured errors, not exceptions.
10. **Telegram is output only.** It receives messages. It does not control GitHub.
11. **Development plane is separate.** UEA, skills, MCPs, agents are for building gh-ops, not running it.
12. **OSS is first-class.** Dedicated workflow, dedicated scoring, dedicated state.
13. **Scoring is transparent.** Explicit weights, explicit signals, stable tie-breaking.
14. **No unnecessary dependencies.** Runtime needs: requests + pyyaml + pytest. That's it.
