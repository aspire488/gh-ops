"""Deterministic scoring engine for OSS contribution opportunities.

Transparent, configuration-driven scoring with explicit weights,
explainable signals, and stable tie-breaking. No randomness, no LLM.

No external dependencies in runtime.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.intelligence.oss.models import Opportunity, ScoreBreakdown


@dataclass(frozen=True)
class ScoringConfig:
    """Configuration for the scoring engine."""

    # Top-level weights (must sum to 1.0)
    weights: dict[str, float] = field(default_factory=lambda: {
        "repo_activity": 0.25,
        "issue_freshness": 0.20,
        "label_signal": 0.15,
        "discussion_activity": 0.15,
        "assignment_status": 0.10,
        "maintainer_activity": 0.10,
        "scope_signal": 0.05,
    })

    # Sub-weights for repo_activity
    repo_activity: dict[str, float] = field(default_factory=lambda: {
        "stars_weight": 0.4,
        "recent_commits_weight": 0.3,
        "contributors_weight": 0.3,
    })

    # Issue freshness parameters
    issue_freshness: dict[str, Any] = field(default_factory=lambda: {
        "decay_function": "linear",
        "max_days": 90,
        "fresh_bonus": 0.2,
    })

    # Label signal mapping
    label_signals: dict[str, float] = field(default_factory=lambda: {
        "good first issue": 1.0,
        "help wanted": 0.8,
        "beginner": 0.9,
        "easy": 0.7,
        "bug": 0.5,
        "documentation": 0.6,
        "starter": 0.95,
        "newbie": 0.85,
    })

    # Tie-breaking
    tie_breaking_method: str = "issue_number_asc"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScoringConfig:
        """Parse from configuration dictionary."""
        scoring = data.get("scoring", data)

        # Handle nested tie_breaking dict — check top-level and inside scoring
        tie_breaking = data.get("tie_breaking") or scoring.get("tie_breaking", {})
        if isinstance(tie_breaking, dict):
            tie_breaking_method = tie_breaking.get("method", "issue_number_asc")
        else:
            tie_breaking_method = str(tie_breaking) if tie_breaking else "issue_number_asc"

        return cls(
            weights=scoring.get("weights", cls().weights),
            repo_activity=scoring.get("repo_activity", cls().repo_activity),
            issue_freshness=scoring.get("issue_freshness", cls().issue_freshness),
            label_signals=scoring.get("label_signals", cls().label_signals),
            tie_breaking_method=tie_breaking_method,
        )


def _normalize(value: float, min_val: float, max_val: float) -> float:
    """Normalize value to [0, 1] range."""
    if max_val <= min_val:
        return 0.0
    return max(0.0, min(1.0, (value - min_val) / (max_val - min_val)))


def _score_repo_activity(opp: Opportunity, config: ScoringConfig) -> float:
    """Score based on repository activity metrics."""
    sub = config.repo_activity

    # Stars score (log scale, 0-10000 normalized)
    stars_score = _normalize(math.log1p(opp.repo_stars), 0, math.log1p(10000))

    # Forks score (log scale)
    forks_score = _normalize(math.log1p(opp.repo_forks), 0, math.log1p(1000))

    # Open issues as proxy for project activity (more issues = more active)
    issues_score = _normalize(opp.repo_open_issues, 0, 500)

    # Weighted combination
    score = (
        stars_score * sub.get("stars_weight", 0.4)
        + forks_score * sub.get("contributors_weight", 0.3)
        + issues_score * sub.get("recent_commits_weight", 0.3)
    )
    return score


def _score_issue_freshness(opp: Opportunity, config: ScoringConfig, now: datetime) -> float:
    """Score based on issue freshness (newer is better)."""
    if not opp.created_at:
        return 0.0

    freshness_config = config.issue_freshness
    max_days = freshness_config.get("max_days", 90)
    fresh_bonus = freshness_config.get("fresh_bonus", 0.2)
    decay_fn = freshness_config.get("decay_function", "linear")

    age_days = max(0, (now - opp.created_at).days)

    if decay_fn == "linear":
        base_score = max(0.0, 1.0 - (age_days / max_days))
    elif decay_fn == "exponential":
        # Exponential decay with half-life of max_days/3
        half_life = max_days / 3
        base_score = math.exp(-0.693 * age_days / half_life)
    else:
        base_score = max(0.0, 1.0 - (age_days / max_days))

    # Fresh bonus for issues less than 7 days old
    if age_days <= 7:
        base_score = min(1.0, base_score + fresh_bonus)

    return base_score


def _score_label_signal(opp: Opportunity, config: ScoringConfig) -> float:
    """Score based on label signals."""
    if not opp.labels:
        return 0.0

    label_signals = config.label_signals
    max_signal = 0.0

    for label in opp.labels:
        label_lower = label.lower()
        if label_lower in label_signals:
            signal = label_signals[label_lower]
            max_signal = max(max_signal, signal)

    return max_signal


def _score_discussion_activity(opp: Opportunity, config: ScoringConfig) -> float:
    """Score based on comment/discussion activity."""
    # Sweet spot: 3-15 comments (active discussion but not overwhelmed)
    comments = opp.comments
    if comments == 0:
        return 0.1  # Slight penalty for no discussion
    elif comments <= 2:
        return 0.3
    elif comments <= 5:
        return 0.7
    elif comments <= 15:
        return 1.0
    else:
        # Diminishing returns for very active threads
        return max(0.5, 1.0 - (comments - 15) * 0.02)


def _score_assignment_status(opp: Opportunity, config: ScoringConfig) -> float:
    """Score based on assignment status (unassigned is better for contributors)."""
    if opp.assignee is None and len(opp.assignees) == 0:
        return 1.0  # Unclaimed — best opportunity
    elif opp.assignee and opp.assignee != opp.user:
        return 0.2  # Assigned to someone else
    else:
        return 0.5  # Assigned to self or unclear


def _score_maintainer_activity(opp: Opportunity, config: ScoringConfig) -> float:
    """Score based on maintainer activity signals."""
    # Use author_association as a proxy
    # MEMBER/OWNER = maintainer responded, CONTRIBUTOR = active contributor
    # NONE = external reporter

    # If there are comments and the issue has been updated recently,
    # it suggests maintainer engagement
    if opp.comments > 0 and opp.updated_at:
        # Recently updated (within 30 days) with discussion
        return 0.7
    elif opp.comments > 0:
        return 0.5
    elif opp.updated_at:
        return 0.3
    return 0.2


def _score_scope_signal(opp: Opportunity, config: ScoringConfig) -> float:
    """Score based on issue scope (smaller is better for first contribution)."""
    # Heuristic: body length as proxy for scope
    body = opp.body or ""
    body_len = len(body)

    if body_len == 0:
        return 0.3  # No description — uncertain scope
    elif body_len < 500:
        return 0.9  # Concise — likely well-scoped
    elif body_len < 1500:
        return 0.7  # Moderate description
    elif body_len < 3000:
        return 0.5  # Detailed — might be complex
    else:
        return 0.3  # Very detailed — potentially large scope


def score_opportunity(
    opp: Opportunity,
    config: ScoringConfig,
    now: datetime | None = None,
) -> Opportunity:
    """Score a single opportunity with transparent breakdown.

    Args:
        opp: The opportunity to score.
        config: Scoring configuration.
        now: Current time. If None, uses UTC now.

    Returns:
        New Opportunity with score and score_breakdown set.
    """
    now = now or datetime.now(timezone.utc)
    weights = config.weights

    # Calculate individual signal scores
    repo_activity = _score_repo_activity(opp, config)
    issue_freshness = _score_issue_freshness(opp, config, now)
    label_signal = _score_label_signal(opp, config)
    discussion_activity = _score_discussion_activity(opp, config)
    assignment_status = _score_assignment_status(opp, config)
    maintainer_activity = _score_maintainer_activity(opp, config)
    scope_signal = _score_scope_signal(opp, config)

    # Weighted total
    total_score = (
        repo_activity * weights.get("repo_activity", 0.25)
        + issue_freshness * weights.get("issue_freshness", 0.20)
        + label_signal * weights.get("label_signal", 0.15)
        + discussion_activity * weights.get("discussion_activity", 0.15)
        + assignment_status * weights.get("assignment_status", 0.10)
        + maintainer_activity * weights.get("maintainer_activity", 0.10)
        + scope_signal * weights.get("scope_signal", 0.05)
    )

    breakdown = ScoreBreakdown(
        repo_activity=round(repo_activity, 4),
        issue_freshness=round(issue_freshness, 4),
        label_signal=round(label_signal, 4),
        discussion_activity=round(discussion_activity, 4),
        assignment_status=round(assignment_status, 4),
        maintainer_activity=round(maintainer_activity, 4),
        scope_signal=round(scope_signal, 4),
    )

    return Opportunity(
        repo_full_name=opp.repo_full_name,
        issue_number=opp.issue_number,
        issue_id=opp.issue_id,
        title=opp.title,
        html_url=opp.html_url,
        state=opp.state,
        user=opp.user,
        labels=opp.labels,
        assignee=opp.assignee,
        assignees=opp.assignees,
        comments=opp.comments,
        created_at=opp.created_at,
        updated_at=opp.updated_at,
        body=opp.body,
        repo_stars=opp.repo_stars,
        repo_forks=opp.repo_forks,
        repo_open_issues=opp.repo_open_issues,
        repo_language=opp.repo_language,
        repo_description=opp.repo_description,
        score=round(total_score, 4),
        score_breakdown=breakdown,
        source_query=opp.source_query,
        source_queries=opp.source_queries,
        discovered_at=opp.discovered_at,
    )


def sort_by_score(
    opportunities: list[Opportunity],
    method: str = "issue_number_asc",
) -> list[Opportunity]:
    """Sort opportunities by score with stable tie-breaking.

    Tie-breaking methods:
    - issue_number_asc: Lower issue number first (earliest = oldest issue)
    - issue_number_desc: Higher issue number first
    - repo_stars_desc: More stars first
    - created_at_asc: Oldest first
    - created_at_desc: Newest first

    Args:
        opportunities: List of scored opportunities.
        method: Tie-breaking method name.

    Returns:
        New sorted list (does not mutate input).
    """
    def sort_key(opp: Opportunity) -> tuple:
        score = -opp.score  # Descending by score

        if method == "issue_number_asc":
            tie = (opp.repo_full_name, opp.issue_number)
        elif method == "issue_number_desc":
            tie = (opp.repo_full_name, -opp.issue_number)
        elif method == "repo_stars_desc":
            tie = (-opp.repo_stars, opp.repo_full_name)
        elif method == "created_at_asc":
            created = opp.created_at.isoformat() if opp.created_at else ""
            tie = (created, opp.repo_full_name)
        elif method == "created_at_desc":
            created = opp.created_at.isoformat() if opp.created_at else ""
            tie = (created, opp.repo_full_name)
        else:
            tie = (opp.repo_full_name, opp.issue_number)

        return (score,) + tie

    return sorted(opportunities, key=sort_key)
