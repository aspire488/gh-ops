"""Kestrel's voice for deterministic (non-LLM) messages.

Phrase pools instead of fixed strings so alerts, progress lines and replies
don't read like a robot repeating itself. Tone is casual-friendly; critical
incidents stay direct (no jokes when main is on fire).
"""
from __future__ import annotations

import random
from datetime import datetime

_rng = random.Random()

POOLS: dict[str, tuple[str, ...]] = {
    "thinking": ("On it, {name}…", "Digging in…", "Give me a sec, {name}…", "Checking, bro…",
                 "Let me look…", "Pulling it up…", "One moment, {name}…"),
    "reset": ("Clean slate, {name}. What's next?", "Done. Memory wiped, fresh start.",
              "Fresh start, bro. Ask away.", "Reset. I've forgotten everything we said. Mostly."),
    "unmute": ("Unmuted everything, {name}. You'll hear it all again.", "All mutes cleared, bro."),
    "broke": ("Ugh, something broke on my side ({err}), {name}. Try again, or /reset if it keeps happening.",
              "That one crashed on me ({err}). My bad, bro. Give it another go."),
    "watch_failed": ("Couldn't reach GitHub just now ({err}). I'll keep trying, {name}.",
                     "GitHub isn't answering ({err}). Retrying in a bit, bro."),
    "llm_down": ("Bro, my AI providers are all rate-limited or down right now, so I can't dig into that properly.",
                 "{name}, my reasoning engines are tapped out at the moment (free-tier limits, probably).",
                 "Can't think straight right now, {name}. Every model provider is refusing me."),
    "alert_one": ("Heads up {name}", "Yo {name}", "{name}, this needs you", "Bro, quick one"),
    "alert_critical": ("🔴 {name}, this is urgent", "🔴 Critical, {name}", "🔴 Drop what you're doing, {name}"),
    "alert_many": ("Heads up {name}, {n} things need you", "Yo {name}, {n} things want your attention",
                   "{name}, {n} things piling up"),
    "start_with": ("Start with", "I'd hit this first:", "Priority:"),
    "digest": ("{greeting} {name}, here's your catch-up", "Catch-up time, {name}", "Quick roundup, bro"),
    "attention": ("Here's what needs you, {name}", "Alright {name}, here's the state of things",
                  "Your attention list, bro"),
    "calm": ("All quiet, {name}. No review requests, no blocked PRs, no new failures. 🎉",
             "Nothing needs you right now, bro. Go build something.",
             "Clean board, {name}. Nothing is waiting on you."),
    "not_authorized": ("Not authorized.",),
}


def greeting(local_now: datetime | None) -> str:
    if local_now is None:
        return "Hey"
    h = local_now.hour
    return "Morning" if 5 <= h < 12 else "Afternoon" if 12 <= h < 17 else "Evening" if 17 <= h < 22 else "Hey"


def say(kind: str, name: str = "", **fmt: object) -> str:
    """Random phrase from a pool. Without a name, name-addressed phrasing degrades gracefully."""
    text = _rng.choice(POOLS[kind]).format(name=name or "boss", **fmt)
    return text
