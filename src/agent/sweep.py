"""Proactive sweeps: find problems and opportunities nobody notified you about.

Notifications only cover what GitHub decides to tell you. The sweep looks at
your repos directly (deterministic, ~5 GET requests per repo) and produces
Findings: red default branches, stale PRs, approved-but-unmerged PRs,
unanswered change requests, unreleased work piling up, open high/critical
Dependabot alerts, and star spikes. The LLM only *writes* the brief from these
facts; if it is unavailable, a deterministic brief is sent instead.

``diagnose_ci`` is the auto-investigation used by alerts: before pinging,
Kestrel fetches the failing run, job, step and error annotations.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from src.utils.logging import get_logger

logger = get_logger("agent.sweep")


@dataclass
class Finding:
    kind: str       # main_red | stale_pr | merge_ready | changes_requested | review_requested | release_due | security | stars
    repo: str
    title: str
    detail: str
    url: str
    priority: int   # 0-100
    action: str


def _days(iso: str | None, now: datetime) -> int:
    if not iso:
        return 0
    try:
        return (now - datetime.fromisoformat(iso.replace("Z", "+00:00"))).days
    except ValueError:
        return 0


def _items(data: Any, key: str = "items") -> list[dict[str, Any]]:
    if isinstance(data, dict):
        return [x for x in data.get(key) or [] if isinstance(x, dict)]
    return [x for x in data or [] if isinstance(x, dict)]


def diagnose_ci(client: Any, repo: str, branch: str, workflow: str = "") -> dict[str, Any] | None:
    """Latest failed run for (repo, branch[, workflow]) → failing jobs/steps/errors. None if not found."""
    runs = _items(client.get(f"/repos/{repo}/actions/runs",
                             params={"branch": branch, "status": "failure", "per_page": 10}), "workflow_runs")
    run = next((r for r in runs if not workflow or r.get("name") == workflow), None)
    if run is None:
        return None
    jobs = _items(client.get(f"/repos/{repo}/actions/runs/{run['id']}/jobs", params={"per_page": 30}), "jobs")
    failed = []
    for job in jobs:
        if job.get("conclusion") != "failure":
            continue
        steps = [s.get("name") for s in job.get("steps") or [] if s.get("conclusion") == "failure"]
        errors: list[str] = []
        try:
            for a in client.get(f"/repos/{repo}/check-runs/{job.get('id')}/annotations", params={"per_page": 5}) or []:
                if a.get("annotation_level") == "failure":
                    loc = f"{a.get('path', '')}:{a.get('start_line', '')}".strip(":")
                    errors.append(f"{loc} {str(a.get('message', '')).splitlines()[0][:160]}".strip())
        except Exception:
            pass
        failed.append({"job": job.get("name"), "steps": steps, "errors": errors[:3]})
    return {"workflow": run.get("name"), "url": run.get("html_url"), "attempt": run.get("run_attempt"),
            "commit": str((run.get("head_commit") or {}).get("message", "")).splitlines()[0][:100] if run.get("head_commit") else "",
            "author": ((run.get("head_commit") or {}).get("author") or {}).get("name"), "failed_jobs": failed[:3]}


def diagnosis_lines(diag: dict[str, Any] | None) -> list[str]:
    if not diag or not diag.get("failed_jobs"):
        return []
    out = []
    for job in diag["failed_jobs"][:2]:
        step = f" → step `{job['steps'][0]}`" if job["steps"] else ""
        out.append(f"Failing: `{job['job']}`{step}")
        out += [f"  `{e[:150]}`" for e in job["errors"][:2]]
    if diag.get("commit"):
        out.append(f"Last commit: \"{diag['commit']}\"" + (f" ({diag['author']})" if diag.get("author") else ""))
    return out


class Sweeper:
    def __init__(self, client: Any, login: str, repos: list[str], star_memory: dict[str, int] | None = None) -> None:
        self.client, self.login = client, login
        self.repos = repos
        self.star_memory = star_memory if star_memory is not None else {}

    def own_repos(self, now: datetime) -> list[str]:
        """Configured repos plus the operator's own non-fork repos pushed in the last 30 days (cap 12)."""
        names = list(dict.fromkeys(self.repos))
        try:
            for r in _items(self.client.get("/user/repos", params={"affiliation": "owner", "sort": "pushed", "per_page": 30})):
                if not r.get("fork") and not r.get("archived") and _days(r.get("pushed_at"), now) <= 30:
                    names.append(r["full_name"])
        except Exception as exc:
            logger.warning("sweep: repo listing failed (%s)", type(exc).__name__)
        return list(dict.fromkeys(names))[:12]

    def run(self, now: datetime | None = None) -> list[Finding]:
        now = now or datetime.now(timezone.utc)
        findings: list[Finding] = []
        findings += self._people(now)
        for repo in self.own_repos(now):
            try:
                findings += self._repo(repo, now)
            except Exception as exc:  # one bad repo never kills the sweep
                logger.warning("sweep: %s failed (%s)", repo, type(exc).__name__)
        findings.sort(key=lambda f: f.priority, reverse=True)
        return findings

    def _search(self, q: str) -> list[dict[str, Any]]:
        return _items(self.client.get("/search/issues", params={"q": q, "per_page": 10, "sort": "updated"}))

    def _people(self, now: datetime) -> list[Finding]:
        """Things blocked on the operator, with age-based nudges."""
        out: list[Finding] = []
        if not self.login:
            return out
        def repo_of(item: dict[str, Any]) -> str:
            return str(item.get("repository_url", "")).removeprefix("https://api.github.com/repos/")

        for i in self._search(f"is:pr is:open review-requested:{self.login} archived:false"):
            age = _days(i.get("created_at"), now)
            out.append(Finding("review_requested", repo_of(i), f"#{i['number']} {i.get('title', '')[:90]}",
                               f"waiting on your review for {age}d", i.get("html_url", ""), 70 + min(20, age * 3),
                               "Review it or tell them you can't get to it."))
        for i in self._search(f"is:pr is:open author:{self.login} review:approved archived:false"):
            age = _days(i.get("updated_at"), now)
            out.append(Finding("merge_ready", repo_of(i), f"#{i['number']} {i.get('title', '')[:90]}",
                               f"approved, idle {age}d" if age else "approved today", i.get("html_url", ""),
                               55 + min(25, age * 5), "Merge it (or ping the maintainer if you can't)."))
        for i in self._search(f"is:pr is:open author:{self.login} review:changes_requested archived:false"):
            age = _days(i.get("updated_at"), now)
            out.append(Finding("changes_requested", repo_of(i), f"#{i['number']} {i.get('title', '')[:90]}",
                               f"changes requested, last activity {age}d ago", i.get("html_url", ""),
                               60 + min(25, age * 4), "Push the requested changes before the PR goes stale."))
        return out

    def _repo(self, repo: str, now: datetime) -> list[Finding]:
        out: list[Finding] = []
        r = self.client.get(f"/repos/{repo}")
        url = r.get("html_url") or f"https://github.com/{repo}"
        default = r.get("default_branch") or "main"
        stars = int(r.get("stargazers_count") or 0)
        prev = self.star_memory.get(repo)
        if prev is not None and stars - prev >= max(3, prev // 10):
            out.append(Finding("stars", repo, f"+{stars - prev} stars since last sweep", f"now {stars}", url, 45,
                               "Something's driving traffic: check referrers, maybe share it while it's hot."))
        self.star_memory[repo] = stars
        runs = _items(self.client.get(f"/repos/{repo}/actions/runs",
                                      params={"branch": default, "per_page": 5, "status": "completed"}), "workflow_runs")
        latest_per_wf: dict[str, dict[str, Any]] = {}
        for run in runs:
            latest_per_wf.setdefault(str(run.get("name")), run)
        red = [w for w, run in latest_per_wf.items() if run.get("conclusion") == "failure"]
        if red:
            out.append(Finding("main_red", repo, f"{', '.join(red[:3])} red on {default}",
                               "latest run on the default branch failed", f"{url}/actions", 85,
                               "Fix or revert. Everything built on this branch is suspect."))
        pulls = _items(self.client.get(f"/repos/{repo}/pulls", params={"state": "open", "per_page": 50}))
        stale = [p for p in pulls if _days(p.get("updated_at"), now) >= 14 and not p.get("draft")]
        if stale:
            oldest = max(stale, key=lambda p: _days(p.get("updated_at"), now))
            out.append(Finding("stale_pr", repo, f"{len(stale)} PR{'s' if len(stale) > 1 else ''} idle 14d+",
                               f"oldest: #{oldest['number']} {str(oldest.get('title', ''))[:60]} ({_days(oldest.get('updated_at'), now)}d)",
                               oldest.get("html_url", url), 40 + min(20, len(stale) * 5),
                               "Merge, close, or comment. Stale PRs scare off contributors."))
        rels = _items(self.client.get(f"/repos/{repo}/releases", params={"per_page": 1}))
        if rels and not rels[0].get("draft"):
            try:
                ahead = int(self.client.get(f"/repos/{repo}/compare/{rels[0]['tag_name']}...{default}").get("ahead_by") or 0)
            except Exception:
                ahead = 0
            age = _days(rels[0].get("published_at"), now)
            if ahead >= 15 or (ahead >= 5 and age >= 30):
                out.append(Finding("release_due", repo, f"{ahead} commits unreleased since {rels[0]['tag_name']}",
                                   f"last release {age}d ago", url, 35 + min(20, ahead // 5),
                                   "Cut a release: users of the last tag are missing this work."))
        try:
            alerts = _items(self.client.get(f"/repos/{repo}/dependabot/alerts", params={"state": "open", "per_page": 30}))
            bad = [a for a in alerts if (a.get("security_advisory") or {}).get("severity") in ("high", "critical")]
            if bad:
                pkgs = sorted({str((a.get("dependency") or {}).get("package", {}).get("name")) for a in bad})
                out.append(Finding("security", repo, f"{len(bad)} high/critical Dependabot alert{'s' if len(bad) > 1 else ''}",
                                   f"packages: {', '.join(pkgs[:4])}", f"{url}/security/dependabot", 80,
                                   "Bump those dependencies; most have a patched version."))
        except Exception:
            pass  # Dependabot disabled / no access: not a finding
        return out


BRIEF_PROMPT = """You are Kestrel writing {name}'s {when} GitHub brief for Telegram. Local time {local}.
Below are FACTS from a deterministic sweep (JSON). Write the brief from them only; do not invent anything.
Style: like a sharp engineer friend. Open with a short casual greeting using their name ("{greeting} {name}"). Then the
2-3 things that matter most today with why and the action, then a compact list of the rest. Use **bold**, "- " bullets
and [label](url) links. Under 1800 characters. No closing filler. If nothing is urgent, say so happily in one line.

FACTS:
{facts}"""


def compose_brief(findings: list[Finding], name: str, local_now: datetime, greeting: str,
                  chat_fn: Any = None) -> str:
    """LLM-written brief from facts; deterministic fallback when the LLM is unavailable."""
    if chat_fn is not None and findings:
        try:
            prompt = BRIEF_PROMPT.format(name=name or "there", when="morning" if local_now.hour < 12 else "daily",
                                         local=local_now.strftime("%a %d %b %H:%M"), greeting=greeting,
                                         facts=json.dumps([asdict(f) for f in findings[:25]], ensure_ascii=False))
            text = str(chat_fn([{"role": "user", "content": prompt}], max_tokens=1200).content).strip()
            if text and len(text) < 3800:
                return text
        except Exception as exc:
            logger.warning("brief LLM failed (%s); sending deterministic brief", type(exc).__name__)
    if not findings:
        return f"{greeting} {name}! Clean board today: no red builds, nothing waiting on you. 🎉".replace("  ", " ")
    lines = [f"**{greeting} {name}, here's your GitHub brief**".replace("  ", " "), ""]
    top, rest = findings[:3], findings[3:]
    for f in top:
        lines.append(f"- **{f.repo}**: [{f.title}]({f.url}). {f.detail}\n  → {f.action}")
    if rest:
        lines.append("\n**Also**")
        lines += [f"- {f.repo}: [{f.title}]({f.url}) ({f.detail})" for f in rest[:10]]
    return "\n".join(lines)
