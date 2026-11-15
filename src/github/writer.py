"""The ONLY GitHub write exit. Allowlisted operations, never a generic request.

``src/github/client.py`` stays GET-only. Writes exist solely to execute an
action the operator explicitly confirmed in Telegram (see
``src/agent/actions.py``). This module:

- exposes a fixed table of operations (method + path template), nothing else;
- validates every path component against strict GitHub name patterns, so no
  caller (or LLM-produced argument) can redirect a request elsewhere;
- sends exactly one attempt per write (no automatic retry, so a timeout can
  never create a duplicate issue or comment);
- never logs or returns the token, the URL with credentials, or raw exception text.
"""
from __future__ import annotations

import re
from typing import Any

import requests

from src.github.auth import AuthConfig, resolve_auth

API_BASE = "https://api.github.com"

REPO_RE = re.compile(r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$")
REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")
WORKFLOW_RE = re.compile(r"^([0-9]{1,20}|[A-Za-z0-9._-]{1,100}\.ya?ml)$")

#: operation -> (HTTP method, path template). The complete write surface.
OPERATIONS: dict[str, tuple[str, str]] = {
    "create_issue": ("POST", "/repos/{repo}/issues"),
    "comment": ("POST", "/repos/{repo}/issues/{number}/comments"),
    "add_labels": ("POST", "/repos/{repo}/issues/{number}/labels"),
    "set_issue_state": ("PATCH", "/repos/{repo}/issues/{number}"),
    "rerun_failed_jobs": ("POST", "/repos/{repo}/actions/runs/{run_id}/rerun-failed-jobs"),
    "dispatch_workflow": ("POST", "/repos/{repo}/actions/workflows/{workflow}/dispatches"),
    "update_repo": ("PATCH", "/repos/{repo}"),
    "replace_topics": ("PUT", "/repos/{repo}/topics"),
    "create_pull_request": ("POST", "/repos/{repo}/pulls"),
}


class WriteError(Exception):
    """A write was refused or failed. Message is safe to show the operator."""


def _check_path_args(args: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in args.items():
        text = str(value)
        if key == "repo" and not REPO_RE.match(text):
            raise WriteError(f"invalid repository name: {text[:60]!r}")
        if key in ("number", "run_id") and not text.isdigit():
            raise WriteError(f"invalid {key}: {text[:20]!r}")
        if key == "workflow" and not WORKFLOW_RE.match(text):
            raise WriteError(f"invalid workflow id: {text[:60]!r}")
        out[key] = text
    return out


class GitHubWriter:
    def __init__(
        self,
        auth: AuthConfig | None = None,
        session: Any = None,
        timeout: float = 20.0,
    ) -> None:
        self._auth = auth or resolve_auth()
        self._session = session if session is not None else requests.Session()
        self._timeout = timeout

    def perform(self, operation: str, path_args: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
        """Execute one allowlisted write. Returns the parsed JSON (or {})."""
        if operation not in OPERATIONS:
            raise WriteError(f"operation not allowed: {operation}")
        if not self._auth.has_token:
            raise WriteError("no GitHub token configured")
        method, template = OPERATIONS[operation]
        try:
            path = template.format(**_check_path_args(path_args))
        except KeyError as exc:
            raise WriteError(f"missing argument: {exc.args[0]}") from None
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "gh-ops/1.0",
            **self._auth.auth_headers(),
        }
        try:
            response = self._session.request(
                method, f"{API_BASE}{path}", json=body, headers=headers, timeout=self._timeout
            )
        except requests.exceptions.RequestException as exc:
            # Outcome unknown on timeout: say so rather than retrying blindly.
            raise WriteError(f"request failed ({type(exc).__name__}); verify on GitHub before retrying") from None
        if response.status_code >= 400:
            try:
                message = str(response.json().get("message", ""))[:200]
            except ValueError:
                message = ""
            raise WriteError(f"GitHub returned {response.status_code}{': ' + message if message else ''}")
        if response.status_code == 204 or not response.content:
            return {}
        try:
            data = response.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {"items": data}
