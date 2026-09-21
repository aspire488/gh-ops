"""Tests for src.monitors.endpoint (Phase 4 endpoint monitor security)."""
import pytest
from src.core.models import CurrentState
from src.core.monitor import MonitorCategory, MonitorStatus
from src.monitors.endpoint import (
    EndpointConfigError,
    EndpointSecurityError,
    check_endpoint,
    monitor_endpoints,
    parse_endpoint_config,
    validate_endpoint_url,
)


def _make_state() -> CurrentState:
    return CurrentState(resources={}, update_log={})


class TestValidateEndpointUrl:
    def test_valid_https(self):
        validate_endpoint_url("https://example.com/health")

    def test_empty_url_raises(self):
        with pytest.raises(EndpointConfigError, match="cannot be empty"):
            validate_endpoint_url("")

    def test_no_scheme_raises(self):
        with pytest.raises(EndpointConfigError, match="must have a scheme"):
            validate_endpoint_url("example.com/health")

    def test_http_not_allowed_by_default(self):
        with pytest.raises(EndpointSecurityError, match="not allowed"):
            validate_endpoint_url("http://example.com/health")

    def test_http_allowed_when_configured(self):
        validate_endpoint_url("http://example.com/health", allow_http=True)

    def test_ftp_not_allowed(self):
        with pytest.raises(EndpointSecurityError, match="not allowed"):
            validate_endpoint_url("ftp://example.com/health")

    def test_credentials_in_url_rejected(self):
        with pytest.raises(EndpointSecurityError, match="Credentials"):
            validate_endpoint_url("https://user:pass@example.com/health")

    def test_username_only_rejected(self):
        with pytest.raises(EndpointSecurityError, match="Credentials"):
            validate_endpoint_url("https://user@example.com/health")

    def test_loopback_ip_rejected(self):
        with pytest.raises(EndpointSecurityError, match="private"):
            validate_endpoint_url("https://127.0.0.1/health")

    def test_private_ip_10_rejected(self):
        with pytest.raises(EndpointSecurityError, match="private"):
            validate_endpoint_url("https://10.0.0.1/health")

    def test_private_ip_192_rejected(self):
        with pytest.raises(EndpointSecurityError, match="private"):
            validate_endpoint_url("https://192.168.1.1/health")

    def test_private_ip_172_rejected(self):
        with pytest.raises(EndpointSecurityError, match="private"):
            validate_endpoint_url("https://172.16.0.1/health")

    def test_link_local_rejected(self):
        with pytest.raises(EndpointSecurityError, match="private"):
            validate_endpoint_url("https://169.254.169.254/health")

    def test_metadata_hostname_rejected(self):
        with pytest.raises(EndpointSecurityError, match="metadata"):
            validate_endpoint_url("https://metadata.google.internal/health")


class TestParseEndpointConfig:
    def test_valid_config(self):
        checks = [
            {
                "url": "https://example.com/health",
                "name": "example",
                "timeout": 5,
                "expected_status": 200,
            }
        ]
        result = parse_endpoint_config(checks)
        assert len(result) == 1
        assert result[0]["name"] == "example"
        assert result[0]["timeout"] == 5

    def test_defaults_applied(self):
        checks = [{"url": "https://example.com/health"}]
        result = parse_endpoint_config(checks)
        assert result[0]["name"] == "endpoint-0"
        assert result[0]["timeout"] == 10
        assert result[0]["max_redirects"] == 3
        assert result[0]["max_response_bytes"] == 1048576
        assert result[0]["expected_status"] == 200
        assert result[0]["method"] == "GET"

    def test_missing_url_raises(self):
        with pytest.raises(EndpointConfigError, match="url is required"):
            parse_endpoint_config([{"name": "test"}])

    def test_invalid_timeout_raises(self):
        with pytest.raises(EndpointConfigError, match="timeout"):
            parse_endpoint_config([
                {"url": "https://example.com", "timeout": -1}
            ])

    def test_invalid_max_redirects_raises(self):
        with pytest.raises(EndpointConfigError, match="max_redirects"):
            parse_endpoint_config([
                {"url": "https://example.com", "max_redirects": -1}
            ])

    def test_invalid_max_response_bytes_raises(self):
        with pytest.raises(EndpointConfigError, match="max_response_bytes"):
            parse_endpoint_config([
                {"url": "https://example.com", "max_response_bytes": 0}
            ])

    def test_invalid_method_raises(self):
        with pytest.raises(EndpointConfigError, match="method must be GET or HEAD"):
            parse_endpoint_config([
                {"url": "https://example.com", "method": "POST"}
            ])

    def test_http_rejected_in_config(self):
        with pytest.raises(EndpointSecurityError):
            parse_endpoint_config([
                {"url": "http://example.com/health"}
            ])

    def test_http_allowed_with_config(self):
        result = parse_endpoint_config([
            {"url": "http://example.com/health", "allow_http": True}
        ])
        assert len(result) == 1


class TestMonitorEndpoints:
    def test_disabled_returns_empty(self):
        state = _make_state()
        results = monitor_endpoints(state, {"enabled": False})
        assert results == []

    def test_no_checks_returns_empty(self):
        state = _make_state()
        results = monitor_endpoints(state, {"enabled": True, "checks": []})
        assert results == []

    def test_config_error_returns_error(self):
        state = _make_state()
        results = monitor_endpoints(state, {
            "enabled": True,
            "checks": [{"name": "bad"}],  # missing url
        })
        assert len(results) == 1
        assert results[0].status == MonitorStatus.ERROR
        assert "configuration error" in results[0].summary.lower()
