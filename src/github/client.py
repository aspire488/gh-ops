"""Central HTTP client for GitHub API.

ONLY file that executes HTTP requests. Enforces:
- GET-only (WriteDeniedError for anything else)
- Auth via GITHUB_TOKEN env var
- Timeout handling (30s default)
- Retry with exponential backoff (3 retries, jitter)
- Rate limit tracking (reads headers, exposes via on_rate_limit callback)
- ETag support (304 Not Modified returns PaginatedResult with not_modified=True)
- Pagination via Link header
- Structured error mapping (401->API_AUTH_FAILED, 403->API_FORBIDDEN, etc.)
- Response logging with redaction
"""
from __future__ import annotations

import json
import logging
import re
import time
import random
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

import requests

from src.core.errors import (
    ErrorCode,
    ErrorSeverity,
    GhOpsError,
    WriteDeniedError,
)
from src.core.rate_limit import RateLimitTracker, RateLimitState
from src.github.auth import AuthConfig, resolve_auth
from src.github.models import PaginatedResult

logger = logging.getLogger("gh_ops.github.client")

T = TypeVar("T")


class GitHubClientError(GhOpsError):
    """Error from GitHub API operations."""

    def __init__(
        self,
        message: str,
        status_code: int = 0,
        error_code: ErrorCode = ErrorCode.INTERNAL_ERROR,
        endpoint: str = "",
        rate_limit_remaining: Optional[int] = None,
        retry_after: Optional[int] = None,
    ):
        super().__init__(
            code=error_code,
            message=message,
            module="github.client",
            severity=ErrorSeverity.HIGH if status_code >= 500 else ErrorSeverity.MEDIUM,
            context={
                "status_code": status_code,
                "endpoint": endpoint,
                "rate_limit_remaining": rate_limit_remaining,
                "retry_after": retry_after,
            },
        )
        self.status_code = status_code
        self.endpoint = endpoint
        self.rate_limit_remaining = rate_limit_remaining
        self.retry_after = retry_after


class GitHubClient:
    """Central HTTP client for GitHub API.

    Usage:
        client = GitHubClient()
        user = client.get("/user")
        repos = client.get_paginated("/user/repos", per_page=100)

    All requests go through execute(), which enforces GET-only.
    """

    def __init__(
        self,
        auth: Optional[AuthConfig] = None,
        base_url: str = "https://api.github.com",
        timeout: int = 30,
        max_retries: int = 3,
        on_rate_limit: Optional[Callable[[RateLimitState], None]] = None,
    ):
        self._auth = auth or resolve_auth()
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._on_rate_limit = on_rate_limit
        self._session = requests.Session()
        self._rate_limit_tracker = RateLimitTracker()
        self._setup_session()

    def _setup_session(self):
        """Configure session with auth headers and defaults."""
        self._session.headers.update({
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        auth_headers = self._auth.auth_headers()
        if auth_headers:
            self._session.headers.update(auth_headers)
        self._session.headers["User-Agent"] = "gh-ops/1.0"

    @property
    def authenticated(self) -> bool:
        """True if a real token is configured."""
        return self._auth.has_token

    @property
    def rate_limit_state(self) -> RateLimitState:
        """Current rate limit state (core endpoint)."""
        return self._rate_limit_tracker.core

    def get(self, endpoint: str, **kwargs) -> Any:
        """Execute a GET request.

        Args:
            endpoint: API endpoint (e.g., "/user", "/repos/owner/name")
            **kwargs: Additional arguments passed to requests

        Returns:
            Parsed JSON response

        Raises:
            GitHubClientError: On HTTP errors
            WriteDeniedError: If method is not GET (defensive)
        """
        return self.execute("GET", endpoint, **kwargs)

    def get_paginated(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        per_page: int = 30,
        max_pages: int = 10,
        etag: Optional[str] = None,
    ) -> PaginatedResult:
        """Execute a paginated GET request.

        Follows Link headers for pagination. Returns all items across pages.

        Args:
            endpoint: API endpoint
            params: Query parameters
            per_page: Items per page (max 100)
            max_pages: Maximum pages to fetch (safety limit)
            etag: ETag for conditional request (304 handling)

        Returns:
            PaginatedResult with all items
        """
        all_items: List[Any] = []
        params = dict(params or {})
        params["per_page"] = min(per_page, 100)
        params["page"] = 1

        current_etag = etag
        page_count = 0
        has_next = False

        while page_count < max_pages:
            headers = {}
            if current_etag:
                headers["If-None-Match"] = current_etag

            response = self._do_request("GET", endpoint, params=params, headers=headers)

            # Handle 304 Not Modified
            if response.status_code == 304:
                return PaginatedResult(
                    items=tuple(all_items),
                    total_count=len(all_items),
                    page=1,
                    per_page=per_page,
                    has_next=False,
                    etag=current_etag,
                    not_modified=True,
                )

            # Parse response
            try:
                data = response.json()
            except json.JSONDecodeError as e:
                raise GitHubClientError(
                    message=f"Invalid JSON response: {e}",
                    status_code=response.status_code,
                    error_code=ErrorCode.API_MALFORMED_RESPONSE,
                    endpoint=endpoint,
                )

            # Handle list responses
            if isinstance(data, list):
                all_items.extend(data)
            elif isinstance(data, dict):
                for key in ("items", "repositories", "workflow_runs", "alerts"):
                    if key in data and isinstance(data[key], list):
                        all_items.extend(data[key])
                        break
                else:
                    all_items.append(data)

            # Update ETag from response
            resp_etag = response.headers.get("ETag")
            if resp_etag:
                current_etag = resp_etag

            # Check for next page via Link header
            link_header = response.headers.get("Link", "")
            has_next = 'rel="next"' in link_header

            page_count += 1

            if not has_next:
                break

            params["page"] += 1

        return PaginatedResult(
            items=tuple(all_items),
            total_count=len(all_items),
            page=1,
            per_page=per_page,
            has_next=has_next and page_count >= max_pages,
            etag=current_etag,
        )

    def execute(self, method: str, endpoint: str, **kwargs) -> Any:
        """Execute an API request.

        ENFORCES GET-ONLY for gh-ops V1.

        Args:
            method: HTTP method (MUST be GET)
            endpoint: API endpoint
            **kwargs: Additional arguments passed to requests

        Returns:
            Parsed JSON response

        Raises:
            WriteDeniedError: If method is not GET
            GitHubClientError: On HTTP errors
        """
        if method.upper() != "GET":
            raise WriteDeniedError(method=method.upper(), url=endpoint)

        response = self._do_request(method, endpoint, **kwargs)

        # Handle 304 Not Modified
        if response.status_code == 304:
            return {"_not_modified": True, "_etag": response.headers.get("ETag")}

        # Parse JSON
        try:
            return response.json()
        except json.JSONDecodeError as e:
            raise GitHubClientError(
                message=f"Invalid JSON response: {e}",
                status_code=response.status_code,
                error_code=ErrorCode.API_MALFORMED_RESPONSE,
                endpoint=endpoint,
            )

    def _do_request(
        self,
        method: str,
        endpoint: str,
        headers: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> requests.Response:
        """Execute HTTP request with retries and rate limit handling."""
        url = f"{self._base_url}{endpoint}" if endpoint.startswith("/") else endpoint
        merged_headers = dict(headers or {})

        for attempt in range(self._max_retries + 1):
            try:
                response = self._session.request(
                    method=method,
                    url=url,
                    headers=merged_headers,
                    timeout=self._timeout,
                    **kwargs,
                )

                # Update rate limit tracker from headers
                self._update_rate_limits(response)

                # Log request
                self._log_request(method, endpoint, response.status_code)

                # Handle rate limiting (429)
                if response.status_code == 429:
                    retry_after = self._parse_retry_after(response)
                    remaining = self._rate_limit_remaining(response)

                    if attempt < self._max_retries:
                        wait_time = retry_after or self._calculate_backoff(attempt)
                        logger.warning(
                            f"Rate limited on {endpoint}, retrying in {wait_time}s "
                            f"(attempt {attempt + 1}/{self._max_retries})"
                        )
                        time.sleep(wait_time)
                        continue

                    raise GitHubClientError(
                        message=f"Rate limited after {self._max_retries} retries",
                        status_code=429,
                        error_code=ErrorCode.API_RATE_LIMITED,
                        endpoint=endpoint,
                        rate_limit_remaining=remaining,
                        retry_after=retry_after,
                    )

                # Handle server errors (5xx) with retry
                if response.status_code >= 500 and attempt < self._max_retries:
                    wait_time = self._calculate_backoff(attempt)
                    logger.warning(
                        f"Server error {response.status_code} on {endpoint}, "
                        f"retrying in {wait_time}s (attempt {attempt + 1}/{self._max_retries})"
                    )
                    time.sleep(wait_time)
                    continue

                # Map HTTP errors to structured errors
                if response.status_code >= 400:
                    self._raise_for_status(response, endpoint)

                return response

            except requests.exceptions.ConnectionError as e:
                if attempt < self._max_retries:
                    wait_time = self._calculate_backoff(attempt)
                    logger.warning(
                        f"Connection error on {endpoint}, retrying in {wait_time}s "
                        f"(attempt {attempt + 1}/{self._max_retries})"
                    )
                    time.sleep(wait_time)
                    continue
                raise GitHubClientError(
                    message=f"Connection failed: {e}",
                    status_code=0,
                    error_code=ErrorCode.API_TIMEOUT,
                    endpoint=endpoint,
                )

            except requests.exceptions.Timeout:
                if attempt < self._max_retries:
                    wait_time = self._calculate_backoff(attempt)
                    logger.warning(
                        f"Timeout on {endpoint}, retrying in {wait_time}s "
                        f"(attempt {attempt + 1}/{self._max_retries})"
                    )
                    time.sleep(wait_time)
                    continue
                raise GitHubClientError(
                    message=f"Request timed out after {self._timeout}s",
                    status_code=0,
                    error_code=ErrorCode.API_TIMEOUT,
                    endpoint=endpoint,
                )

        raise GitHubClientError(
            message=f"Request failed after {self._max_retries} retries",
            status_code=0,
            error_code=ErrorCode.API_UNEXPECTED_STATUS,
            endpoint=endpoint,
        )

    def _update_rate_limits(self, response: requests.Response):
        """Update rate limit tracker from response headers."""
        try:
            # Normalize header keys to lowercase for RateLimitTracker
            headers = {k.lower(): v for k, v in response.headers.items()}
            self._rate_limit_tracker.update_from_headers(headers)
            if self._on_rate_limit:
                self._on_rate_limit(self._rate_limit_tracker.core)
        except Exception:
            pass

    def _rate_limit_remaining(self, response: requests.Response) -> Optional[int]:
        """Get remaining rate limit from response."""
        remaining = response.headers.get("X-RateLimit-Remaining")
        if remaining is not None:
            try:
                return int(remaining)
            except (ValueError, TypeError):
                pass
        return None

    def _parse_retry_after(self, response: requests.Response) -> Optional[int]:
        """Parse Retry-After header (seconds or HTTP-date)."""
        retry_after = response.headers.get("Retry-After")
        if retry_after is None:
            return None

        try:
            return int(retry_after)
        except ValueError:
            pass

        from email.utils import parsedate_to_datetime
        from datetime import datetime, timezone
        try:
            reset_time = parsedate_to_datetime(retry_after)
            now = datetime.now(timezone.utc)
            delta = (reset_time - now).total_seconds()
            return max(0, int(delta))
        except (ValueError, TypeError):
            pass

        return None

    def _calculate_backoff(self, attempt: int) -> float:
        """Calculate exponential backoff with jitter."""
        base_delay = 1.0
        max_delay = 30.0
        jitter = random.uniform(0, 0.5)
        return min(base_delay * (2 ** attempt) + jitter, max_delay)

    def _raise_for_status(self, response: requests.Response, endpoint: str):
        """Map HTTP status codes to structured errors."""
        status_code = response.status_code
        remaining = self._rate_limit_remaining(response)

        try:
            error_data = response.json()
            message = error_data.get("message", f"HTTP {status_code}")
        except (json.JSONDecodeError, ValueError):
            message = f"HTTP {status_code}"

        error_map = {
            401: ErrorCode.API_AUTH_FAILED,
            403: ErrorCode.API_FORBIDDEN,
            404: ErrorCode.API_NOT_FOUND,
            422: ErrorCode.VALIDATION_ERROR if hasattr(ErrorCode, 'VALIDATION_ERROR') else ErrorCode.API_UNEXPECTED_STATUS,
            429: ErrorCode.API_RATE_LIMITED,
            500: ErrorCode.INTERNAL_ERROR,
            502: ErrorCode.INTERNAL_ERROR,
            503: ErrorCode.INTERNAL_ERROR,
        }

        error_code = error_map.get(status_code, ErrorCode.API_UNEXPECTED_STATUS)

        # Special handling for 403 — check if it's rate limiting
        if status_code == 403 and remaining is not None and remaining == 0:
            error_code = ErrorCode.API_RATE_LIMITED

        raise GitHubClientError(
            message=message,
            status_code=status_code,
            error_code=error_code,
            endpoint=endpoint,
            rate_limit_remaining=remaining,
        )

    def _log_request(self, method: str, endpoint: str, status_code: int):
        """Log request with redacted sensitive data."""
        log_level = logging.DEBUG if status_code < 400 else logging.WARNING
        logger.log(
            log_level,
            f"{method} {endpoint} -> {status_code}",
            extra={
                "method": method,
                "endpoint": endpoint,
                "status_code": status_code,
            },
        )

    def close(self):
        """Close the HTTP session."""
        self._session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
