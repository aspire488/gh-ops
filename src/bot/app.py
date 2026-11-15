"""Kestrel Telegram bot: conversation + proactive attention, one process.

Loop: getUpdates (long poll) → handle each update → between polls, run the
attention watcher when due. Sequential by design: one operator, bounded work
per update, and no shared-state races between conversation and watcher.

Reliability:
- The update offset is persisted BEFORE an update is handled, so a crash on a
  poison message (or mid-write) never replays it.
- Watcher and agent failures are caught per unit of work; the loop survives
  GitHub, LLM and Telegram outages with capped backoff.
- Only allow-listed Telegram users in allow-listed chats get any response.
"""
from __future__ import annotations

import contextlib
import os
import signal
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import IO, Any

from src.agent import voice
from src.agent.actions import ActionQueue
from src.agent.agent import PERSONA, Agent
from src.agent.attention import AttentionPolicy, AttentionWatcher, Signal, short_id, waiting_on_me
from src.agent.memory import ConversationStore, conversation_key
from src.agent.render import github_links, render_messages, strip_html, to_html
from src.agent.sweep import Sweeper, compose_brief, diagnose_ci, diagnosis_lines
from src.agent.tools import ToolContext, ToolRegistry
from src.core import state as state_store
from src.intelligence import llm
from src.notifications.telegram import TelegramError
from src.utils.logging import get_logger

logger = get_logger("bot")

STATE_FILE = "agent_bot.json"
LOCK_FILE = "kestrel.lock"
POLL_TIMEOUT_S = 25


def acquire_instance_lock() -> IO[str] | None:
    """Exclusive OS lock held for the process lifetime (freed even on a crash).

    None means another Kestrel is already polling: two pollers on one token
    split updates between them and double-send proactive alerts.
    """
    path = state_store._state_dir() / LOCK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle

HELP = f"""Hey! I'm **{PERSONA}**, your GitHub sidekick. 🦅

Just talk to me like you'd talk to a teammate. Stuff like:
- "What needs my attention today?"
- "Why is CI failing on Kio?"
- "Is my icalendar PR ready to merge?"
- "Compare star growth across my repos this month"
- "Find me a Python issue worth contributing to"
- "What did GitHub announce this week?"

I also keep an eye on your notifications and ping you when something actually needs you. Quiet hours and digests keep the noise down.

/attention: what needs you right now
/brief: full sweep of your repos now
/status: system health
/reset: start a fresh conversation
/unmute: clear all mutes"""


@dataclass
class BotConfig:
    allowed_users: set[int] = field(default_factory=set)
    allowed_chats: set[int] = field(default_factory=set)
    max_messages: int = 40
    max_chars: int = 30_000
    ttl_hours: float = 12.0
    writes_enabled: bool = True
    allowed_owners: set[str] = field(default_factory=set)
    proactive_enabled: bool = True
    poll_seconds: int = 300
    waiting_on_me_in_digest: bool = True
    name: str = ""
    daily_brief_at: str = "09:00"  # local time (policy.utc_offset); "" disables
    runner: str = "daemon"         # who sends proactive messages: "daemon" (python -m src.bot) or "actions"
    policy: AttentionPolicy = field(default_factory=AttentionPolicy)

    @classmethod
    def from_config(cls, config: Any) -> BotConfig:
        def get(*keys: str, default: Any = None) -> Any:
            return config.get(*keys, default=default) if config is not None else default

        telegram_chats = [int(c["chat_id"]) for c in get("telegram", "telegram", "chat_ids", default=[]) or []
                          if isinstance(c, dict) and str(c.get("chat_id", "")).lstrip("-").isdigit()]
        users = {int(u) for u in get("agent", "agent", "allowed_user_ids", default=[]) or []}
        chats = {int(c) for c in get("agent", "agent", "allowed_chat_ids", default=[]) or []}
        q = get("agent", "proactive", "quiet_hours", default={}) or {}
        return cls(
            allowed_users=users or {c for c in telegram_chats if c > 0},  # private chat id == user id
            allowed_chats=chats or set(telegram_chats),
            max_messages=int(get("agent", "agent", "conversation", "max_messages", default=40)),
            max_chars=int(get("agent", "agent", "conversation", "max_chars", default=30000)),
            ttl_hours=float(get("agent", "agent", "conversation", "ttl_hours", default=12)),
            writes_enabled=bool(get("agent", "agent", "write_actions", "enabled", default=True)),
            allowed_owners={o.lower() for o in get("agent", "agent", "write_actions", "allowed_owners", default=[]) or []},
            proactive_enabled=bool(get("agent", "proactive", "enabled", default=True)),
            poll_seconds=max(60, int(get("agent", "proactive", "poll_seconds", default=300))),
            waiting_on_me_in_digest=bool(get("agent", "proactive", "waiting_on_me_in_digest", default=True)),
            name=str(get("agent", "agent", "operator_name", default="") or ""),
            daily_brief_at=str(get("agent", "proactive", "daily_brief_at", default="09:00") or ""),
            runner=str(get("agent", "proactive", "runner", default="daemon") or "daemon").lower(),
            policy=AttentionPolicy(
                immediate_threshold=int(get("agent", "proactive", "immediate_threshold", default=65)),
                digest_min_score=int(get("agent", "proactive", "digest_min_score", default=35)),
                digest_hours=float(get("agent", "proactive", "digest_hours", default=4)),
                quiet_start=str(q.get("start", "23:00")), quiet_end=str(q.get("end", "07:00")),
                utc_offset=str(q.get("utc_offset", "+00:00")),
                max_catchup_days=int(get("agent", "proactive", "max_catchup_days", default=3)),
            ),
        )


def _keyboard(rows: list[list[dict[str, str]]]) -> dict[str, Any] | None:
    rows = [r for r in rows if r]
    return {"inline_keyboard": rows} if rows else None


def _link_rows(links: list[tuple[str, str]]) -> list[list[dict[str, str]]]:
    buttons = [{"text": label, "url": url} for label, url in links]
    return [buttons[i:i + 2] for i in range(0, len(buttons), 2)]


class KestrelBot:
    def __init__(self, *, transport: Any, poll_transport: Any, client: Any, cfg: BotConfig,
                 agent: Agent, tools: ToolRegistry, store: ConversationStore, actions: ActionQueue,
                 watcher: AttentionWatcher | None, login: str, clock: Any = time.time,
                 repos: list[str] | None = None, brief_llm: Any = None, interactive: bool = True) -> None:
        self.tg, self.poll_tg, self.client, self.cfg = transport, poll_transport, client, cfg
        self.agent, self.tools, self.store, self.actions, self.watcher = agent, tools, store, actions, watcher
        self.login = login
        self.clock = clock
        self.repos = repos or []
        self.brief_llm = brief_llm  # chat_fn for writing the daily brief; None = deterministic brief
        self.interactive = interactive  # False in Actions: no process is there to receive button presses
        saved = state_store.load(STATE_FILE)
        self.offset: int = int(saved.get("offset", 0))
        self.mute_tokens: dict[str, dict[str, str]] = dict(saved.get("mute_tokens", {}))
        self.surfaced: set[str] = set(saved.get("surfaced", []))
        self.last_brief: str = str(saved.get("last_brief", ""))
        self.star_memory: dict[str, int] = dict(saved.get("star_memory", {}))
        self.next_watch = 0.0
        self.watch_backoff = 0.0
        self.watch_paused_until = 0.0
        self.stopping = False

    # ── persistence ──────────────────────────────────────────────

    def _save(self) -> None:
        state_store.save_atomic(STATE_FILE, {"offset": self.offset, "mute_tokens": dict(list(self.mute_tokens.items())[-300:]),
                                             "surfaced": sorted(self.surfaced)[-500:], "last_brief": self.last_brief,
                                             "star_memory": self.star_memory})

    # ── telegram helpers ─────────────────────────────────────────

    def send(self, chat_id: int, html_text: str, markup: dict[str, Any] | None = None,
             reply_to: int | None = None) -> int:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": html_text, "parse_mode": "HTML",
                                   "link_preview_options": {"is_disabled": True}}
        if markup:
            payload["reply_markup"] = markup
        if reply_to:
            payload["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        try:
            body = self.tg.call("sendMessage", payload, chat_id=str(chat_id))
        except TelegramError as exc:
            if getattr(exc, "status_code", 0) != 400:
                raise
            payload.pop("parse_mode")  # formatting rejected: degrade to plain text, never lose the answer
            payload["text"] = strip_html(html_text)
            body = self.tg.call("sendMessage", payload, chat_id=str(chat_id))
        return int((body.get("result") or {}).get("message_id", 0))

    def edit(self, chat_id: int, message_id: int, html_text: str, markup: dict[str, Any] | None = None,
             plain: bool = False) -> bool:
        payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": html_text,
                                   "link_preview_options": {"is_disabled": True}}
        if not plain:
            payload["parse_mode"] = "HTML"
        if markup:
            payload["reply_markup"] = markup
        try:
            self.tg.call("editMessageText", payload, chat_id=str(chat_id))
            return True
        except TelegramError as exc:
            return "not modified" in str(exc).lower()

    def send_markdown(self, chat_id: int, md: str, *, links: bool = True, edit_id: int = 0,
                      extra_rows: list[list[dict[str, str]]] | None = None) -> None:
        chunks = render_messages(md) or ["(empty answer)"]
        rows = (_link_rows(github_links(md)) if links else []) + (extra_rows or [])
        markup = _keyboard(rows)
        for i, chunk in enumerate(chunks):
            last = i == len(chunks) - 1
            if i == 0 and edit_id and self.edit(chat_id, edit_id, chunk, markup if last else None):
                continue
            self.send(chat_id, chunk, markup if last else None)

    # ── main loop ────────────────────────────────────────────────

    def run(self) -> None:
        signal.signal(signal.SIGINT, self._stop)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, self._stop)
        logger.info("%s started; %d authorized user(s), proactive=%s", PERSONA, len(self.cfg.allowed_users),
                    self.cfg.proactive_enabled and self.watcher is not None)
        failures = 0
        while not self.stopping:
            try:
                self.poll_once()
                failures = 0
            except TelegramError as exc:
                failures += 1
                wait = 30 if getattr(exc, "status_code", 0) == 409 else min(60, 2 ** failures)
                logger.warning("getUpdates failed (%s, HTTP %s); retry in %ss", type(exc).__name__,
                               getattr(exc, "status_code", 0), wait)
                self._sleep(wait)
            except Exception as exc:  # e.g. state write failed: never let one bad poll kill the bot
                failures += 1
                logger.error("poll cycle failed (%s); retry in %ss", type(exc).__name__, min(60, 2 ** failures))
                self._sleep(min(60, 2 ** failures))
            if self.clock() < self.watch_paused_until:
                continue
            try:
                self.maybe_watch()
            except Exception as exc:  # proactive bugs must not take conversation down with them
                self.watch_paused_until = self.clock() + self.cfg.poll_seconds
                logger.error("proactive check failed (%s); paused %ss, chat unaffected",
                             type(exc).__name__, self.cfg.poll_seconds)
        self._save()
        logger.info("%s stopped cleanly", PERSONA)

    def _stop(self, *_: Any) -> None:
        self.stopping = True

    def _sleep(self, seconds: float) -> None:
        end = time.time() + seconds
        while not self.stopping and time.time() < end:
            time.sleep(min(1.0, end - time.time()))

    def poll_once(self) -> None:
        body = self.poll_tg.call("getUpdates", {"offset": self.offset, "timeout": POLL_TIMEOUT_S,
                                                "allowed_updates": ["message", "callback_query"]})
        for update in body.get("result") or []:
            self.offset = int(update.get("update_id", 0)) + 1
            self._save()  # before handling: never replay an update after a crash
            try:
                self.handle_update(update)
            except Exception as exc:
                logger.error("update handling failed (%s)", type(exc).__name__)
                chat = ((update.get("message") or {}).get("chat") or {}).get("id")
                if chat and self._authorized(update.get("message") or {}):
                    with contextlib.suppress(Exception):
                        self.send(int(chat), voice.say("broke", self.cfg.name, err=type(exc).__name__))

    # ── authorization ────────────────────────────────────────────

    def _authorized(self, item: dict[str, Any]) -> bool:
        user = (item.get("from") or {}).get("id")
        chat = ((item.get("message") or item).get("chat") or {}).get("id")
        return user in self.cfg.allowed_users and chat in self.cfg.allowed_chats

    # ── updates ──────────────────────────────────────────────────

    def handle_update(self, update: dict[str, Any]) -> None:
        if "callback_query" in update:
            return self.handle_callback(update["callback_query"])
        msg = update.get("message") or {}
        if not msg.get("text"):
            return None
        if not self._authorized(msg):
            logger.warning("ignored message from unauthorized user=%s chat=%s",
                           (msg.get("from") or {}).get("id"), (msg.get("chat") or {}).get("id"))
            return None
        chat_id, user_id = int(msg["chat"]["id"]), int(msg["from"]["id"])
        text = msg["text"].strip()
        command = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
        key = conversation_key(chat_id, user_id)
        if command == "/start":
            return self.send_markdown(chat_id, self.welcome(), links=False)
        if command == "/help":
            return self.send_markdown(chat_id, HELP, links=False)
        if command == "/reset":
            self.store.reset(key)
            self.send(chat_id, voice.say("reset", self.cfg.name))
            return None
        if command == "/status":
            return self.send_markdown(chat_id, self.status_text(key), links=False)
        if command == "/unmute":
            if self.watcher:
                self.watcher.store.muted_repos.clear()
                self.watcher.store.muted_threads.clear()
                self.watcher.store.save()
            self.send(chat_id, voice.say("unmute", self.cfg.name))
            return None
        if command == "/brief":
            return self.send_brief(only_chat=chat_id)
        if command in ("/attention", "/digest"):
            return self.proactive_tick(force_digest=True, only_chat=chat_id, explicit=True)
        return self.converse(chat_id, user_id, text, reply_to=int(msg.get("message_id", 0)))

    def converse(self, chat_id: int, user_id: int, text: str, reply_to: int = 0) -> None:
        key = conversation_key(chat_id, user_id)
        with contextlib.suppress(TelegramError):
            self.tg.call("sendChatAction", {"chat_id": chat_id, "action": "typing"})
        progress_id = self.send(chat_id, "<i>" + voice.say("thinking", self.cfg.name) + "</i>", reply_to=reply_to or None)
        last_edit: dict[str, Any] = {"at": 0.0, "label": ""}

        def on_progress(label: str) -> None:
            if label != last_edit["label"] and time.time() - last_edit["at"] > 1.5:
                last_edit.update(at=time.time(), label=label)
                self.edit(chat_id, progress_id, f"<i>{label}…</i>")

        ctx = ToolContext(client=self.client, login=self.login, actions=self.actions if self.cfg.writes_enabled else None,
                          conversation_key=key, user_id=user_id, surfaced=self.surfaced)
        reply = self.agent.respond(key, text, ctx, on_progress=on_progress)
        self.send_markdown(chat_id, reply.text, edit_id=progress_id)
        for action in self.actions.pending_for(key):
            markup = _keyboard([[{"text": "✅ Confirm", "callback_data": f"a:c:{action.id}"},
                                 {"text": "Cancel", "callback_data": f"a:x:{action.id}"}]])
            mid = self.send(chat_id, f"<b>Want me to do this, {self.cfg.name or 'boss'}?</b>\n<pre>{to_html_escape(action.preview())}</pre>\n"
                                     "<i>Nothing happens until you press Confirm. Expires in 15 min.</i>", markup)
            self.actions.attach_message(action.id, mid)
        self._save()

    def handle_callback(self, cb: dict[str, Any]) -> None:
        user_id = int((cb.get("from") or {}).get("id", 0))
        msg = cb.get("message") or {}
        chat_id = int((msg.get("chat") or {}).get("id", 0))
        data = str(cb.get("data") or "")

        def answer(text: str) -> None:
            with contextlib.suppress(TelegramError):
                self.tg.call("answerCallbackQuery", {"callback_query_id": cb.get("id"), "text": text[:190]})

        if user_id not in self.cfg.allowed_users or chat_id not in self.cfg.allowed_chats:
            logger.warning("ignored callback from unauthorized user=%s", user_id)
            return answer("Not authorized.")
        kind, _, rest = data.partition(":")
        if kind == "a":
            verb, _, action_id = rest.partition(":")
            action = self.actions.actions.get(action_id)
            was_pending = action is not None and action.status == "pending"
            ok, outcome = self.actions.resolve(action_id, user_id, confirm=verb == "c")
            answer(outcome)
            if action is not None and was_pending and action.status != "pending":  # repeat presses: toast only
                mark = "✅" if ok and verb == "c" else "✖️" if verb == "x" else "⚠️"
                self.edit(chat_id, int(msg.get("message_id", 0)),
                          f"<pre>{to_html_escape(action.preview())}</pre>\n{mark} {to_html(outcome)}")
                self.store.append(action.conversation_key,
                                  [{"role": "user", "content": f"[button] {'confirm' if verb == 'c' else 'cancel'} action {action_id}"},
                                   {"role": "assistant", "content": f"Action {action.operation} on {action.repo}: {outcome}"}])
            return None
        if kind == "m" and self.watcher:
            target = self.mute_tokens.get(rest)
            if not target:
                return answer("That mute button expired.")
            if target["type"] == "repo":
                self.watcher.store.muted_repos.add(target["value"])
            else:
                self.watcher.store.muted_threads.update(target["value"].split(","))
            self.watcher.store.save()
            return answer(f"Muted {target['label']}. /unmute to undo.")
        return answer("Unknown button.")

    def welcome(self) -> str:
        hi = f"Hey {self.cfg.name}!" if self.cfg.name else "Hey there!"
        return chr(10).join([
            f"{hi} I'm **{PERSONA}** 🦅, your GitHub sidekick.",
            "",
            "I know your repos, PRs, CI and notifications, and I can dig into pretty much anything on GitHub "
            "for you. Just message me like you'd message a teammate, no special syntax.",
            "",
            "**Try one of these:**",
            "- What needs my attention?",
            "- How are my repos doing this month?",
            "- Why is CI failing on my latest PR?",
            "",
            "I'll also ping you myself when something actually needs you, and stay quiet when it doesn't. "
            "Anything that changes GitHub always waits for your Confirm tap.",
            "",
            "/help for more · /attention for a quick check right now",
        ])

    # ── status ───────────────────────────────────────────────────

    def status_text(self, key: str) -> str:
        lines = [f"**{PERSONA} status**"]
        try:
            core = self.client.get("/rate_limit").get("resources", {}).get("core", {})
            lines.append(f"- GitHub: connected as `{self.login or '?'}`, {core.get('remaining')}/{core.get('limit')} requests left")
        except Exception as exc:
            lines.append(f"- GitHub: **unreachable** ({type(exc).__name__})")
        for name, p in llm.provider_status().items():
            state = "not configured" if not p["configured"] else "healthy" if p["healthy"] else f"cooling down ({p['last_error']})"
            lines.append(f"- {name} `{p['model']}`: {state}, {p['tokens_used']} tokens this session")
        if self.watcher:
            st = self.watcher.store
            quiet = self.cfg.policy.in_quiet_hours(datetime.now(timezone.utc))
            last = (self.cfg.policy.local_now(datetime.fromisoformat(st.last_check)).strftime("%d %b %H:%M")
                    if st.last_check else "never")
            lines.append(f"- Watcher: last check {last} (local), "
                         f"{len(st.queue)} queued for digest, {len(st.muted_repos)} muted repos"
                         + (", quiet hours now" if quiet else ""))
        else:
            lines.append("- Watcher: disabled")
        lines.append(f"- This conversation: {len(self.store.history(key))} messages remembered, {self.tools.calls} tool calls since start")
        return "\n".join(lines)

    # ── proactive ────────────────────────────────────────────────

    def maybe_watch(self) -> None:
        if not (self.cfg.proactive_enabled and self.watcher) or self.cfg.runner != "daemon":
            return  # runner == "actions": the scheduled workflow owns proactivity (no duplicates)
        if self.brief_due(datetime.now(timezone.utc)):
            self.send_brief()
        if self.clock() >= self.next_watch:
            self.proactive_tick()

    # ── proactive: auto-investigation and daily brief ────────────

    def investigate(self, signals: list[Signal]) -> dict[str, list[str]]:
        """Before alerting, diagnose the top CI failures (failing job, step, error lines)."""
        out: dict[str, list[str]] = {}
        ci = [s for s in sorted(signals, key=lambda s: s.score, reverse=True) if s.kind == "ci_failure" and not s.fork]
        for sig in ci[:2]:
            body = sig.group.split(":", 1)[1]           # "<owner/repo>:<workflow>:<branch>"
            repo, _, rest = body.partition(":")
            workflow, _, branch = rest.rpartition(":")
            try:
                diag = diagnose_ci(self.client, repo, branch, workflow)
            except Exception as exc:
                logger.warning("auto-diagnosis failed for %s (%s)", repo, type(exc).__name__)
                continue
            lines = diagnosis_lines(diag)
            if lines:
                out[sig.group] = lines
                if diag and diag.get("url"):
                    sig.url = diag["url"]  # button opens the failing run, not the generic Actions tab
        return out

    def brief_due(self, now: datetime) -> bool:
        if not self.cfg.daily_brief_at:
            return False
        local = self.cfg.policy.local_now(now)
        return local.strftime("%H:%M") >= self.cfg.daily_brief_at and self.last_brief != local.strftime("%Y-%m-%d")

    def send_brief(self, only_chat: int | None = None) -> None:
        now = datetime.now(timezone.utc)
        local = self.cfg.policy.local_now(now)
        try:
            findings = Sweeper(self.client, self.login, self.repos, self.star_memory).run(now)
        except Exception as exc:
            logger.warning("sweep failed (%s)", type(exc).__name__)
            if only_chat:
                self.send(only_chat, voice.say("watch_failed", self.cfg.name, err=type(exc).__name__))
            return
        text = compose_brief(findings, self.cfg.name, local, voice.greeting(local), chat_fn=self.brief_llm)
        chats = [only_chat] if only_chat else sorted(self.cfg.allowed_chats)
        try:
            for chat in chats:
                self.send_markdown(chat, text)
        except TelegramError as exc:
            logger.warning("brief delivery failed (%s)", type(exc).__name__)
            return  # not marked sent: retried on the next loop
        if not only_chat:
            self.last_brief = local.strftime("%Y-%m-%d")
        self._save()

    def proactive_tick(self, force_digest: bool = False, only_chat: int | None = None, explicit: bool = False) -> None:
        if not self.watcher:
            if only_chat:
                self.send(only_chat, "My watcher is switched off (config/agent.yml → proactive.enabled).")
            return
        chats = [only_chat] if only_chat else sorted(self.cfg.allowed_chats)
        try:
            result = self.watcher.tick(force_digest=force_digest)
            waiting = waiting_on_me(self.client, self.login) if (result.digest or explicit) and self.cfg.waiting_on_me_in_digest else {}
        except Exception as exc:
            self.watch_backoff = min(3600.0, max(self.cfg.poll_seconds, self.watch_backoff * 2 or self.cfg.poll_seconds))
            self.next_watch = self.clock() + self.watch_backoff
            logger.warning("watcher tick failed (%s); next attempt in %.0fs", type(exc).__name__, self.watch_backoff)
            if only_chat:
                self.send(only_chat, voice.say("watch_failed", self.cfg.name, err=type(exc).__name__))
            return
        self.watch_backoff = 0.0
        self.next_watch = self.clock() + self.cfg.poll_seconds
        try:
            if result.immediate:
                md, rows = self.compose_alert(result.immediate, self.investigate(result.immediate))
                for chat in chats:
                    self.send_markdown(chat, md, links=False, extra_rows=rows)
                self.watcher.mark_delivered(result.immediate)
            if result.digest or explicit:
                md, rows = self.compose_digest(result.digest, waiting, result.suppressed, explicit)
                if md:
                    for chat in chats:
                        self.send_markdown(chat, md, links=False, extra_rows=rows)
                self.watcher.mark_delivered(result.digest, digest=True)
        except TelegramError as exc:
            # Undelivered signals stay queued; they go out on the next successful tick.
            logger.warning("proactive delivery failed (%s)", type(exc).__name__)
        finally:
            self.watcher.store.save()
            self._save()

    def _mute_button(self, label: str, kind: str, value: str) -> dict[str, str]:
        token = short_id(f"{kind}:{value}")
        self.mute_tokens[token] = {"type": kind, "value": value, "label": label}
        return {"text": f"🔕 Mute {label}"[:40], "callback_data": f"m:{token}"}

    def compose_alert(self, signals: list[Signal],
                      diagnoses: dict[str, list[str]] | None = None) -> tuple[str, list[list[dict[str, str]]]]:
        signals = sorted(signals, key=lambda s: s.score, reverse=True)
        top = signals[0]
        diag = (diagnoses or {}).get(top.group, [])
        # With the cause already found, the next step is fixing it, not asking me to look.
        next_action = ("Fix it from the error above, or ask me to dig deeper." if diag else top.next_action)
        if len(signals) == 1:
            head = "**" + voice.say("alert_critical" if top.level == "critical" else "alert_one", self.cfg.name) + "**"
            md = f"{head} · {top.repo}\n**{top.title}**\n{top.why}"
            md += ("\n" + "\n".join(diag) if diag else "") + f"\n→ {next_action}"
        else:
            by_repo: dict[str, list[Signal]] = {}
            for sig in signals:
                by_repo.setdefault(sig.repo, []).append(sig)
            n = len(signals)
            parts = ["**" + voice.say("alert_many", self.cfg.name, n=n) + "**" + f" across {len(by_repo)} repo{'s' if len(by_repo) > 1 else ''}"]
            for repo, items in list(by_repo.items())[:6]:
                mark = "🔴 " if any(i.level == "critical" for i in items) else ""
                listed = "; ".join(i.title + (f" ({i.count}x)" if i.count > 1 else "") for i in items[:3])
                more = f" +{len(items) - 3} more" if len(items) > 3 else ""
                parts.append(f"- {mark}**{repo}**: {listed}{more}")
            if len(by_repo) > 6:
                parts.append(f"- …{len(by_repo) - 6} more repos in /attention")
            parts.append(f"\n{voice.say('start_with')} **{top.repo}**: {top.why}")
            parts += diag
            parts.append(f"→ {next_action}")
            md = "\n".join(parts)
        first_per_repo: dict[str, Signal] = {}
        for sig in signals:  # highest-scored signal per repo, in score order
            first_per_repo.setdefault(sig.repo, sig)
        rows = [[{"text": f"↗ {s.repo.split('/')[-1]}"[:30], "url": s.url}
                 for s in list(first_per_repo.values())[:2]]]
        mutes = [self._mute_button(top.repo.split("/")[-1], "repo", top.repo)]
        if top.kind != "ci_failure":
            mutes.append(self._mute_button("thread", "thread", ",".join(top.thread_ids)))
        return md, rows + ([mutes] if self.interactive else [])

    def compose_digest(self, signals: list[Signal], waiting: dict[str, Any], suppressed: int,
                       explicit: bool) -> tuple[str, list[list[dict[str, str]]]]:
        by_repo: dict[str, list[Signal]] = {}
        for s in sorted(signals, key=lambda s: s.score, reverse=True):
            by_repo.setdefault(s.repo, []).append(s)
        parts: list[str] = []
        blocking = {k: v for k, v in (waiting or {}).items() if isinstance(v, list) and v}
        if blocking:
            labels = {"review_requested_from_you": "Review requested from you",
                      "your_prs_with_changes_requested": "Your PRs with changes requested",
                      "your_prs_approved_not_merged": "Approved, ready to merge"}
            parts.append("**Waiting on you**")
            for k, items in blocking.items():
                for i in items[:5]:
                    parts.append(f"- {labels.get(k, k)}: [{i['repo']}#{i['number']}]({i['url']}) {i['title']}")
        if by_repo:
            parts.append(f"**Since the last digest**: {len(signals)} signal{'s' if len(signals) != 1 else ''}"
                         f" across {len(by_repo)} repo{'s' if len(by_repo) != 1 else ''}")
            for repo, items in list(by_repo.items())[:10]:
                parts.append(f"**{repo}**" + (" · fork, upstream PR checks are what count" if items[0].fork else ""))
                for s in items[:4]:
                    parts.append(f"- {s.title}" + (f" ({s.count}x)" if s.count > 1 else ""))
                if len(items) > 4:
                    parts.append(f"- +{len(items) - 4} more")
        if not parts:
            return (voice.say("calm", self.cfg.name)
                    if explicit else "", [])
        if suppressed:
            parts.append(f"_{suppressed} already-reported signals suppressed._")
        rows = [[{"text": "↗ Notifications", "url": "https://github.com/notifications"}]]
        noisy = max(by_repo.items(), key=lambda kv: len(kv[1]), default=None)
        if noisy and len(noisy[1]) >= 3 and self.interactive:
            rows.append([self._mute_button(noisy[0].split("/")[-1], "repo", noisy[0])])
        local = self.cfg.policy.local_now(datetime.now(timezone.utc))
        title = "**" + (voice.say("attention", self.cfg.name) if explicit
                        else voice.say("digest", self.cfg.name, greeting=voice.greeting(local))) + "**"
        return title + "\n\n" + "\n".join(parts), rows


def to_html_escape(text: str) -> str:
    import html

    return html.escape(text, quote=False)
