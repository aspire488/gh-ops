"""Static validation of the GitHub Actions workflows.

These tests parse the workflow YAML and assert the security and state invariants
documented in ARCHITECTURE.md. Nothing here triggers a workflow or contacts
GitHub: it is pure static analysis, so it runs in the normal test suite.

The point is that a future edit which drops a permission, unpins an action,
interpolates untrusted input into a shell command, or reverts the state cache to
a run-id-only key fails a test instead of silently shipping.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from src.jobs.jobs import ALL_JOB_NAMES

WORKFLOW_DIR = Path(__file__).resolve().parents[2] / ".github" / "workflows"

#: Expected workflow filename -> (job name, cron schedules).
EXPECTED_WORKFLOWS = {
    "daily.yml": ("daily", ["0 8 * * *"]),
    "monitoring.yml": ("monitoring", ["0 */2 * * *"]),
    "weekly-report.yml": ("weekly-report", ["0 9 * * 1"]),
    "oss-hunter.yml": ("oss-hunt", ["0 10 * * *"]),
    "security.yml": ("security", ["0 6 * * *"]),
    "manual.yml": ("manual", []),
}

#: Workflows that read-modify-write the shared state file.
STATE_WRITING = (
    "daily.yml",
    "monitoring.yml",
    "weekly-report.yml",
    "oss-hunter.yml",
    "security.yml",
    "manual.yml",
)

#: Workflows that never touch state. Every workflow must appear in exactly
#: one of STATE_WRITING or STATE_FREE — asserted by test_state_lists_partition.
STATE_FREE: tuple[str, ...] = ("ci.yml",)

#: Repo CI workflows that do not dispatch a gh-ops job. They are validated for
#: presence and state-partition only; job-dispatch tests use EXPECTED_WORKFLOWS.
SUPPORT_WORKFLOWS: tuple[str, ...] = ("ci.yml",)

#: SHA -> the version comment it must carry, so pins stay auditable.
PINNED_ACTIONS = {
    "actions/checkout": (
        "11bd71901bbe5b1630ceea73d27597364c9af683",
        "v4.2.2",
    ),
    "actions/setup-python": (
        "ece7cb06caefa5fff74198d8649806c4678c61a1",
        "v6.3.0",
    ),
    "actions/cache": (
        "55cc8345863c7cc4c66a329aec7e433d2d1c52a9",
        "v6.1.0",
    ),
}

SHA40 = re.compile(r"^[0-9a-f]{40}$")
INTERPOLATION = re.compile(r"\$\{\{.*?\}\}", re.DOTALL)


# ── Helpers ──────────────────────────────────────────────────────


def load_workflow(filename: str) -> dict:
    """Parse a workflow file.

    PyYAML resolves the bare ``on`` key to the boolean True (YAML 1.1), so the
    trigger block is read back through ``_triggers``.
    """
    path = WORKFLOW_DIR / filename
    assert path.exists(), f"missing workflow: {filename}"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    """Return the ``on:`` block, working around PyYAML's boolean coercion."""
    return workflow.get("on", workflow.get(True, {}))


def _steps(workflow: dict) -> list[dict]:
    """All steps across all jobs."""
    collected: list[dict] = []
    for job in workflow.get("jobs", {}).values():
        collected.extend(job.get("steps", []))
    return collected


def _uses_steps(workflow: dict) -> list[dict]:
    return [step for step in _steps(workflow) if "uses" in step]


def _run_steps(workflow: dict) -> list[dict]:
    return [step for step in _steps(workflow) if "run" in step]


def _main_run_step(workflow: dict) -> dict:
    """The step that actually executes a gh-ops job.

    Distinguishes it from the dependency-install step, which also uses ``run:``
    but carries no job selection.
    """
    for step in _run_steps(workflow):
        if "GHOPS_JOB" in step.get("env", {}):
            return step
    raise AssertionError("no step dispatches a gh-ops job")


def all_workflow_names() -> list[str]:
    return sorted(EXPECTED_WORKFLOWS)


# ── Presence and structure ───────────────────────────────────────


class TestWorkflowSet:
    def test_exactly_the_expected_workflows_exist(self):
        found = sorted(p.name for p in WORKFLOW_DIR.glob("*.yml"))
        expected = sorted(set(EXPECTED_WORKFLOWS) | set(SUPPORT_WORKFLOWS))
        assert found == expected

    @pytest.mark.parametrize("filename", all_workflow_names())
    def test_is_valid_yaml(self, filename):
        workflow = load_workflow(filename)
        assert isinstance(workflow, dict)

    @pytest.mark.parametrize("filename", all_workflow_names())
    def test_has_a_name(self, filename):
        assert load_workflow(filename).get("name")

    @pytest.mark.parametrize("filename", all_workflow_names())
    def test_has_exactly_one_job(self, filename):
        """One job per workflow keeps the permission surface minimal."""
        jobs = load_workflow(filename).get("jobs", {})
        assert len(jobs) == 1

    @pytest.mark.parametrize("filename", all_workflow_names())
    def test_runs_on_github_hosted_runner(self, filename):
        for job in load_workflow(filename)["jobs"].values():
            assert job["runs-on"] == "ubuntu-latest"

    @pytest.mark.parametrize("filename", all_workflow_names())
    def test_every_job_has_a_timeout(self, filename):
        for job in load_workflow(filename)["jobs"].values():
            assert job.get("timeout-minutes") == 25

    @pytest.mark.parametrize("filename", all_workflow_names())
    def test_supports_manual_dispatch(self, filename):
        assert "workflow_dispatch" in _triggers(load_workflow(filename))


class TestSchedules:
    @pytest.mark.parametrize("filename", all_workflow_names())
    def test_cron_schedules_match_the_architecture(self, filename):
        _, expected_crons = EXPECTED_WORKFLOWS[filename]
        triggers = _triggers(load_workflow(filename))
        schedule = triggers.get("schedule") or []
        actual = [entry["cron"] for entry in schedule]
        assert sorted(actual) == sorted(expected_crons)

    @pytest.mark.parametrize("filename", all_workflow_names())
    def test_no_duplicate_cron_across_workflows(self, filename):
        """Overlapping schedules would contend for the same state cache."""
        mine = [e["cron"] for e in (_triggers(load_workflow(filename)).get("schedule") or [])]
        others: list[str] = []
        for other in EXPECTED_WORKFLOWS:
            if other == filename:
                continue
            others.extend(
                e["cron"] for e in (_triggers(load_workflow(other)).get("schedule") or [])
            )
        assert not set(mine) & set(others), f"{filename} duplicates a cron in another workflow"


# ── Security ─────────────────────────────────────────────────────


class TestPermissions:
    @pytest.mark.parametrize("filename", all_workflow_names())
    def test_declares_explicit_permissions(self, filename):
        workflow = load_workflow(filename)
        assert "permissions" in workflow, f"{filename} relies on default permissions"

    @pytest.mark.parametrize("filename", all_workflow_names())
    def test_never_grants_write(self, filename):
        """V1 is read-only: no workflow may request a write scope."""
        permissions = load_workflow(filename)["permissions"]
        assert isinstance(permissions, dict)
        for scope, level in permissions.items():
            assert level == "read", f"{filename} grants {scope}: {level}"

    @pytest.mark.parametrize("filename", all_workflow_names())
    def test_permission_scopes_use_valid_hyphenated_names(self, filename):
        """`security_events` is not a valid key; `security-events` is."""
        valid = {
            "actions",
            "attestations",
            "checks",
            "contents",
            "deployments",
            "discussions",
            "id-token",
            "issues",
            "models",
            "packages",
            "pages",
            "pull-requests",
            "repository-projects",
            "security-events",
            "statuses",
        }
        for scope in load_workflow(filename)["permissions"]:
            assert scope in valid, f"{filename} has invalid permission scope {scope!r}"

    def test_manual_workflow_covers_the_union_of_job_scopes(self):
        """manual.yml may dispatch any job, so it needs every read scope."""
        manual = set(load_workflow("manual.yml")["permissions"])
        union: set[str] = set()
        for filename in EXPECTED_WORKFLOWS:
            union |= set(load_workflow(filename)["permissions"])
        assert manual == union

    @pytest.mark.parametrize("filename", all_workflow_names())
    def test_no_pull_request_target_trigger(self, filename):
        """`pull_request_target` plus fork checkout is a privilege escalation path."""
        triggers = _triggers(load_workflow(filename))
        assert "pull_request_target" not in triggers
        assert "pull_request" not in triggers

    @pytest.mark.parametrize("filename", all_workflow_names())
    def test_no_untrusted_event_context_anywhere(self, filename):
        """No `github.event.*` references at all.

        There is no event payload this pipeline needs, so the safest rule is to
        reference none of it.
        """
        text = (WORKFLOW_DIR / filename).read_text(encoding="utf-8")
        assert "github.event." not in text


class TestInjectionSafety:
    @pytest.mark.parametrize("filename", all_workflow_names())
    def test_no_expression_interpolation_in_run_steps(self, filename):
        """`run:` strings must be static.

        Interpolating `${{ ... }}` into a shell command is how workflow
        injection happens. Job data reaches the runtime through the environment.
        """
        for step in _run_steps(load_workflow(filename)):
            script = step["run"]
            assert not INTERPOLATION.search(script), (
                f"{filename}: `run:` step interpolates an expression: {script!r}"
            )

    @pytest.mark.parametrize("filename", all_workflow_names())
    def test_secrets_are_never_passed_as_cli_arguments(self, filename):
        """A secret on a command line leaks into process listings and logs."""
        for step in _run_steps(load_workflow(filename)):
            assert "secrets." not in step["run"]
            assert "secrets." not in step.get("name", "")
        for step in _uses_steps(load_workflow(filename)):
            # Cache keys must not embed a credential either.
            for value in step.get("with", {}).values():
                assert "secrets." not in str(value)

    @pytest.mark.parametrize("filename", all_workflow_names())
    def test_job_name_travels_via_environment(self, filename):
        """The job name is an env var, so the shell never parses it."""
        main = _main_run_step(load_workflow(filename))
        assert main["run"].strip() == "python -m src.jobs"
        assert "GHOPS_JOB" in main["env"]
        # The job name must appear exactly once per workflow.
        dispatchers = [s for s in _run_steps(load_workflow(filename)) if "GHOPS_JOB" in s.get("env", {})]
        assert len(dispatchers) == 1

    @pytest.mark.parametrize("filename", all_workflow_names())
    def test_no_shell_execution_primitives_in_run_steps(self, filename):
        for step in _run_steps(load_workflow(filename)):
            script = step["run"]
            for bad in ("eval ", "exec ", "curl ", "wget "):
                assert bad not in script, f"{filename}: unexpected {bad!r} in run step"

    @pytest.mark.parametrize("filename", all_workflow_names())
    def test_no_write_verbs_in_run_steps(self, filename):
        """The pipeline is read-only: no step may mutate GitHub."""
        for step in _run_steps(load_workflow(filename)):
            script = step["run"].lower()
            for verb in ("git push", "gh pr create", "gh issue ", "gh api -x post"):
                assert verb not in script


class TestActionPinning:
    @pytest.mark.parametrize("filename", all_workflow_names())
    def test_all_actions_are_sha_pinned(self, filename):
        for step in _uses_steps(load_workflow(filename)):
            ref = step["uses"]
            action, _, version = ref.partition("@")
            assert SHA40.match(version), f"{filename}: {action} is not SHA-pinned ({ref})"

    @pytest.mark.parametrize("filename", all_workflow_names())
    def test_pinned_shas_are_the_approved_ones(self, filename):
        for step in _uses_steps(load_workflow(filename)):
            action, _, version = step["uses"].partition("@")
            base = action.rsplit("/", 1)[0] if action.count("/") > 1 else action
            assert base in PINNED_ACTIONS, f"unapproved action: {action}"
            expected_sha, _ = PINNED_ACTIONS[base]
            assert version == expected_sha, f"{filename}: {action}@{version} is not the pin"

    @pytest.mark.parametrize("filename", all_workflow_names())
    def test_each_pin_carries_a_version_comment(self, filename):
        """A bare SHA is unauditable; the comment records which release it is."""
        text = (WORKFLOW_DIR / filename).read_text(encoding="utf-8")
        for action, (sha, version) in PINNED_ACTIONS.items():
            if action not in text:
                continue
            uses_lines = [
                line for line in text.splitlines() if action in line and "uses:" in line
            ]
            assert uses_lines
            for line in uses_lines:
                assert f"# {version}" in line, f"{filename}: {line.strip()} lacks a version comment"

    @pytest.mark.parametrize("filename", all_workflow_names())
    def test_no_action_uses_a_mutable_tag(self, filename):
        text = (WORKFLOW_DIR / filename).read_text(encoding="utf-8")
        for mutable in ("@v1", "@v2", "@v3", "@v4", "@v5", "@v6", "@v7"):
            assert mutable not in text, f"{filename}: mutable ref {mutable}"
        for branch in ("@main", "@master"):
            assert branch not in text, f"{filename}: branch ref {branch}"


# ── State persistence ────────────────────────────────────────────


class TestStatePersistence:
    @pytest.mark.parametrize("filename", all_workflow_names())
    def test_never_uses_a_run_id_only_cache_key(self, filename):
        """The original defect: a run-id-only key can never be hit by the next run."""
        text = (WORKFLOW_DIR / filename).read_text(encoding="utf-8")
        assert "gh-ops-state-${{ github.run_id }}" not in text

    @pytest.mark.parametrize("filename", STATE_WRITING)
    def test_restores_with_the_shared_prefix(self, filename):
        """Restore relies on the prefix, so a fresh run finds the last snapshot."""
        workflow = load_workflow(filename)
        restores = [s for s in _uses_steps(workflow) if "cache/restore" in s["uses"]]
        assert len(restores) == 1
        with_block = restores[0]["with"]
        assert with_block["path"] == "data/state"
        assert "gh-ops-state-" in with_block["restore-keys"]

    @pytest.mark.parametrize("filename", STATE_WRITING)
    def test_restore_key_is_not_the_save_key_alone(self, filename):
        """A primary key that never exists is fine only because of restore-keys."""
        workflow = load_workflow(filename)
        restore = next(s for s in _uses_steps(workflow) if "cache/restore" in s["uses"])
        assert restore["with"]["restore-keys"].strip() == "gh-ops-state-"

    @pytest.mark.parametrize("filename", STATE_WRITING)
    def test_saves_under_a_fresh_immutable_key(self, filename):
        """Caches are immutable, so the save key must be unique per attempt."""
        workflow = load_workflow(filename)
        saves = [s for s in _uses_steps(workflow) if "cache/save" in s["uses"]]
        assert len(saves) == 1
        key = saves[0]["with"]["key"]
        assert "github.run_id" in key
        assert "github.run_attempt" in key
        assert key == next(
            s for s in _uses_steps(workflow) if "cache/restore" in s["uses"]
        )["with"]["key"]

    @pytest.mark.parametrize("filename", STATE_WRITING)
    def test_save_only_runs_on_success(self, filename):
        """A failed run must not publish unverified state."""
        workflow = load_workflow(filename)
        save = next(s for s in _uses_steps(workflow) if "cache/save" in s["uses"])
        assert save.get("if") == "success()"

    @pytest.mark.parametrize("filename", STATE_FREE)
    def test_state_free_workflows_do_not_touch_the_cache(self, filename):
        workflow = load_workflow(filename)
        assert not [s for s in _uses_steps(workflow) if "actions/cache" in s["uses"]]

    def test_state_lists_partition_all_workflows(self):
        """Each workflow is either state-writing or state-free — never both."""
        names = set(EXPECTED_WORKFLOWS) | set(SUPPORT_WORKFLOWS)
        assert set(STATE_WRITING) | set(STATE_FREE) == names
        assert not (set(STATE_WRITING) & set(STATE_FREE))

    @pytest.mark.parametrize("filename", STATE_WRITING)
    def test_restore_precedes_run_and_save_follows_it(self, filename):
        """Read state, run, then write it — in that order."""
        workflow = load_workflow(filename)
        steps = _steps(workflow)
        names = [s.get("name", "") for s in steps]
        restore_at = next(i for i, s in enumerate(steps) if "cache/restore" in s.get("uses", ""))
        run_at = steps.index(_main_run_step(workflow))
        save_at = next(i for i, s in enumerate(steps) if "cache/save" in s.get("uses", ""))
        assert restore_at < run_at < save_at, names

    @pytest.mark.parametrize("filename", STATE_WRITING)
    def test_state_writers_share_one_concurrency_group(self, filename):
        """Serialized access is what makes the shared state file safe."""
        workflow = load_workflow(filename)
        assert workflow["concurrency"]["group"] == "gh-ops-state"
        assert workflow["concurrency"]["cancel-in-progress"] is False

    @pytest.mark.parametrize("filename", all_workflow_names())
    def test_cache_key_contains_no_secret(self, filename):
        text = (WORKFLOW_DIR / filename).read_text(encoding="utf-8")
        for step in _uses_steps(load_workflow(filename)):
            if "actions/cache" in step["uses"]:
                assert "secrets." not in step["with"]["key"]


# ── Runtime wiring ───────────────────────────────────────────────


class TestRuntimeWiring:
    @pytest.mark.parametrize("filename", all_workflow_names())
    def test_environment_provides_required_credentials(self, filename):
        env = _main_run_step(load_workflow(filename))["env"]
        assert env["GITHUB_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"
        assert env["TELEGRAM_BOT_TOKEN"] == "${{ secrets.TELEGRAM_BOT_TOKEN }}"

    @pytest.mark.parametrize("filename", all_workflow_names())
    def test_security_events_is_scoped_to_the_workflows_that_need_it(self, filename):
        scopes = load_workflow(filename)["permissions"]
        needs = filename in ("monitoring.yml", "security.yml", "manual.yml")
        assert ("security-events" in scopes) is needs

    @pytest.mark.parametrize("filename", all_workflow_names())
    def test_dispatched_job_is_a_registered_job(self, filename):
        """Guards against a workflow calling a job name that does not exist."""
        job = _main_run_step(load_workflow(filename))["env"]["GHOPS_JOB"]
        if "${{" in job:
            # manual.yml selects at dispatch time; verify the choice list instead.
            options = _triggers(load_workflow(filename))["workflow_dispatch"]["inputs"]["job"][
                "options"
            ]
            assert set(options) <= set(ALL_JOB_NAMES), options
        else:
            assert job in ALL_JOB_NAMES, job

    @pytest.mark.parametrize("filename", all_workflow_names())
    def test_dependencies_are_installed_before_the_job_runs(self, filename):
        workflow = load_workflow(filename)
        steps = _steps(workflow)
        install_at = next(
            i for i, s in enumerate(steps) if s.get("name") == "Install dependencies"
        )
        assert install_at < steps.index(_main_run_step(workflow))

    @pytest.mark.parametrize("filename", all_workflow_names())
    def test_checkout_precedes_python_setup(self, filename):
        steps = _steps(load_workflow(filename))
        checkout_at = next(i for i, s in enumerate(steps) if "actions/checkout" in s.get("uses", ""))
        python_at = next(
            i for i, s in enumerate(steps) if "actions/setup-python" in s.get("uses", "")
        )
        assert checkout_at < python_at

    @pytest.mark.parametrize("filename", all_workflow_names())
    def test_python_version_is_supported_by_the_project(self, filename):
        steps = _steps(load_workflow(filename))
        setup = next(s for s in steps if "actions/setup-python" in s.get("uses", ""))
        version = setup["with"]["python-version"]
        major, minor = version.split(".")
        assert (int(major), int(minor)) >= (3, 10)
