"""Brief interpretation: Laya System-1 triage, cloud LLM System-2 writing.

Two entry points, both display-only and never raising:

- ``interpret_context`` (used by jobs): builds the focus from the
  deterministic intelligence context, lets Laya propose a label from the
  extended allow-list, reconciles (deterministic attention/security always
  win; ``quiet`` only silences non-urgent foci), then grounds the LLM
  sentence against the context's evidence pack.
- ``interpret_brief``: the original digest-based path with the fixed
  ``FOCUS_LABELS`` set, retained for API compatibility.

Failure semantics keep the deterministic brief fully functional:

- Laya unavailable/disabled/failed  -> proceed with the deterministic focus.
- LLM unavailable/failed/invalid    -> return None (brief renders unchanged).
- Grounding rejects the claim       -> return None.
- Both healthy                      -> validated, evidence-grounded headline.

The headline is advisory display only; it never influences event identity,
lifecycle, deduplication, priority, persistence, delivery, or security.
"""
from __future__ import annotations

from src.intelligence import attention, grounding, laya_adapter, llm
from src.intelligence.context import IntelligenceContext

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


#: Focus → prompt framing for context-based interpretation.
_CONTEXT_FRAMING: dict[str, str] = {
    "attention": "Focus on what needs action.",
    "security": "Focus on the security posture.",
    "operations": "Focus on repository health.",
    "developer": "Focus on developer activity.",
    "oss": "Focus on opportunities and scan outcomes.",
    "release": "Focus on releases and shipping activity.",
    "recovery": "Focus on what just recovered.",
    "trend": "Focus on the recurring pattern.",
    "mixed": "Give an even-handed overview.",
}


def _context_prompt(focus: str, purpose: str, digest: str) -> str:
    framing = _CONTEXT_FRAMING.get(focus, "Give an even-handed overview.")
    return (
        f"{framing} For the operator's {purpose} brief. One plain sentence, "
        f"max 60 words. Mention only repositories, links, and item numbers "
        f"that appear in the context below; never invent them. No evidence "
        f"IDs, no model scores, no JSON.\n\n{digest}"
    )


def interpret_context(
    context: IntelligenceContext,
    *,
    config: llm.LlmConfig | None = None,
    session=None,
    laya_loader=None,
) -> str | None:
    """Evidence-grounded headline for an intelligence context, or None.

    Never raises. Flow: skip non-meaningful/empty contexts → Laya System-1
    triage with the extended label set → reconcile with the deterministic
    focus (deterministic security/attention always wins) → cloud LLM System-2
    sentence → grounding validation against the context's evidence. Any
    failure at any stage returns None and the caller renders the
    deterministic brief unchanged.
    """
    if not context.meaningful or not context.pack.items:
        return None
    digest = context.digest()
    if not digest:
        return None

    focus = context.focus
    decision = attention.triage(digest, loader=laya_loader)
    if decision is not None:
        focus = attention.reconcile(context.focus, decision[0])
    if focus == "quiet":
        return None

    raw = llm.summarize(
        _context_prompt(focus, context.purpose, digest),
        config=config,
        session=session,
    )
    if raw is None:
        return None
    return grounding.ground_claim(
        raw,
        urls=context.urls,
        slugs=context.slugs,
        evidence_ids=context.pack.ids,
        item_numbers=context.pack.item_numbers,
    )
