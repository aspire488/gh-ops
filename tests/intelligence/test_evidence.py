"""Tests for src.intelligence.evidence (deterministic evidence packs)."""
from __future__ import annotations

from src.intelligence.evidence import MAX_EVIDENCE_ITEMS, build_evidence
from src.intelligence.temporal import TemporalProfile, TemporalState
from src.reporting.model import ReportEvent, Severity


def _event(
    identity: str = "ci:owner/repo/run/1:failure",
    *,
    subsystem: str = "ci",
    severity: Severity = Severity.IMPORTANT,
    repository: str = "owner/repo",
    title: str = "CI failed",
    url: str = "",
    metadata: dict | None = None,
    timestamp: str = "2026-09-24T12:00:00+00:00",
) -> ReportEvent:
    return ReportEvent(
        subsystem=subsystem,
        event_type="failure",
        severity=severity,
        title=title,
        repository=repository,
        url=url,
        identity=identity,
        timestamp=timestamp,
        source=subsystem,
        metadata=dict(metadata or {}),
    )


class TestBuildEvidence:
    def test_duplicate_identities_collapse_to_one_item(self):
        pack = build_evidence([_event(), _event()])
        assert len(pack.items) == 1

    def test_ordered_by_severity_then_subsystem(self):
        events = [
            _event("oss:a:info", subsystem="oss", severity=Severity.INFORMATION),
            _event("ci:b:fail", severity=Severity.ACTION_REQUIRED),
            _event("ci:c:fail", severity=Severity.IMPORTANT),
        ]
        pack = build_evidence(events)
        assert [item.severity for item in pack.items] == [
            "action_required",
            "important",
            "information",
        ]

    def test_limit_caps_item_count(self):
        events = [
            _event(f"ci:owner/repo/run/{index}:failure", title=f"run {index}")
            for index in range(MAX_EVIDENCE_ITEMS + 10)
        ]
        pack = build_evidence(events)
        assert len(pack.items) == MAX_EVIDENCE_ITEMS
        assert pack.items[-1].eid == f"E{MAX_EVIDENCE_ITEMS}"

    def test_evidence_ids_are_positional(self):
        pack = build_evidence([_event(), _event("ci:owner/repo/run/2:failure")])
        assert pack.ids == frozenset({"E1", "E2"})

    def test_temporal_summary_attached_from_profiles(self):
        profile = TemporalProfile(
            condition="ci:owner/repo/run/1",
            state=TemporalState.PERSISTENT,
            observations=4,
            first_observed="",
            last_observed="",
        )
        pack = build_evidence(
            [_event()],
            profiles={"ci:owner/repo/run/1": profile},
        )
        assert pack.items[0].temporal == "persistent across 4 observations"

    def test_facts_copied_from_known_metadata_keys_only(self):
        pack = build_evidence(
            [
                _event(
                    metadata={
                        "branch": "main",
                        "workflow_name": "build",
                        "secret_token": "should-not-appear",
                    }
                )
            ]
        )
        facts = dict(pack.items[0].facts)
        assert facts == {"branch": "main", "workflow_name": "build"}

    def test_empty_facts_skipped(self):
        pack = build_evidence([_event(metadata={"branch": "", "conclusion": None})])
        assert pack.items[0].facts == ()


class TestPackDerivations:
    def test_repositories_and_urls_exclude_empties(self):
        pack = build_evidence(
            [
                _event(repository="owner/repo", url="https://example.test/a"),
                _event("ci:other:failure", repository="", url=""),
            ]
        )
        assert pack.repositories == frozenset({"owner/repo"})
        assert pack.urls == frozenset({"https://example.test/a"})

    def test_slugs_include_slash_bearing_fact_values(self):
        pack = build_evidence([_event(metadata={"branch": "feature/login"})])
        assert "owner/repo" in pack.slugs
        assert "feature/login" in pack.slugs

    def test_item_numbers_from_references_and_urls(self):
        pack = build_evidence(
            [
                _event(
                    "developer:owner/repo#42:issue",
                    url="https://github.com/owner/repo/issues/42",
                ),
                _event("developer:owner/repo#7:pr", url=""),
            ]
        )
        assert pack.item_numbers == frozenset({42, 7})

    def test_subsystem_counts(self):
        pack = build_evidence(
            [
                _event(),
                _event("ci:owner/repo/run/2:failure"),
                _event("security:owner/repo/alert:1", subsystem="security"),
            ]
        )
        assert pack.subsystem_counts() == {"ci": 2, "security": 1}

    def test_has_security_action_requires_open_security_finding(self):
        action = build_evidence(
            [
                _event(
                    "security:owner/repo/alert:1",
                    subsystem="security",
                    severity=Severity.ACTION_REQUIRED,
                )
            ]
        )
        info = build_evidence(
            [
                _event(
                    "security:owner/repo/alert:2",
                    subsystem="security",
                    severity=Severity.INFORMATION,
                )
            ]
        )
        assert action.has_security_action() is True
        assert info.has_security_action() is False

    def test_count_severity(self):
        pack = build_evidence(
            [
                _event(),
                _event("ci:owner/repo/run/2:failure", severity=Severity.ACTION_REQUIRED),
            ]
        )
        assert pack.count_severity("important") == 1
        assert pack.count_severity("action_required") == 1


class TestEvidenceBlock:
    def test_block_contains_header_and_metadata(self):
        pack = build_evidence([_event(url="https://example.test/a")])
        block = pack.items[0].block
        assert block.startswith("E1 [ci|important] owner/repo · CI failed")
        assert "observed 2026-09-24T12:00:00+00:00" in block

    def test_block_includes_facts_when_present(self):
        pack = build_evidence([_event(metadata={"branch": "main"})])
        assert "branch=main" in pack.items[0].block
