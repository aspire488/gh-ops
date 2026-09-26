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

import os
from collections.abc import Sequence
from dataclasses import dataclass

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
            providers.append(
                Provider(
                    name=name,
                    base_url=(
                        os.environ.get(f"{upper}_BASE_URL", "").strip()
                        or DEFAULT_BASE_URLS.get(name, "")
                    ).rstrip("/"),
                    api_key=os.environ.get(f"{upper}_API_KEY", "").strip(),
                    model=os.environ.get(f"{upper}_MODEL", "").strip(),
                )
            )
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
