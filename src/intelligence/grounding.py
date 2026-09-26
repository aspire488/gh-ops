"""Evidence-grounding validation for model-generated claims.

Every sentence produced by System-2 must reference only facts that exist
in the intelligence context: known URLs, known ``owner/repo`` slugs,
evidence ids, and issue/PR numbers that appear in the pack. Anything else
is rejected and the caller falls back to the deterministic brief.
"""
from __future__ import annotations

import re

from src.intelligence import llm

#: Hard cap on words in a grounded claim.
MAX_CLAIM_WORDS = 60

_URL_RE = re.compile(r"https?://[^\s)\]>\"']+")
#: Dots live inside segments (owner/next.js) but never terminate one, so a
#: sentence-final period ("...for octo/example.") is not part of the slug.
_SLUG_RE = re.compile(
    r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*/[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*"
)
_EVIDENCE_RE = re.compile(r"\bE\d{1,3}\b")
_ITEM_RE = re.compile(r"#(\d{1,9})\b")


def ground_claim(
    text: object,
    *,
    urls: frozenset[str] | set[str],
    slugs: frozenset[str] | set[str],
    evidence_ids: frozenset[str] | set[str],
    item_numbers: frozenset[int] | set[int],
    max_words: int = MAX_CLAIM_WORDS,
) -> str | None:
    """Validate a model claim against known evidence, or reject it.

    Rejected: anything ``llm.validate_output`` rejects, claims longer than
    ``max_words``, and any URL, ``owner/repo`` slug, ``E#`` evidence id, or
    ``#N`` item number not present in the allowed sets. Returns the
    validated single-line text when fully grounded, else None.
    """
    cleaned = llm.validate_output(text)
    if cleaned is None:
        return None
    if len(cleaned.split()) > max_words:
        return None
    for url in _URL_RE.findall(cleaned):
        if url not in urls:
            return None
    # Blank out URLs before the slug scan so "github.com/owner" inside an
    # allowed URL is never mistaken for a repository slug claim.
    for slug in _SLUG_RE.findall(_URL_RE.sub(" ", cleaned)):
        if slug not in slugs:
            return None
    for eid in _EVIDENCE_RE.findall(cleaned):
        if eid not in evidence_ids:
            return None
    for number in _ITEM_RE.findall(cleaned):
        if int(number) not in item_numbers:
            return None
    return cleaned
