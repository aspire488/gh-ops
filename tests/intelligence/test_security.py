"""Tests for Security Intelligence radar (analyze_security / models)."""
from __future__ import annotations

from src.core.models import CurrentState
from src.intelligence.security import (
    DEFAULT_ALERT_SEVERITIES,
    SEVERITY_RANK,
    RadarConfig,
    SecurityFinding,
    analyze_security,
    extract_findings,
    normalize_severity,
    parse_identity,
    severity_rank,
)


def _state(security: dict) -> CurrentState:
    return CurrentState(resources={"security": dict(security)})


def _item(**overrides):
    data = {
        "id": 1,
        "package_name": "left-pad",
        "severity": "high",
        "summary": "Prototype pollution",
        "state": "open",
        "html_url": "https://example.invalid/1",
    }
    data.update(overrides)
    return data


class TestSeverityHelpers:
    def test_normalize_known(self):
        assert normalize_severity("CRITICAL") == "critical"
        assert normalize_severity("High") == "high"

    def test_normalize_unknown(self):
        assert normalize_severity("blocker") == "unknown"
        assert normalize_severity(None) == "unknown"
        assert normalize_severity("") == "unknown"

    def test_rank_orders_most_severe_first(self):
        assert severity_rank("critical") < severity_rank("high")
        assert severity_rank("high") < severity_rank("medium")
        assert severity_rank("medium") < severity_rank("low")
        assert severity_rank("low") < severity_rank("unknown")
        assert severity_rank("blocker") == SEVERITY_RANK["unknown"]

    def test_default_alert_severities(self):
        assert frozenset({"critical", "high"}) == DEFAULT_ALERT_SEVERITIES


class TestParseIdentity:
    def test_dependabot_key(self):
        assert parse_identity("octo/repo/dependabot/9") == ("octo/repo", "dependabot")

    def test_codescan_key(self):
        assert parse_identity("octo/repo/codescan/3") == ("octo/repo", "codescan")

    def test_unexpected_shape_falls_back(self):
        assert parse_identity("weird") == ("weird", "security")


class TestSecurityFinding:
    def test_from_state_item_parses_identity(self):
        finding = SecurityFinding.from_state_item("octo/repo/dependabot/9", _item())
        assert finding.repository == "octo/repo"
        assert finding.source == "dependabot"
        assert finding.severity == "high"
        assert finding.is_open is True

    def test_non_dict_data_tolerated(self):
        finding = SecurityFinding.from_state_item("octo/repo/dependabot/9", None)  # type: ignore[arg-type]
        assert finding.severity == "unknown"
        assert finding.state == "open"

    def test_to_dict_is_deterministic(self):
        finding = SecurityFinding.from_state_item("octo/repo/dependabot/9", _item())
        assert finding.to_dict() == finding.to_dict()


class TestRadarConfig:
    def test_defaults(self):
        config = RadarConfig.from_dict(None)
        assert config.enabled is True
        assert config.alert_severities == frozenset(DEFAULT_ALERT_SEVERITIES)
        assert config.max_findings == 50

    def test_parses_severities_and_max(self):
        config = RadarConfig.from_dict({
            "enabled": False,
            "alert_severities": ["critical", "MEDIUM"],
            "max_findings": 5,
        })
        assert config.enabled is False
        assert config.alert_severities == frozenset({"critical", "medium"})
        assert config.max_findings == 5

    def test_invalid_max_findings_falls_back(self):
        assert RadarConfig.from_dict({"max_findings": "nope"}).max_findings == 50
        assert RadarConfig.from_dict({"max_findings": -1}).max_findings == 0


class TestAnalyzeSecurity:
    def test_empty_state(self):
        report = analyze_security(CurrentState())
        assert report.total == 0
        assert report.actionable == ()
        assert report.by_severity == {}
        assert report.open_count == 0

    def test_sorts_by_severity_then_identity(self):
        state = _state({
            "octo/b/dependabot/1": _item(severity="low"),
            "octo/a/dependabot/2": _item(severity="critical"),
            "octo/a/dependabot/1": _item(severity="critical"),
        })
        report = analyze_security(state)
        identities = [f.identity for f in report.findings]
        assert identities == [
            "octo/a/dependabot/1",
            "octo/a/dependabot/2",
            "octo/b/dependabot/1",
        ]

    def test_actionable_filters_open_and_severity(self):
        state = _state({
            "octo/a/dependabot/1": _item(severity="critical"),
            "octo/a/dependabot/2": _item(severity="medium"),
            "octo/a/dependabot/3": _item(severity="high", state="dismissed"),
            "octo/a/dependabot/4": _item(severity="high"),
        })
        report = analyze_security(state)
        assert [f.identity for f in report.actionable] == [
            "octo/a/dependabot/1",
            "octo/a/dependabot/4",
        ]
        assert report.actionable_count == 2
        assert report.open_count == 3
        assert report.total == 4

    def test_by_severity_counts(self):
        state = _state({
            "octo/a/dependabot/1": _item(severity="critical"),
            "octo/a/dependabot/2": _item(severity="critical"),
            "octo/a/dependabot/3": _item(severity="low"),
        })
        report = analyze_security(state)
        assert report.by_severity == {"critical": 2, "low": 1}

    def test_max_findings_caps_report_body(self):
        security = {
            f"octo/a/dependabot/{i}": _item(severity="low") for i in range(10)
        }
        report = analyze_security(_state(security), {"max_findings": 3})
        assert report.total == 3
        assert len(report.findings) == 3

    def test_max_findings_zero_means_unlimited(self):
        security = {
            f"octo/a/dependabot/{i}": _item(severity="low") for i in range(10)
        }
        report = analyze_security(_state(security), {"max_findings": 0})
        assert report.total == 10

    def test_custom_alert_severities(self):
        state = _state({
            "octo/a/dependabot/1": _item(severity="medium"),
            "octo/a/dependabot/2": _item(severity="low"),
        })
        report = analyze_security(state, {"alert_severities": ["medium"]})
        assert [f.identity for f in report.actionable] == ["octo/a/dependabot/1"]

    def test_accepts_radar_config_instance(self):
        state = _state({"octo/a/dependabot/1": _item(severity="low")})
        report = analyze_security(state, RadarConfig(alert_severities=frozenset({"low"})))
        assert report.actionable_count == 1

    def test_non_security_resource_ignored(self):
        state = CurrentState(resources={"repos": {"x": {}}, "security": {}})
        assert extract_findings(state) == []

    def test_non_mapping_security_key_tolerated(self):
        state = CurrentState(resources={"security": "bogus"})  # type: ignore[dict-item]
        assert extract_findings(state) == []

    def test_report_to_dict_is_json_ready(self):
        state = _state({"octo/a/dependabot/1": _item()})
        payload = analyze_security(state).to_dict()
        assert payload["total"] == 1
        assert payload["findings"][0]["identity"] == "octo/a/dependabot/1"
        assert payload["alert_severities"] == ["critical", "high"]
