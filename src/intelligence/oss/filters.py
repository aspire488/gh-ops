"""Filter pipeline for OSS contribution opportunities.

Deterministic, composable filters that remove ineligible opportunities.
No external dependencies in runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from src.intelligence.oss.models import Opportunity


@dataclass(frozen=True)
class FilterConfig:
    """Configuration for the filter pipeline."""

    exclude_labels: tuple[str, ...] = (
        "wontfix", "duplicate", "invalid", "wip", "not-a-bug",
    )
    exclude_bot_authors: bool = True
    exclude_locked: bool = True
    exclude_draft: bool = True
    exclude_pull_requests: bool = True
    min_repo_stars: int = 0
    max_issue_age_days: int | None = None
    exclude_repos: tuple[str, ...] = ()
    require_labels: tuple[str, ...] = ()
    require_language: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FilterConfig:
        """Parse from configuration dictionary."""
        return cls(
            exclude_labels=tuple(data.get("exclude_labels", [])),
            exclude_bot_authors=data.get("exclude_bot_authors", True),
            exclude_locked=data.get("exclude_locked", True),
            exclude_draft=data.get("exclude_draft", True),
            exclude_pull_requests=data.get("exclude_pull_requests", True),
            min_repo_stars=data.get("min_repo_stars", 0),
            max_issue_age_days=data.get("max_issue_age_days"),
            exclude_repos=tuple(data.get("exclude_repos", [])),
            require_labels=tuple(data.get("require_labels", [])),
            require_language=data.get("require_language"),
        )


@dataclass
class FilterResult:
    """Result of filtering a list of opportunities."""

    passed: list[Opportunity] = field(default_factory=list)
    excluded_by_rule: dict[str, int] = field(default_factory=dict)
    total_input: int = 0

    @property
    def total_excluded(self) -> int:
        return sum(self.excluded_by_rule.values())

    @property
    def pass_rate(self) -> float:
        if self.total_input == 0:
            return 0.0
        return len(self.passed) / self.total_input


def _is_bot(user: str) -> bool:
    """Check if a user is a bot account."""
    bot_indicators = ("[bot]", "-bot", "bot-", "dependabot", "renovate")
    user_lower = user.lower()
    return any(indicator in user_lower for indicator in bot_indicators)


def filter_opportunities(
    opportunities: list[Opportunity],
    config: FilterConfig,
    now: datetime | None = None,
) -> FilterResult:
    """Apply filter pipeline to opportunities.

    Filters are applied in order. Each filter is independent — an opportunity
    excluded by one filter is not checked by subsequent filters.

    Args:
        opportunities: List of opportunities to filter.
        config: Filter configuration.
        now: Current time for age calculations. If None, uses UTC now.

    Returns:
        FilterResult with passed opportunities and exclusion counts.
    """
    now = now or datetime.now(timezone.utc)
    result = FilterResult(total_input=len(opportunities))

    exclude_label_set = set(config.exclude_labels)
    require_label_set = set(config.require_labels)
    exclude_repo_set = set(config.exclude_repos)

    for opp in opportunities:
        excluded = False

        # Rule 1: Excluded labels
        if exclude_label_set and exclude_label_set.intersection(opp.labels):
            result.excluded_by_rule["excluded_label"] = result.excluded_by_rule.get("excluded_label", 0) + 1
            excluded = True

        # Rule 2: Bot authors
        if not excluded and config.exclude_bot_authors and opp.user and _is_bot(opp.user):
            result.excluded_by_rule["bot_author"] = result.excluded_by_rule.get("bot_author", 0) + 1
            excluded = True

        # Rule 3: Locked issues
        if not excluded and config.exclude_locked and opp.locked if hasattr(opp, 'locked') else False:
            result.excluded_by_rule["locked"] = result.excluded_by_rule.get("locked", 0) + 1
            excluded = True

        # Rule 4: Draft issues
        if not excluded and config.exclude_draft and opp.draft if hasattr(opp, 'draft') else False:
            result.excluded_by_rule["draft"] = result.excluded_by_rule.get("draft", 0) + 1
            excluded = True

        # Rule 5: Minimum repo stars
        if not excluded and config.min_repo_stars > 0 and opp.repo_stars < config.min_repo_stars:
            result.excluded_by_rule["min_stars"] = result.excluded_by_rule.get("min_stars", 0) + 1
            excluded = True

        # Rule 6: Max issue age
        if not excluded and config.max_issue_age_days is not None and opp.created_at:
            age_days = (now - opp.created_at).days
            if age_days > config.max_issue_age_days:
                result.excluded_by_rule["max_age"] = result.excluded_by_rule.get("max_age", 0) + 1
                excluded = True

        # Rule 7: Excluded repos
        if not excluded and opp.repo_full_name in exclude_repo_set:
            result.excluded_by_rule["excluded_repo"] = result.excluded_by_rule.get("excluded_repo", 0) + 1
            excluded = True

        # Rule 8: Required labels
        if not excluded and require_label_set:
            opp_labels = set(opp.labels)
            if not require_label_set.issubset(opp_labels):
                result.excluded_by_rule["missing_required_label"] = result.excluded_by_rule.get("missing_required_label", 0) + 1
                excluded = True

        # Rule 9: Required language
        if not excluded and config.require_language:
            if opp.repo_language and opp.repo_language.lower() != config.require_language.lower():
                result.excluded_by_rule["wrong_language"] = result.excluded_by_rule.get("wrong_language", 0) + 1
                excluded = True

        if not excluded:
            result.passed.append(opp)

    return result


def compose_filters(
    *filter_fns: Callable[[Opportunity], bool],
) -> Callable[[Opportunity], bool]:
    """Compose multiple filter functions into a single predicate.

    Returns True only if ALL filters pass (AND logic).
    """
    def combined(opp: Opportunity) -> bool:
        return all(fn(opp) for fn in filter_fns)
    return combined
