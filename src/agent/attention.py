"""Proactive attention engine.

Turns the GitHub notifications feed (the cheapest event stream GitHub offers:
incremental via ``since``, X-Poll-Interval ~60s) plus three targeted searches
into a small number of prioritised, deduplicated signals, each carrying a
concrete next action. Deterministic by design: scoring, grouping, dedup,
quiet hours and escalation are code, not model output.

Persistence (``data/state/agent_attention.json``) holds: last successful
check, delivered signal keys, muted threads/repos, and the pending digest
queue. A Telegram failure leaves signals queued; nothing is marked delivered
until delivery succeeds, so restarts neither drop nor duplicate alerts.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from src.core import state as state_store
from src.utils.logging import get_logger

logger = get_logger("agent.attention")

STATE_FILE = "agent_attention.json"

#: Base score per notification reason (0-100).
REASON_SCORE: dict[str, int] = {
    "security_alert": 90, "review_requested": 75, "approval_requested": 75,
    "mention": 70, "team_mention": 60, "assign": 65, "invitation": 60,
    "ci_activity": 45, "author": 50, "comment": 40, "state_change": 35,
    "manual": 30, "subscribed": 20, "member_feature_requested": 20,
}

NEXT_ACTION: dict[str, str] = {
    "security_alert": "Open the alert, check if the vulnerable path is reachable, and bump the dependency.",
    "review_requested": "Someone is blocked on your review — review or hand it off.",
    "approval_requested": "A deployment is waiting for your approval.",
    "mention": "You were mentioned — a reply is likely expected.",
    "team_mention": "Your team was mentioned — check whether you own the answer.",
    "assign": "You were assigned — acknowledge or reassign.",
    "invitation": "Accept or decline the repository invitation.",
    "author": "New activity on your thread — check whether it needs a response.",
    "comment": "New comment on a thread you follow.",
    "state_change": "Thread state changed (closed/merged/reopened).",
}

CI_TITLE = re.compile(r"^(?P<workflow>.+?) workflow run (?P<result>\w+) for (?P<branch>.+?) branch$")


@dataclass
class Signal:
    key: str            # dedup identity (includes updated-at / failure count band)
    group: str          # collapse identity (thread or repo+workflow+branch)
    repo: str
    kind: str           # reason or "ci_failure"
    title: str
    url: str
    score: int
    why: str
    next_action: str
    count: int = 1
    updated_at: str = ""
    thread_ids: list[str] = field(default_factory=list)
    owned: bool = False
    fork: bool = False

    @property
    def level(self) -> str:
        return "critical" if self.score >= 85 else "high" if self.score >= 65 else "normal" if self.score >= 40 else "low"

    def to_brief(self) -> dict[str, Any]:
        return {"repo": self.repo, "kind": self.kind, "title": self.title, "level": self.level,
                "occurrences": self.count, "why": self.why, "next_action": self.next_action, "url": self.url}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Signal:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def html_url(api_url: str | None, repo: str) -> str:
    """Map an API subject URL to its github.com page (no extra request)."""
    if not api_url:
        return f"https://github.com/{repo}"
    url = api_url.replace("https://api.github.com/repos/", "https://github.com/")
    url = re.sub(r"/pulls/(\d+)$", r"/pull/\1", url)
    url = re.sub(r"/commits/([0-9a-f]+)$", r"/commit/\1", url)
    return url if url.startswith("https://github.com/") else f"https://github.com/{repo}"


def signals_from_notifications(raw: list[dict[str, Any]], login: str) -> list[Signal]:
    out: list[Signal] = []
    for n in raw:
        if not isinstance(n, dict):
            continue
        repo = (n.get("repository") or {}).get("full_name", "")
        fork = bool((n.get("repository") or {}).get("fork"))
        subject = n.get("subject") or {}
        reason = n.get("reason", "subscribed")
        title = str(subject.get("title") or "")[:200]
        owned = bool(login) and repo.split("/")[0].lower() == login.lower()
        tid = str(n.get("id", ""))
        updated = str(n.get("updated_at", ""))
        m = CI_TITLE.match(title) if subject.get("type") == "CheckSuite" else None
        if m:
            if m["result"] != "failed":
                continue  # successes/cancellations are not attention-worthy
            workflow, branch = m["workflow"], m["branch"]
            out.append(Signal(
                key="", group=f"ci:{repo}:{workflow}:{branch}", repo=repo, kind="ci_failure",
                title=f"{workflow} failing on {branch}", url=f"https://github.com/{repo}/actions",
                score=55, why="", next_action="", updated_at=updated, thread_ids=[tid], owned=owned, fork=fork,
            ))
            continue
        score = REASON_SCORE.get(reason, 25) + (5 if owned else 0)
        if subject.get("type") == "RepositoryVulnerabilityAlert":
            score = max(score, 88)
        out.append(Signal(
            key=f"{tid}:{updated}", group=f"thread:{tid}", repo=repo, kind=reason,
            title=f"{subject.get('type', 'Thread')}: {title}", url=html_url(subject.get("url"), repo),
            score=min(score, 100), why=f"GitHub notified you ({reason.replace('_', ' ')}).",
            next_action=NEXT_ACTION.get(reason, "Take a look when convenient."), updated_at=updated, thread_ids=[tid],
        ))
    return out


def collapse(signals: list[Signal]) -> list[Signal]:
    """Merge signals sharing a group; repeated CI failures escalate."""
    merged: dict[str, Signal] = {}
    for s in signals:
        cur = merged.get(s.group)
        if cur is None:
            merged[s.group] = s
            continue
        cur.count += s.count
        cur.thread_ids += s.thread_ids
        cur.updated_at = max(cur.updated_at, s.updated_at)
        cur.score = max(cur.score, s.score)
    for s in merged.values():
        if s.kind == "ci_failure":
            escalate_ci(s)
    return list(merged.values())


def escalate_ci(s: Signal) -> None:
    """Score/explain a CI failure from its streak length (s.count)."""
    branch = s.group.rsplit(":", 1)[-1]
    default = branch in ("main", "master")
    # A fork's feature-branch CI mostly duplicates the upstream PR's checks: digest, not alert.
    s.score = min(100, 55 + (10 if s.owned else 0) + (15 if default else 0)
                  + min(20, 5 * (s.count - 1)) - (25 if s.fork and not default else 0))
    band = "1" if s.count == 1 else "2-4" if s.count < 5 else "5+"
    s.key = f"{s.group}:{band}"  # re-alert only when the failure streak escalates
    streak = f"Failed {s.count}x in a row" if s.count > 1 else "Failed"
    s.why = streak + (f" on {branch}, the default branch." if default
                      else " on a fork's feature branch (upstream PR checks are what count)." if s.fork
                      else f" on {branch}; this likely blocks its PR.")
    s.next_action = ("Open the latest failing run and fix or revert. Ask me to explain the failure."
                     if s.count < 3 else "Repeated failure, so this is not flaky. Ask me to diagnose the root cause.")


def waiting_on_me(client: Any, login: str) -> dict[str, Any]:
    """Three targeted searches: what is blocked on the operator right now."""
    if not login:
        return {"error": "operator login unknown"}

    def search(q: str) -> list[dict[str, Any]]:
        data = client.get("/search/issues", params={"q": q, "per_page": 10, "sort": "updated"})
        return [{"repo": (i.get("repository_url") or "").removeprefix("https://api.github.com/repos/"),
                 "number": i.get("number"), "title": str(i.get("title", ""))[:120], "url": i.get("html_url"),
                 "updated": str(i.get("updated_at", ""))[:10]} for i in (data or {}).get("items", [])]

    return {
        "review_requested_from_you": search(f"is:pr is:open review-requested:{login} archived:false"),
        "your_prs_with_changes_requested": search(f"is:pr is:open author:{login} review:changes_requested archived:false"),
        "your_prs_approved_not_merged": search(f"is:pr is:open author:{login} review:approved archived:false"),
    }


# ── persistence + policy ─────────────────────────────────────────


@dataclass
class AttentionPolicy:
    immediate_threshold: int = 65
    digest_min_score: int = 35
    digest_hours: float = 4.0
    quiet_start: str = "23:00"
    quiet_end: str = "07:00"
    utc_offset: str = "+00:00"
    max_catchup_days: int = 3

    def local_now(self, now: datetime) -> datetime:
        sign = -1 if self.utc_offset.startswith("-") else 1
        hh, mm = self.utc_offset.lstrip("+-").split(":")
        return now + sign * timedelta(hours=int(hh), minutes=int(mm))

    def in_quiet_hours(self, now: datetime) -> bool:
        if not self.quiet_start or not self.quiet_end:
            return False
        t = self.local_now(now).strftime("%H:%M")
        a, b = self.quiet_start, self.quiet_end
        return (a <= t < b) if a < b else (t >= a or t < b)


class AttentionStore:
    def __init__(self, filename: str = STATE_FILE) -> None:
        self.filename = filename
        data = state_store.load(filename)
        self.last_check: str = data.get("last_check", "")
        self.last_digest: str = data.get("last_digest", "")
        self.delivered: dict[str, str] = dict(data.get("delivered", {}))
        self.muted_threads: set[str] = set(data.get("muted_threads", []))
        self.muted_repos: set[str] = set(data.get("muted_repos", []))
        self.ci_streaks: dict[str, dict[str, Any]] = dict(data.get("ci_streaks", {}))
        self.queue: list[Signal] = [Signal.from_dict(s) for s in data.get("queue", [])]

    def save(self) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        self.delivered = {k: v for k, v in self.delivered.items() if v >= cutoff}
        state_store.save_atomic(self.filename, {
            "last_check": self.last_check, "last_digest": self.last_digest, "delivered": self.delivered,
            "muted_threads": sorted(self.muted_threads), "muted_repos": sorted(self.muted_repos),
            "queue": [asdict(s) for s in self.queue[-200:]],
            "ci_streaks": {k: v for k, v in self.ci_streaks.items() if v.get("last", "") >= cutoff},
        })

    def is_muted(self, s: Signal) -> bool:
        return s.repo in self.muted_repos or any(t in self.muted_threads for t in s.thread_ids)


def short_id(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:10]


@dataclass
class TickResult:
    immediate: list[Signal]
    digest: list[Signal]
    suppressed: int


class AttentionWatcher:
    """One ``tick()`` = fetch → signals → dedup/mute → route (now/digest/drop)."""

    def __init__(self, client: Any, login: str, policy: AttentionPolicy, store: AttentionStore | None = None) -> None:
        self.client, self.login, self.policy = client, login, policy
        self.store = store or AttentionStore()

    def tick(self, now: datetime | None = None, force_digest: bool = False) -> TickResult:
        now = now or datetime.now(timezone.utc)
        floor = now - timedelta(days=self.policy.max_catchup_days)
        since = self.store.last_check or floor.isoformat()
        since = max(since, floor.isoformat())  # recovery after downtime: bounded catch-up
        raw = self.client.get("/notifications", params={"all": "false", "per_page": 50,
                                                         "since": since[:19] + "Z"})
        signals = collapse(signals_from_notifications(raw if isinstance(raw, list) else [], self.login))
        week_ago = (now - timedelta(days=7)).isoformat()
        for s in signals:
            if s.kind != "ci_failure":
                continue
            prev = self.store.ci_streaks.get(s.group)
            if prev and prev["last"] >= week_ago:
                s.count += int(prev["count"])
            self.store.ci_streaks[s.group] = {"count": s.count, "last": now.isoformat()}
            escalate_ci(s)
        fresh = [s for s in signals if s.key not in self.store.delivered and not self.store.is_muted(s)]
        suppressed = len(signals) - len(fresh)
        quiet = self.policy.in_quiet_hours(now)
        immediate = [s for s in fresh if s.score >= 85 or (s.score >= self.policy.immediate_threshold and not quiet)]
        queued_keys = {q.key for q in self.store.queue}
        for s in fresh:  # immediates are queued too, so a failed send is retried, never lost
            if (s in immediate or s.score >= self.policy.digest_min_score) and s.key not in queued_keys:
                self.store.queue.append(s)
        self.store.last_check = now.isoformat()
        digest: list[Signal] = []
        due = not self.store.last_digest or (
            now - datetime.fromisoformat(self.store.last_digest) >= timedelta(hours=self.policy.digest_hours))
        if self.store.queue and (force_digest or (due and not quiet)):
            immediate_keys = {s.key for s in immediate}
            digest = [s for s in self.store.queue if s.key not in self.store.delivered
                      and s.key not in immediate_keys and not self.store.is_muted(s)]
        return TickResult(immediate, digest, suppressed)

    def mark_delivered(self, signals: list[Signal], now: datetime | None = None, digest: bool = False) -> None:
        stamp = (now or datetime.now(timezone.utc)).isoformat()
        keys = {s.key for s in signals}
        for k in keys:
            self.store.delivered[k] = stamp
        self.store.queue = [q for q in self.store.queue if q.key not in keys]
        if digest:
            self.store.last_digest = stamp
