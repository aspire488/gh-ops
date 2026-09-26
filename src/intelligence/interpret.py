"""Brief interpretation: Laya System-1 triage, cloud LLM System-2 writing.

Pipeline (display-only, never mutates state):

    bounded brief digest
        -> Laya decides focus in {"none", "operations", "security", "oss"}
        -> "none" skips the LLM entirely; any other focus frames the prompt
        -> cloud LLM writes one short sentence
        -> strict validation before it reaches the caller

Failure semantics keep the deterministic brief fully functional:

- Laya unavailable/disabled/failed  -> proceed with a neutral focus.
- LLM unavailable/failed/invalid    -> return None (brief renders unchanged).
- Both healthy                      -> validated headline.

The headline is advisory display only; it never influences event identity,
lifecycle, deduplication, priority, persistence, delivery, or security.
"""
from __future__ import annotations

from src.intelligence import laya_adapter, llm

#: Allowed System-1 labels (validated against this set, nothing else).
FOCUS_LABELS: tuple[str, ...] = ("none", "operations", "security", "oss")

_NEUTRAL_FOCUS = "operations"

_INSTRUCTIONS = (
    "Triage this GH-OPS brief for an operator. Choose 'none' when it needs "
    "no interpretation, 'operations' when repository and developer activity "
    "dominates, 'security' when security findings dominate, 'oss' when "
    "opportunity scanning dominates."
)

#: Bounded digest size (chars) fed to System-1 and System-2.
MAX_DIGEST_CHARS = 4000


def _focus_prompt(focus: str, digest: str) -> str:
    framing = {
        "operations": "Focus on repository health and developer activity.",
        "security": "Focus on the security posture.",
        "oss": "Focus on opportunities and scan outcomes.",
    }.get(focus, "Give an even-handed overview.")
    return (
        f"{framing} One plain sentence, max 60 words, for the operator "
        f"reading this daily/weekly brief:\n\n{digest}"
    )


def interpret_brief(
    digest: str,
    *,
    config: llm.LlmConfig | None = None,
    session=None,
    laya_loader=None,
) -> str | None:
    """Return a validated one-sentence headline for a brief, or None.

    Never raises. Returns None whenever interpretation is not possible or
    not wanted; the caller then renders the deterministic brief unchanged.
    """
    text = " ".join(str(digest).split())[:MAX_DIGEST_CHARS]
    if not text:
        return None

    focus = _NEUTRAL_FOCUS
    decision = laya_adapter.decide(_INSTRUCTIONS, text, FOCUS_LABELS, loader=laya_loader)
    if decision is not None:
        label, _model_score = decision
        if label == "none":
            return None
        focus = label

    return llm.summarize(_focus_prompt(focus, text), config=config, session=session)
