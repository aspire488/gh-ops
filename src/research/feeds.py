"""Official GitHub announcements feed (external research).

A deliberately tiny HTTP exit: one fixed URL, no credentials ever attached,
a response size cap, and plain-text output. It exists so the agent can answer
"what's new on GitHub" from the official source instead of model memory.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET  # stdlib expat rejects entity-expansion bombs
from typing import Any

import requests

CHANGELOG_FEED = "https://github.blog/changelog/feed/"
MAX_BYTES = 2_000_000
_TAG = re.compile(r"<[^>]+>")


def parse_feed(xml_text: str, limit: int) -> list[dict[str, Any]]:
    # RSS never needs DTDs; refusing them rules out entity-expansion/XXE tricks.
    # Only the prolog can hold a DTD (article CDATA legitimately contains "<!DOCTYPE html").
    root_at = xml_text.find("<rss")
    prolog = xml_text[: root_at if root_at >= 0 else len(xml_text)]
    if "<!DOCTYPE" in prolog or "<!ENTITY" in prolog:
        raise ValueError("feed contains a DTD; refusing to parse")
    root = ET.fromstring(xml_text)
    out: list[dict[str, Any]] = []
    for item in root.iter("item"):
        desc = _TAG.sub(" ", item.findtext("description") or "")
        out.append({
            "title": (item.findtext("title") or "").strip(),
            "published": (item.findtext("pubDate") or "").strip()[:16],
            "url": (item.findtext("link") or "").strip(),
            "summary": " ".join(desc.split())[:280],
        })
        if len(out) >= limit:
            break
    return out


def fetch_github_changelog(limit: int = 8, session: Any = None) -> list[dict[str, Any]]:
    http = session if session is not None else requests.Session()
    response = http.get(CHANGELOG_FEED, timeout=15, headers={"User-Agent": "kestrel/1.0"}, stream=True)
    response.raise_for_status()
    body = response.raw.read(MAX_BYTES + 1, decode_content=True) if hasattr(response, "raw") and response.raw else response.content
    if len(body) > MAX_BYTES:
        raise ValueError("feed too large")
    return parse_feed(body.decode("utf-8", errors="replace"), limit)
