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
│   │   ├── __init__.py           # Public API
│   │   ├── activity.py           # Normalized activity from Phase 3 state/events
│   │   ├── statistics.py         # Descriptive statistics over activity
│   │   └── reports.py            # Structured DeveloperReport (Phase 7 consumes)
│   │
│   ├── monitors/
│   │   ├── __init__.py
│   │   ├── repository.py         # Stars, forks, issues, PRs, activity
│   │   ├── ci.py                 # Workflow health, repeated failures
│   │   ├── release.py            # New release detection
│   │   └── endpoint.py           # Generic URL/API health checks
│   │
│   ├── notifications/
│   │   ├── __init__.py           # Public API
│   │   └── telegram.py           # Outbound Telegram Bot API adapter
│   │
│   ├── jobs/
│   │   ├── __init__.py           # Public API
│   │   ├── __main__.py           # CLI entry point: python -m src.jobs <job>
│   │   ├── pipeline.py           # Shared plumbing (config, collection, state)
│   │   └── jobs.py               # The six jobs + registration
│   │
│   └── utils/
│       ├── __init__.py
│       ├── logging.py            # Structured collector logging
│       ├── text.py               # MarkdownV2 escaping, splitting, formatting
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
│   ├── jobs/
│   │   ├── __init__.py
│   │   ├── test_jobs.py          # Job dispatch, partial failure, isolation
│   │   ├── test_dispatcher_cli.py # Exit codes, env-var job selection, entry points
│   │   └── test_architecture_boundary.py # Dependency direction
│   ├── ci/
│   │   ├── __init__.py
│   │   └── test_workflows.py     # Static workflow validation
│   ├── monitors/
│   │   ├── __init__.py
│   │   ├── test_repository.py
│   │   ├── test_ci.py
│   │   └── test_release.py
│   └── notifications/
│       ├── __init__.py
│       ├── _fakes.py             # Mocked Telegram HTTP test doubles
│       ├── test_telegram_config.py
│       ├── test_telegram_transport.py
│       ├── test_telegram_formatters.py
│       └── test_telegram_notifier.py
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

### Execution Layer

Actions is the **scheduler**; it is not the application. The entire job body is
one command:

```yaml
- name: Run daily job
  env:
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
    TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
    GHOPS_JOB: daily
  run: python -m src.jobs
```

`src/jobs/` is the thin orchestration layer that sits between Actions and the
existing runtime. Each job is a short sequence of calls into public APIs that
Phases 1–7 already expose:

```
GitHub Actions (schedule / dispatch)
        ↓
python -m src.jobs            ← reads GHOPS_JOB from the environment
        ↓
core/dispatcher.py            ← routes job name → function
        ↓
src/jobs/jobs.py              ← one function per job
        ↓
jobs/pipeline.py              ← collect → apply → diff → persist
        ↓
collectors → core/state → monitors / intelligence / developer
        ↓
notifications/telegram.py     ← outbound delivery only
```

The six jobs, one per workflow:

| Job | Collects | Then runs | Writes state |
|-----|----------|-----------|--------------|
| `daily` | repos, issues, pulls, releases, workflows | Phase 4 monitors + alert digest | yes |
| `monitoring` | workflows, security | Phase 4 monitors + alerts | yes |
| `weekly-report` | user, issues, pulls | Phase 6 activity → `DeveloperReport` | yes |
| `oss-hunt` | — | Phase 5 hunter + delivery | no |
| `security` | security | — | yes |
| `status` | — | read-only state/config report | no |

`jobs.py` and `pipeline.py` contain **no business logic**: no scoring, no
statistics, no diffing, no formatting, no HTTP. If a behaviour is not already
owned by a Phase 1–7 module, it does not belong in this layer.

### Workflow Map

| Workflow | Trigger | Job | Permissions |
|----------|---------|-----|-------------|
| `daily.yml` | `cron '0 8 * * *'` + `workflow_dispatch` | `daily` | `contents:read`, `issues:read`, `pull-requests:read`, `actions:read` |
| `monitoring.yml` | `cron '0 */2 * * *'` + `workflow_dispatch` | `monitoring` | `contents:read`, `actions:read`, `security-events:read` |
| `weekly-report.yml` | `cron '0 9 * * 1'` + `workflow_dispatch` | `weekly-report` | `contents:read`, `issues:read`, `pull-requests:read` |
| `oss-hunter.yml` | `cron '0 10 * * *'` + `workflow_dispatch` | `oss-hunt` | `contents:read`, `issues:read` |
| `security.yml` | `cron '0 6 * * *'` + `workflow_dispatch` | `security` | `contents:read`, `security-events:read` |
| `manual.yml` | `workflow_dispatch` only | any of the six | union of the above, all read-only |

All schedules are UTC. Every job has `timeout-minutes: 25`, matching
`settings.yml`. The schedules are staggered and non-overlapping so that two runs
rarely contend for the state cache.

### Workflow Design Principles

1. **Thin workflows.** All logic in Python modules.
2. **Explicit permissions.** No speculative write permissions.
3. **SHA-pinned actions.** Third-party actions are pinned to a full commit SHA
   with a version comment, so the pin is auditable.
4. **Secrets in env vars.** Never in CLI arguments, never in cache keys.
5. **No untrusted interpolation.** No `${{ }}` expression appears inside any
   `run:` step; the `manual.yml` job name is a constrained `choice` input passed
   through the environment.
6. **Timeout limits.** Every job has a timeout.
7. **Cache state.** GitHub Actions cache carries `data/state/` between runs.

Every one of these is enforced by `tests/ci/test_workflows.py`, which parses the
workflow YAML and fails the suite if a future edit weakens any of them.

### Failure Behaviour

| Condition | Result |
|-----------|--------|
| A collector fails for one repository | Job succeeds. Failed collector is recorded, other repositories still collected, previous state preserved (Phase 3 partial-failure semantics). |
| Every repository fails for a collector | That collector is marked FAILURE; state keeps the previous valid items. |
| Bad config / no credentials / unreadable state | Job fails, exit code 1, no state is saved. |
| Telegram delivery fails or raises | Job still succeeds. Delivery is the last step and state is already persisted, so a Telegram outage cannot discard collected data. |
| Unknown job name | Exit code 1. |
| No job name supplied | Exit code 1 with usage text. |

A job **must never exit 0 without having executed a job**. This is why
`python -m src.jobs` (and `python -m src.core.dispatcher`) end with
`raise SystemExit(main())` and why `main()` returns 1 rather than 0 when it has
nothing to run. An earlier draft of this document specified
`python -m src.core.dispatcher run daily`, which both named a non-existent `run`
sub-command and omitted the `__main__` guard — a green CI check that collected
nothing. See "Known Limitations" below.

### State Persistence in Actions

**GitHub Actions cache is not a mutable store.** A cache entry is immutable once
written, entries are evicted after 7 idle days, and total cache storage is a
10 GB LRU pool per repository. The design therefore treats the cache as a
**rolling snapshot**, not a database.

The rule that matters: **the restore path must never depend on a run-specific
key.** A key containing `${{ github.run_id }}` is unique to one run, so the next
run can never hit it exactly and would depend entirely on a prefix fallback
working. Instead, restore is *explicitly* prefix-based and the unique key is used
only for the save:

```yaml
- name: Restore state
  uses: actions/cache/restore@<sha>   # v6.1.0
  with:
    path: data/state
    # Unique primary key → always misses on a fresh run, so the
    # restore-keys prefix below is what actually resolves the snapshot.
    key: gh-ops-state-daily-${{ github.run_id }}-${{ github.run_attempt }}
    restore-keys: |
      gh-ops-state-

- name: Run daily job
  env:
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
    TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
    GHOPS_JOB: daily
  run: python -m src.jobs

- name: Save state
  if: success()
  uses: actions/cache/save@<sha>     # v6.1.0
  with:
    path: data/state
    # Caches are immutable, so each successful run needs a fresh key.
    key: gh-ops-state-daily-${{ github.run_id }}-${{ github.run_attempt }}
```

Restoring by the shared `gh-ops-state-` prefix means a run picks up the **most
recently created** snapshot, whichever workflow wrote it. That is correct because
all state-writing workflows share one state file, `data/state/current_state.json`.

### Concurrency

Every workflow that reads and writes the state file joins one concurrency group:

```yaml
concurrency:
  group: gh-ops-state
  cancel-in-progress: false
```

This matters because a run is a read-modify-write cycle against a single file:
restore → mutate → save. Two runs doing that simultaneously could each save a
snapshot that omits the other's update. Serializing them removes the race.

`cancel-in-progress: false` is deliberate. Cancelling a run that has already
persisted state would discard that update.

`oss-hunter.yml` is **not** in the group, because the hunter job neither reads
nor writes state; it only sends a Telegram message.

### Known Limitations of the Actions Layer

1. **Cache eviction.** If no workflow runs for 7 days, the state cache is
   evicted and the next run starts from empty state. For a system scheduled at
   least once every two hours this does not occur in normal operation, but it is
   a real consequence of using the cache as the store. GitHub also deletes caches
   on repository archive and after a period of inactivity.
2. **Cache growth.** Each successful run adds a new immutable entry. Older
   entries age out of the 10 GB LRU pool, but the pool is shared with any other
   cache use in the repository. This is the cost of not needing a durable store.
3. **Concurrency queue depth.** GitHub guarantees at most one *running* and one
   *pending* run per concurrency group; if a third run arrives it replaces the
   pending one. With staggered schedules this is not expected to bite, but a
   backlog of manual dispatches could drop a queued run.
4. **Dependabot via `GITHUB_TOKEN`.** The Dependabot alerts endpoint generally
   requires a token with explicit security-event access, which the default
   `GITHUB_TOKEN` may not be granted. When refused, the collector is recorded as
   a partial failure and previous state is preserved; the system does not report
   "no alerts" for a check it could not perform.
5. **Cache is not a lock.** The concurrency group serializes runs *within* one
   repository's Actions. A local run against the same state directory is not
   coordinated with it.
6. **No secrets are cached.** `data/state/` holds only collected GitHub data and
   ETags; tokens are environment variables only.

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

### Preventing Telegram Token Leakage

1. The bot token is read from `TELEGRAM_BOT_TOKEN` only — never from config
   files, CLI arguments, or state. A config that points at any other env var is
   rejected at load time rather than silently ignored.
2. The token is embedded in the API request URL, so raw request URLs are never
   logged and never attached to errors or error context.
3. Raw third-party exception text is never interpolated into our error messages,
   because those strings can embed the URL. Only the exception type name is used.
4. `logging.py` additionally redacts Telegram bot token patterns as
   defence-in-depth.
5. `TelegramError` carries no token, no URL, and no raw exception text.

### Preventing Telegram Inbound Control

1. Outbound only. There is no `getUpdates` polling and no webhook registration.
2. No command parsing: inbound messages are never read.
3. The API base URL is a module constant, so delivery cannot be redirected to an
   arbitrary host, and HTTPS is enforced by that constant.

### Preventing Workflow Injection

1. **No expression interpolation in `run:` steps.** Not a single `${{ }}`
   appears in any shell command in any workflow. This is asserted by test.
2. **Job selection travels through the environment.** The dispatcher reads
   `GHOPS_JOB`, so the shell never parses the value.
3. **`manual.yml` constrains its input.** The job name is a `choice` input, so
   GitHub limits it to the six enumerated values before it reaches the runner.
4. **No event payload is referenced at all.** No workflow mentions
   `github.event.*`; there is no payload this pipeline needs.
5. No `eval()`, no `exec()`, no shell string interpolation of payloads.
6. Python entry points use env vars, not parsed event JSON.

### Preventing Supply-Chain Compromise

1. **Every third-party action is pinned to a full 40-character commit SHA.**
   Mutable tags such as `@v4` can be repointed at new code after review.
2. **Every pin carries a version comment** (`# v6.3.0`) so the pin stays
   human-auditable, and the approved SHAs are asserted by test.
3. Only three actions are used: `actions/checkout`, `actions/setup-python`, and
   `actions/cache`. Nothing else is introduced.

### Preventing Unsafe Fork Execution

1. No `pull_request_target` workflows with fork code checkout.
2. No workflow triggers on `pull_request` at all: nothing in this pipeline runs
   on untrusted code.
3. No execution of code from untrusted sources.
4. `manual.yml` runs only on `workflow_dispatch` from maintainers.

### Workflow Permission Audit

| Workflow | `contents` | `issues` | `pull-requests` | `actions` | `security-events` |
|----------|-----------|----------|-----------------|-----------|-------------------|
| `daily.yml` | read | read | read | read | — |
| `monitoring.yml` | read | — | — | read | read |
| `weekly-report.yml` | read | read | read | — | — |
| `oss-hunter.yml` | read | read | — | — | — |
| `security.yml` | read | — | — | — | read |
| `manual.yml` | read | read | read | read | read |

No workflow has `write` permissions in V1, and every workflow declares its
permissions explicitly rather than relying on repository defaults.

Note the spelling: the scope key is `security-events` with a hyphen. The
underscore form `security_events` is not a valid key and would be silently
ignored, leaving the token without the access the collector needs.

---

## J. State Model

### State Strategy

**Runtime state is NOT committed to Git.**

Instead:
- GitHub Actions cache persists state between runs (see §H,
  "State Persistence in Actions", for the rolling-key design and its limits)
- `data/state/` is gitignored
- State is restored at workflow start, saved at workflow end

### State Files

Phase 3 stores one consolidated state document rather than one file per
resource type. Resource types are keys inside it:

```
data/state/
├── current_state.json      # Consolidated state: resources, etags, update_log
└── etags.json              # ETag cache for conditional requests

data/history/
└── <timestamped>.json      # History entries (written by save_history)
```

`current_state.json` holds:

```json
{
  "schema_version": 2,
  "last_updated": "2026-09-21T08:00:00+00:00",
  "resources": {
    "repos":     { "owner/name": { "...": "..." } },
    "issues":    { "owner/name#123": { "...": "..." } },
    "pulls":     { "owner/name#45": { "...": "..." } },
    "releases":  { "owner/name@v1.2.3": { "...": "..." } },
    "workflows": { "owner/name/run/123": { "...": "..." } },
    "security":  { "owner/name/dependabot/9": { "...": "..." } },
    "user":      { "login": { "...": "..." } }
  },
  "etags": { "collector": "etag-value" },
  "update_log": {
    "repos": { "status": "success", "observed_at": "...", "item_count": 12 }
  }
}
```

Each job collects only the resource types it needs, and `apply_snapshot`
**preserves resource types that are not in the snapshot**. That is what makes it
safe for `security.yml` to run against state that `daily.yml` last wrote.

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

src/developer/activity.py
    → (uses) core/events.py, core/models.py, utils/time.py
    → (NEVER) github/client.py, requests, notifications/*

src/developer/statistics.py
    → (uses) developer/activity.py (pure aggregation, no I/O)

src/developer/reports.py
    → (uses) developer/activity.py, developer/statistics.py
    → (emits) DeveloperReport for Phase 7 notification formatting

src/notifications/telegram.py
    → (uses) core/errors.py, core/monitor.py, core/rate_limit.py,
             developer/reports.py, utils/logging.py, utils/text.py, requests
    → (calls) Telegram Bot API — https://api.telegram.org (fixed module constant)
    → (never) github/client.py, or any GitHub HTTP call of any kind
    → (never) imports src/monitors/* or src/intelligence/*: monitor and OSS
              results are consumed structurally, precisely so that the delivery
              layer never pulls in the GitHub client
    → no Phase 1–6 module imports notifications (no reverse dependency)

src/jobs/pipeline.py
    → (uses) core/config.py, core/state.py, core/events.py, core/models.py,
             github/client.py, github/collectors/*, utils/logging.py
    → composes the public APIs; contains no intelligence or scoring of its own

src/jobs/jobs.py
    → (uses) jobs/pipeline.py, monitors/*, intelligence/oss/hunter.py,
             developer/activity.py, developer/reports.py, notifications/telegram.py
    → (never) requests, urllib, httpx: the job layer composes modules, it does
              not perform I/O itself
    → (never) LLM, agent, MCP, or UEA runtime dependencies
    → (never) eval/exec/subprocess

src/core/dispatcher.py
    → (used by) src/jobs/*
    → imports src.jobs.* only inside `if __name__ == "__main__":`, so importing
      the dispatcher as a library does not load the job layer
    → no Phase 1–7 library module imports src.jobs at module scope; this is
      enforced by tests/jobs/test_architecture_boundary.py
```

---

## M. Implementation Phases

### Phase 0 — Architecture + Migration (COMPLETE)

- [x] Inspect existing project
- [x] Copy to gh-ops/
- [x] Initialize Git
- [x] Write .gitignore
- [x] Produce ARCHITECTURE.md v1
- [x] Inspect UEA repository
- [x] Classify UEA capabilities
- [x] Identify architecture corrections
- [x] Produce ARCHITECTURE.md v2 (this document)
- [x] Delete old skeleton files (config.yml, data/*.json, src/collectors/, src/hunters/, src/telegram/)
- [x] Initial commit (e2286eb)

### Phase 1 — Core Runtime (COMPLETE)

**Goal:** Foundation that everything else depends on.

**Status:** Complete. 112/112 tests passing.

- [x] `src/core/config.py` — YAML config loading + validation
- [x] `src/core/state.py` — JSON state persistence (atomic writes)
- [x] `src/core/errors.py` — Structured error types
- [x] `src/core/rate_limit.py` — Rate-limit header parsing + backoff
- [x] `src/core/events.py` — Event detection (diff state)
- [x] `src/core/dispatcher.py` — Lightweight job dispatcher
- [x] `src/utils/logging.py` — Structured log output
- [x] `src/utils/text.py` — Text formatting utilities
- [x] `src/utils/time.py` — Timezone/date helpers
- [x] `config/settings.yml` — Global settings
- [x] Tests for all core modules (112 tests)

**Verification:** `verify-change` at `standard` tier.

### Phase 2 — GitHub API Client (COMPLETE)

**Goal:** Central, secure, resilient API layer.

**Status:** Complete. 185/185 tests passing. Security audit: 0 FAIL / 0 WARN.

#### Implementation Summary

- [x] `src/github/auth.py` — Token resolution from `GITHUB_TOKEN` / `GH_TOKEN` env vars
- [x] `src/github/client.py` — Central HTTP client with retry, backoff, ETag, timeout, GET-only enforcement
- [x] `src/github/models.py` — 8 frozen dataclasses for API responses
- [x] `src/github/collectors/repos.py` — Repository metadata collector
- [x] `src/github/collectors/issues.py` — Issue collector + search
- [x] `src/github/collectors/pulls.py` — PR collector
- [x] `src/github/collectors/releases.py` — Release collector
- [x] `src/github/collectors/workflows.py` — Workflow run collector
- [x] `src/github/collectors/security.py` — Dependabot/code-scanning alert collector
- [x] `src/github/collectors/user.py` — Authenticated user collector
- [x] `config/repositories.yml` — Monitored repository list (empty, ready for config)
- [x] Tests with mock HTTP responses (73 Phase 2 tests)

#### auth.py Responsibilities

- Resolves token from `GITHUB_TOKEN` (preferred) or `GH_TOKEN` (fallback)
- `AuthConfig` frozen dataclass with `token`, `token_source`, `has_token`, `auth_headers()`, `to_headers()`
- Safe `__repr__` — shows only `token_source`, never the token value
- `validate_token_format()` — checks prefix/length, warns on unknown formats
- Raises `GhOpsError` (CRITICAL) if no token found
- Token held only in memory, never serialized or logged

#### client.py Responsibilities

- **Single HTTP exit point** — only file that imports `requests`
- GET-only enforcement via `WriteDeniedError` in `execute()`
- `get()` — single-page GET request
- `get_paginated()` — multi-page GET with Link header following
- `_do_request()` — internal method with retry logic (bypasses GET-only check; pagination is inherently GET)
- Retry: exponential backoff `min(1.0 * 2^attempt + jitter, 30.0)`, max 3 retries
- Rate limit tracking via `RateLimitTracker.update_from_headers()`
- ETag/304: returns `PaginatedResult(not_modified=True)` on 304
- Error mapping: 401→`API_AUTH_FAILED`, 403→`API_FORBIDDEN`, 404→`API_NOT_FOUND`, 429→`API_RATE_LIMITED`, 5xx→`INTERNAL_ERROR`
- Context manager support (`with GitHubClient() as client:`)

#### models.py Normalized Models

8 frozen dataclasses with `from_api()` classmethods and `to_dict()` serialization:

| Model | Key Fields |
|-------|------------|
| `Repository` | `id`, `full_name`, `owner`, `name`, `stars`, `forks`, `language`, `topics`, `license_key` |
| `Issue` | `id`, `number`, `title`, `state`, `labels`, `user`, `comments`, `repo_full_name` |
| `PullRequest` | `id`, `number`, `title`, `state`, `head_branch`, `base_branch`, `merged` |
| `Release` | `id`, `tag_name`, `name`, `author`, `assets_count` |
| `WorkflowRun` | `id`, `name`, `status`, `conclusion`, `is_success`, `is_failure` |
| `SecurityAlert` | `id`, `package_name`, `severity`, `summary`, `state` |
| `User` | `id`, `login`, `name`, `public_repos`, `followers` |
| `PaginatedResult` | `items`, `total_count`, `has_next`, `etag`, `not_modified` |

#### Collector Boundaries

- **No `requests` import** — collectors use only `GitHubClient` and models
- **No credential access** — collectors never see the token
- **No HTTP logic** — pagination, retries, rate limits are in `client.py`
- **Thin wrappers** — each function calls `client.get()` or `client.get_paginated()` and returns models
- **Deterministic parsing** — `Model.from_api()` handles all field extraction

#### Pagination / ETag / Rate-Limit Behavior

- Pagination follows `Link` header `rel="next"` references, bounded by `max_pages`
- ETag passed via `If-None-Match` header; 304 returns `not_modified=True`
- Rate limit headers (`X-RateLimit-Remaining/Limit/Reset`) update tracker on every response
- `Retry-After` header honored on 429; integer and HTTP-date formats supported
- `on_rate_limit` callback invoked when rate limit state changes

#### Authentication Boundary

- Token resolved at `GitHubClient.__init__()` time
- Token injected into `requests.Session` headers once
- Collectors receive `GitHubClient` instance, not the token
- No collector can access the raw token
- `auth_headers()` returns `Authorization: token {token}` header dict

#### Security Invariants

1. **Single HTTP exit point** — `client.py` is the only file importing `requests`
2. **GET-only enforcement** — `execute()` raises `WriteDeniedError` for non-GET
3. **No credential logging** — `AuthConfig.__repr__` shows only source variable name
4. **No credential serialization** — token held in memory only
5. **No collector HTTP access** — collectors call client methods, not `requests`
6. **Structured errors** — no raw exception propagation, all errors typed
7. **SSRF protection** — base URL hardcoded to `https://api.github.com`, collectors use relative endpoints

#### Test Results

- Phase 2 tests: 73 (auth: 15, client: 16, models: 19, collectors: 23)
- Phase 1 tests: 112
- **Total: 185/185 passing**
- Security audit: 0 FAIL / 0 WARN

**Verification:** `verify-change` at `strict` tier (shared code). Security audit passed.

### Phase 3 — State & Event Engine (COMPLETE)

**Goal:** Detect changes between collection runs.

**Status:** Complete. 291/291 tests passing. Security audit: 0 FAIL / 0 WARN.

#### Implementation Summary

- [x] `src/core/models.py` — State models: CollectorResult, CollectorStatus, Snapshot, CurrentState, StateValidation, FieldChange, ResourceEvent, EventType, HistoryEntry
- [x] `src/core/state.py` — Extended with schema versioning, validation, atomic persistence, CurrentState persistence, apply_snapshot, ETag persistence, history
- [x] `src/core/events.py` — Extended with deterministic diff engine, field-level change tracking, NEW/CHANGED/REMOVED/UNCHANGED semantics
- [x] `src/core/errors.py` — New error codes: STATE_UNSUPPORTED_VERSION, STATE_VALIDATION_FAILED
- [x] Tests for all Phase 3 additions (106 new tests)

#### State Models (`src/core/models.py`)

Four distinct concepts, kept SEPARATE:

| Concept | Description |
|---------|-------------|
| **Snapshot** | What was observed during a single collection run |
| **CurrentState** | The latest successfully accepted observation per resource type |
| **Event** | The deterministic transition between observations |
| **HistoryEntry** | What has previously been observed/emitted |

Additional models:
- `CollectorResult` — Result of a single collector execution (SUCCESS/FAILURE/NOT_MODIFIED)
- `CollectorStatus` — Outcome enum for collector runs
- `StateValidation` — Classification of state file condition (MISSING/VALID/CORRUPT/UNSUPPORTED_VERSION)
- `FieldChange` — A single field-level change between two resource states
- `EventType` — Event types: NEW, CHANGED, REMOVED, UNCHANGED

#### Schema Versioning

- `STATE_SCHEMA_VERSION = 1` — current version
- `validate_state()` — classifies state as MISSING/VALID/CORRUPT/UNSUPPORTED_VERSION
- `load_validated()` — returns (data, validation) tuple
- Version check: `schema_version > STATE_SCHEMA_VERSION` → UNSUPPORTED_VERSION

#### Deterministic Diff Engine

`diff_resources()` produces NEW/CHANGED/REMOVED/UNCHANGED events:
- Events sorted by resource_id (deterministic ordering)
- Field changes sorted by field name
- Ignored fields: `updated_at`, `pushed_at`, `last_modified`, `etag`, `node_id`, `url`, and any `*_url` field
- Normalization: lists sorted by string representation for determinism
- Same input always produces same output (tested with 10 iterations)

#### Field-Level Change Tracking

For CHANGED resources, captures:
```python
FieldChange(field="state", before="open", after="closed")
```
- Only meaningful fields compared (volatile/API-template fields excluded)
- Changes sorted by field name for deterministic ordering
- Full previous/current state preserved for audit

#### Partial Failure Semantics

`apply_snapshot()` handles three collector outcomes:
- **SUCCESS** — overwrite that collector's items in current state
- **NOT_MODIFIED** — preserve existing items, update observed_at
- **FAILURE** — preserve previous items, record the failure

Critical invariant: A failed collector MUST NOT cause valid previous state to disappear.

#### ETag Persistence

- `load_etags()` — load persisted ETags from current state
- `save_etag()` — save a single ETag
- Flow: Previous ETag → Phase 2 client → If-None-Match → 200/304 → Phase 3 state layer → Persist

#### Atomic Persistence

`save_atomic()` follows: temp file → write → flush → fsync → atomic replace
- `os.fsync()` ensures data reaches disk
- `os.replace()` is atomic on POSIX and Windows (NTFS)
- Temp files cleaned up on failure

#### Corruption Handling

| Status | Behavior |
|--------|----------|
| MISSING | Return empty state (safe default) |
| VALID | Accept and return data |
| CORRUPT | Raise StateError (do not silently use empty state) |
| UNSUPPORTED_VERSION | Raise StateError (do not proceed with unknown schema) |

#### History

- `save_history()` — append-only persistence with atomic writes
- `load_history()` — load recent entries, sorted by ISO timestamp
- Retention policy: DEFERRED (clean interface for future extension)
- History is SEPARATE from current state

#### Security

- Path traversal prevention: `_safe_filename()` validates filenames
- No secrets in state files (data model is inherently safe)
- Atomic writes prevent partial corruption
- JSON-only deserialization (no eval/pickle/exec)

#### Test Results

Cumulative suite totals by subsystem. These are reproducible with
`pytest --collect-only` and sum exactly to the suite total; per-phase cumulative
figures are in the README status table.

- `tests/core/` — 182 (config, state, events, errors, models, rate limit, dispatcher)
- `tests/github/` — 73 (client, auth, models, collectors)
- `tests/monitors/` — 97 (includes 5 property tests)
- `tests/utils/` — 70 (text, time, .env loading)
- `tests/intelligence/` — 106 (models, queries, filters, scorer, dedup, hunter)
- `tests/developer/` — 75 (activity, statistics, reports)
- `tests/notifications/` — 163 (telegram config, transport, formatters, notifier)
- `tests/jobs/` — 75 (job dispatch, CLI entry points, architecture boundary)
- `tests/ci/` — 213 (static workflow validation)
- **Total: 1054 passing**
- Security audit: 0 FAIL / 0 WARN

`ruff` and `mypy` are declared development dependencies but were not installed in
the working environment, so those two checks were not run and are not claimed to
have passed.

**Verification:** `verify-change` at `standard` tier.

### Phase 4 — Repository Monitoring ✅ COMPLETE

**Goal:** Track repository health and changes.

Implemented:
- [x] `src/core/monitor.py` — MonitorResult model, MonitorStatus, MonitorCategory
- [x] `src/monitors/__init__.py` — Monitor registry and evaluator
- [x] `src/monitors/repository.py` — Repository metadata monitoring (stars, forks, issues, archival)
- [x] `src/monitors/ci.py` — CI/workflow failure/recovery detection
- [x] `src/monitors/release.py` — Release event monitoring (new, prerelease, draft)
- [x] `src/monitors/endpoint.py` — HTTP endpoint health checks with full SSRF protection
- [x] `config/monitoring.yml` — Monitor configuration (enabled/disabled, thresholds)
- [x] Tests: 97 new (models, registry, repository, CI, release, endpoint security, properties)
- [x] Security analysis: SSRF, loopback, metadata, DNS rebinding documented
- [x] Property-based testing: MonitorResult, classify_changes_as_alert

**Total: 388/388 passing**

**Verification:** `verify-change` at `standard` tier.

### Phase 5 — OSS Intelligence ✅ COMPLETE

**Goal:** Discover contribution opportunities.

Implemented:
- [x] `src/intelligence/oss/models.py` — Opportunity frozen dataclass, ScoreBreakdown, from_search_result parser
- [x] `src/intelligence/oss/queries.py` — Multi-query strategy with 7 default categories, deduplication, config/custom query support
- [x] `src/intelligence/oss/filters.py` — 9 filter rules (excluded labels, bot authors, locked, draft, min stars, max age, excluded repos, required labels, required language)
- [x] `src/intelligence/oss/scorer.py` — 7 transparent scoring signals with configurable weights, decay functions, 5 tie-breaking methods
- [x] `src/intelligence/oss/dedup.py` — Identity-based dedup `(repo, issue_number)`, merges source_queries provenance, idempotent
- [x] `src/intelligence/oss/hunter.py` — Orchestrator: queries → search → enrich → filter → score → dedup → sort → top-N
- [x] `src/intelligence/oss/__init__.py` — Public API
- [x] `config/oss_hunter.yml` — Updated with dedup, categories, languages, custom_queries config
- [x] Tests: 106 total (models, queries, filters, scorer, dedup, hunter)
- [x] Security audit: 0 FAIL / 0 WARN (no write ops, no LLM, no dangerous patterns)

**Total: 106 Phase 5 tests** (part of the suite total)

**Verification:** `verify-change` at `standard` tier.

### Phase 6 — Developer Intelligence ✅ COMPLETE

**Goal:** Personal activity reports.

Implemented:
- [x] `src/developer/activity.py` — Activity normalization: ActivityRecord, ActivityType, DataQuality, extract_activity, filter_activity, deduplicate_activity, summarize_activity
- [x] `src/developer/statistics.py` — Descriptive statistics: ActivityCounts, RepoActivity, PeriodActivity, compute_counts, compute_by_repository, compute_by_period, compute_active_repositories, compute_active_days, compute_date_range, summarize_data_quality
- [x] `src/developer/reports.py` — Structured reports: DeveloperReport, ReportPeriod (last N days, this week, this month), generate_report, format_summary
- [x] `src/developer/__init__.py` — Public API
- [x] Tests: 75 total (activity extraction, statistics, reports, filtering, dedup, data quality)

Key design decisions:
- Activity timestamps from resource fields (created_at, closed_at, merged_at), NOT collection time
- No LLM, no scoring, no personality inference — descriptive only
- Data quality tracked per collector — failed collector ≠ zero activity
- Closed issues/merged PRs detected on NEW events (first observation)
- All models frozen dataclasses, deterministic serialization

#### Activity Model

`ActivityRecord` (frozen dataclass) carries `activity_type`, `timestamp`,
`repository`, `item_id`, `title`, `state`, `url`, `meta`.

`item_id` is the deterministic identity of the thing that happened:
`owner/repo#123` for issues/PRs, the tag name for releases, `workflow/{id}`
for workflow runs.

| ActivityType | Timestamp source | Emitted when |
|--------------|------------------|--------------|
| `issue_created` | `created_at` | NEW issue event |
| `issue_closed` | `closed_at` | `state` CHANGED to `closed`, or already closed on first observation |
| `issue_commented` | `updated_at` | `comments` count increased |
| `pr_opened` | `created_at` | NEW pull event |
| `pr_merged` | `merged_at` | `merged` CHANGED to true, or already merged on first observation |
| `pr_closed` | `closed_at` | `state` CHANGED to `closed` with no merge evidence |
| `release_published` | `published_at` | NEW release event |
| `workflow_run` | `run_started_at` (fallback `created_at`) | NEW or CHANGED workflow event |

No activity is fabricated: a record is emitted only when the corresponding
resource field exists and parses to a UTC datetime.

#### Statistics

`statistics.py` is pure aggregation over `ActivityRecord` lists — no I/O.

- `ActivityCounts` — per-type counts plus `total`
- `compute_by_repository()` — one `RepoActivity` per repository (counts and distinct item IDs)
- `compute_by_period()` — fixed-width `PeriodActivity` buckets
- `compute_active_days()`, `compute_active_repositories()`, `compute_date_range()`
- `summarize_data_quality()` — collector completeness percentage

#### Reports and Reporting Periods

`generate_report()` is the entry point: deduplicate → bound to period →
compute statistics → attach data-quality context.

`ReportPeriod` presets: `last(days)`, `this_week()` (Monday 00:00 UTC),
`this_month()` (1st 00:00 UTC). Report bounds are `since` inclusive and
`until` inclusive; `by_period` buckets are `[start, end)`.

`DeveloperReport.to_dict()` is the deterministic serialized output shape.

#### Data-Quality Semantics

Every collector entry in `CurrentState.update_log` becomes a `DataQuality`
record.

| Collector status | `is_complete` | Effect on the report |
|------------------|---------------|----------------------|
| success | `True` | counts treated as complete |
| not_modified | `False` | prior items preserved, flagged |
| failure | `False` (+ `error`) | **never** reported as zero activity |

`DeveloperReport.is_complete` is true only when `completeness_pct == 100.0`.
`format_summary()` surfaces an explicit completeness warning below 100%.

#### State / Event Integration

```
CurrentState (resources)                 Phase 3 events (ResourceEvent)
        │                                         │
        └── diff_resources(prev={}, cur=…) ───────┤
                                                  ▼
                                    extract_activity()
                                                  ▼
                            (ActivityRecord[], DataQuality[])
```

Current state supplies the snapshot of what exists; Phase 3 events supply
transitions observed between runs (for example open → closed). Both paths run
through the same extractors, and `deduplicate_activity()` collapses anything
produced twice (key: activity_type, item_id, timestamp).

#### Determinism

- Frozen dataclasses; no mutable global state and no randomness in aggregation
- Records sorted by timestamp; `by_repository` sorted by total descending then
  name; `by_period` chronological; `active_repositories` sorted
- Identical input yields identical counts, `by_repository`, and record ordering
- Two values are generation-time by construction, and therefore differ between
  runs: `generated_at`, and the trailing `by_period` bucket's `period_end`, which
  is clamped to the reference time. Passing an explicit `reference_time` to
  `compute_by_period()` makes bucket boundaries fully reproducible.

#### Limitations

- Coverage is bounded by what Phase 2/3 collected; Phase 6 performs no backfill
- Workflow runs are not attributed to a developer (the normalized record has no
  user field), so per-user reports still include collected workflow runs
- Releases and workflow runs carry no repository name in the normalized record
  and therefore group under `(unknown)` in per-repository breakdowns
- A PR already closed-without-merge at first observation yields only
  `pr_opened`, because the close transition was never observed
- No trend or persistence layer: a report describes one period from one snapshot
- No evaluation of any kind — no productivity, quality, or behavioural judgement

**Total: 569/569 passing**

**Verification:** `verify-change` at `standard` tier.

### Phase 7 — Telegram Integration ✅ COMPLETE

**Goal:** Outbound notification delivery.

Implemented:
- [x] `src/notifications/telegram.py` — Config + token resolution, formatters, `TelegramTransport`, `TelegramNotifier`
- [x] `src/notifications/__init__.py` — Public API
- [x] MarkdownV2 escaping and message splitting, reusing `utils/text.py`
- [x] Monitor alert, OSS opportunity, and developer report formatting
- [x] Bounded retries, 429 / `Retry-After`, structured errors, per-chat failure isolation
- [x] `config/telegram.yml` — enabled flag, chat IDs with topic subscriptions, message limits, timeout
- [x] Tests: 165 (config, transport, formatters, notifier, failure isolation, secret safety)
- [ ] Daily / weekly digest *scheduling* — deferred to Phase 8 (workflow layer)

#### Delivery Architecture

```
structured results (MonitorResult / Opportunity / DeveloperReport)
        ↓
formatters          — deterministic, escaped MarkdownV2, split to fit
        ↓
TelegramTransport   — finite timeout, bounded retries, 429 / Retry-After
        ↓
Telegram Bot API    — https://api.telegram.org (fixed module constant)
```

`src/notifications/telegram.py` is the single Telegram HTTP exit point, in the
same spirit as `github/client.py` being the single GitHub exit point. It does
not reuse the GitHub client: that client is GET-only and GitHub-specific, while
Telegram delivery requires POST.

Telegram is a THIN DELIVERY LAYER. It does not query GitHub, contains no
intelligence or scoring, does not mutate state, performs no GitHub writes,
accepts no inbound control, and calls no LLM, agent, MCP, or UEA runtime.

#### Topics and Routing

| Topic | Produced by |
|-------|-------------|
| `alerts` | `notify_monitor_alerts` — ALERT and ERROR monitor results only |
| `oss_opportunities` | `notify_oss_opportunities` — ranked opportunities |
| `developer_report` | `notify_developer_report` — a Phase 6 `DeveloperReport` |

A chat target with no `topics` receives every topic. Chats are served in
configured order.

#### Configuration and Token Handling

- The bot token comes from `TELEGRAM_BOT_TOKEN` ONLY. `bot_token_ref` set to any
  other value raises `ConfigError` instead of being silently ignored.
- Telegram config contains no secrets and its serialization has no token field.
- `max_length` is clamped to Telegram's 4096-character limit; non-positive
  timeouts, negative retry counts, and malformed `chat_ids` raise `ConfigError`.
- Delivery is a safe no-op when disabled, when no chat matches the topic, or
  when no token is configured — none of which raise or perform HTTP.

#### Transport Behavior

| Condition | Behavior |
|-----------|----------|
| 2xx with `ok: true` | parsed body returned |
| 2xx with `ok: false` | `TELEGRAM_DELIVERY_FAILED` |
| 400 / 404 | `TELEGRAM_DELIVERY_FAILED`, not retried |
| 401 / 403 | `TELEGRAM_DELIVERY_FAILED`, `HIGH` severity, not retried |
| 429 | retried after `Retry-After` (header, else body `parameters.retry_after`), clamped to `max_retry_delay`; exhausted to `TELEGRAM_RATE_LIMITED` |
| 5xx | retried with exponential backoff, then `TELEGRAM_DELIVERY_FAILED` |
| timeout / network error | retried, then `TELEGRAM_DELIVERY_FAILED` |

Attempts are bounded at `retry_attempts + 1`, each with a finite timeout.
Backoff reuses `core/rate_limit.py` (`calculate_backoff`, `parse_retry_after`).

#### Formatting, Escaping, and Splitting

- Every character of text placed in a message is escaped with
  `escape_markdown_v2` — including the title. The bold title is the only
  unescaped markup, so a hostile repository name or issue title cannot open or
  close an entity.
- Blocks are escaped independently and then greedily packed, so packing can
  never split an escape sequence. A block that alone exceeds the limit is split
  on its escaped form, and a backslash left dangling at a cut is carried onto the
  next chunk.
- Every returned message is at most `max_length` characters, even when escaping
  doubles the text length.
- Output depends only on the input, never on the clock: the same input always
  produces byte-identical messages.

#### Failure Isolation

Delivery never raises for delivery or configuration problems. Failures are
reported through `NotificationResult` (`sent`, `skipped`, `skip_reason`,
`attempts`, `errors`, `ok`) so a failed notification cannot break a collection
run. A failure for one chat stops further messages to that chat but does not
prevent delivery to the others.

#### Limitations

- No inbound handling: no commands, no polling, no webhooks.
- URLs are transmitted as escaped plain text rather than inline links, keeping
  the escaping surface to a single audited utility.
- No parse-mode fallback: unescaped-markup problems would surface as a 400, but
  the escaping rules above are designed to make that unreachable.
- Digest scheduling and composition (daily/weekly jobs) belongs to Phase 8; this
  phase only provides formatting and delivery.

**Total: 165 Phase 7 tests** (part of the suite total)

**Verification:** `verify-change` at `standard` tier.

### Phase 8 — GitHub Actions Workflows ✅ COMPLETE

**Goal:** Production automation.

Implemented:
- [x] `.github/workflows/daily.yml` — `cron '0 8 * * *'` + dispatch
- [x] `.github/workflows/monitoring.yml` — `cron '0 */2 * * *'` + dispatch
- [x] `.github/workflows/weekly-report.yml` — `cron '0 9 * * 1'` + dispatch
- [x] `.github/workflows/oss-hunter.yml` — `cron '0 10 * * *'` + dispatch
- [x] `.github/workflows/security.yml` — `cron '0 6 * * *'` + dispatch
- [x] `.github/workflows/manual.yml` — `workflow_dispatch` only, `choice` job input
- [x] `src/jobs/pipeline.py` — shared plumbing: config, collection, state, diff
- [x] `src/jobs/jobs.py` — the six jobs + `register_all_jobs`
- [x] `src/jobs/__main__.py` — `python -m src.jobs <job>`
- [x] `src/core/dispatcher.py` — module guard fixed: it no longer exits 0 without running
- [x] `src/core/__main__.py` — same, plus job registration at the CLI boundary
- [x] Workflow permission lockdown — every workflow declares explicit read-only scopes
- [x] Pinned action versions — SHA-pinned with version comments
- [x] State caching strategy — shared restore prefix, fresh immutable save key
- [x] Secret handling audit — `GITHUB_TOKEN` / `TELEGRAM_BOT_TOKEN` via Actions Secrets, env only
- [x] Tests: 288 (job dispatch, CLI exit codes, architecture boundary, workflow validation)

#### Execution Model

```
GitHub Actions (cron / workflow_dispatch)
        ↓  python -m src.jobs      (GHOPS_JOB from the environment)
core/dispatcher.py                   ← routes job name → function
        ↓
src/jobs/jobs.py                     ← six thin jobs
        ↓
src/jobs/pipeline.py                 ← collect → apply_snapshot → diff_resources → persist
        ↓
collectors → core/state → monitors / intelligence / developer
        ↓
notifications/telegram.py            ← outbound only
        ↓
run summaries (successful daily/monitoring runs; configured repositories only)
```

No business logic lives in the job layer. It composes public APIs that Phases 1–7
already own; anything else would duplicate an existing layer.

#### Jobs

| Job | Collects | Then runs | Writes state |
|-----|----------|-----------|--------------|
| `daily` | repos, issues, pulls, releases, workflows | Phase 4 monitors + alert digest | yes |
| `monitoring` | workflows, security | Phase 4 monitors + alerts | yes |
| `weekly-report` | user, issues, pulls | Phase 6 activity → `DeveloperReport` | yes |
| `oss-hunt` | — | Phase 5 hunter + delivery | no |
| `security` | security | — | yes |
| `status` | — | read-only state/config report | no |

#### Failure Semantics

A collector failure is **partial, not fatal**: the failed collector is recorded,
the other repositories are still collected, and Phase 3 preserves the previous
valid items. Only config, credential, and state failures are fatal. Delivery
failures never fail a job — state is already persisted by then, so a Telegram
outage cannot discard collected data.

An unknown or missing job name exits non-zero. A job invocation can never exit 0
without having executed a job.

#### State Persistence

The Actions cache is a **rolling snapshot, not a mutable store**. Restore is
prefix-based (`restore-keys: gh-ops-state-`), so it resolves the most recently
created snapshot regardless of which workflow wrote it; save uses a fresh
immutable key (`gh-ops-state-<job>-${{ github.run_id }}-${{ github.run_attempt }}`)
because cache entries cannot be overwritten. All state-writing workflows share a
single `concurrency: gh-ops-state` group with `cancel-in-progress: false`, which
serializes the read-modify-write cycle against the one shared state file.

Documented limitations: 7-day idle eviction, cache growth inside a 10 GB LRU
pool, concurrency queue depth, and Dependabot alerts potentially being refused by
`GITHUB_TOKEN`. See §H, "Known Limitations of the Actions Layer".

**Total: 1054/1054 passing**

**Verification:** static workflow validation, architecture boundary tests, and
subprocess exit-code tests. `ruff` and `mypy` are declared dev dependencies but
were not installed in the working environment, so those two checks were not run
and are not claimed to have passed.

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
6. **Workflows are thin.** Logic lives in Python, not YAML. A workflow's only
   substantive step is `python -m src.jobs`, and no expression is interpolated
   into any `run:` step. Actions schedules; `src/jobs/` orchestrates by
   composing Phase 1–7 public APIs, adding no intelligence of its own.
7. **Configs are YAML.** Logic lives in Python, not configs.
8. **Secrets in env vars only.** Never in CLI args, never in logs.
9. **Every collector is isolated.** Failures propagate as structured errors, not exceptions.
10. **Telegram is output only.** It receives messages. It does not control GitHub.
11. **Development plane is separate.** UEA, skills, MCPs, agents are for building gh-ops, not running it.
12. **OSS is first-class.** Dedicated workflow, dedicated scoring, dedicated state.
13. **Scoring is transparent.** Explicit weights, explicit signals, stable tie-breaking.
14. **No unnecessary dependencies.** Runtime needs: requests + pyyaml + pytest. That's it.
15. **Dependency direction is one-way.** `core/github → state/events → monitors /
    intelligence / developer → notifications`, with `src/jobs/` composing them
    from above. No lower layer imports `src.jobs` except inside a `__main__`
    guard, which is enforced by test.
