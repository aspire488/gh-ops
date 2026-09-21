"""Tests for src.github.auth — token resolution and validation."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from src.github.auth import AuthConfig, resolve_auth, validate_token_format


class TestValidateTokenFormat:
    """Tests for validate_token_format."""

    def test_valid_token(self):
        """ghp_ followed by 36 alphanumeric chars is valid."""
        token = "ghp_" + "A" * 36
        assert validate_token_format(token) is True

    def test_valid_fine_grained_token(self):
        """github_pat_ followed by alphanumeric is valid."""
        token = "github_pat_" + "A" * 82
        assert validate_token_format(token) is True

    def test_empty_string(self):
        assert validate_token_format("") is False

    def test_non_empty_unknown_prefix(self):
        """Unknown prefix but non-empty still returns True (warns but doesn't reject)."""
        token = "unknown_" + "A" * 20
        assert validate_token_format(token) is True


class TestAuthConfig:
    """Tests for AuthConfig."""

    def test_auth_headers(self):
        auth = AuthConfig(token="ghp_" + "A" * 36, token_source="GITHUB_TOKEN")
        headers = auth.auth_headers()
        assert headers["Authorization"] == "token ghp_" + "A" * 36
        assert "Accept" in headers
        assert "X-GitHub-Api-Version" in headers

    def test_repr_redacted(self):
        auth = AuthConfig(token="ghp_secret1234567890123456789012345678", token_source="GITHUB_TOKEN")
        r = repr(auth)
        assert "secret" not in r
        assert "token_source=" in r

    def test_eq(self):
        a = AuthConfig(token="ghp_" + "A" * 36, token_source="GITHUB_TOKEN")
        b = AuthConfig(token="ghp_" + "A" * 36, token_source="GITHUB_TOKEN")
        assert a == b

    def test_neq(self):
        a = AuthConfig(token="ghp_" + "A" * 36, token_source="GITHUB_TOKEN")
        b = AuthConfig(token="ghp_" + "B" * 36, token_source="GITHUB_TOKEN")
        assert a != b

    def test_different_source_still_equal(self):
        """Dataclass __eq__ compares all fields; same token + different source = not equal."""
        a = AuthConfig(token="ghp_" + "A" * 36, token_source="GITHUB_TOKEN")
        b = AuthConfig(token="ghp_" + "A" * 36, token_source="GH_TOKEN")
        assert a != b

    def test_frozen(self):
        auth = AuthConfig(token="ghp_" + "A" * 36, token_source="GITHUB_TOKEN")
        with pytest.raises(AttributeError):
            auth.token = "new"


class TestResolveAuth:
    """Tests for resolve_auth."""

    @patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_" + "A" * 36}, clear=False)
    def test_from_env_var(self):
        auth = resolve_auth()
        assert auth.token_source == "GITHUB_TOKEN"

    @patch.dict(os.environ, {"GH_TOKEN": "ghp_" + "B" * 36}, clear=True)
    def test_from_gh_token(self):
        """GH_TOKEN is also accepted."""
        auth = resolve_auth()
        assert auth.token_source == "GH_TOKEN"

    @patch.dict(os.environ, {}, clear=True)
    def test_no_env_var_raises(self):
        """Raises GhOpsError when no token found."""
        from src.core.errors import GhOpsError
        with pytest.raises(GhOpsError):
            resolve_auth()

    @patch.dict(os.environ, {"GITHUB_TOKEN": "  "}, clear=True)
    def test_whitespace_only_token_raises(self):
        """Whitespace-only tokens are not accepted."""
        from src.core.errors import GhOpsError
        with pytest.raises(GhOpsError):
            resolve_auth()

    @patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_" + "A" * 36, "GH_TOKEN": "ghp_" + "B" * 36}, clear=True)
    def test_github_token_preferred(self):
        """GITHUB_TOKEN takes precedence over GH_TOKEN."""
        auth = resolve_auth()
        assert auth.token_source == "GITHUB_TOKEN"
