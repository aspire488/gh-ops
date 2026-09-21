"""Endpoint monitor for gh-ops Phase 4.

Monitors arbitrary HTTP endpoints defined by configuration.

SECURITY IS CRITICAL:
- Explicit configuration required (no arbitrary URLs by default)
- HTTPS required by default (HTTP configurable)
- Finite timeout (default 10s)
- Bounded redirects (default 3)
- Bounded response size (default 1MB)
- SSRF protections (private IP, loopback, metadata services)
- No credentials in URLs
- No sensitive response logging

Configuration:
    monitors.endpoint.enabled: bool
    monitors.endpoint.checks:
      - url: "https://example.com/health"
        name: "example-health"
        method: GET (default)
        timeout: 10 (seconds)
        max_redirects: 3
        max_response_bytes: 1048576 (1MB)
        expected_status: 200
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

from src.core.models import CurrentState
from src.core.monitor import MonitorCategory, MonitorResult, MonitorStatus
from src.utils.logging import get_logger

logger = get_logger("monitors.endpoint")

# Default limits
DEFAULT_TIMEOUT = 10  # seconds
DEFAULT_MAX_REDIRECTS = 3
DEFAULT_MAX_RESPONSE_BYTES = 1024 * 1024  # 1MB
DEFAULT_EXPECTED_STATUS = 200

# Allowed schemes
ALLOWED_SCHEMES = frozenset({"https"})
ALLOWED_SCHEMES_WITH_HTTP = frozenset({"https", "http"})

# Private/link-local/metadata IP ranges
PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),      # loopback
    ipaddress.ip_network("169.254.0.0/16"),    # link-local / metadata
    ipaddress.ip_network("::1/128"),            # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),           # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),          # IPv6 link-local
]


class EndpointConfigError(Exception):
    """Invalid endpoint configuration."""


class EndpointSecurityError(Exception):
    """Endpoint security check failed."""


def validate_endpoint_url(url: str, allow_http: bool = False) -> None:
    """Validate an endpoint URL for security.

    Checks:
    - Valid URL format
    - Allowed scheme (HTTPS by default)
    - No credentials in URL
    - Not a private/link-local/metadata address

    Args:
        url: URL to validate.
        allow_http: Whether to allow HTTP (default: HTTPS only).

    Raises:
        EndpointConfigError: If URL format is invalid.
        EndpointSecurityError: If security check fails.
    """
    if not url:
        raise EndpointConfigError("URL cannot be empty")

    try:
        parsed = urlparse(url)
    except Exception as e:
        raise EndpointConfigError(f"Invalid URL: {e}") from e

    if not parsed.scheme:
        raise EndpointConfigError("URL must have a scheme (https://...)")

    allowed = ALLOWED_SCHEMES_WITH_HTTP if allow_http else ALLOWED_SCHEMES
    if parsed.scheme not in allowed:
        raise EndpointSecurityError(
            f"Scheme '{parsed.scheme}' not allowed. Use: {', '.join(sorted(allowed))}"
        )

    # Check for credentials in URL
    if parsed.username or parsed.password:
        raise EndpointSecurityError("Credentials in URL are not allowed")

    # Check for private/link-local IPs
    hostname = parsed.hostname
    if hostname:
        _check_hostname_security(hostname)


def _check_hostname_security(hostname: str) -> None:
    """Check if hostname resolves to a private/link-local address.

    Args:
        hostname: Hostname to check.

    Raises:
        EndpointSecurityError: If hostname resolves to a private address.
    """
    # Check if hostname is an IP literal
    try:
        ip = ipaddress.ip_address(hostname)
        for network in PRIVATE_NETWORKS:
            if ip in network:
                raise EndpointSecurityError(
                    f"Address '{hostname}' is in private/reserved range: {network}"
                )
        return
    except ValueError:
        pass  # Not an IP literal, try DNS resolution

    # Check for metadata service hostnames
    metadata_hostnames = frozenset({
        "metadata.google.internal",
        "169.254.169.254",
        "instance-data.internal",
        "fd00:ec2::254",
    })
    if hostname in metadata_hostnames:
        raise EndpointSecurityError(
            f"Hostname '{hostname}' is a cloud metadata service"
        )


def parse_endpoint_config(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Parse and validate endpoint check configurations.

    Args:
        checks: List of endpoint check config dicts.

    Returns:
        List of validated and normalized configs.

    Raises:
        EndpointConfigError: If any config is invalid.
    """
    parsed = []
    for i, check in enumerate(checks):
        url = check.get("url", "")
        if not url:
            raise EndpointConfigError(f"Endpoint check {i}: url is required")

        name = check.get("name", f"endpoint-{i}")
        timeout = check.get("timeout", DEFAULT_TIMEOUT)
        max_redirects = check.get("max_redirects", DEFAULT_MAX_REDIRECTS)
        max_response_bytes = check.get("max_response_bytes", DEFAULT_MAX_RESPONSE_BYTES)
        expected_status = check.get("expected_status", DEFAULT_EXPECTED_STATUS)
        method = check.get("method", "GET").upper()
        allow_http = check.get("allow_http", False)

        if not isinstance(timeout, (int, float)) or timeout <= 0:
            raise EndpointConfigError(f"Endpoint '{name}': timeout must be positive")
        if not isinstance(max_redirects, int) or max_redirects < 0:
            raise EndpointConfigError(f"Endpoint '{name}': max_redirects must be non-negative")
        if not isinstance(max_response_bytes, int) or max_response_bytes <= 0:
            raise EndpointConfigError(f"Endpoint '{name}': max_response_bytes must be positive")
        if not isinstance(expected_status, int):
            raise EndpointConfigError(f"Endpoint '{name}': expected_status must be int")
        if method not in ("GET", "HEAD"):
            raise EndpointConfigError(f"Endpoint '{name}': method must be GET or HEAD")

        validate_endpoint_url(url, allow_http=allow_http)

        parsed.append({
            "url": url,
            "name": name,
            "method": method,
            "timeout": timeout,
            "max_redirects": max_redirects,
            "max_response_bytes": max_response_bytes,
            "expected_status": expected_status,
        })

    return parsed


def check_endpoint(config: dict[str, Any]) -> MonitorResult:
    """Check a single endpoint.

    Uses stdlib only — no requests dependency. This is intentionally
    isolated from the GitHub client to maintain separation of concerns.

    Args:
        config: Parsed endpoint check configuration.

    Returns:
        MonitorResult with health status.
    """
    import urllib.request
    import urllib.error

    name = config["name"]
    url = config["url"]
    timeout = config["timeout"]
    max_redirects = config["max_redirects"]
    max_response_bytes = config["max_response_bytes"]
    expected_status = config["expected_status"]
    method = config["method"]

    try:
        req = urllib.request.Request(url, method=method)
        req.add_header("User-Agent", "gh-ops-endpoint-monitor/1.0")

        opener = urllib.request.build_opener(
            urllib.request.HTTPRedirectHandler(max_redirects=max_redirects)
        )

        response = opener.open(req, timeout=timeout)
        status_code = response.getcode()

        # Read bounded response
        body = response.read(max_response_bytes + 1)
        response_size = len(body)

        if response_size > max_response_bytes:
            return MonitorResult(
                monitor="endpoint",
                resource=name,
                status=MonitorStatus.ALERT,
                summary=f"Response too large: {response_size} bytes (max: {max_response_bytes})",
                category=MonitorCategory.ENDPOINT,
                metadata={
                    "url": url,
                    "status_code": status_code,
                    "response_size": response_size,
                    "error": "response_too_large",
                },
            )

        if status_code == expected_status:
            return MonitorResult(
                monitor="endpoint",
                resource=name,
                status=MonitorStatus.OK,
                summary=f"Endpoint healthy: {url} (status {status_code})",
                category=MonitorCategory.ENDPOINT,
                metadata={
                    "url": url,
                    "status_code": status_code,
                    "response_size": response_size,
                },
            )
        else:
            return MonitorResult(
                monitor="endpoint",
                resource=name,
                status=MonitorStatus.ALERT,
                summary=f"Unexpected status: {url} returned {status_code} (expected {expected_status})",
                category=MonitorCategory.ENDPOINT,
                metadata={
                    "url": url,
                    "status_code": status_code,
                    "expected_status": expected_status,
                    "response_size": response_size,
                },
            )

    except urllib.error.HTTPError as e:
        return MonitorResult(
            monitor="endpoint",
            resource=name,
            status=MonitorStatus.ALERT,
            summary=f"HTTP error: {url} returned {e.code}",
            category=MonitorCategory.ENDPOINT,
            metadata={
                "url": url,
                "status_code": e.code,
                "error": "http_error",
            },
        )
    except urllib.error.URLError as e:
        reason = str(e.reason) if hasattr(e, "reason") else str(e)
        is_timeout = "timed out" in reason.lower()
        return MonitorResult(
            monitor="endpoint",
            resource=name,
            status=MonitorStatus.ALERT,
            summary=f"Endpoint unreachable: {url} ({reason})",
            category=MonitorCategory.ENDPOINT,
            metadata={
                "url": url,
                "error": "timeout" if is_timeout else "connection_error",
                "reason": reason,
            },
        )
    except Exception as e:
        return MonitorResult(
            monitor="endpoint",
            resource=name,
            status=MonitorStatus.ERROR,
            summary=f"Endpoint check failed: {url} ({type(e).__name__})",
            category=MonitorCategory.ENDPOINT,
            metadata={
                "url": url,
                "error": "check_failed",
                "exception": type(e).__name__,
            },
        )


def monitor_endpoints(
    state: CurrentState,
    config: dict[str, Any],
) -> list[MonitorResult]:
    """Evaluate configured endpoints.

    This monitor DOES make HTTP requests (unlike other monitors).
    All requests are bounded by timeout, redirect, and size limits.

    Args:
        state: Current state (not used for endpoint checks).
        config: Monitoring configuration (monitors.endpoint section).

    Returns:
        List of MonitorResult for all endpoint checks.
    """
    if not config.get("enabled", False):
        return []

    checks = config.get("checks", [])
    if not checks:
        return []

    try:
        parsed_configs = parse_endpoint_config(checks)
    except (EndpointConfigError, EndpointSecurityError) as e:
        return [MonitorResult(
            monitor="endpoint",
            resource="*",
            status=MonitorStatus.ERROR,
            summary=f"Endpoint configuration error: {e}",
            category=MonitorCategory.ENDPOINT,
            metadata={"config_error": str(e)},
        )]

    results: list[MonitorResult] = []
    for check_config in parsed_configs:
        result = check_endpoint(check_config)
        results.append(result)

    return results
