"""Cloud LLM interpretation layer (OpenAI-compatible chat completions).

GH-OPS integrates real cloud LLM providers using the KIO provider
configuration pattern — provider name + API key + model + base URL + timeout,
all read from the environment. No Ollama, no local models, no hardcoded
credentials, nothing committed beyond this code.

Environment (see .env, gitignored):

    GH_OPS_LLM_ENABLED=true          # kill-switch; default true
    GH_OPS_LLM_PROVIDERS=groq,gemini # try order
    GH_OPS_LLM_TIMEOUT_S=12
    GROQ_API_KEY / GROQ_MODEL / GROQ_BASE_URL      (base URL has a default)
    GEMINI_API_KEY / GEMINI_MODEL / GEMINI_BASE_URL

Guarantees:

- Fully functional without the LLM: no keys, dead provider, timeout,
  network failure, or malformed output all collapse to ``None`` and the
  deterministic GH-OPS pipeline is unaffected.
- The provider chain fails over: first provider whose validated answer
  succeeds wins.
- Every model output passes strict validation (length cap, printable text,
  no raw JSON, no internal telemetry strings) before any caller sees it.
- The LLM never owns event identity, lifecycle, deduplication, priority,
  persistence, delivery, security decisions, or allowed-value validation.
- The HTTP session is injectable so tests never touch the network.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import requests

#: Hard cap on any model-provided line shown to users.
MAX_OUTPUT_CHARS = 400

#: Known provider default endpoints (keys still come from the environment).
DEFAULT_BASE_URLS: dict[str, str] = {
    "groq": "https://api.groq.com/openai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "openrouter": "https://openrouter.ai/api/v1",
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "together": "https://api.together.xyz/v1",
    "cerebras": "https://api.cerebras.ai/v1",
}

#: Internal implementation strings that must never surface in messages.
_FORBIDDEN: tuple[str, ...] = (
    "reporting_events",
    "security_notified",
    "oss_opportunities",
    "schema_version",
    "state:",
    "alerts:",
    "cache restored",
    "monitoring complete",
    "oss hunter complete",
    "security scan complete",
    "run summary",
    "opportunities found",
    "collection failed",
)


def _env_flag(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Provider:
    """One cloud chat-completions provider (values never rendered)."""

    name: str
    base_url: str
    api_key: str
    model: str

    @property
    def usable(self) -> bool:
        return bool(self.base_url) and bool(self.api_key) and bool(self.model)


@dataclass(frozen=True)
class LlmConfig:
    """Environment-derived LLM configuration."""

    enabled: bool
    timeout_s: float
    providers: tuple[Provider, ...]

    @classmethod
    def from_env(cls) -> LlmConfig:
        names = [
            name.strip().lower()
            for name in os.environ.get("GH_OPS_LLM_PROVIDERS", "groq,gemini").split(",")
            if name.strip()
        ]
        providers: list[Provider] = []
        for name in names:
            upper = name.upper()
            base_url = (
                os.environ.get(f"{upper}_BASE_URL", "").strip() or DEFAULT_BASE_URLS.get(name, "")
            ).rstrip("/")
            api_key = os.environ.get(f"{upper}_API_KEY", "").strip()
            # NAME_MODEL may list several models: each is its own failover step
            # (Groq rate-limits per model, so this multiplies usable quota).
            models = [m.strip() for m in os.environ.get(f"{upper}_MODEL", "").split(",") if m.strip()] or [""]
            for i, model in enumerate(models):
                label = name if i == 0 else f"{name}:{model.rsplit('/', 1)[-1]}"
                providers.append(Provider(name=label, base_url=base_url, api_key=api_key, model=model))
        return cls(
            enabled=_env_flag("GH_OPS_LLM_ENABLED", "true"),
            timeout_s=float(os.environ.get("GH_OPS_LLM_TIMEOUT_S", "12") or 12),
            providers=tuple(providers),
        )

    @property
    def usable(self) -> bool:
        """True when enabled and at least one provider is fully configured."""
        return self.enabled and any(provider.usable for provider in self.providers)


def validate_output(text: object) -> str | None:
    """Validate model output for user-facing display, or reject it.

    Rejects: non-strings, empty/over-long output, raw JSON, control
    characters, and internal telemetry strings. Returns the collapsed string
    when acceptable.
    """
    if not isinstance(text, str):
        return None
    cleaned = " ".join(text.split()).strip()
    if not cleaned or len(cleaned) > MAX_OUTPUT_CHARS:
        return None
    if cleaned.startswith(("{", "[")):
        return None
    if any(ord(ch) < 32 for ch in cleaned):
        return None
    lowered = cleaned.lower()
    if any(marker in lowered for marker in _FORBIDDEN):
        return None
    return cleaned


def _ask(provider: Provider, prompt: str, timeout_s: float, session: requests.Session) -> str | None:
    payload = {
        "model": provider.model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You summarize GitHub intelligence for an operator. "
                    "Answer with one plain sentence, at most 60 words. "
                    "No JSON, no markdown, no internal identifiers."
                ),
            },
            {"role": "user", "content": prompt[:4000]},
        ],
        "temperature": 0.2,
        "max_tokens": 512,
    }
    try:
        response = session.post(
            f"{provider.base_url}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {provider.api_key}"},
            timeout=timeout_s,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
    except Exception:
        return None
    return validate_output(content)


# ── Tool-calling chat (conversational agent) ─────────────────────

#: Cooldown after a provider fails, so a dead provider is not retried every turn.
_AUTH_COOLDOWN_S = 1800.0
_FAILURE_COOLDOWN_S = 60.0

#: provider name -> (unix time until which it is skipped, short reason).
_provider_health: dict[str, tuple[float, str]] = {}
#: Running token totals per provider for /status (process lifetime).
_token_usage: dict[str, int] = {}


class LlmUnavailableError(Exception):
    """Every configured provider failed; the message lists short reasons only."""


@dataclass(frozen=True)
class ChatResult:
    provider: str
    model: str
    content: str
    tool_calls: tuple[dict[str, str], ...]  # {"id", "name", "arguments"}


def provider_status() -> dict[str, Any]:
    """Configured providers with health and token usage. Never contains keys."""
    now = time.time()
    cfg = LlmConfig.from_env()
    out: dict[str, Any] = {}
    for p in cfg.providers:
        until, reason = _provider_health.get(p.name, (0.0, ""))
        out[p.name] = {
            "configured": p.usable,
            "model": p.model or None,
            "healthy": until <= now,
            "last_error": reason or None,
            "tokens_used": _token_usage.get(p.name, 0),
        }
    return out


def _retry_after_s(response: Any) -> float | None:
    raw = getattr(response, "headers", {}).get("retry-after") if hasattr(response, "headers") else None
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _parse_chat_response(body: Any) -> tuple[str, tuple[dict[str, str], ...]] | None:
    """Validate an OpenAI-compatible response shape; None when malformed."""
    try:
        message = body["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return None
    if not isinstance(message, dict):
        return None
    content = message.get("content") or ""
    if not isinstance(content, str):
        return None
    calls: list[dict[str, str]] = []
    for i, call in enumerate(message.get("tool_calls") or []):
        fn = call.get("function") if isinstance(call, dict) else None
        if not isinstance(fn, dict) or not isinstance(fn.get("name"), str):
            return None
        args = fn.get("arguments")
        if isinstance(args, dict):
            args = json.dumps(args)
        calls.append({
            "id": str(call.get("id") or f"call_{i}"),
            "name": fn["name"],
            "arguments": args if isinstance(args, str) else "{}",
        })
    if not content.strip() and not calls:
        return None
    return content, tuple(calls)


def chat(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    config: LlmConfig | None = None,
    session: requests.Session | None = None,
    max_tokens: int = 1500,
    retries: int = 3,
    sleep: Any = time.sleep,
    budget_s: float = 90.0,
) -> ChatResult:
    """One chat-completions turn with tool support and provider failover.

    Order: GH_OPS_LLM_PROVIDERS (primary first). Per provider: timeout,
    bounded retry with exponential backoff on 429/5xx/network errors,
    immediate failover on auth/quota errors (with a long cooldown), and
    response-shape validation. Raises LlmUnavailableError when all fail.
    """
    cfg = config if config is not None else LlmConfig.from_env()
    if not cfg.usable:
        raise LlmUnavailableError("no LLM provider configured")
    http = session if session is not None else requests.Session()
    reasons: list[str] = []
    now = time.time()
    started = time.monotonic()
    chain = [p for p in cfg.providers if p.usable]
    healthy = [p for p in chain if _provider_health.get(p.name, (0.0, ""))[0] <= now]
    # If every provider is cooling down, try them anyway rather than fail blind.
    for provider in healthy or chain:
        payload: dict[str, Any] = {
            "model": provider.model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
        for attempt in range(retries + 1):
            reason = ""
            try:
                response = http.post(
                    f"{provider.base_url}/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {provider.api_key}"},
                    timeout=cfg.timeout_s,
                )
            except requests.exceptions.RequestException as exc:
                reason = type(exc).__name__  # exception text may contain the URL
                retryable = True
            else:
                status = response.status_code
                if status in (401, 403):
                    _provider_health[provider.name] = (time.time() + _AUTH_COOLDOWN_S, f"auth {status}")
                    reasons.append(f"{provider.name}: auth {status}")
                    break
                if status == 429 or status >= 500:
                    reason, retryable = f"HTTP {status}", True
                    if status == 429 and attempt < retries:
                        # Free tiers reset within seconds-to-a-minute: waiting beats failing.
                        wait = max(_retry_after_s(response) or 0.0, 10.0 * (attempt + 1))
                        if wait <= 60 and time.monotonic() - started + wait <= budget_s:
                            sleep(wait + 0.5)
                            continue
                    if status == 429:
                        retryable = False  # can't wait it out in budget: fail over now
                elif status >= 400:
                    try:
                        code = str((response.json().get("error") or {}).get("code") or "")[:40]
                    except (ValueError, AttributeError):
                        code = ""
                    reason, retryable = f"HTTP {status}{' ' + code if code else ''}", code == "tool_use_failed"
                else:
                    try:
                        body = response.json()
                    except ValueError:
                        body = None
                    parsed = _parse_chat_response(body)
                    if parsed is not None:
                        _provider_health.pop(provider.name, None)
                        usage = (body or {}).get("usage") or {}
                        total = usage.get("total_tokens") if isinstance(usage, dict) else None
                        if isinstance(total, int):
                            _token_usage[provider.name] = _token_usage.get(provider.name, 0) + total
                        return ChatResult(provider.name, provider.model, parsed[0], parsed[1])
                    reason, retryable = "malformed response", False
            if retryable and attempt < retries and time.monotonic() - started < budget_s:
                sleep(min(2.0 ** attempt, 8.0))
                continue
            _provider_health[provider.name] = (time.time() + _FAILURE_COOLDOWN_S, reason)
            reasons.append(f"{provider.name}: {reason}")
            break
    raise LlmUnavailableError("; ".join(reasons) or "no healthy provider")


def summarize(
    prompt: str,
    *,
    config: LlmConfig | None = None,
    session: requests.Session | None = None,
    providers: Sequence[Provider] | None = None,
) -> str | None:
    """Get a short interpretation from the first cloud provider that works.

    Never raises. Returns None when disabled, unconfigured, or every
    provider fails.
    """
    cfg = config if config is not None else LlmConfig.from_env()
    if not cfg.usable:
        return None
    chain = tuple(providers) if providers is not None else cfg.providers

    http = session if session is not None else requests.Session()
    for provider in chain:
        if not provider.usable:
            continue
        result = _ask(provider, prompt, cfg.timeout_s, http)
        if result is not None:
            return result
    return None
