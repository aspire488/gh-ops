"""Text formatting utilities for gh-ops.

Handles message escaping, splitting for Telegram limits,
and general text manipulation.
"""
from __future__ import annotations

import re
from typing import Iterator

# Telegram MarkdownV2 special characters that need escaping.
#
# The backslash is the escape character itself and MUST also be escaped,
# otherwise untrusted text containing a backslash produces an invalid escape
# sequence and Telegram rejects the entire message with a 400 error.
_TELEGRAM_MDV2_SPECIAL = "_*[]()~`>#+-=|{}.!\\"

# Pattern to match characters that need escaping in MarkdownV2
_TELEGRAM_ESCAPE_RE = re.compile(f"([{re.escape(_TELEGRAM_MDV2_SPECIAL)}])")


def escape_markdown_v2(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2.

    Args:
        text: Raw text.

    Returns:
        Text with special characters escaped.
    """
    return _TELEGRAM_ESCAPE_RE.sub(r"\\\1", text)


def split_message(text: str, max_length: int = 4096, separator: str = "\n\n") -> list[str]:
    """Split a message into chunks that fit Telegram's message limit.

    Tries to split at separator boundaries. Falls back to hard splitting
    if separators aren't available.

    Args:
        text: The full message.
        max_length: Maximum characters per chunk.
        separator: Preferred split point.

    Returns:
        List of message chunks, each <= max_length.
    """
    if len(text) <= max_length:
        return [text]

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break

        # Find the last separator within the limit
        split_at = remaining.rfind(separator, 0, max_length)
        if split_at <= 0:
            # No separator found; try other separators
            for alt in ["\n", ". ", ", ", " "]:
                split_at = remaining.rfind(alt, 0, max_length)
                if split_at > 0:
                    split_at += len(alt)
                    break
            else:
                # Hard split at max_length
                split_at = max_length

        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()

    return chunks


def truncate(text: str, max_length: int, suffix: str = "...") -> str:
    """Truncate text to max_length, adding suffix if truncated.

    Args:
        text: Text to truncate.
        max_length: Maximum length including suffix.
        suffix: What to append if truncated.

    Returns:
        Truncated text.
    """
    if len(text) <= max_length:
        return text
    return text[: max_length - len(suffix)] + suffix


def slugify(text: str) -> str:
    """Convert text to a URL/file-safe slug.

    Args:
        text: Input text.

    Returns:
        Lowercase, hyphen-separated slug.
    """
    result = text.lower().strip()
    result = re.sub(r"[^\w\s-]", "", result)
    result = re.sub(r"[\s_]+", "-", result)
    result = re.sub(r"-+", "-", result)
    return result.strip("-")


def ellipsis_join(items: list[str], max_length: int = 100) -> str:
    """Join items with commas, truncating with ellipsis if too long.

    Args:
        items: Strings to join.
        max_length: Maximum output length.

    Returns:
        Comma-separated string, possibly truncated.
    """
    if not items:
        return ""
    if len(items) == 1:
        return items[0]

    result = ", ".join(items)
    if len(result) <= max_length:
        return result

    # Truncate and add ellipsis
    truncated = result[: max_length - 3]
    # Don't cut in the middle of a word
    last_space = truncated.rfind(" ")
    if last_space > max_length // 2:
        truncated = truncated[:last_space]
    return truncated + "..."
