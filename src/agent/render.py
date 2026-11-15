"""Model markdown → Telegram HTML, split safely under the 4096-char limit.

HTML parse mode is used for conversation because escaping it is total and
simple (& < > only), unlike MarkdownV2. The model writes a small markdown
subset; everything else is escaped literally, so untrusted GitHub text can
never inject markup or links.
"""
from __future__ import annotations

import html
import re

LIMIT = 4096
CHUNK = 3500  # raw budget; rendering can grow text (&amp; etc.)

_LINK = re.compile(r"\[([^\]\n]{1,200})\]\((https?://[^\s)]{1,500})\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![\w*])[*_](?!\s)([^*_\n]+?)(?<!\s)[*_](?![\w*])")
_CODE = re.compile(r"`([^`\n]+)`")
_HEADING = re.compile(r"^#{1,6}\s+(.+)$", re.M)
_BULLET = re.compile(r"^(\s*)[-*]\s+", re.M)
_GH_URL = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+(?:/(?:pull|issues|actions/runs|releases/tag|commit)/[\w.-]+)?")


def _inline(text: str) -> str:
    """Escape, then apply inline markup. Code spans are protected first."""
    codes: list[str] = []

    def stash(m: re.Match[str]) -> str:
        codes.append(f"<code>{html.escape(m.group(1), quote=False)}</code>")
        return f"\x00{len(codes) - 1}\x00"

    text = _CODE.sub(stash, text)
    links: list[str] = []

    def link(m: re.Match[str]) -> str:
        links.append(f'<a href="{html.escape(m.group(2), quote=True)}">{html.escape(m.group(1), quote=False)}</a>')
        return f"\x01{len(links) - 1}\x01"

    text = _LINK.sub(link, text)
    text = html.escape(text, quote=False)
    text = _BOLD.sub(r"<b>\1</b>", text)
    text = _ITALIC.sub(r"<i>\1</i>", text)
    text = re.sub("\x01(\\d+)\x01", lambda m: links[int(m.group(1))], text)
    return re.sub("\x00(\\d+)\x00", lambda m: codes[int(m.group(1))], text)


def to_html(md: str) -> str:
    out: list[str] = []
    parts = re.split(r"```[\w+-]*\n?(.*?)```", md, flags=re.S)
    for i, part in enumerate(parts):
        if i % 2:  # fenced code
            out.append(f"<pre>{html.escape(part.rstrip(), quote=False)}</pre>")
            continue
        part = _HEADING.sub(lambda m: f"**{m.group(1).strip().strip('*')}**", part)
        part = _BULLET.sub(lambda m: f"{m.group(1)}• ", part)
        part = re.sub(r"^\s*(---+|\*\*\*+)\s*$", "", part, flags=re.M)  # rules look broken in Telegram
        out.append(_inline(part))
    return re.sub(r"\n{3,}", "\n\n", "".join(out)).strip()


def split_markdown(md: str, limit: int = CHUNK) -> list[str]:
    """Split at paragraph, then line boundaries; never inside a code fence."""
    blocks: list[str] = []
    buf = ""
    in_fence = False
    for para in re.split(r"(\n\n)", md):
        candidate = buf + para
        fence_open = in_fence ^ (para.count("```") % 2 == 1)
        if len(candidate) <= limit or not buf:
            buf, in_fence = candidate, fence_open
            continue
        if in_fence:
            buf += "\n```"
        blocks.append(buf.strip())
        buf = ("```\n" if in_fence else "") + para
        in_fence = fence_open
    if buf.strip():
        blocks.append(buf.strip())
    final: list[str] = []
    for block in blocks:  # a single giant paragraph: fall back to line splits
        while len(block) > limit:
            cut = block.rfind("\n", 0, limit)
            cut = cut if cut > limit // 3 else limit
            final.append(block[:cut])
            block = block[cut:].lstrip("\n")
        if block:
            final.append(block)
    return final


def render_messages(md: str) -> list[str]:
    """Markdown → list of Telegram-HTML messages, each ≤ 4096 chars."""
    out: list[str] = []
    for chunk in split_markdown(md):
        rendered = to_html(chunk)
        if len(rendered) <= LIMIT:
            out.append(rendered)
        else:  # escaping inflated it; render halves
            out += render_messages(chunk[: len(chunk) // 2]) + render_messages(chunk[len(chunk) // 2:])
    return [m for m in out if m]


def strip_html(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def github_links(md: str, limit: int = 3) -> list[tuple[str, str]]:
    """Distinct GitHub object URLs in the answer → button (label, url)."""
    seen: list[str] = []
    for m in _GH_URL.finditer(md):
        url = m.group(0).rstrip(".")
        if url not in seen:
            seen.append(url)
    out = []
    for url in seen[:limit]:
        tail = url.removeprefix("https://github.com/")
        parts = tail.split("/")
        label = (f"{parts[1]} #{parts[3]}" if len(parts) >= 4 and parts[2] in ("pull", "issues")
                 else f"{parts[1]} run" if "actions/runs" in tail else tail.split("/")[1] if len(parts) > 1 else tail)
        out.append((f"↗ {label}"[:40], url))
    return out
