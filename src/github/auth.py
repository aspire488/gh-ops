"""GitHub token resolution for gh-ops.

Reads GITHUB_TOKEN from environment variables only.
Never persists, logs, or exposes credentials.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from src.core.errors import GhOpsError, ErrorCode, ErrorSeverity
from src.utils.logging import get_logger

logger = get_logger("github.auth")

# Environment variable names to check (in order of preference)
_TOKEN_ENV_VARS = [
    "GITHUB_TOKEN",
    "GH_TOKEN",
]


@dataclass(frozen=True)
class AuthConfig:
    """Resolved authentication configuration.

    Attributes:
        token: The GitHub token (never logged or printed).
        token_source: Which env var provided the token.
    """

    token: str
    token_source: str

    def auth_headers(self) -> dict[str, str]:
        """Generate HTTP headers for authenticated requests.

        Returns:
            Dict with Authorization and Accept headers.
        """
        return {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def __repr__(self) -> str:
        """Safe repr that never exposes the token."""
        return f"AuthConfig(token_source={self.token_source!r})"


def resolve_auth() -> AuthConfig:
    """Resolve GitHub authentication from environment variables.

    Checks GITHUB_TOKEN, then GH_TOKEN.

    Returns:
        AuthConfig with resolved token.

    Raises:
        GhOpsError: If no token is found.
    """
    for env_var in _TOKEN_ENV_VARS:
        token = os.environ.get(env_var, "").strip()
        if token:
            logger.info("GitHub token resolved from %s", env_var)
            return AuthConfig(token=token, token_source=env_var)

    raise GhOpsError(
        code=ErrorCode.API_AUTH_FAILED,
        message="No GitHub token found. Set GITHUB_TOKEN or GH_TOKEN environment variable.",
        module="github.auth",
        severity=ErrorSeverity.CRITICAL,
    )


def validate_token_format(token: str) -> bool:
    """Basic format validation for GitHub tokens.

    Does NOT validate the token works — only checks format.

    Args:
        token: The token to validate.

    Returns:
        True if format looks valid.
    """
    if not token:
        return False

    # GitHub token formats:
    # - Classic PAT: ghp_... (40+ chars after prefix)
    # - Fine-grained PAT: github_pat_... (82+ chars after prefix)
    # - OAuth: gho_... (36+ chars after prefix)
    # - App installation: ghs_... (36+ chars after prefix)
    valid_prefixes = ("ghp_", "gho_", "ghs_", "ghr_", "github_pat_")
    has_valid_prefix = any(token.startswith(p) for p in valid_prefixes)

    # Also accept tokens without known prefix (e.g., from GitHub Actions)
    # but log a warning
    if not has_valid_prefix:
        logger.warning(
            "Token does not match known GitHub token format. "
            "This may be valid (e.g., GitHub Actions token) but cannot be format-validated."
        )

    return len(token) > 0
