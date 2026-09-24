"""Architecture boundary tests for the Phase 8 execution layer.

Phase 8 sits on top of the existing runtime:

    src.jobs  →  core / github / monitors / intelligence / developer / notifications

No Phase 1-7 module may depend on ``src.jobs``. The only exception is the CLI
entry points, which import the job layer lazily inside a ``__main__`` guard.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"

#: Packages whose import must never drag in the job layer.
PHASE_1_7_PACKAGES = (
    "src.core",
    "src.core.dispatcher",
    "src.core.state",
    "src.github",
    "src.monitors",
    "src.intelligence",
    "src.developer",
    "src.notifications",
    "src.reporting",
    "src.utils",
)

def _is_main_guard(test: ast.expr) -> bool:
    """True for ``if __name__ == "__main__":`` and its reversed form."""
    if not isinstance(test, ast.Compare) or len(test.comparators) != 1:
        return False
    left, right = test.left, test.comparators[0]
    names = {n.id for n in (left, right) if isinstance(n, ast.Name)}
    constants = {n.value for n in (left, right) if isinstance(n, ast.Constant)}
    return names == {"__name__"} and constants == {"__main__"}


def _module_level_imports(path: Path) -> list[str]:
    """Imports at module scope, ignoring anything inside an ``if`` block.

    An import nested in ``if __name__ == "__main__":`` never executes on a
    normal library import, so it does not create a dependency edge.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []

    for node in tree.body:  # top level only
        if isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
        elif isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)

    return found


def _unguarded_job_imports(path: Path) -> list[str]:
    """Imports of ``src.jobs`` that are NOT inside a ``__main__`` guard.

    Exempting guarded imports is not a loophole: a guarded import runs only when
    the file is executed as a script, so it creates no import-time dependency.
    The runtime test below is what actually proves the boundary holds.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))

    guarded: set[int] = set()
    for node in tree.body:
        if isinstance(node, ast.If) and _is_main_guard(node.test):
            guarded.update(id(child) for child in ast.walk(node))

    found: list[str] = []
    for node in ast.walk(tree):
        if id(node) in guarded:
            continue
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and (node.module == "src.jobs" or node.module.startswith("src.jobs."))
        ):
            found.append(node.module)
        elif isinstance(node, ast.Import):
            found.extend(
                alias.name
                for alias in node.names
                if alias.name == "src.jobs" or alias.name.startswith("src.jobs.")
            )
    return found


class TestNoReverseDependency:
    def test_no_library_module_imports_jobs_at_module_scope(self):
        """An unguarded import of src.jobs would invert the dependency."""
        violations: list[str] = []
        for path in sorted(SRC.rglob("*.py")):
            if path.relative_to(SRC).parts[0] == "jobs":
                continue
            for module in _module_level_imports(path):
                if module == "src.jobs" or module.startswith("src.jobs."):
                    violations.append(f"{path.relative_to(REPO_ROOT)} imports {module}")
        assert not violations, violations

    def test_job_layer_is_only_imported_from_main_guards(self):
        """Outside src/jobs, every reference to src.jobs is behind __main__."""
        offenders: list[str] = []
        for path in sorted(SRC.rglob("*.py")):
            if path.relative_to(SRC).parts[0] == "jobs":
                continue
            for module in _unguarded_job_imports(path):
                offenders.append(f"{path.relative_to(REPO_ROOT)} imports {module}")
        assert not offenders, offenders

    def test_the_main_guard_detector_works(self):
        """Sanity-check the AST helper, so the test above cannot pass vacuously."""
        source = 'if __name__ == "__main__":\n    import src.jobs\n'
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as handle:
            handle.write(source)
            temp_path = Path(handle.name)
        try:
            assert _unguarded_job_imports(temp_path) == []
            temp_path.write_text("import src.jobs\n", encoding="utf-8")
            assert _unguarded_job_imports(temp_path) == ["src.jobs"]
        finally:
            temp_path.unlink(missing_ok=True)

    @pytest.mark.parametrize("package", PHASE_1_7_PACKAGES)
    def test_importing_a_phase_1_7_package_does_not_load_jobs(self, package):
        """Checked at runtime, since that is what actually matters."""
        script = (
            "import sys\n"
            f"import {package}\n"
            "leaked = [m for m in sys.modules if m.startswith('src.jobs')]\n"
            "print(leaked)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "[]", f"{package} pulled in src.jobs: {result.stdout}"


class TestJobLayerDependencies:
    def test_job_layer_does_not_import_http_clients_directly(self):
        """The job layer composes modules; it never makes HTTP calls itself."""
        violations: list[str] = []
        for path in sorted((SRC / "jobs").glob("*.py")):
            for module in _module_level_imports(path):
                for forbidden in ("requests", "urllib", "httpx", "socket"):
                    if module == forbidden or module.startswith(f"{forbidden}."):
                        violations.append(f"{path.name} imports {module}")
        assert not violations, violations

    def test_job_layer_has_no_llm_or_agent_dependency(self):
        violations: list[str] = []
        for path in sorted((SRC / "jobs").glob("*.py")):
            text = path.read_text(encoding="utf-8").lower()
            for forbidden in ("openai", "anthropic", "langchain", " mcp", "uaе"):
                if forbidden in text:
                    violations.append(f"{path.name}: {forbidden}")
        assert not violations, violations

    def test_job_layer_never_uses_eval_or_exec(self):
        violations: list[str] = []
        for path in sorted((SRC / "jobs").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id in ("eval", "exec", "compile"):
                    violations.append(f"{path.name}:{node.lineno} uses {node.id}")
                if isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Attribute) and func.attr in ("system", "popen"):
                        violations.append(f"{path.name}:{node.lineno} calls {func.attr}")
        assert not violations, violations

    def test_job_layer_does_not_read_secrets_from_config(self):
        """Credentials come from the environment only (Phase 7 owns that)."""
        violations: list[str] = []
        for path in sorted((SRC / "jobs").glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for pattern in ("TOKEN =", "token=", "api_key", "password"):
                if pattern in text and "secrets." not in text:
                    violations.append(f"{path.name}: {pattern}")
        assert not violations, violations

    def test_jobs_module_declares_all_six_job_names(self):
        from src.jobs.jobs import ALL_JOB_NAMES

        assert len(ALL_JOB_NAMES) == 6
        assert len(set(ALL_JOB_NAMES)) == 6
