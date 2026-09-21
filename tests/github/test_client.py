"""Tests for src.github.client — HTTP client with mocked requests."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, Mock, patch

import pytest
import requests

from src.core.errors import ErrorCode, WriteDeniedError
from src.github.auth import AuthConfig
from src.github.client import GitHubClient, GitHubClientError
from src.github.models import PaginatedResult


@pytest.fixture
def mock_session():
    """Create a mock requests.Session."""
    session = MagicMock(spec=requests.Session)
    session.headers = {}
    return session


@pytest.fixture
def client(mock_session):
    """Create a GitHubClient with mocked session."""
    auth = AuthConfig(token="ghp_" + "A" * 36, token_source="test")
    with patch("src.github.client.requests.Session", return_value=mock_session):
        c = GitHubClient(auth=auth, max_retries=0, timeout=5)
    c._session = mock_session
    return c


def _make_response(status_code=200, json_data=None, headers=None):
    """Create a mock response."""
    resp = Mock()
    resp.status_code = status_code
    resp.headers = headers or {}
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.return_value = {}
    return resp


class TestWriteDenied:
    """V1 enforcement: GET-only."""

    def test_post_denied(self, client):
        with pytest.raises(WriteDeniedError):
            client.execute("POST", "/repos/o/r/issues")

    def test_put_denied(self, client):
        with pytest.raises(WriteDeniedError):
            client.execute("PUT", "/repos/o/r/issues/1")

    def test_patch_denied(self, client):
        with pytest.raises(WriteDeniedError):
            client.execute("PATCH", "/repos/o/r/issues/1")

    def test_delete_denied(self, client):
        with pytest.raises(WriteDeniedError):
            client.execute("DELETE", "/repos/o/r/issues/1")


class TestGetRequest:
    """GET requests succeed."""

    def test_get_returns_json(self, client, mock_session):
        resp = _make_response(200, {"login": "testuser"})
        mock_session.request.return_value = resp

        result = client.get("/user")
        assert result["login"] == "testuser"
        mock_session.request.assert_called_once()

    def test_get_paginated_returns_items(self, client, mock_session):
        resp = _make_response(200, [{"id": 1}, {"id": 2}])
        mock_session.request.return_value = resp

        result = client.get_paginated("/repos/o/r/issues", per_page=100, max_pages=1)
        assert len(result) == 2


class TestPagination:
    """Pagination follows Link headers."""

    def test_follows_next_link(self, client, mock_session):
        page1 = _make_response(
            200,
            [{"id": 1}],
            headers={"Link": '<https://api.github.com/repos/o/r/issues?page=2>; rel="next"'},
        )
        page2 = _make_response(200, [{"id": 2}])
        mock_session.request.side_effect = [page1, page2]

        result = client.get_paginated("/repos/o/r/issues", per_page=1, max_pages=3)
        assert len(result) == 2
        assert mock_session.request.call_count == 2

    def test_stops_at_no_next(self, client, mock_session):
        resp = _make_response(200, [{"id": 1}])
        mock_session.request.return_value = resp

        result = client.get_paginated("/repos/o/r/issues", per_page=100, max_pages=5)
        assert len(result) == 1
        assert mock_session.request.call_count == 1


class TestETag:
    """ETag / 304 handling."""

    def test_304_returns_not_modified(self, client, mock_session):
        resp = _make_response(304, headers={"ETag": '"abc123"'})
        mock_session.request.return_value = resp

        result = client.get_paginated(
            "/repos/o/r/issues",
            etag='"abc123"',
            max_pages=1,
        )
        assert result.not_modified is True

    def test_etag_passed_in_header(self, client, mock_session):
        resp = _make_response(200, [{"id": 1}])
        mock_session.request.return_value = resp

        client.get_paginated("/repos/o/r/issues", etag='"old_etag"', max_pages=1)
        call_kwargs = mock_session.request.call_args
        assert "If-None-Match" in call_kwargs.kwargs.get("headers", {})


class TestRateLimit:
    """Rate limit tracking."""

    def test_updates_from_headers(self, client, mock_session):
        resp = _make_response(
            200,
            {},
            headers={
                "X-RateLimit-Remaining": "4999",
                "X-RateLimit-Limit": "5000",
                "X-RateLimit-Reset": "1700000000",
            },
        )
        mock_session.request.return_value = resp

        client.get("/user")
        state = client.rate_limit_state
        assert state.remaining == 4999
        assert state.limit == 5000

    def test_rate_limit_callback(self, mock_session):
        callback = MagicMock()
        auth = AuthConfig(token="ghp_" + "A" * 36, token_source="test")
        with patch("src.github.client.requests.Session", return_value=mock_session):
            c = GitHubClient(auth=auth, on_rate_limit=callback, max_retries=0)
        c._session = mock_session

        resp = _make_response(
            200,
            {},
            headers={
                "X-RateLimit-Remaining": "4999",
                "X-RateLimit-Limit": "5000",
                "X-RateLimit-Reset": "1700000000",
            },
        )
        mock_session.request.return_value = resp

        c.get("/user")
        callback.assert_called()


class TestErrorMapping:
    """HTTP errors map to structured errors."""

    def test_401_maps_to_auth_failed(self, client, mock_session):
        resp = _make_response(401, {"message": "Bad credentials"})
        mock_session.request.return_value = resp

        with pytest.raises(GitHubClientError) as exc_info:
            client.get("/user")
        assert exc_info.value.code == ErrorCode.API_AUTH_FAILED

    def test_404_maps_to_not_found(self, client, mock_session):
        resp = _make_response(404, {"message": "Not Found"})
        mock_session.request.return_value = resp

        with pytest.raises(GitHubClientError) as exc_info:
            client.get("/repos/o/nonexistent")
        assert exc_info.value.code == ErrorCode.API_NOT_FOUND

    def test_403_maps_to_forbidden(self, client, mock_session):
        resp = _make_response(403, {"message": "Forbidden"})
        mock_session.request.return_value = resp

        with pytest.raises(GitHubClientError) as exc_info:
            client.get("/user")
        assert exc_info.value.code == ErrorCode.API_FORBIDDEN


class TestClientContext:
    """Context manager support."""

    def test_context_manager(self, mock_session):
        auth = AuthConfig(token="ghp_" + "A" * 36, token_source="test")
        with patch("src.github.client.requests.Session", return_value=mock_session):
            with GitHubClient(auth=auth) as client:
                assert client is not None
        mock_session.close.assert_called_once()
