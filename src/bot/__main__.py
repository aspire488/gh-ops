"""Run Kestrel: ``python -m src.bot`` (or ``--check`` for a health check).

Composition root: wires config, credentials (environment / .env only),
GitHub read client, write executor, LLM providers, memory and watcher.
"""
from __future__ import annotations

import sys
from typing import Any

from src.agent.actions import ActionQueue
from src.agent.agent import Agent
from src.agent.attention import AttentionWatcher
from src.agent.memory import ConversationStore
from src.agent.tools import ToolRegistry
from src.bot.app import POLL_TIMEOUT_S, BotConfig, KestrelBot
from src.core.config import Config
from src.github.client import GitHubClient
from src.github.writer import GitHubWriter
from src.intelligence import llm
from src.notifications.telegram import TelegramTransport, resolve_telegram_token
from src.utils.env import load_env_file
from src.utils.logging import get_logger

logger = get_logger("bot.main")


def _tz_label(offset: str) -> str:
    return "IST, UTC+05:30" if offset == "+05:30" else f"UTC{offset}"


def check() -> int:
    """Live, read-only integration check. Prints no secrets."""
    load_env_file()
    ok = True
    config = Config.load()
    cfg = BotConfig.from_config(config)
    print(f"authorized users: {len(cfg.allowed_users)}, chats: {len(cfg.allowed_chats)}")
    try:
        client = GitHubClient()
        login = client.get("/user").get("login")
        print(f"github: ok ({login}), {client.rate_limit_state.remaining} requests left")
    except Exception as exc:
        ok = False
        print(f"github: FAILED ({type(exc).__name__})")
    try:
        tg = TelegramTransport(resolve_telegram_token(), parse_mode="")
        me = tg.call("getMe", {})["result"]
        hook = tg.call("getWebhookInfo", {})["result"].get("url")
        print(f"telegram: ok (@{me.get('username')})" + (" WARNING: webhook set, polling will fail" if hook else ""))
    except Exception as exc:
        ok = False
        print(f"telegram: FAILED ({type(exc).__name__})")
    cfg_llm = llm.LlmConfig.from_env()
    for provider in cfg_llm.providers:
        if not provider.usable:
            print(f"llm {provider.name}: not configured")
            continue
        try:
            single = llm.LlmConfig(enabled=True, timeout_s=cfg_llm.timeout_s, providers=(provider,))
            llm.chat([{"role": "user", "content": "Reply with: ok"}], config=single, max_tokens=400, retries=0)
            print(f"llm {provider.name}: ok ({provider.model})")
        except llm.LlmUnavailableError as exc:
            ok = False
            print(f"llm {provider.name}: FAILED ({exc})")
    return 0 if ok else 1


def build() -> KestrelBot:
    load_env_file()
    config = Config.load()
    cfg = BotConfig.from_config(config)
    if not cfg.allowed_users:
        raise SystemExit("No authorized Telegram users: set agent.allowed_user_ids in config/agent.yml")
    token = resolve_telegram_token()
    transport = TelegramTransport(token, timeout=20, max_retries=2, parse_mode="")
    poll_transport = TelegramTransport(token, timeout=POLL_TIMEOUT_S + 15, max_retries=0, parse_mode="")
    client = GitHubClient()
    login = str(client.get("/user").get("login") or "")
    owners = cfg.allowed_owners or {login.lower()}

    def can_write(repo: str) -> tuple[bool, str]:
        if repo.split("/")[0].lower() not in owners:
            return False, f"Writes are only allowed on repos owned by: {', '.join(sorted(owners))}"
        perms: dict[str, Any] = client.get(f"/repos/{repo}").get("permissions") or {}
        return (True, "") if perms.get("push") else (False, f"Your token cannot push to {repo}")

    store = ConversationStore(cfg.max_messages, cfg.max_chars, cfg.ttl_hours)
    actions = ActionQueue(GitHubWriter() if cfg.writes_enabled else None, can_write)
    tools = ToolRegistry()
    repos = [f"{r.owner}/{r.repo}" for r in config.repositories]
    agent = Agent(tools, store, login=login, repos=repos, local_time=cfg.policy.local_now,
                  tz_label=_tz_label(cfg.policy.utc_offset), name=cfg.name)
    watcher = AttentionWatcher(client, login, cfg.policy) if cfg.proactive_enabled else None
    return KestrelBot(transport=transport, poll_transport=poll_transport, client=client, cfg=cfg, agent=agent,
                      tools=tools, store=store, actions=actions, watcher=watcher, login=login,
                      repos=repos, brief_llm=llm.chat)


if __name__ == "__main__":
    if "--check" in sys.argv:
        raise SystemExit(check())
    build().run()
