"""Write path: allowlist, validation, confirmation, single execution, audit."""
import json
import time

import pytest
import requests

from src.agent.actions import ActionError, ActionQueue, build_request
from src.github.auth import AuthConfig
from src.github.writer import OPERATIONS, GitHubWriter, WriteError
from src.research.feeds import parse_feed


class RecordingWriter:
    def __init__(self, fail=False):
        self.performed, self.fail = [], fail

    def perform(self, op, path_args, body):
        self.performed.append((op, path_args, body))
        if self.fail:
            raise WriteError("GitHub returned 403")
        return {"html_url": "https://github.com/me/app/issues/9"}


def propose(queue, **kw):
    args = {"operation": "create_issue", "repo": "me/app", "params": {"title": "Bug", "body": "details"},
            "summary": "file bug", "conversation_key": "1:1", "user_id": 1} | kw
    return queue.propose(**args)


class TestActions:
    def test_proposal_executes_nothing(self):
        writer = RecordingWriter()
        a = propose(ActionQueue(writer=writer))
        assert a.status == "pending" and writer.performed == []

    def test_confirm_executes_exactly_once(self):
        writer = RecordingWriter()
        q = ActionQueue(writer=writer)
        a = propose(q)
        assert q.resolve(a.id, 1, True) == (True, "Done. https://github.com/me/app/issues/9")
        assert q.resolve(a.id, 1, True)[0] is False  # double-press
        assert len(writer.performed) == 1

    def test_other_user_cannot_confirm(self):
        writer = RecordingWriter()
        q = ActionQueue(writer=writer)
        a = propose(q)
        ok, msg = q.resolve(a.id, 2, True)
        assert not ok and "Only the person" in msg and writer.performed == []

    def test_expired_and_cancelled(self, monkeypatch):
        writer = RecordingWriter()
        q = ActionQueue(writer=writer)
        a, b = propose(q), propose(q)
        assert q.resolve(b.id, 1, False) == (True, "Cancelled. Nothing was changed.")
        real = time.time
        monkeypatch.setattr(time, "time", lambda: real() + 3600)
        assert "expired" in q.resolve(a.id, 1, True)[1] and writer.performed == []

    def test_executing_status_is_persisted_before_write_so_restart_cannot_replay(self):
        q = ActionQueue(writer=RecordingWriter())
        a = propose(q)
        seen = []

        class Spy(RecordingWriter):
            def perform(self, *args):
                seen.append(ActionQueue().actions[a.id].status)  # what a restarted process would load
                return {}

        q._writer = Spy()
        q.resolve(a.id, 1, True)
        assert seen == ["executing"]

    def test_failure_reported_and_audited(self, isolated_state):
        q = ActionQueue(writer=RecordingWriter(fail=True))
        a = propose(q)
        ok, msg = q.resolve(a.id, 1, True)
        events = [json.loads(line)["event"] for line in (isolated_state / "agent_audit.jsonl").read_text().splitlines()]
        assert not ok and "403" in msg and events == ["proposed", "confirmed", "failed"]

    def test_permission_gate(self):
        q = ActionQueue(writer=RecordingWriter(), can_write=lambda repo: (False, "not yours"))
        with pytest.raises(ActionError, match="not yours"):
            propose(q, repo="other/repo")

    @pytest.mark.parametrize("op,params", [
        ("create_issue", {}), ("comment", {"number": "x", "body": "b"}), ("set_issue_state", {"number": 1, "state": "deleted"}),
        ("add_labels", {"number": 1, "labels": "notalist"}), ("dispatch_workflow", {"workflow": "ci.yml", "ref": "main; rm -rf"}),
        ("update_repo", {}), ("delete_repo", {}),
    ])
    def test_invalid_params_rejected(self, op, params):
        with pytest.raises(ActionError):
            build_request(op, "me/app", params)


class FakeHTTP:
    def __init__(self, status=201, body=None, exc=None):
        self.status, self.body, self.exc, self.requests = status, body or {}, exc, []

    def request(self, method, url, json, headers, timeout):
        self.requests.append((method, url))
        if self.exc:
            raise self.exc
        resp = requests.Response()
        resp.status_code = self.status
        resp._content = (__import__("json").dumps(self.body)).encode()
        return resp


AUTH = AuthConfig(token="ghp_" + "A" * 36, token_source="test")


class TestWriter:
    def test_allowlist_is_the_whole_surface(self):
        with pytest.raises(WriteError, match="not allowed"):
            GitHubWriter(AUTH, FakeHTTP()).perform("delete_repo", {"repo": "a/b"}, {})
        assert all(m in ("POST", "PATCH", "PUT") for m, _ in OPERATIONS.values())  # no DELETE anywhere

    @pytest.mark.parametrize("args", [{"repo": "../x"}, {"repo": "a/b", "number": "1/../../x"}, {"repo": "https://evil/a"}])
    def test_path_injection_blocked(self, args):
        http = FakeHTTP()
        with pytest.raises(WriteError):
            GitHubWriter(AUTH, http).perform("comment", {"number": 1} | args, {"body": "x"})
        assert http.requests == []

    def test_targets_only_api_github_com(self):
        http = FakeHTTP(body={"html_url": "u"})
        GitHubWriter(AUTH, http).perform("comment", {"repo": "a/b", "number": 3}, {"body": "x"})
        assert http.requests == [("POST", "https://api.github.com/repos/a/b/issues/3/comments")]

    def test_no_retry_on_timeout(self):
        http = FakeHTTP(exc=requests.exceptions.Timeout("https://x?token=SECRET"))
        with pytest.raises(WriteError) as err:
            GitHubWriter(AUTH, http).perform("comment", {"repo": "a/b", "number": 3}, {"body": "x"})
        assert len(http.requests) == 1 and "SECRET" not in str(err.value) and "verify" in str(err.value)

    def test_http_error_message(self):
        with pytest.raises(WriteError, match="404: Not Found"):
            GitHubWriter(AUTH, FakeHTTP(404, {"message": "Not Found"})).perform("create_issue", {"repo": "a/b"}, {"title": "t"})


class TestFeeds:
    FEED = ("<?xml version='1.0'?><rss><channel><item><title>New thing</title><link>https://github.blog/x</link>"
            "<pubDate>Fri, 13 Nov 2026 10:00:00 +0000</pubDate><description><![CDATA[<p>Hello <b>world</b></p>]]></description>"
            "<content><![CDATA[<!DOCTYPE html><p>body</p>]]></content></item></channel></rss>")

    def test_parses_and_strips_html(self):
        assert parse_feed(self.FEED, 5) == [{"title": "New thing", "published": "Fri, 13 Nov 2026", "url": "https://github.blog/x",
                                             "summary": "Hello world"}]

    def test_rejects_dtd_in_prolog(self):
        bomb = '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><rss><channel></channel></rss>'
        with pytest.raises(ValueError):
            parse_feed(bomb, 5)
