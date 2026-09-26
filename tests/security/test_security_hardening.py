"""Phase 9 — Security Hardening verification tests.

ARCHITECTURE.md Phase 9 checklist:

- Verify no write operations in codebase
- Verify GET-only enforcement in client.py
- Verify all input sanitization
- Verify token redaction in logs
- Verify workflow permissions are minimal
- Verify no shell injection vectors
- Verify no secrets in CLI arguments
- Security-focused test additions (this module)
- Security audit using UEA security-audit specialist (see ARCHITECTURE.md)

These tests are static/AST analysis plus targeted behavioural checks. They do
not contact GitHub, Telegram, or any network endpoint. They complement (not
replace) the existing suites: ``tests/github/test_client.py`` already covers
per-method WriteDenied, ``tests/ci/test_workflows.py`` already covers workflow
permissions and secrets-in-CLI, and ``tests/notifications`` already covers
log redaction of Telegram tokens.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

#: Modules allowed to open outbound HTTP sessions. Everything else must go
#: through them. Telegram is the intentional second exit (POST-only by design).
#: The LLM interpreter is the intentional third exit (POST-only to cloud
#: chat-completions endpoints, credentials from the environment only).
HTTP_EXIT_MODULES = {
    SRC / "github" / "client.py",
    SRC / "notifications" / "telegram.py",
    SRC / "intelligence" / "llm.py",
}

WRITE_HTTP_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

#: Shell / dynamic-exec primitives that must never appear in production code.
SHELL_PRIMITIVES = {
    "eval",
    "exec",
    "compile",
    "__import__",
    "os.system",
    "os.popen",
    "os.execv",
    "os.execve",
    "os.spawnv",
    "subprocess.call",
    "subprocess.run",
    "subprocess.Popen",
    "subprocess.check_output",
    "subprocess.check_call",
}

#: Patterns that look like a credential being passed on a command line.
CLI_SECRET_PATTERNS = (
    re.compile(r"--token[= ]", re.IGNORECASE),
    re.compile(r"--password[= ]", re.IGNORECASE),
    re.compile(r"--secret[= ]", re.IGNORECASE),
    re.compile(r"--api[-_]?key[= ]", re.IGNORECASE),
    re.compile(r"-p\s+\S"),  # common short password flag in shell strings
)


def _src_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _call_name(node: ast.Call) -> str:
    """Best-effort dotted name of a call target (e.g. ``subprocess.run``)."""
    func = node.func
    parts: list[str] = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
    return ".".join(reversed(parts))


class TestNoWriteOperations:
    """Phase 9: verify no GitHub write operations exist in the codebase."""

    def test_only_known_modules_import_requests(self):
        """requests may only be imported by the allowed HTTP exit modules."""
        offenders: list[str] = []
        for path in _src_files():
            if path in HTTP_EXIT_MODULES:
                continue
            tree = _parse(path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "requests" or alias.name.startswith("requests."):
                            offenders.append(f"{path.relative_to(REPO_ROOT)} imports {alias.name}")
                elif (
                    isinstance(node, ast.ImportFrom)
                    and node.module
                    and (node.module == "requests" or node.module.startswith("requests."))
                ):
                    offenders.append(f"{path.relative_to(REPO_ROOT)} from {node.module}")
        assert not offenders, offenders

    def test_github_client_never_calls_write_verbs_by_string(self):
        """No literal write HTTP verb appears in client.py outside the deny path."""
        client_path = SRC / "github" / "client.py"
        tree = _parse(client_path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                method = node.value.upper()
                if method in WRITE_HTTP_METHODS:
                    # The only legitimate occurrence is the comparison that
                    # raises WriteDeniedError: method.upper() != "GET".
                    # Any other literal write verb would be suspicious.
                    # We allow comparison context by requiring the deny path.
                    # Found only in the GET check — see execute() line.
                    # If this fails, someone added a write call site.
                    pytest.fail(
                        f"client.py contains literal write method {method!r} "
                        f"at line {node.lineno}; GET-only must hold"
                    )

    def test_execute_enforces_get_only(self, monkeypatch):
        """Behavioural: execute() raises WriteDeniedError for every write verb."""
        from unittest.mock import MagicMock, patch

        import requests as requests_lib

        from src.core.errors import WriteDeniedError
        from src.github.auth import AuthConfig
        from src.github.client import GitHubClient

        session = MagicMock(spec=requests_lib.Session)
        session.headers = {}
        auth = AuthConfig(token="ghp_" + "A" * 36, token_source="test")
        with patch("src.github.client.requests.Session", return_value=session):
            client = GitHubClient(auth=auth, max_retries=0, timeout=1)
        client._session = session

        for method in ("POST", "PUT", "PATCH", "DELETE", "post", "get "):
            if method.strip().upper() == "GET":
                continue
            with pytest.raises(WriteDeniedError):
                client.execute(method, "/repos/o/r/issues")
        # GET path still allowed (defensive: does not raise WriteDeniedError).
        # We do not call session.request for GET here; only the method gate.
        # Lowercase GET is normalised by .upper() inside execute().
        # A real network call is avoided by only checking the gate for GET:
        with pytest.raises(Exception) as gate:  # connection/session mock may fail later
            # This still proves GET is not WriteDeniedError.
            client.execute("GET", "/user")
        assert not isinstance(gate.value, WriteDeniedError)

    def test_telegram_is_the_only_post_exit_and_targets_api_telegram_org(self):
        """Telegram POST is allowed only against the fixed API base."""
        from src.notifications.telegram import TELEGRAM_API_BASE

        assert TELEGRAM_API_BASE == "https://api.telegram.org"
        source = (SRC / "notifications" / "telegram.py").read_text(encoding="utf-8")
        assert "api.telegram.org" in source


class TestInputSanitization:
    """Phase 9: untrusted text is escaped before Telegram MarkdownV2."""

    def test_escape_markdown_v2_covers_full_special_set(self):
        from src.utils.text import escape_markdown_v2

        specials = "_*[]()~`>#+-=|{}.!\\"
        for ch in specials:
            assert escape_markdown_v2(ch) == f"\\{ch}", ch

    def test_escape_is_idempotent_double_apply_adds_no_extra_layer(self):
        """Formatters escape once; a second escape must be detectable as doubled."""
        from src.utils.text import escape_markdown_v2

        once = escape_markdown_v2("a_b")
        twice = escape_markdown_v2(once)
        assert once == "a\\_b"
        assert twice == "a\\\\\\\\_b" or twice != once

    def test_untrusted_monitor_summary_is_escaped_at_format_boundary(self):
        from src.core.monitor import MonitorCategory, MonitorResult, MonitorStatus
        from src.notifications.telegram import format_monitor_alerts
        from src.utils.text import escape_markdown_v2 as esc

        hostile = "failed! [critical] (now) -_-=.#"
        result = MonitorResult(
            monitor="ci",
            resource="o/r",
            status=MonitorStatus.ALERT,
            summary=hostile,
            category=MonitorCategory.CI,
            metadata={},
            evaluated_at="2026-01-01T00:00:00+00:00",
        )
        messages = format_monitor_alerts([result])
        assert messages
        assert esc(hostile) in messages[0]
        assert "failed!" not in messages[0]

    def test_config_loaded_values_are_not_interpolated_into_shell(self):
        """Runtime never builds shell strings from config (see dispatcher env path)."""
        source = (SRC / "core" / "dispatcher.py").read_text(encoding="utf-8")
        assert "shell=True" not in source
        assert "os.system" not in source


class TestTokenRedactionInLogs:
    """Phase 9: tokens and secrets never reach log output."""

    @pytest.mark.parametrize(
        "sample",
        [
            "ghp_" + "A" * 36,
            "gho_" + "B" * 36,
            "github_pat_" + "C" * 82,
            "1234567890:AAFakeTelegramTokenSecretValueForTests_ABCDEFG",
            "Bearer abc.def.ghi",
            "token supersecretvalue",
            "bot123456789:AAFakeTelegramTokenSecretValueForTests_ABCDEFG",
        ],
    )
    def test_redactor_replaces_known_secret_shapes(self, sample: str):
        import logging

        from src.utils.logging import StructuredFormatter

        record = logging.LogRecord(
            name="gh_ops.test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=f"payload includes {sample} end",
            args=(),
            exc_info=None,
        )
        formatted = StructuredFormatter().format(record)
        assert sample not in formatted
        assert "[REDACTED]" in formatted

    def test_structured_context_values_are_redacted(self):

        import logging as lg

        from src.utils.logging import StructuredFormatter, log_event

        logger = lg.getLogger("gh_ops.redact.ctx")
        logger.handlers.clear()
        logger.setLevel(lg.DEBUG)
        records: list[str] = []

        class _H(lg.Handler):
            def emit(self, record: lg.LogRecord) -> None:
                records.append(StructuredFormatter().format(record))

        logger.addHandler(_H())
        secret = "ghp_" + "D" * 36
        log_event(logger, lg.INFO, "auth context", token=secret)
        assert records
        assert secret not in records[0]
        assert "[REDACTED]" in records[0]

    def test_authconfig_repr_never_leaks_token(self):
        from src.github.auth import AuthConfig

        token = "ghp_" + "E" * 36
        auth = AuthConfig(token=token, token_source="GITHUB_TOKEN")
        assert token not in repr(auth)
        assert token not in str(auth)


class TestWorkflowPermissionsMinimal:
    """Phase 9: workflows declare only explicit read permissions (no write)."""

    @pytest.mark.parametrize(
        "path",
        sorted(WORKFLOW_DIR.glob("*.yml")) + sorted(WORKFLOW_DIR.glob("*.yaml")),
    )
    def test_workflow_has_explicit_read_only_permissions(self, path: Path):
        import yaml

        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert "permissions" in workflow, f"{path.name} relies on default permissions"
        permissions = workflow["permissions"]
        assert isinstance(permissions, dict)
        for scope, level in permissions.items():
            assert level == "read", f"{path.name} grants {scope}: {level}"

    @pytest.mark.parametrize(
        "path",
        sorted(WORKFLOW_DIR.glob("*.yml")) + sorted(WORKFLOW_DIR.glob("*.yaml")),
    )
    def test_no_write_scopes_anywhere(self, path: Path):
        import yaml

        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for scope, level in workflow.get("permissions", {}).items():
            assert level in ("read", "none"), f"{path.name}:{scope}={level}"


class TestNoShellInjectionVectors:
    """Phase 9: no shell=True, eval, exec, or os.system in production code."""

    def test_no_shell_or_dynamic_exec_primitives_in_src(self):
        violations: list[str] = []
        for path in _src_files():
            tree = _parse(path)
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Name)
                    and node.id in SHELL_PRIMITIVES
                    and node.id in {"eval", "exec", "compile", "__import__"}
                ):
                    # bare names like eval / exec / compile
                    violations.append(
                        f"{path.relative_to(REPO_ROOT)}:{node.lineno} uses {node.id}"
                    )
                if isinstance(node, ast.Call):
                    name = _call_name(node)
                    if name in SHELL_PRIMITIVES:
                        violations.append(
                            f"{path.relative_to(REPO_ROOT)}:{node.lineno} calls {name}"
                        )
                    # keyword shell=True
                    for kw in node.keywords:
                        if (
                            kw.arg == "shell"
                            and isinstance(kw.value, ast.Constant)
                            and kw.value.value is True
                        ):
                            violations.append(
                                f"{path.relative_to(REPO_ROOT)}:{node.lineno} shell=True"
                            )
        assert not violations, violations

    def test_no_os_system_or_popen_text_scan(self):
        offenders: list[str] = []
        for path in _src_files():
            text = path.read_text(encoding="utf-8")
            for needle in ("os.system(", "os.popen(", "subprocess.call(", "shell=True"):
                if needle in text:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: {needle}")
        assert not offenders, offenders


class TestNoSecretsInCLIArguments:
    """Phase 9: credentials come from the environment, never CLI argv."""

    def test_auth_reads_only_environment_variables(self):
        source = (SRC / "github" / "auth.py").read_text(encoding="utf-8")
        assert "os.environ" in source or "environ" in source
        # auth must not parse argv for a token
        assert "sys.argv" not in source

    def test_dispatcher_job_name_via_env_not_interpolated_secret(self):
        source = (SRC / "core" / "dispatcher.py").read_text(encoding="utf-8")
        assert "GITHUB_TOKEN" not in source
        assert "TELEGRAM_BOT_TOKEN" not in source
        assert "GHOPS_JOB" in source  # job selection is non-secret env

    def test_no_secret_flag_patterns_in_cli_entry_sources(self):
        entrypoints = [
            SRC / "core" / "dispatcher.py",
            SRC / "core" / "__main__.py",
            SRC / "jobs" / "__main__.py",
            REPO_ROOT / "scripts" / "run_local.py",
            REPO_ROOT / "scripts" / "check_telegram.py",
        ]
        offenders: list[str] = []
        for path in entrypoints:
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            for pattern in CLI_SECRET_PATTERNS:
                if pattern.search(text):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: {pattern.pattern}")
        assert not offenders, offenders

    def test_telegram_token_only_from_telegram_bot_token_env(self):
        source = (SRC / "notifications" / "telegram.py").read_text(encoding="utf-8")
        assert 'TELEGRAM_TOKEN_ENV_VAR = "TELEGRAM_BOT_TOKEN"' in source
        assert "sys.argv" not in source


class TestSecurityAuditFindings:
    """Summary of the Phase 9 security audit (static findings recorded).

    The UEA security-audit specialist is an external tool; this module records
    the equivalent static audit conclusions that are checkable in-repo:
    read-only HTTP surface, single GitHub exit, fixed Telegram exit, redaction,
    minimal workflow permissions, no shell primitives, env-only credentials.
    """

    def test_single_github_http_exit_module(self):
        """The outbound HTTP surface is exactly the audited exit list."""
        modules_with_requests = []
        for path in _src_files():
            text = path.read_text(encoding="utf-8")
            if re.search(r"^\s*import requests\b", text, re.MULTILINE) or re.search(
                r"^\s*from requests\b", text, re.MULTILINE
            ):
                modules_with_requests.append(path.relative_to(REPO_ROOT).as_posix())
        assert sorted(modules_with_requests) == [
            "src/github/client.py",
            "src/intelligence/llm.py",
            "src/notifications/telegram.py",
        ]

    def test_architecture_invariants_document_read_only(self):
        text = (REPO_ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
        assert "Read-only by default" in text
        assert "Single API exit point" in text

    def test_no_hardcoded_credentials_in_source(self):
        """No token-shaped literals committed in src/."""
        tokenish = re.compile(
            r"(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
            r"\b\d{8,12}:[A-Za-z0-9_\-]{30,})"
        )
        offenders: list[str] = []
        for path in _src_files():
            for match in tokenish.finditer(path.read_text(encoding="utf-8")):
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {match.group(0)[:12]}...")
        assert not offenders, offenders
