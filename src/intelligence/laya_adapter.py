"""Laya System-1 decision layer (local, lazy, one checkpoint).

Laya is a small local routing/classification model used as an ADVISORY
fast-decision layer. Deterministic GH-OPS rules remain authoritative: Laya
output is a recommendation that must pass allowed-label validation before any
caller may use it, and it never mutates state or executes anything.

Configuration:

    LAYA_ENABLED=true   # default: on (System-1 is first-class, not optional)
    LAYA_ENABLED=false  # kill-switch to skip Laya entirely

Resource contract (measured on the development machine):

- Idle router (not loaded): ~22 MB RSS.
- One checkpoint loaded: ~2.19 GB RSS; cold load ~6.3 s; inference ~0.36 s
  CPU. CUDA is unavailable with the CPU-only torch build, so no GPU is used.
- ONE checkpoint maximum, loaded lazily on first explicit use and cached.
- Never preload all checkpoints; never load at import time; never run in
  GitHub Actions (LAYA_ENABLED stays unset there).

Calibration limitation: the shipped checkpoint emits an invalid-temperature
RuntimeWarning, so the numeric score returned alongside a label is an
uncalibrated MODEL SCORE / DECISION SIGNAL. Never present it to users as a
probability or confidence value.

Tests must inject a fake loader; they must never import real Laya or
download weights.
"""
from __future__ import annotations

import os
import threading
from collections.abc import Callable, Sequence

#: The single checkpoint this adapter is allowed to load.
DEFAULT_CHECKPOINT = "convaiinnovations/laya"

_Loader = Callable[[], object]

_LOCK = threading.Lock()
_AGENT: object | None = None


def laya_enabled() -> bool:
    """True unless LAYA_ENABLED is explicitly falsy (default: on).

    Enabled does not mean loaded: the checkpoint is fetched lazily on the
    first real decision, so the system stays light when no decision runs.
    """
    return os.environ.get("LAYA_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


def reset_cache() -> None:
    """Drop the cached agent (test isolation only)."""
    global _AGENT
    with _LOCK:
        _AGENT = None


def _default_loader() -> object:
    """Import Laya and load exactly one checkpoint, lazily and once."""
    global _AGENT
    with _LOCK:
        if _AGENT is None:
            import laya  # noqa: PLC0415 - optional heavy dependency, lazy by contract

            _AGENT = laya.load(DEFAULT_CHECKPOINT)
    return _AGENT


def decide(
    instructions: str,
    state: str,
    labels: Sequence[str],
    *,
    loader: _Loader | None = None,
) -> tuple[str, float] | None:
    """Return ``(label, model_score)`` for a typed decision, or None.

    Returns None when: Laya is disabled via LAYA_ENABLED, labels are empty,
    the model is unavailable, inference fails, or the predicted label is not
    in the supplied allowed set. The score is an uncalibrated model score,
    not a probability.
    """
    if not laya_enabled():
        return None
    allowed = [str(label) for label in labels]
    if not allowed or not instructions.strip():
        return None

    try:
        agent = (loader or _default_loader)()
        result = agent.predict(  # type: ignore[union-attr]
            state,
            {"decision": {"type": "choice", "instructions": instructions, "criteria": allowed}},
        )
        answer = result["answers"]["decision"]
        label = str(answer["choice"])
        score = float(answer.get("confidence") or 0.0)
    except Exception:
        return None

    if label not in allowed:
        return None
    return label, score
