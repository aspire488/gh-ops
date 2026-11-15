import json

import pytest

from src.agent.tools import ToolContext, ToolError, ToolRegistry, repo_arg, validate_args
from tests.agent.conftest import FakeClient

SCHEMA = {"type": "object", "required": ["repo"], "properties": {
    "repo": {"type": "string", "maxLength": 141},
    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
    "state": {"type": "string", "enum": ["open", "closed"], "default": "open"},
    "flag": {"type": "boolean"}}}


class TestValidation:
    def test_defaults_applied_and_integers_coerced(self):
        assert validate_args(SCHEMA, {"repo": "a/b", "limit": "7"}) == {"repo": "a/b", "limit": 7, "state": "open"}

    def test_limit_is_clamped_not_rejected(self):
        assert validate_args(SCHEMA, {"repo": "a/b", "limit": 999})["limit"] == 50

    @pytest.mark.parametrize("args", [{}, {"repo": "a/b", "extra": 1}, {"repo": "a/b", "state": "merged"},
                                      {"repo": "a/b", "limit": 0}, {"repo": "a/b", "flag": "yes"}, "notadict"])
    def test_invalid_arguments_rejected(self, args):
        with pytest.raises(ToolError):
            validate_args(SCHEMA, args)

    @pytest.mark.parametrize("bad", ["../../user", "https://evil.example/x", "a/b/c", "a b/c", "owner/",
                                     "a/b?x=1", "a/b#frag"])
    def test_repo_arg_blocks_path_and_host_injection(self, bad):
        with pytest.raises(ToolError):
            repo_arg(bad)

    def test_repo_arg_accepts_github_url_form(self):
        assert repo_arg("https://github.com/aspire488/gh-ops/") == "aspire488/gh-ops"


class TestRegistry:
    def ctx(self, routes):
        return ToolContext(client=FakeClient(routes), login="me")

    def test_every_tool_has_valid_spec(self):
        for spec in ToolRegistry().specs():
            fn = spec["function"]
            assert fn["name"] and fn["description"] and fn["parameters"]["type"] == "object"

    def test_result_is_shaped_not_raw(self):
        ctx = self.ctx({"/repos/a/b": {"full_name": "a/b", "stargazers_count": 5, "node_id": "X", "owner": {"huge": 1}}})
        out = json.loads(ToolRegistry().run(ctx, "get_repository", '{"repo": "a/b"}'))
        assert out["data"]["stars"] == 5 and "node_id" not in out["data"] and "owner" not in out["data"]

    def test_cache_avoids_repeat_api_calls(self):
        ctx = self.ctx({"/repos/a/b": {"full_name": "a/b"}})
        reg = ToolRegistry()
        reg.run(ctx, "get_repository", '{"repo": "a/b"}')
        reg.run(ctx, "get_repository", '{"repo": "a/b"}')
        assert len(ctx.client.calls) == 1 and reg.calls == 1

    def test_cache_is_bounded(self):
        ctx = self.ctx({f"/repos/a/r{i}": {"full_name": f"a/r{i}"} for i in range(5)})
        reg = ToolRegistry(cache_size=2)
        for i in range(5):
            reg.run(ctx, "get_repository", json.dumps({"repo": f"a/r{i}"}))
        assert len(reg._cache) == 2

    def test_github_errors_become_explainable_data(self):
        out = json.loads(ToolRegistry().run(self.ctx({}), "get_repository", '{"repo": "a/missing"}'))
        assert "not found" in out["error"]

    def test_bad_json_and_unknown_tool_do_not_raise(self):
        reg = ToolRegistry()
        assert "invalid arguments" in json.loads(reg.run(self.ctx({}), "get_repository", "{not json"))["error"]
        assert "unknown tool" in json.loads(reg.run(self.ctx({}), "rm_rf", "{}"))["error"]

    def test_injection_never_reaches_client(self):
        ctx = self.ctx({})
        ToolRegistry().run(ctx, "get_repository", json.dumps({"repo": "../../user"}))
        assert ctx.client.calls == []

    def test_oversized_results_are_truncated(self):
        ctx = self.ctx({"/repos/a/b/readme": {"content": "", "path": "README.md"},
                        "/repos/a/b/issues": [{"number": i, "title": "x" * 140} for i in range(100)]})
        out = ToolRegistry().run(ctx, "list_issues", '{"repo": "a/b", "limit": 50}')
        assert len(out) <= 4600

    def test_pull_request_reports_latest_review_per_reviewer_and_failing_run_ids(self):
        routes = {
            "/repos/a/b/pulls/3": {"title": "t", "head": {"sha": "abc"}, "base": {"ref": "main"}},
            "/repos/a/b/pulls/3/reviews": [{"user": {"login": "r"}, "state": "CHANGES_REQUESTED"},
                                           {"user": {"login": "r"}, "state": "APPROVED"}],
            "/repos/a/b/commits/abc/check-runs": {"check_runs": [
                {"name": "tests", "status": "completed", "conclusion": "failure",
                 "details_url": "https://github.com/a/b/actions/runs/777/job/1"},
                {"name": "lint", "status": "completed", "conclusion": "success"}]},
            "/repos/a/b/issues/3": {"comments": 0},
        }
        data = json.loads(ToolRegistry().run(self.ctx(routes), "get_pull_request", '{"repo": "a/b", "number": 3}'))["data"]
        assert data["review_states"] == {"r": "APPROVED"}
        assert data["checks"]["failing"][0]["run_id"] == 777 and data["checks"]["passing"] == 1

    def test_opportunities_skip_already_suggested(self, monkeypatch):
        from types import SimpleNamespace

        from src.intelligence.oss import hunter

        opp = lambda n: SimpleNamespace(repo_full_name="o/r", issue_number=n, title="t", html_url="u", labels=(),  # noqa: E731
                                        repo_stars=1, comments=0, score=0.5, assignee=None, updated_at=None)
        monkeypatch.setattr(hunter, "run_hunter", lambda c, cfg: SimpleNamespace(opportunities=[opp(1), opp(2)], queries_run=1))
        ctx = self.ctx({})
        reg = ToolRegistry()
        first = json.loads(reg.run(ctx, "find_contribution_opportunities", '{"limit": 1}'))["data"]
        reg._cache.clear()
        second = json.loads(reg.run(ctx, "find_contribution_opportunities", '{"limit": 1}'))["data"]
        assert first["opportunities"][0]["issue"] == "o/r#1" and second["opportunities"][0]["issue"] == "o/r#2"

    def test_propose_action_without_queue_is_refused(self):
        out = json.loads(ToolRegistry().run(self.ctx({}), "propose_action",
                                            json.dumps({"operation": "comment", "repo": "a/b", "summary": "x"})))
        assert "disabled" in out["error"]
