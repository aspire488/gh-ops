"""Permission-controlled write actions.

Flow: the model may only *propose* (tools.propose_action) → a PendingAction is
stored and shown with Confirm/Cancel buttons → only the same authorized
Telegram user pressing Confirm, within the expiry window, executes it once
through the allowlisted GitHubWriter. Every proposal and outcome is appended
to an audit log. No conversational text ever counts as confirmation.
"""
from __future__ import annotations

import json
import secrets
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.core import state as state_store
from src.github.writer import REF_RE, GitHubWriter, WriteError
from src.utils.logging import get_logger

logger = get_logger("agent.actions")

STATE_FILE = "agent_actions.json"
AUDIT_FILE = "agent_audit.jsonl"
EXPIRY_S = 15 * 60
MAX_BODY = 20_000


class ActionError(Exception):
    """Proposal rejected; message is shown to the model/operator."""


def _text(params: dict[str, Any], key: str, required: bool = True, limit: int = 256) -> str:
    value = params.get(key)
    if value is None or value == "":
        if required:
            raise ActionError(f"missing {key}")
        return ""
    if not isinstance(value, str) or len(value) > limit:
        raise ActionError(f"{key} must be text up to {limit} chars")
    return value


def _number(params: dict[str, Any], key: str) -> int:
    value = params.get(key)
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ActionError(f"{key} must be a positive integer")
    return value


def _str_list(params: dict[str, Any], key: str, limit: int = 20) -> list[str]:
    value = params.get(key) or []
    if not isinstance(value, list) or not all(isinstance(v, str) and 0 < len(v) <= 50 for v in value) or len(value) > limit:
        raise ActionError(f"{key} must be a list of up to {limit} short strings")
    return value


def build_request(operation: str, repo: str, params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate params for an operation → (path_args, json_body)."""
    p = params
    if operation == "create_issue":
        body: dict[str, Any] = {"title": _text(p, "title"), "body": _text(p, "body", False, MAX_BODY)}
        if p.get("labels"):
            body["labels"] = _str_list(p, "labels")
        return {"repo": repo}, body
    if operation == "comment":
        return {"repo": repo, "number": _number(p, "number")}, {"body": _text(p, "body", True, MAX_BODY)}
    if operation == "add_labels":
        return {"repo": repo, "number": _number(p, "number")}, {"labels": _str_list(p, "labels")}
    if operation == "set_issue_state":
        state = _text(p, "state")
        if state not in ("open", "closed"):
            raise ActionError("state must be open or closed")
        return {"repo": repo, "number": _number(p, "number")}, {"state": state}
    if operation == "rerun_failed_jobs":
        return {"repo": repo, "run_id": _number(p, "run_id")}, {}
    if operation == "dispatch_workflow":
        ref = _text(p, "ref")
        if not REF_RE.match(ref):
            raise ActionError("invalid ref")
        inputs = p.get("inputs") or {}
        if not isinstance(inputs, dict) or not all(isinstance(v, str) for v in inputs.values()):
            raise ActionError("inputs must map names to strings")
        return {"repo": repo, "workflow": _text(p, "workflow", True, 100)}, {"ref": ref, "inputs": inputs}
    if operation == "update_repo":
        body = {k: _text(p, k, False, 350) for k in ("description", "homepage") if k in p}
        if not body:
            raise ActionError("nothing to update (description/homepage)")
        return {"repo": repo}, body
    if operation == "replace_topics":
        return {"repo": repo}, {"names": [t.lower() for t in _str_list(p, "topics")]}
    if operation == "create_pull_request":
        for key in ("head", "base"):
            if not REF_RE.match(_text(p, key)):
                raise ActionError(f"invalid {key}")
        return {"repo": repo}, {"title": _text(p, "title"), "head": p["head"], "base": p["base"],
                                "body": _text(p, "body", False, MAX_BODY)}
    raise ActionError(f"unsupported operation {operation}")


@dataclass
class PendingAction:
    id: str
    operation: str
    repo: str
    path_args: dict[str, Any]
    body: dict[str, Any]
    summary: str
    conversation_key: str
    user_id: int
    created: float = field(default_factory=time.time)
    status: str = "pending"  # pending|executing|done|failed|cancelled|expired
    message_id: int = 0

    def preview(self) -> str:
        target = self.repo + (f"#{self.path_args['number']}" if "number" in self.path_args else "")
        lines = [f"{self.operation.replace('_', ' ')} → {target}", self.summary]
        for key, value in self.body.items():
            shown = value if not isinstance(value, str) or len(value) < 400 else value[:400] + "…"
            lines.append(f"{key}: {json.dumps(shown, ensure_ascii=False) if not isinstance(shown, str) else shown}")
        return "\n".join(lines)


class ActionQueue:
    def __init__(self, writer: GitHubWriter | None = None, can_write: Any = None,
                 filename: str = STATE_FILE, audit_path: Path | None = None) -> None:
        self._writer = writer
        self._can_write = can_write  # callable(repo) -> (bool, reason)
        self.filename = filename
        self.audit_path = audit_path or (state_store._state_dir() / AUDIT_FILE)
        raw = state_store.load(filename).get("actions", {})
        self.actions: dict[str, PendingAction] = {k: PendingAction(**v) for k, v in raw.items()}

    def _save(self) -> None:
        cutoff = time.time() - 7 * 86400
        self.actions = {k: v for k, v in self.actions.items() if v.created >= cutoff}
        state_store.save_atomic(self.filename, {"actions": {k: asdict(v) for k, v in self.actions.items()}})

    def _audit(self, action: PendingAction, event: str, detail: str = "") -> None:
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        record = {"at": datetime.now(timezone.utc).isoformat(), "event": event, "id": action.id,
                  "operation": action.operation, "repo": action.repo, "path_args": action.path_args,
                  "user_id": action.user_id, "summary": action.summary, "detail": detail[:300]}
        with open(self.audit_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def propose(self, *, operation: str, repo: str, params: dict[str, Any], summary: str,
                conversation_key: str, user_id: int) -> PendingAction:
        path_args, body = build_request(operation, repo, params)
        if self._can_write is not None:
            ok, reason = self._can_write(repo)
            if not ok:
                raise ActionError(reason)
        action = PendingAction(secrets.token_hex(4), operation, repo, path_args, body, summary[:200],
                               conversation_key, user_id)
        self.actions[action.id] = action
        self._audit(action, "proposed")
        self._save()
        return action

    def pending_for(self, conversation_key: str) -> list[PendingAction]:
        return [a for a in self.actions.values() if a.conversation_key == conversation_key
                and a.status == "pending" and a.message_id == 0]

    def attach_message(self, action_id: str, message_id: int) -> None:
        if action_id in self.actions:
            self.actions[action_id].message_id = message_id
            self._save()

    def resolve(self, action_id: str, user_id: int, confirm: bool) -> tuple[bool, str]:
        """Confirm or cancel. Returns (succeeded, operator-facing message)."""
        action = self.actions.get(action_id)
        if action is None:
            return False, "That action no longer exists."
        if user_id != action.user_id:
            return False, "Only the person who requested this action can confirm it."
        if action.status != "pending":
            return False, f"Already {action.status}."
        if time.time() - action.created > EXPIRY_S:
            action.status = "expired"
            self._audit(action, "expired")
            self._save()
            return False, "This confirmation expired. Ask again if you still want it."
        if not confirm:
            action.status = "cancelled"
            self._audit(action, "cancelled")
            self._save()
            return True, "Cancelled. Nothing was changed."
        action.status = "executing"  # persisted first: a crash can never replay the write
        self._save()
        self._audit(action, "confirmed")
        try:
            if self._writer is None:
                raise WriteError("write actions are disabled")
            result = self._writer.perform(action.operation, action.path_args, action.body)
        except WriteError as exc:
            action.status = "failed"
            self._audit(action, "failed", str(exc))
            self._save()
            return False, f"Failed: {exc}"
        action.status = "done"
        url = result.get("html_url") or ""
        self._audit(action, "executed", url)
        self._save()
        return True, "Done." + (f" {url}" if url else "")
