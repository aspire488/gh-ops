"""One proactive pass without the long-running bot: ``GHOPS_JOB=proactive``.

Runs the same watcher, auto-diagnosis and daily brief as the daemon, then
exits. Built for GitHub Actions (every 15 min), so proactivity never depends
on a laptop being on. Works with or without LLM keys: alerts and diagnosis are
deterministic; the brief is LLM-written only when a provider is configured.

Token: notifications and cross-repo sweeps need a personal token, so
``KESTREL_GITHUB_TOKEN`` is preferred over the Actions-provided GITHUB_TOKEN
(which cannot read /notifications).
"""
from __future__ import annotations

import os
from typing import Any

from src.agent.actions import ActionQueue
from src.agent.agent import Agent
from src.agent.attention import AttentionWatcher
from src.agent.memory import ConversationStore
from src.agent.tools import ToolRegistry
from src.bot.app import BotConfig, KestrelBot
from src.core.config import Config
from src.github.auth import AuthConfig
from src.github.client import GitHubClient
from src.intelligence import llm
from src.notifications.telegram import TelegramTransport, resolve_telegram_token
from src.utils.env import load_env_file
from src.utils.logging import get_logger

logger = get_logger("bot.proactive")

PAT_ENV = "KESTREL_GITHUB_TOKEN"


def run_once(config: Config | None = None) -> dict[str, Any]:
    load_env_file()
    config = config or Config.load()
    cfg = BotConfig.from_config(config)
    if not cfg.proactive_enabled:
        return {"skipped": "proactive.enabled is false"}
    if cfg.runner != "actions" and os.environ.get("GITHUB_ACTIONS") == "true":
        return {"skipped": "proactive.runner is 'daemon'; the local bot owns proactivity"}
    pat = os.environ.get(PAT_ENV, "").strip()
    client = GitHubClient(auth=AuthConfig(token=pat, token_source=PAT_ENV)) if pat else GitHubClient()
    login = str(client.get("/user").get("login") or "")  # app tokens have no /user: fails loudly, by design
    tg = TelegramTransport(resolve_telegram_token(), timeout=20, max_retries=2, parse_mode="")
    repos = [f"{r.owner}/{r.repo}" for r in config.repositories]
    bot = KestrelBot(
        transport=tg, poll_transport=tg, client=client, cfg=cfg,
        agent=Agent(ToolRegistry(), ConversationStore(filename="agent_conversations.json"), login=login, name=cfg.name),
        tools=ToolRegistry(), store=ConversationStore(filename="agent_conversations.json"),
        actions=ActionQueue(writer=None), watcher=AttentionWatcher(client, login, cfg.policy), login=login,
        repos=repos, brief_llm=llm.chat if llm.LlmConfig.from_env().usable else None, interactive=False,
    )
    sent_brief = False
    from datetime import datetime, timezone

    if bot.brief_due(datetime.now(timezone.utc)):
        bot.send_brief()
        sent_brief = True
    bot.proactive_tick()
    assert bot.watcher is not None
    return {"login": login, "brief_sent": sent_brief, "queued": len(bot.watcher.store.queue),
            "llm": bot.brief_llm is not None, "token": "personal" if pat else "actions"}
