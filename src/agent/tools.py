"""Typed tool registry: the agent's only window onto GitHub.

Every tool declares a JSON-schema for its arguments. Arguments are validated
(types, enums, bounds, patterns, unknown keys rejected) BEFORE any API call,
so LLM output can never inject paths or hosts into a request. Results are
compact, interpreted dicts — never raw API dumps — and are cached with a TTL.

All tools here are read-only, except ``propose_action``, which only *queues*
a write for explicit operator confirmation (see actions.py).
"""
from __future__ import annotations

import base64
import json
import re
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from src.github.writer import REF_RE, REPO_RE
from src.utils.logging import get_logger

logger = get_logger("agent.tools")

#: Max serialized size of one tool result handed to the model.
MAX_RESULT_CHARS = 4500


class ToolError(Exception):
    """Invalid arguments or an unavailable capability; shown to the model."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[ToolContext, dict[str, Any]], Any]
    cache_ttl: float = 120.0
    progress: str = "Checking GitHub"

    def spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolContext:
    """Per-turn dependencies. ``client`` is a GET-only GitHubClient."""

    client: Any
    login: str = ""
    actions: Any = None  # ActionQueue
    conversation_key: str = ""
    user_id: int = 0
    surfaced: set[str] = field(default_factory=set)  # OSS issues already shown
    extras: dict[str, Any] = field(default_factory=dict)


# ── argument validation ──────────────────────────────────────────


def validate_args(schema: dict[str, Any], args: Any) -> dict[str, Any]:
    """Validate against the small JSON-schema subset the tools use."""
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ToolError("arguments must be an object")
    props: dict[str, Any] = schema.get("properties", {})
    unknown = set(args) - set(props)
    if unknown:
        raise ToolError(f"unknown argument(s): {', '.join(sorted(unknown))}")
    out: dict[str, Any] = {}
    for name, spec in props.items():
        if name not in args or args[name] is None:
            if name in schema.get("required", []):
                raise ToolError(f"missing required argument: {name}")
            if "default" in spec:
                out[name] = spec["default"]
            continue
        value = args[name]
        kind = spec.get("type")
        if kind == "integer":
            if isinstance(value, str) and value.strip().lstrip("-").isdigit():
                value = int(value)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ToolError(f"{name} must be an integer")
            if "minimum" in spec and value < spec["minimum"]:
                raise ToolError(f"{name} must be >= {spec['minimum']}")
            if "maximum" in spec and value > spec["maximum"]:
                value = spec["maximum"]  # clamp: asking for "100" items is not an error
        elif kind == "boolean":
            if not isinstance(value, bool):
                raise ToolError(f"{name} must be true or false")
        elif kind == "string":
            if not isinstance(value, str):
                raise ToolError(f"{name} must be a string")
            value = value.strip()
            if len(value) > spec.get("maxLength", 500):
                raise ToolError(f"{name} is too long")
            if "enum" in spec and value not in spec["enum"]:
                raise ToolError(f"{name} must be one of {spec['enum']}")
        elif kind == "array":
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ToolError(f"{name} must be a list of strings")
            value = [v.strip() for v in value][: spec.get("maxItems", 20)]
        elif kind == "object":
            if not isinstance(value, dict):
                raise ToolError(f"{name} must be an object")
        out[name] = value
    return out


def repo_arg(value: str) -> str:
    value = value.strip().removeprefix("https://github.com/").strip("/")
    if not REPO_RE.match(value):
        raise ToolError(f"'{value[:60]}' is not an owner/name repository")
    return value


# ── shaping helpers ──────────────────────────────────────────────


def _ago(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        then = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    delta = datetime.now(timezone.utc) - then
    if delta.days >= 1:
        return f"{delta.days}d ago"
    hours = delta.seconds // 3600
    return f"{hours}h ago" if hours else f"{delta.seconds // 60}m ago"


def _clip(text: str | None, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _since(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _labels(item: dict[str, Any]) -> list[str]:
    return [lbl.get("name", "") for lbl in item.get("labels") or [] if isinstance(lbl, dict)]


def _issue_row(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": item.get("number"),
        "title": _clip(item.get("title"), 140),
        "author": (item.get("user") or {}).get("login"),
        "labels": _labels(item),
        "comments": item.get("comments"),
        "assignees": [a.get("login") for a in item.get("assignees") or []],
        "created": _ago(item.get("created_at")),
        "updated": _ago(item.get("updated_at")),
        "url": item.get("html_url"),
    }


def _get_items(ctx: ToolContext, path: str, params: dict[str, Any] | None = None) -> list[Any]:
    data = ctx.client.get(path, params=params or {})
    if isinstance(data, dict):
        for key in ("items", "workflow_runs", "jobs", "check_runs"):
            if isinstance(data.get(key), list):
                return list(data[key])
        return []
    return list(data or [])


# ── tool handlers ────────────────────────────────────────────────


def t_profile(ctx: ToolContext, a: dict[str, Any]) -> Any:
    u = ctx.client.get("/user")
    rate = ctx.client.rate_limit_state
    return {
        "login": u.get("login"), "name": u.get("name"), "bio": u.get("bio"),
        "company": u.get("company"), "location": u.get("location"),
        "public_repos": u.get("public_repos"), "private_repos": u.get("total_private_repos"),
        "followers": u.get("followers"), "following": u.get("following"),
        "account_created": (u.get("created_at") or "")[:10], "url": u.get("html_url"),
        "api_requests_remaining": getattr(rate, "remaining", None),
    }


def t_list_repos(ctx: ToolContext, a: dict[str, Any]) -> Any:
    owner = a.get("owner") or ""
    if owner:
        if not REPO_RE.match(f"{owner}/x"):
            raise ToolError("invalid owner")
        repos = _get_items(ctx, f"/users/{owner}/repos", {"per_page": 100, "sort": "pushed"})
    else:
        repos = _get_items(ctx, "/user/repos", {"per_page": 100, "affiliation": "owner", "sort": "pushed"})
    if not a["include_forks"]:
        repos = [r for r in repos if not r.get("fork")]
    key = {"stars": "stargazers_count", "pushed": "pushed_at", "updated": "updated_at",
           "forks": "forks_count", "issues": "open_issues_count"}[a["sort"]]
    repos.sort(key=lambda r: r.get(key) or 0, reverse=True)
    return {
        "total": len(repos),
        "repos": [{
            "name": r.get("full_name"), "description": _clip(r.get("description"), 100),
            "stars": r.get("stargazers_count"), "forks": r.get("forks_count"),
            "open_issues_and_prs": r.get("open_issues_count"), "language": r.get("language"),
            "private": r.get("private"), "archived": r.get("archived"), "fork": r.get("fork"),
            "last_push": _ago(r.get("pushed_at")),
        } for r in repos[: a["limit"]]],
    }


def t_get_repo(ctx: ToolContext, a: dict[str, Any]) -> Any:
    r = ctx.client.get(f"/repos/{repo_arg(a['repo'])}")
    return {
        "name": r.get("full_name"), "description": r.get("description"), "url": r.get("html_url"),
        "stars": r.get("stargazers_count"), "forks": r.get("forks_count"),
        "watchers": r.get("subscribers_count"), "open_issues_and_prs": r.get("open_issues_count"),
        "language": r.get("language"), "topics": r.get("topics"),
        "default_branch": r.get("default_branch"), "license": (r.get("license") or {}).get("spdx_id"),
        "visibility": r.get("visibility"), "archived": r.get("archived"), "fork": r.get("fork"),
        "parent": (r.get("parent") or {}).get("full_name"),
        "created": (r.get("created_at") or "")[:10], "last_push": _ago(r.get("pushed_at")),
        "homepage": r.get("homepage"), "you_can_push": (r.get("permissions") or {}).get("push"),
    }


def t_commits(ctx: ToolContext, a: dict[str, Any]) -> Any:
    params: dict[str, Any] = {"per_page": a["limit"], "since": _since(a["since_days"])}
    if a.get("author"):
        params["author"] = a["author"]
    if a.get("branch"):
        if not REF_RE.match(a["branch"]):
            raise ToolError("invalid branch")
        params["sha"] = a["branch"]
    commits = _get_items(ctx, f"/repos/{repo_arg(a['repo'])}/commits", params)
    return {
        "window_days": a["since_days"], "count": len(commits),
        "commits": [{
            "sha": (c.get("sha") or "")[:7],
            "message": _clip((c.get("commit", {}).get("message") or "").splitlines()[0] if c.get("commit", {}).get("message") else "", 120),
            "author": (c.get("author") or {}).get("login") or c.get("commit", {}).get("author", {}).get("name"),
            "when": _ago(c.get("commit", {}).get("author", {}).get("date")),
            "url": c.get("html_url"),
        } for c in commits],
    }


def t_issues(ctx: ToolContext, a: dict[str, Any]) -> Any:
    params: dict[str, Any] = {"state": a["state"], "per_page": min(100, a["limit"] * 2), "sort": "updated"}
    if a.get("labels"):
        params["labels"] = ",".join(a["labels"])
    items = [i for i in _get_items(ctx, f"/repos/{repo_arg(a['repo'])}/issues", params)
             if "pull_request" not in i][: a["limit"]]
    return {"state": a["state"], "count": len(items), "issues": [_issue_row(i) for i in items]}


def t_pulls(ctx: ToolContext, a: dict[str, Any]) -> Any:
    items = _get_items(ctx, f"/repos/{repo_arg(a['repo'])}/pulls",
                       {"state": a["state"], "per_page": a["limit"], "sort": "updated", "direction": "desc"})
    return {"state": a["state"], "count": len(items), "pull_requests": [{
        **_issue_row(p), "draft": p.get("draft"),
        "base": (p.get("base") or {}).get("ref"), "head": (p.get("head") or {}).get("label"),
        "requested_reviewers": [r.get("login") for r in p.get("requested_reviewers") or []],
        "merged": bool(p.get("merged_at")),
    } for p in items]}


def _comments(ctx: ToolContext, repo: str, number: int, limit: int = 6) -> list[dict[str, Any]]:
    issue = ctx.client.get(f"/repos/{repo}/issues/{number}")
    total = issue.get("comments") or 0
    if not total:
        return []
    last_page = max(1, (total + 29) // 30)
    rows = _get_items(ctx, f"/repos/{repo}/issues/{number}/comments", {"per_page": 30, "page": last_page})
    return [{"author": (c.get("user") or {}).get("login"), "when": _ago(c.get("created_at")),
             "body": _clip(c.get("body"), 500)} for c in rows[-limit:]]


def t_pull(ctx: ToolContext, a: dict[str, Any]) -> Any:
    repo, n = repo_arg(a["repo"]), a["number"]
    p = ctx.client.get(f"/repos/{repo}/pulls/{n}")
    reviews = _get_items(ctx, f"/repos/{repo}/pulls/{n}/reviews", {"per_page": 100})
    latest: dict[str, str] = {}
    for r in reviews:  # chronological: last state per reviewer wins
        login = (r.get("user") or {}).get("login")
        if login and r.get("state") != "COMMENTED":
            latest[login] = r.get("state", "")
    sha = (p.get("head") or {}).get("sha")
    checks: dict[str, Any] = {}
    if sha:
        runs = _get_items(ctx, f"/repos/{repo}/commits/{sha}/check-runs", {"per_page": 100})
        failing = [{"name": c.get("name"), "url": c.get("details_url"),
                    "run_id": int(m.group(1)) if (m := re.search(r"/actions/runs/(\d+)", c.get("details_url") or "")) else None}
                   for c in runs if c.get("conclusion") in ("failure", "timed_out", "action_required")]
        pending = [c.get("name") for c in runs if c.get("status") != "completed"]
        checks = {"total": len(runs), "failing": failing, "pending": pending,
                  "passing": sum(1 for c in runs if c.get("conclusion") == "success")}
    return {
        "number": n, "title": p.get("title"), "url": p.get("html_url"), "state": p.get("state"),
        "draft": p.get("draft"), "merged": p.get("merged"), "author": (p.get("user") or {}).get("login"),
        "base": (p.get("base") or {}).get("ref"), "head": (p.get("head") or {}).get("label"),
        "created": _ago(p.get("created_at")), "updated": _ago(p.get("updated_at")),
        "mergeable": p.get("mergeable"), "mergeable_state": p.get("mergeable_state"),
        "additions": p.get("additions"), "deletions": p.get("deletions"), "changed_files": p.get("changed_files"),
        "requested_reviewers": [r.get("login") for r in p.get("requested_reviewers") or []],
        "review_states": latest, "checks": checks,
        "body": _clip(p.get("body"), 1200), "recent_comments": _comments(ctx, repo, n),
    }


def t_issue(ctx: ToolContext, a: dict[str, Any]) -> Any:
    repo, n = repo_arg(a["repo"]), a["number"]
    i = ctx.client.get(f"/repos/{repo}/issues/{n}")
    return {**_issue_row(i), "state": i.get("state"), "is_pull_request": "pull_request" in i,
            "body": _clip(i.get("body"), 1500), "recent_comments": _comments(ctx, repo, n)}


def t_runs(ctx: ToolContext, a: dict[str, Any]) -> Any:
    params: dict[str, Any] = {"per_page": a["limit"]}
    if a.get("branch"):
        if not REF_RE.match(a["branch"]):
            raise ToolError("invalid branch")
        params["branch"] = a["branch"]
    if a.get("status"):
        params["status"] = a["status"]
    runs = _get_items(ctx, f"/repos/{repo_arg(a['repo'])}/actions/runs", params)
    done = [r for r in runs if r.get("status") == "completed"]
    ok = sum(1 for r in done if r.get("conclusion") == "success")
    return {
        "success_rate": f"{ok}/{len(done)}" if done else None,
        "runs": [{
            "id": r.get("id"), "workflow": r.get("name"), "branch": r.get("head_branch"),
            "event": r.get("event"), "status": r.get("status"), "conclusion": r.get("conclusion"),
            "when": _ago(r.get("created_at")), "commit": _clip((r.get("head_commit") or {}).get("message", "").split("\n")[0], 80),
            "url": r.get("html_url"),
        } for r in runs],
    }


def t_run(ctx: ToolContext, a: dict[str, Any]) -> Any:
    repo, run_id = repo_arg(a["repo"]), a["run_id"]
    run = ctx.client.get(f"/repos/{repo}/actions/runs/{run_id}")
    jobs = _get_items(ctx, f"/repos/{repo}/actions/runs/{run_id}/jobs", {"per_page": 50})
    out_jobs = []
    for j in jobs:
        row: dict[str, Any] = {"name": j.get("name"), "conclusion": j.get("conclusion"), "status": j.get("status")}
        if j.get("conclusion") == "failure":
            row["failed_steps"] = [s.get("name") for s in j.get("steps") or [] if s.get("conclusion") == "failure"]
            try:  # job id == check-run id; annotations carry the actual error lines
                ann = ctx.client.get(f"/repos/{repo}/check-runs/{j.get('id')}/annotations", params={"per_page": 10})
                row["errors"] = [_clip(f"{x.get('path', '')}:{x.get('start_line', '')} {x.get('message', '')}", 300)
                                 for x in ann or [] if x.get("annotation_level") in ("failure", "warning")][:8]
            except Exception:
                row["errors"] = []
        out_jobs.append(row)
    return {
        "workflow": run.get("name"), "branch": run.get("head_branch"), "event": run.get("event"),
        "status": run.get("status"), "conclusion": run.get("conclusion"), "attempt": run.get("run_attempt"),
        "when": _ago(run.get("created_at")), "url": run.get("html_url"),
        "commit": _clip((run.get("head_commit") or {}).get("message"), 200), "jobs": out_jobs,
        "note": "Full logs are not retrievable via this tool; errors come from check annotations.",
    }


def t_releases(ctx: ToolContext, a: dict[str, Any]) -> Any:
    repo = repo_arg(a["repo"])
    rels = _get_items(ctx, f"/repos/{repo}/releases", {"per_page": a["limit"]})
    out: dict[str, Any] = {"releases": [{
        "tag": r.get("tag_name"), "name": r.get("name"), "prerelease": r.get("prerelease"),
        "draft": r.get("draft"), "published": _ago(r.get("published_at")), "url": r.get("html_url"),
        "notes": _clip(r.get("body"), 300),
    } for r in rels]}
    published = [r for r in rels if not r.get("draft")]
    if published:
        default = ctx.client.get(f"/repos/{repo}").get("default_branch", "main")
        try:
            cmp = ctx.client.get(f"/repos/{repo}/compare/{published[0]['tag_name']}...{default}")
            out["unreleased_commits_on_default_branch"] = cmp.get("ahead_by")
        except Exception:
            pass
    else:
        out["note"] = "No published releases."
    return out


def t_contributors(ctx: ToolContext, a: dict[str, Any]) -> Any:
    rows = _get_items(ctx, f"/repos/{repo_arg(a['repo'])}/contributors", {"per_page": a["limit"]})
    return {"contributors": [{"login": c.get("login"), "commits": c.get("contributions"),
                              "bot": c.get("type") == "Bot"} for c in rows]}


def t_traffic(ctx: ToolContext, a: dict[str, Any]) -> Any:
    repo = repo_arg(a["repo"])
    views = ctx.client.get(f"/repos/{repo}/traffic/views")
    clones = ctx.client.get(f"/repos/{repo}/traffic/clones")
    refs = ctx.client.get(f"/repos/{repo}/traffic/popular/referrers")
    return {"window": "last 14 days",
            "views": {"total": views.get("count"), "unique": views.get("uniques")},
            "clones": {"total": clones.get("count"), "unique": clones.get("uniques")},
            "top_referrers": [{"source": r.get("referrer"), "views": r.get("count")} for r in (refs or [])[:5]]}


def t_star_history(ctx: ToolContext, a: dict[str, Any]) -> Any:
    if not a.get("repo"):  # all of the operator's starred repos in one call
        repos = [r for r in _get_items(ctx, "/user/repos", {"per_page": 100, "affiliation": "owner"})
                 if (r.get("stargazers_count") or 0) > 0 and not r.get("fork")]
        rows = [{"repo": r["full_name"], **t_star_history(ctx, {"repo": r["full_name"]})} for r in repos[:15]]
        rows.sort(key=lambda x: x.get("last_30_days") or 0, reverse=True)
        return {"repos_with_stars": len(repos), "note": "Repos with zero stars are omitted.", "by_repo": rows}
    repo = repo_arg(a["repo"])
    total = ctx.client.get(f"/repos/{repo}").get("stargazers_count") or 0
    if total > 3000:
        return {"total_stars": total, "note": "Too many stars for a timestamped scan; only the total is available."}
    stamps: list[str] = []
    for page in range(1, (total + 99) // 100 + 1):
        rows = ctx.client.get(f"/repos/{repo}/stargazers", params={"per_page": 100, "page": page},
                              headers={"Accept": "application/vnd.github.star+json"})
        stamps += [r.get("starred_at", "") for r in rows or [] if isinstance(r, dict)]
    now = datetime.now(timezone.utc)

    def within(days: int) -> int:
        cutoff = (now - timedelta(days=days)).isoformat()
        return sum(1 for s in stamps if s.replace("Z", "+00:00") >= cutoff)

    return {"total_stars": total, "last_7_days": within(7), "last_30_days": within(30),
            "last_90_days": within(90), "previous_30_days": within(60) - within(30),
            "most_recent_star": _ago(max(stamps)) if stamps else None}


def t_search(ctx: ToolContext, a: dict[str, Any]) -> Any:
    kind = a["kind"]
    params: dict[str, Any] = {"q": a["query"], "per_page": a["limit"]}
    if a.get("sort"):
        params["sort"] = a["sort"]
    data = ctx.client.get(f"/search/{kind}", params=params)
    items = data.get("items") or []
    if kind == "repositories":
        rows = [{"name": r.get("full_name"), "description": _clip(r.get("description"), 120),
                 "stars": r.get("stargazers_count"), "language": r.get("language"),
                 "last_push": _ago(r.get("pushed_at")), "url": r.get("html_url")} for r in items]
    else:
        rows = [{**_issue_row(i), "repo": (i.get("repository_url") or "").removeprefix("https://api.github.com/repos/"),
                 "is_pr": "pull_request" in i, "state": i.get("state")} for i in items]
    return {"total_matches": data.get("total_count"), "results": rows}


def t_activity(ctx: ToolContext, a: dict[str, Any]) -> Any:
    user = a.get("username") or ctx.login
    if not REPO_RE.match(f"{user}/x"):
        raise ToolError("invalid username")
    events = _get_items(ctx, f"/users/{user}/events", {"per_page": 100})
    from collections import Counter
    by_type = Counter(e.get("type", "").removesuffix("Event") for e in events)
    by_repo = Counter((e.get("repo") or {}).get("name") for e in events)
    days = Counter((e.get("created_at") or "")[:10] for e in events)
    return {"user": user, "events_sampled": len(events), "span": f"{min(days) if days else '?'} → {max(days) if days else '?'}",
            "by_type": dict(by_type.most_common()), "most_active_repos": dict(by_repo.most_common(8)),
            "active_days": len(days),
            "recent": [{"type": e.get("type", "").removesuffix("Event"), "repo": (e.get("repo") or {}).get("name"),
                        "when": _ago(e.get("created_at"))} for e in events[:12]]}


def t_user(ctx: ToolContext, a: dict[str, Any]) -> Any:
    if not REPO_RE.match(f"{a['username']}/x"):
        raise ToolError("invalid username")
    u = ctx.client.get(f"/users/{a['username']}")
    return {k: u.get(k) for k in ("login", "name", "bio", "company", "blog", "location", "public_repos",
                                  "followers", "following", "html_url", "type")} | {"joined": (u.get("created_at") or "")[:10]}


def t_file(ctx: ToolContext, a: dict[str, Any]) -> Any:
    repo = repo_arg(a["repo"])
    path = a.get("path") or ""
    if path and (".." in path or not REF_RE.match(path)):
        raise ToolError("invalid path")
    params = {"ref": a["ref"]} if a.get("ref") else {}
    if a.get("ref") and not REF_RE.match(a["ref"]):
        raise ToolError("invalid ref")
    data = ctx.client.get(f"/repos/{repo}/readme" if path.upper() in ("", "README") else f"/repos/{repo}/contents/{path}",
                          params=params)
    if isinstance(data, list):
        return {"directory": path or "/", "entries": [{"name": e.get("name"), "type": e.get("type")} for e in data[:100]]}
    content = data.get("content") or ""
    try:
        text = base64.b64decode(content).decode("utf-8", errors="replace")
    except ValueError:
        text = ""
    return {"path": data.get("path"), "size": data.get("size"), "url": data.get("html_url"),
            "content": _clip(text, 5000), "truncated": len(text) > 5000}


def t_health(ctx: ToolContext, a: dict[str, Any]) -> Any:
    """Deterministic health metrics; the model interprets, it does not compute."""
    repo = repo_arg(a["repo"])
    r = ctx.client.get(f"/repos/{repo}")
    out: dict[str, Any] = {"repo": repo, "url": r.get("html_url"), "archived": r.get("archived"),
                           "days_since_push": None, "stars": r.get("stargazers_count")}
    if r.get("pushed_at"):
        out["days_since_push"] = (datetime.now(timezone.utc) - datetime.fromisoformat(r["pushed_at"].replace("Z", "+00:00"))).days
    pulls = _get_items(ctx, f"/repos/{repo}/pulls", {"state": "open", "per_page": 100})
    stale_cut = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    out["open_prs"] = len(pulls)
    out["stale_prs_14d"] = [p.get("number") for p in pulls if (p.get("updated_at") or "").replace("Z", "+00:00") < stale_cut]
    out["open_issues"] = max(0, (r.get("open_issues_count") or 0) - len(pulls))
    runs = _get_items(ctx, f"/repos/{repo}/actions/runs", {"per_page": 30, "status": "completed"})
    if runs:
        fails = [x for x in runs if x.get("conclusion") == "failure"]
        out["ci_last_30_runs"] = {"success": sum(1 for x in runs if x.get("conclusion") == "success"), "failure": len(fails),
                                  "failing_workflows": sorted({x.get("name") for x in fails})}
        default = r.get("default_branch")
        on_default = [x for x in runs if x.get("head_branch") == default]
        out["default_branch_latest_ci"] = on_default[0].get("conclusion") if on_default else None
    else:
        out["ci_last_30_runs"] = None
    try:
        prof = ctx.client.get(f"/repos/{repo}/community/profile")
        files = prof.get("files") or {}
        out["community_health_percent"] = prof.get("health_percentage")
        out["missing_files"] = [k for k in ("readme", "license", "contributing", "code_of_conduct") if not files.get(k)]
    except Exception:
        pass
    try:
        rels = _get_items(ctx, f"/repos/{repo}/releases", {"per_page": 1})
        out["latest_release"] = {"tag": rels[0].get("tag_name"), "published": _ago(rels[0].get("published_at"))} if rels else None
    except Exception:
        pass
    return out


def t_dependabot(ctx: ToolContext, a: dict[str, Any]) -> Any:
    alerts = _get_items(ctx, f"/repos/{repo_arg(a['repo'])}/dependabot/alerts", {"state": "open", "per_page": 30})
    return {"open_alerts": len(alerts), "alerts": [{
        "package": (x.get("dependency") or {}).get("package", {}).get("name"),
        "ecosystem": (x.get("dependency") or {}).get("package", {}).get("ecosystem"),
        "severity": (x.get("security_advisory") or {}).get("severity"),
        "summary": _clip((x.get("security_advisory") or {}).get("summary"), 150),
        "fixed_in": ((x.get("security_vulnerability") or {}).get("first_patched_version") or {}).get("identifier"),
        "url": x.get("html_url")} for x in alerts]}


def t_advisories(ctx: ToolContext, a: dict[str, Any]) -> Any:
    params: dict[str, Any] = {"per_page": a["limit"], "type": "reviewed", "sort": "published"}
    for key in ("ecosystem", "severity"):
        if a.get(key):
            params[key] = a[key]
    if a.get("affects"):
        params["affects"] = a["affects"]
    rows = _get_items(ctx, "/advisories", params)
    return {"source": "GitHub Advisory Database (official)", "advisories": [{
        "id": x.get("ghsa_id"), "severity": x.get("severity"), "summary": _clip(x.get("summary"), 160),
        "published": _ago(x.get("published_at")),
        "packages": [((v.get("package") or {}).get("ecosystem"), (v.get("package") or {}).get("name"))
                     for v in (x.get("vulnerabilities") or [])[:3]],
        "url": x.get("html_url")} for x in rows]}


def t_notifications(ctx: ToolContext, a: dict[str, Any]) -> Any:
    from src.agent.attention import collapse, signals_from_notifications

    raw = ctx.client.get("/notifications", params={"all": "false", "per_page": 50})
    signals = collapse(signals_from_notifications(raw or [], ctx.login))
    signals.sort(key=lambda s: s.score, reverse=True)
    return {"unread_threads": len(raw or []), "grouped_signals": [s.to_brief() for s in signals[: a["limit"]]]}


def t_my_pulls(ctx: ToolContext, a: dict[str, Any]) -> Any:
    q = f"is:pr author:{ctx.login} archived:false" + ("" if a["state"] == "all" else f" is:{a['state']}")
    if a.get("repo"):
        q += f" repo:{repo_arg(a['repo'])}"
    data = ctx.client.get("/search/issues", params={"q": q, "per_page": a["limit"], "sort": "updated"})
    return {"total": data.get("total_count"), "pull_requests": [{
        **_issue_row(i), "repo": (i.get("repository_url") or "").removeprefix("https://api.github.com/repos/"),
        "state": i.get("state"), "draft": i.get("draft")} for i in data.get("items") or []]}


def t_waiting_on_me(ctx: ToolContext, a: dict[str, Any]) -> Any:
    from src.agent.attention import waiting_on_me

    return waiting_on_me(ctx.client, ctx.login)


def t_opportunities(ctx: ToolContext, a: dict[str, Any]) -> Any:
    from src.intelligence.oss.hunter import HunterConfig, run_hunter

    cfg = HunterConfig(max_results=a["limit"] + 5, max_queries=4, per_query_max_pages=1,
                       languages=[a["language"]] if a.get("language") else [])
    result = run_hunter(ctx.client, cfg)
    rows = []
    for o in result.opportunities:
        ident = f"{o.repo_full_name}#{o.issue_number}"
        rows.append({"issue": ident, "title": _clip(o.title, 120), "url": o.html_url, "labels": list(o.labels),
                     "repo_stars": o.repo_stars, "comments": o.comments,
                     "score": round(float(getattr(o, "score", 0) or 0), 2), "assigned": bool(o.assignee),
                     "updated": _ago(o.updated_at.isoformat() if o.updated_at else None),
                     "previously_suggested": ident in ctx.surfaced})
    fresh = [r for r in rows if not r["previously_suggested"]] or rows
    for r in fresh[: a["limit"]]:
        ctx.surfaced.add(str(r["issue"]))
    return {"queries_run": result.queries_run, "opportunities": fresh[: a["limit"]],
            "hidden_already_suggested": len(rows) - len(fresh)}


def t_changelog(ctx: ToolContext, a: dict[str, Any]) -> Any:
    from src.research.feeds import fetch_github_changelog

    return {"source": "github.blog/changelog (official GitHub announcements)",
            "entries": fetch_github_changelog(limit=a["limit"])}


def t_propose(ctx: ToolContext, a: dict[str, Any]) -> Any:
    if ctx.actions is None:
        raise ToolError("write actions are disabled")
    action = ctx.actions.propose(
        operation=a["operation"], repo=repo_arg(a["repo"]), params=a.get("params") or {},
        summary=a["summary"], conversation_key=ctx.conversation_key, user_id=ctx.user_id,
    )
    return {"status": "awaiting_operator_confirmation", "action_id": action.id,
            "preview": action.preview(),
            "instruction": "Nothing has been executed. Tell the operator to press Confirm or Cancel."}


# ── registry ─────────────────────────────────────────────────────

_REPO = {"type": "string", "description": "owner/name, e.g. aspire488/gh-ops", "maxLength": 141}
def _LIMIT(default: int, maximum: int) -> dict[str, Any]:  # noqa: N802
    return {"type": "integer", "minimum": 1, "maximum": maximum, "default": default}


def _obj(props: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(required)}


TOOLS: tuple[Tool, ...] = (
    Tool("get_profile", "The operator's GitHub account: login, bio, repo counts, followers, API quota.",
         _obj({}), t_profile, 600, "Reading your profile"),
    Tool("list_repositories", "List repositories owned by the operator (or another owner), sorted. Use to find repos, compare stars/forks/activity.",
         _obj({"owner": {"type": "string", "maxLength": 39, "description": "omit for the operator"},
               "sort": {"type": "string", "enum": ["pushed", "stars", "updated", "forks", "issues"], "default": "pushed"},
               "include_forks": {"type": "boolean", "default": False}, "limit": _LIMIT(20, 100)}),
         t_list_repos, 300, "Listing repositories"),
    Tool("get_repository", "Metadata and stats for one repository.", _obj({"repo": _REPO}, ("repo",)),
         t_get_repo, 120, "Reading the repository"),
    Tool("repository_health", "Deterministic health check: push recency, stale PRs, CI success rate, default-branch CI, community files, latest release.",
         _obj({"repo": _REPO}, ("repo",)), t_health, 300, "Assessing repository health"),
    Tool("list_commits", "Recent commits on a repository branch.",
         _obj({"repo": _REPO, "since_days": _LIMIT(14, 365), "limit": _LIMIT(20, 100),
               "author": {"type": "string", "maxLength": 39}, "branch": {"type": "string", "maxLength": 200}}, ("repo",)),
         t_commits, 120, "Reading commits"),
    Tool("list_issues", "Issues (not PRs) in a repository.",
         _obj({"repo": _REPO, "state": {"type": "string", "enum": ["open", "closed", "all"], "default": "open"},
               "labels": {"type": "array", "items": {"type": "string"}, "maxItems": 5}, "limit": _LIMIT(15, 50)}, ("repo",)),
         t_issues, 120, "Reading issues"),
    Tool("get_issue", "One issue with body and latest comments.",
         _obj({"repo": _REPO, "number": {"type": "integer", "minimum": 1}}, ("repo", "number")), t_issue, 60, "Reading the issue"),
    Tool("list_pull_requests", "Pull requests in a repository.",
         _obj({"repo": _REPO, "state": {"type": "string", "enum": ["open", "closed", "all"], "default": "open"},
               "limit": _LIMIT(15, 50)}, ("repo",)), t_pulls, 120, "Reading pull requests"),
    Tool("get_pull_request", "One PR: review states per reviewer, requested reviewers, mergeability, check results, latest comments. Use for merge readiness.",
         _obj({"repo": _REPO, "number": {"type": "integer", "minimum": 1}}, ("repo", "number")), t_pull, 60, "Investigating the pull request"),
    Tool("list_workflow_runs", "Recent GitHub Actions runs with conclusions and success rate.",
         _obj({"repo": _REPO, "branch": {"type": "string", "maxLength": 200},
               "status": {"type": "string", "enum": ["completed", "failure", "success", "in_progress", "queued"]},
               "limit": _LIMIT(10, 50)}, ("repo",)), t_runs, 60, "Checking CI runs"),
    Tool("get_workflow_run", "One Actions run: jobs, failed steps and error annotations. Use to explain a CI failure.",
         _obj({"repo": _REPO, "run_id": {"type": "integer", "minimum": 1}}, ("repo", "run_id")), t_run, 60, "Investigating the CI failure"),
    Tool("list_releases", "Releases, plus how many commits on the default branch are unreleased.",
         _obj({"repo": _REPO, "limit": _LIMIT(5, 20)}, ("repo",)), t_releases, 300, "Checking releases"),
    Tool("list_contributors", "Top contributors by commit count.", _obj({"repo": _REPO, "limit": _LIMIT(10, 50)}, ("repo",)),
         t_contributors, 600, "Reading contributors"),
    Tool("get_traffic", "14-day views, clones and referrers (needs push access).", _obj({"repo": _REPO}, ("repo",)),
         t_traffic, 900, "Reading traffic"),
    Tool("get_star_history", "Star growth: stars gained in the last 7/30/90 days vs the previous 30. Omit repo to scan all of the operator's repos at once.",
         _obj({"repo": _REPO}),
         t_star_history, 900, "Analysing star growth"),
    Tool("search_github", "GitHub search. kind=repositories or issues (issues also covers PRs). GitHub query syntax, e.g. 'is:issue is:open label:bug repo:o/r', 'topic:llm stars:>500 pushed:>2026-10-01'.",
         _obj({"kind": {"type": "string", "enum": ["repositories", "issues"]}, "query": {"type": "string", "maxLength": 256},
               "sort": {"type": "string", "enum": ["stars", "updated", "comments", "created", "forks"]}, "limit": _LIMIT(10, 30)},
              ("kind", "query")), t_search, 120, "Searching GitHub"),
    Tool("get_user_activity", "Recent public+visible activity of a user (default: the operator): event types, active repos, cadence.",
         _obj({"username": {"type": "string", "maxLength": 39}}), t_activity, 300, "Reading recent activity"),
    Tool("get_user", "Public profile of any GitHub user.", _obj({"username": {"type": "string", "maxLength": 39}}, ("username",)),
         t_user, 600, "Looking up the user"),
    Tool("get_file", "Read a file (default README) or list a directory in a repository.",
         _obj({"repo": _REPO, "path": {"type": "string", "maxLength": 200}, "ref": {"type": "string", "maxLength": 200}}, ("repo",)),
         t_file, 300, "Reading the file"),
    Tool("list_dependabot_alerts", "Open Dependabot security alerts for a repository.", _obj({"repo": _REPO}, ("repo",)),
         t_dependabot, 600, "Checking security alerts"),
    Tool("search_security_advisories", "GitHub Advisory Database: recent reviewed advisories, filterable by ecosystem/severity/affected package.",
         _obj({"ecosystem": {"type": "string", "enum": ["pip", "npm", "go", "maven", "nuget", "rubygems", "rust", "composer", "actions"]},
               "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
               "affects": {"type": "string", "maxLength": 100}, "limit": _LIMIT(10, 30)}), t_advisories, 1800, "Checking advisories"),
    Tool("get_notifications", "The operator's unread GitHub notifications, grouped and prioritised (CI failures collapsed per workflow/branch).",
         _obj({"limit": _LIMIT(15, 40)}), t_notifications, 60, "Reading your notifications"),
    Tool("list_my_pull_requests", "PRs authored by the operator across ALL repos (incl. external open-source contributions). Then use get_pull_request for checks/reviews.",
         _obj({"state": {"type": "string", "enum": ["open", "closed", "all"], "default": "open"}, "repo": _REPO,
               "limit": _LIMIT(15, 50)}), t_my_pulls, 120, "Finding your pull requests"),
    Tool("whats_waiting_on_me", "PRs awaiting the operator's review, the operator's PRs with changes requested, and approved PRs ready to merge.",
         _obj({}), t_waiting_on_me, 120, "Checking what is waiting on you"),
    Tool("find_contribution_opportunities", "Ranked open-source issues to contribute to (scored on activity, freshness, labels). Skips ones already suggested.",
         _obj({"language": {"type": "string", "maxLength": 30}, "limit": _LIMIT(5, 10)}), t_opportunities, 1800, "Hunting contribution opportunities"),
    Tool("get_github_changelog", "Latest official GitHub platform announcements (github.blog/changelog).",
         _obj({"limit": _LIMIT(8, 20)}), t_changelog, 3600, "Reading the GitHub changelog"),
    Tool("propose_action",
         "Queue a GitHub WRITE for operator confirmation. Only when the operator explicitly asked for this change. "
         "operations: create_issue{title,body,labels}, comment{number,body}, add_labels{number,labels}, "
         "set_issue_state{number,state:open|closed}, rerun_failed_jobs{run_id}, dispatch_workflow{workflow,ref,inputs}, "
         "update_repo{description,homepage}, replace_topics{topics}, create_pull_request{title,head,base,body}.",
         _obj({"operation": {"type": "string", "enum": ["create_issue", "comment", "add_labels", "set_issue_state",
                                                        "rerun_failed_jobs", "dispatch_workflow", "update_repo",
                                                        "replace_topics", "create_pull_request"]},
               "repo": _REPO, "params": {"type": "object"},
               "summary": {"type": "string", "maxLength": 200, "description": "one line: what and why"}},
              ("operation", "repo", "summary")), t_propose, 0, "Preparing the change for your confirmation"),
)


class ToolRegistry:
    def __init__(self, tools: tuple[Tool, ...] = TOOLS, cache_size: int = 256) -> None:
        self._tools = {t.name: t for t in tools}
        self._cache: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._cache_size = cache_size
        self.calls = 0

    def specs(self) -> list[dict[str, Any]]:
        return [t.spec() for t in self._tools.values()]

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def run(self, ctx: ToolContext, name: str, raw_args: str | dict[str, Any] | None) -> str:
        """Execute a tool; always returns a JSON string for the model."""
        tool = self._tools.get(name)
        if tool is None:
            return json.dumps({"error": f"unknown tool {name}"})
        try:
            parsed = json.loads(raw_args) if isinstance(raw_args, str) and raw_args.strip() else (raw_args or {})
            args = validate_args(tool.parameters, parsed)
        except (ValueError, ToolError) as exc:
            return json.dumps({"error": f"invalid arguments: {exc}"})
        key = f"{name}:{json.dumps(args, sort_keys=True)}"
        hit = self._cache.get(key)
        if hit and hit[0] > time.monotonic():
            self._cache.move_to_end(key)
            return hit[1]
        self.calls += 1
        try:
            data = tool.handler(ctx, args)
            text = json.dumps({"data": data}, default=str, ensure_ascii=False)
        except ToolError as exc:
            return json.dumps({"error": str(exc)})
        except Exception as exc:  # GitHub errors become data the model can explain
            status = getattr(exc, "status_code", None)
            msg = {404: "not found (or no access)", 403: "forbidden: token lacks permission or quota exhausted",
                   401: "GitHub authentication failed"}.get(status or 0)
            if status == 422:  # GitHub's validation text helps the model fix its query
                msg = f"GitHub rejected the request: {str(getattr(exc, 'message', '') or exc)[:160]}"
            logger.warning("tool %s failed: %s %s args=%s", name, type(exc).__name__, status, json.dumps(args)[:200])
            return json.dumps({"error": msg or f"{type(exc).__name__}: GitHub request failed"})
        if len(text) > MAX_RESULT_CHARS:
            text = text[: MAX_RESULT_CHARS - 40] + " …[truncated: narrow the query]"
        if tool.cache_ttl > 0:
            self._cache[key] = (time.monotonic() + tool.cache_ttl, text)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return text
