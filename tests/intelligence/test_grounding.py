"""Tests for src.intelligence.grounding (claim validation against evidence)."""
from __future__ import annotations

from src.intelligence.grounding import MAX_CLAIM_WORDS, ground_claim

_URLS = frozenset({"https://github.com/octo/example/issues/42"})
_SLUGS = frozenset({"octo/example"})
_IDS = frozenset({"E1", "E2"})
_NUMBERS = frozenset({42})


def _ground(text: object, **overrides):
    params = {
        "urls": _URLS,
        "slugs": _SLUGS,
        "evidence_ids": _IDS,
        "item_numbers": _NUMBERS,
    }
    params.update(overrides)
    return ground_claim(text, **params)


class TestAcceptedClaims:
    def test_plain_sentence_passes(self):
        assert _ground("CI on octo/example needs review.") == "CI on octo/example needs review."

    def test_whitespace_is_collapsed(self):
        assert _ground("CI   on\nocto/example\tneeds review.") == "CI on octo/example needs review."

    def test_known_url_slug_and_evidence_id_pass(self):
        claim = "E1 tracks https://github.com/octo/example/issues/42 in octo/example."
        assert _ground(claim) == claim

    def test_known_item_number_passes(self):
        assert _ground("Fix #42 first.") is not None


class TestRejections:
    def test_non_string_rejected(self):
        assert _ground(None) is None
        assert _ground(42) is None

    def test_json_output_rejected(self):
        assert _ground('{"summary": "all good"}') is None

    def test_forbidden_telemetry_rejected(self):
        assert _ground("reporting_events loaded fine") is None

    def test_overlong_claim_rejected(self):
        claim = "word " * (MAX_CLAIM_WORDS + 1)
        assert _ground(claim) is None

    def test_unknown_url_rejected(self):
        assert _ground("See https://evil.example/x for details.") is None

    def test_unknown_slug_rejected(self):
        assert _ground("Check stranger/repo#42 now.") is None

    def test_unknown_evidence_id_rejected(self):
        assert _ground("E9 says so.") is None

    def test_unknown_item_number_rejected(self):
        assert _ground("Fix #99 first.") is None

    def test_allowed_url_is_not_mistaken_for_a_slug_claim(self):
        # The URL contains "github.com/octo"-shaped text; blanking URLs
        # before the slug scan keeps this grounded.
        claim = "See https://github.com/octo/example/issues/42 for context."
        assert _ground(claim) == claim

    def test_custom_word_cap_applies(self):
        assert _ground("one two three four", max_words=3) is None
