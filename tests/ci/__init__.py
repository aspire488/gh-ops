"""Static validation of the GitHub Actions execution layer.

Parses .github/workflows/*.yml and asserts the security, permission, pinning,
and state-persistence invariants documented in ARCHITECTURE.md. No test here
triggers a workflow or contacts GitHub.
"""
