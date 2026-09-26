"""Tests for developer activity extraction (Phase 6)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.core.models import CurrentState, EventType, ResourceEvent
from src.developer.activity import (
    ActivityRecord,
    ActivityType,
    DataQuality,
    _extract_repo_name,
    _parse_ts,
    deduplicate_activity,
    extract_activity,
    filter_activity,
    summarize_activity,
)

# ── Helpers ──────────────────────────────────────────────────────


def _make_issue(
    number: int = 1,
    state: str = "open",
    user: str = "testuser",
    created_at: str = "2025-01-15T10:00:00Z",
    closed_at: str | None = None,
    comments: int = 0,
    repo_url: str = "https://api.github.com/repos/testowner/testrepo",
    title: str = "Test Issue",
) -> dict:
    return {
        "id": number + 1000,
        "number": number,
        "title": title,
        "state": state,
        "repository_url": repo_url,
        "user": user,
        "labels": [],
        "assignee": None,
        "assignees": [],
        "comments": comments,
        "created_at": created_at,
        "updated_at": "2025-01-15T10:00:00Z",
        "closed_at": closed_at,
        "html_url": f"https://github.com/testowner/testrepo/issues/{number}",
        "author_association": "OWNER",
        "locked": False,
        "draft": False,
    }


def _make_pr(
    number: int = 1,
    state: str = "open",
    user: str = "testuser",
    created_at: str = "2025-01-15T10:00:00Z",
    merged: bool = False,
    merged_at: str | None = None,
    closed_at: str | None = None,
    additions: int = 10,
    deletions: int = 5,
    repo_url: str = "https://api.github.com/repos/testowner/testrepo",
) -> dict:
    return {
        "id": number + 2000,
        "number": number,
        "title": f"Test PR #{number}",
        "state": state,
        "repository_url": repo_url,
        "user": user,
        "head_branch": "feature",
        "base_branch": "main",
        "draft": False,
        "merged": merged,
        "merged_at": merged_at,
        "created_at": created_at,
        "updated_at": "2025-01-15T10:00:00Z",
        "closed_at": closed_at,
        "html_url": f"https://github.com/testowner/testrepo/pull/{number}",
        "author_association": "OWNER",
        "changed_files": 2,
        "additions": additions,
        "deletions": deletions,
    }


def _make_release(
    tag: str = "v1.0.0",
    author: str = "testuser",
    published_at: str = "2025-01-15T12:00:00Z",
) -> dict:
    return {
        "id": 5000,
        "tag_name": tag,
        "name": f"Release {tag}",
        "body": "Release notes",
        "draft": False,
        "prerelease": False,
        "created_at": "2025-01-15T11:00:00Z",
        "published_at": published_at,
        "html_url": f"https://github.com/testowner/testrepo/releases/{tag}",
        "author": author,
        "assets": [],
    }


def _make_workflow(
    run_id: int = 1,
    name: str = "CI",
    conclusion: str = "success",
    created_at: str = "2025-01-15T13:00:00Z",
) -> dict:
    return {
        "id": run_id,
        "name": name,
        "status": "completed",
        "conclusion": conclusion,
        "run_number": 1,
        "event": "push",
        "created_at": created_at,
        "updated_at": created_at,
        "run_started_at": created_at,
        "html_url": f"https://github.com/testowner/testrepo/actions/runs/{run_id}",
        "head_branch": "main",
        "head_sha": "abc123",
    }


def _make_state(**resources) -> CurrentState:
    return CurrentState(
        resources=resources,
        update_log={
            name: {"status": "success", "observed_at": "2025-01-15T14:00:00Z", "item_count": len(items)}
            for name, items in resources.items()
        },
    )


# ── Unit tests ───────────────────────────────────────────────────


class TestExtractRepoName:
    def test_standard_url(self):
        assert _extract_repo_name("https://api.github.com/repos/owner/repo") == "owner/repo"

    def test_trailing_slash(self):
        assert _extract_repo_name("https://api.github.com/repos/owner/repo/") == "owner/repo"

    def test_empty_string(self):
        assert _extract_repo_name("") == ""

    def test_short_url(self):
        assert _extract_repo_name("repos/owner/repo") == "owner/repo"


class TestParseTimestamp:
    def test_valid_timestamp(self):
        result = _parse_ts("2025-01-15T10:00:00Z")
        assert result is not None
        assert result.year == 2025

    def test_none_returns_none(self):
        assert _parse_ts(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_ts("") is None

    def test_non_string_returns_none(self):
        assert _parse_ts(12345) is None


class TestActivityType:
    def test_all_values(self):
        values = [t.value for t in ActivityType]
        assert "issue_created" in values
        assert "pr_opened" in values
        assert "pr_merged" in values
        assert "release_published" in values
        assert "workflow_run" in values


class TestActivityRecord:
    def test_creation(self):
        record = ActivityRecord(
            activity_type=ActivityType.ISSUE_CREATED,
            timestamp=datetime(2025, 1, 15, tzinfo=timezone.utc),
            repository="owner/repo",
            item_id="owner/repo#1",
            title="Test",
        )
        assert record.activity_type == ActivityType.ISSUE_CREATED
        assert record.repository == "owner/repo"

    def test_to_dict(self):
        record = ActivityRecord(
            activity_type=ActivityType.ISSUE_CREATED,
            timestamp=datetime(2025, 1, 15, tzinfo=timezone.utc),
            repository="owner/repo",
            item_id="owner/repo#1",
            title="Test",
            meta={"labels": ["bug"]},
        )
        d = record.to_dict()
        assert d["activity_type"] == "issue_created"
        assert d["repository"] == "owner/repo"
        assert d["meta"]["labels"] == ["bug"]

    def test_frozen(self):
        record = ActivityRecord(
            activity_type=ActivityType.ISSUE_CREATED,
            timestamp=datetime(2025, 1, 15, tzinfo=timezone.utc),
            repository="owner/repo",
            item_id="owner/repo#1",
        )
        with pytest.raises(AttributeError):
            record.activity_type = ActivityType.PR_OPENED


class TestDataQuality:
    def test_creation(self):
        dq = DataQuality(collector="issues", is_complete=True, item_count=10)
        assert dq.collector == "issues"
        assert dq.is_complete is True

    def test_to_dict(self):
        dq = DataQuality(
            collector="issues",
            is_complete=False,
            error="timeout",
            item_count=5,
        )
        d = dq.to_dict()
        assert d["collector"] == "issues"
        assert d["is_complete"] is False
        assert d["error"] == "timeout"


# ── Issue extraction ─────────────────────────────────────────────


class TestExtractIssueActivity:
    def test_new_issue_creates_created_activity(self):
        issue = _make_issue(number=1, state="open")
        state = _make_state(issues={"1": issue})
        records, quality = extract_activity(state, username="testuser")

        assert len(records) == 1
        assert records[0].activity_type == ActivityType.ISSUE_CREATED
        assert records[0].item_id == "testowner/testrepo#1"
        assert records[0].state == "open"

    def test_closed_issue_creates_closed_activity(self):
        issue = _make_issue(
            number=1, state="closed", closed_at="2025-01-16T10:00:00Z",
        )
        state = _make_state(issues={"1": issue})
        records, quality = extract_activity(state, username="testuser")

        assert len(records) == 2
        types = {r.activity_type for r in records}
        assert ActivityType.ISSUE_CREATED in types
        assert ActivityType.ISSUE_CLOSED in types

    def test_different_user_filtered_out(self):
        issue = _make_issue(number=1, user="otheruser")
        state = _make_state(issues={"1": issue})
        records, quality = extract_activity(state, username="testuser")

        assert len(records) == 0

    def test_no_username_includes_all(self):
        issue = _make_issue(number=1, user="anyuser")
        state = _make_state(issues={"1": issue})
        records, quality = extract_activity(state, username=None)

        assert len(records) == 1

    def test_multiple_issues(self):
        issues = {
            "1": _make_issue(number=1, state="open"),
            "2": _make_issue(number=2, state="closed", closed_at="2025-01-16T10:00:00Z"),
            "3": _make_issue(number=3, state="open"),
        }
        state = _make_state(issues=issues)
        records, quality = extract_activity(state, username="testuser")

        created = [r for r in records if r.activity_type == ActivityType.ISSUE_CREATED]
        assert len(created) == 3

    def test_issue_timestamps_from_resource(self):
        issue = _make_issue(number=1, created_at="2024-06-01T08:00:00Z")
        state = _make_state(issues={"1": issue})
        records, quality = extract_activity(state, username="testuser")

        assert records[0].timestamp.year == 2024
        assert records[0].timestamp.month == 6


# ── PR extraction ────────────────────────────────────────────────


class TestExtractPrActivity:
    def test_new_pr_creates_opened_activity(self):
        pr = _make_pr(number=1, state="open")
        state = _make_state(pulls={"1": pr})
        records, quality = extract_activity(state, username="testuser")

        assert len(records) == 1
        assert records[0].activity_type == ActivityType.PR_OPENED
        assert records[0].item_id == "testowner/testrepo#1"

    def test_merged_pr_creates_merged_activity(self):
        pr = _make_pr(
            number=1, state="closed", merged=True,
            merged_at="2025-01-16T14:00:00Z",
        )
        state = _make_state(pulls={"1": pr})
        records, quality = extract_activity(state, username="testuser")

        types = {r.activity_type for r in records}
        assert ActivityType.PR_OPENED in types
        assert ActivityType.PR_MERGED in types

    def test_pr_metadata_captured(self):
        pr = _make_pr(number=1, additions=50, deletions=20)
        state = _make_state(pulls={"1": pr})
        records, quality = extract_activity(state, username="testuser")

        assert records[0].meta["additions"] == 50
        assert records[0].meta["deletions"] == 20


# ── Release extraction ───────────────────────────────────────────


class TestExtractReleaseActivity:
    def test_new_release_creates_activity(self):
        release = _make_release(tag="v1.0.0")
        state = _make_state(releases={"v1.0.0": release})
        records, quality = extract_activity(state, username="testuser")

        assert len(records) == 1
        assert records[0].activity_type == ActivityType.RELEASE_PUBLISHED
        assert records[0].item_id == "v1.0.0"

    def test_different_author_filtered(self):
        release = _make_release(author="otheruser")
        state = _make_state(releases={"v1.0.0": release})
        records, quality = extract_activity(state, username="testuser")

        assert len(records) == 0


# ── Workflow extraction ──────────────────────────────────────────


class TestExtractWorkflowActivity:
    def test_workflow_run_creates_activity(self):
        wf = _make_workflow(conclusion="success")
        state = _make_state(workflows={"1": wf})
        records, quality = extract_activity(state, username="testuser")

        assert len(records) == 1
        assert records[0].activity_type == ActivityType.WORKFLOW_RUN
        assert records[0].state == "success"

    def test_workflow_failure_captured(self):
        wf = _make_workflow(conclusion="failure")
        state = _make_state(workflows={"1": wf})
        records, quality = extract_activity(state, username="testuser")

        assert records[0].state == "failure"

    def test_workflow_record_derives_repository(self):
        wf = _make_workflow(conclusion="success")
        state = _make_state(workflows={"testowner/testrepo/run/7": wf})
        records, _ = extract_activity(state, username="testuser")

        workflow_records = [
            r for r in records if r.activity_type == ActivityType.WORKFLOW_RUN
        ]
        assert workflow_records
        assert workflow_records[0].repository == "testowner/testrepo"

    def test_ghops_system_workflow_excluded_from_state(self):
        wf = _make_workflow(run_id=12, name="Security", conclusion="failure")
        state = _make_state(workflows={"aspire488/gh-ops/run/12": wf})
        records, _ = extract_activity(state, username="testuser")

        assert not [r for r in records if r.activity_type == ActivityType.WORKFLOW_RUN]

    def test_ghops_system_workflow_excluded_from_events(self):
        event = ResourceEvent(
            event_type=EventType.NEW,
            resource_type="workflows",
            resource_id="aspire488/gh-ops/run/12",
            current=_make_workflow(run_id=12, name="Daily", conclusion="success"),
        )
        records, _ = extract_activity(_make_state(), (event,), username="testuser")

        assert not [r for r in records if r.activity_type == ActivityType.WORKFLOW_RUN]

    def test_ghops_unlisted_workflow_kept(self):
        wf = _make_workflow(run_id=13, name="Deploy")
        state = _make_state(workflows={"aspire488/gh-ops/run/13": wf})
        records, _ = extract_activity(state, username="testuser")

        assert [r for r in records if r.activity_type == ActivityType.WORKFLOW_RUN]

    def test_system_workflow_name_on_other_repo_kept(self):
        wf = _make_workflow(run_id=3, name="Security")
        state = _make_state(workflows={"testowner/testrepo/run/3": wf})
        records, _ = extract_activity(state, username="testuser")

        workflow_records = [
            r for r in records if r.activity_type == ActivityType.WORKFLOW_RUN
        ]
        assert workflow_records
        assert workflow_records[0].repository == "testowner/testrepo"


# ── Data quality ─────────────────────────────────────────────────


class TestDataQualityTracking:
    def test_success_collector_is_complete(self):
        state = _make_state(issues={"1": _make_issue()})
        _, quality = extract_activity(state)

        assert len(quality) == 1
        assert quality[0].is_complete is True

    def test_failure_collector_not_complete(self):
        state = CurrentState(
            resources={"issues": {"1": _make_issue()}},
            update_log={
                "issues": {"status": "failure", "observed_at": "2025-01-15T14:00:00Z", "error": "timeout"},
            },
        )
        _, quality = extract_activity(state)

        assert len(quality) == 1
        assert quality[0].is_complete is False
        assert quality[0].error == "timeout"

    def test_empty_state_no_quality(self):
        state = CurrentState()
        _, quality = extract_activity(state)

        assert len(quality) == 0


# ── Filtering ────────────────────────────────────────────────────


class TestFilterActivity:
    def test_filter_by_type(self):
        records = [
            ActivityRecord(
                activity_type=ActivityType.ISSUE_CREATED,
                timestamp=datetime(2025, 1, 15, tzinfo=timezone.utc),
                repository="owner/repo", item_id="owner/repo#1",
            ),
            ActivityRecord(
                activity_type=ActivityType.PR_OPENED,
                timestamp=datetime(2025, 1, 15, tzinfo=timezone.utc),
                repository="owner/repo", item_id="owner/repo#2",
            ),
        ]
        filtered = filter_activity(records, activity_types=[ActivityType.ISSUE_CREATED])
        assert len(filtered) == 1
        assert filtered[0].activity_type == ActivityType.ISSUE_CREATED

    def test_filter_by_repository(self):
        records = [
            ActivityRecord(
                activity_type=ActivityType.ISSUE_CREATED,
                timestamp=datetime(2025, 1, 15, tzinfo=timezone.utc),
                repository="owner/repo-a", item_id="owner/repo-a#1",
            ),
            ActivityRecord(
                activity_type=ActivityType.ISSUE_CREATED,
                timestamp=datetime(2025, 1, 15, tzinfo=timezone.utc),
                repository="owner/repo-b", item_id="owner/repo-b#1",
            ),
        ]
        filtered = filter_activity(records, repositories=["owner/repo-a"])
        assert len(filtered) == 1
        assert filtered[0].repository == "owner/repo-a"

    def test_filter_by_time_range(self):
        records = [
            ActivityRecord(
                activity_type=ActivityType.ISSUE_CREATED,
                timestamp=datetime(2025, 1, 10, tzinfo=timezone.utc),
                repository="owner/repo", item_id="owner/repo#1",
            ),
            ActivityRecord(
                activity_type=ActivityType.ISSUE_CREATED,
                timestamp=datetime(2025, 1, 20, tzinfo=timezone.utc),
                repository="owner/repo", item_id="owner/repo#2",
            ),
        ]
        filtered = filter_activity(
            records,
            since=datetime(2025, 1, 15, tzinfo=timezone.utc),
        )
        assert len(filtered) == 1
        assert filtered[0].item_id == "owner/repo#2"


# ── Deduplication ────────────────────────────────────────────────


class TestDeduplicateActivity:
    def test_no_duplicates(self):
        records = [
            ActivityRecord(
                activity_type=ActivityType.ISSUE_CREATED,
                timestamp=datetime(2025, 1, 15, tzinfo=timezone.utc),
                repository="owner/repo", item_id="owner/repo#1",
            ),
        ]
        result = deduplicate_activity(records)
        assert len(result) == 1

    def test_removes_duplicates(self):
        records = [
            ActivityRecord(
                activity_type=ActivityType.ISSUE_CREATED,
                timestamp=datetime(2025, 1, 15, tzinfo=timezone.utc),
                repository="owner/repo", item_id="owner/repo#1",
            ),
            ActivityRecord(
                activity_type=ActivityType.ISSUE_CREATED,
                timestamp=datetime(2025, 1, 15, tzinfo=timezone.utc),
                repository="owner/repo", item_id="owner/repo#1",
            ),
        ]
        result = deduplicate_activity(records)
        assert len(result) == 1

    def test_different_items_not_deduped(self):
        records = [
            ActivityRecord(
                activity_type=ActivityType.ISSUE_CREATED,
                timestamp=datetime(2025, 1, 15, tzinfo=timezone.utc),
                repository="owner/repo", item_id="owner/repo#1",
            ),
            ActivityRecord(
                activity_type=ActivityType.ISSUE_CREATED,
                timestamp=datetime(2025, 1, 15, tzinfo=timezone.utc),
                repository="owner/repo", item_id="owner/repo#2",
            ),
        ]
        result = deduplicate_activity(records)
        assert len(result) == 2


# ── Summarize ────────────────────────────────────────────────────


class TestSummarizeActivity:
    def test_summary_counts(self):
        records = [
            ActivityRecord(
                activity_type=ActivityType.ISSUE_CREATED,
                timestamp=datetime(2025, 1, 15, tzinfo=timezone.utc),
                repository="owner/repo", item_id="owner/repo#1",
            ),
            ActivityRecord(
                activity_type=ActivityType.ISSUE_CREATED,
                timestamp=datetime(2025, 1, 15, tzinfo=timezone.utc),
                repository="owner/repo", item_id="owner/repo#2",
            ),
            ActivityRecord(
                activity_type=ActivityType.PR_OPENED,
                timestamp=datetime(2025, 1, 15, tzinfo=timezone.utc),
                repository="owner/repo", item_id="owner/repo#3",
            ),
        ]
        summary = summarize_activity(records)
        assert summary["issue_created"] == 2
        assert summary["pr_opened"] == 1

    def test_empty_records(self):
        assert summarize_activity([]) == {}
