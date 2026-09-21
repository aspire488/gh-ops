"""Tests for src.utils.text"""
import pytest
from src.utils.text import (
    escape_markdown_v2,
    split_message,
    truncate,
    slugify,
    ellipsis_join,
)


class TestEscapeMarkdownV2:
    def test_no_special_chars(self):
        assert escape_markdown_v2("hello world") == "hello world"

    def test_escapes_special_chars(self):
        result = escape_markdown_v2("hello_world")
        assert result == "hello\\_world"

    def test_escapes_all_special(self):
        text = "_*[]()~`>#+-=|{}.!"
        result = escape_markdown_v2(text)
        for char in text:
            assert f"\\{char}" in result

    def test_empty_string(self):
        assert escape_markdown_v2("") == ""

    def test_escapes_the_escape_character(self):
        # The backslash is the escape character itself: unescaped, it would
        # swallow the next character and produce invalid MarkdownV2.
        assert escape_markdown_v2("a\\b") == "a\\\\b"

    def test_trailing_backslash_is_escaped(self):
        result = escape_markdown_v2("trailing\\")
        assert result == "trailing\\\\"
        assert len(result) - len(result.rstrip("\\")) == 2

    def test_backslash_before_special_char_keeps_both_escaped(self):
        assert escape_markdown_v2("\\_") == "\\\\\\_"


class TestSplitMessage:
    def test_short_message(self):
        result = split_message("hello", max_length=100)
        assert result == ["hello"]

    def test_exact_length(self):
        text = "a" * 100
        result = split_message(text, max_length=100)
        assert result == [text]

    def test_splits_at_separator(self):
        text = "paragraph1\n\nparagraph2\n\nparagraph3"
        result = split_message(text, max_length=20)
        assert len(result) > 1
        for chunk in result:
            assert len(chunk) <= 20

    def test_splits_at_newline(self):
        text = "line1\nline2\nline3"
        result = split_message(text, max_length=15)
        assert len(result) > 1

    def test_hard_split_fallback(self):
        text = "a" * 100
        result = split_message(text, max_length=30)
        assert len(result) > 1
        for chunk in result:
            assert len(chunk) <= 30


class TestTruncate:
    def test_no_truncation(self):
        assert truncate("hello", 10) == "hello"

    def test_truncation(self):
        result = truncate("hello world", 8)
        assert len(result) == 8
        assert result.endswith("...")

    def test_custom_suffix(self):
        result = truncate("hello world", 8, suffix="…")
        assert result.endswith("…")


class TestSlugify:
    def test_simple(self):
        assert slugify("Hello World") == "hello-world"

    def test_special_chars(self):
        assert slugify("hello! @world#") == "hello-world"

    def test_multiple_spaces(self):
        assert slugify("hello   world") == "hello-world"

    def test_empty(self):
        assert slugify("") == ""


class TestEllipsisJoin:
    def test_empty(self):
        assert ellipsis_join([]) == ""

    def test_single(self):
        assert ellipsis_join(["hello"]) == "hello"

    def test_within_limit(self):
        result = ellipsis_join(["a", "b", "c"], max_length=100)
        assert result == "a, b, c"

    def test_truncates(self):
        items = ["short", "a" * 50, "another"]
        result = ellipsis_join(items, max_length=30)
        assert len(result) <= 30
        assert result.endswith("...")
