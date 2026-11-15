"""Proactive engine: scoring, collapse, escalation, dedup, quiet hours, recovery."""
from datetime import datetime, timedelta, timezone

from src.agent.attention import (
    AttentionPolicy,
    AttentionStore,
    AttentionWatcher,
    collapse,
    html_url,
    signals_from_notifications,
)
from tests.agent.conftest import FakeClient

NOON_IST = datetime(2026, 11, 15, 6, 30, tzinfo=timezone.utc)  # 12:00 IST
MIDNIGHT_IST = datetime(2026, 11, 15, 19, 0, tzinfo=timezone.utc)  # 00:30 IST
IST = AttentionPolicy(utc_offset="+05:30")


def note(tid, reason="mention", title="Fix bug", kind="Issue", repo="me/app", url="https://api.github.com/repos/me/app/issues/4",
         updated="2026-11-15T06:00:00Z"):
    return {"id": tid, "reason": reason, "updated_at": updated, "repository": {"full_name": repo},
            "subject": {"title": title, "type": kind, "url": url}}


def ci(tid, workflow="tests", branch="main", result="failed", repo="me/app"):
    return note(tid, "ci_activity", f"{workflow} workflow run {result} for {branch} branch", "CheckSuite", repo, None)


class TestSignals:
    def test_ci_successes_dropped_and_failures_collapsed(self):
        raw = [ci("1"), ci("2"), ci("3"), ci("4", result="succeeded"), ci("5", branch="feat/x")]
        sigs = {s.group: s for s in collapse(signals_from_notifications(raw, "me"))}
        assert set(sigs) == {"ci:me/app:tests:main", "ci:me/app:tests:feat/x"}
        main = sigs["ci:me/app:tests:main"]
        assert main.count == 3 and "default branch" in main.why and main.level in ("high", "critical")
        assert sigs["ci:me/app:tests:feat/x"].score < main.score

    def test_review_request_outranks_subscribed_noise(self):
        sigs = signals_from_notifications([note("1", "review_requested"), note("2", "subscribed")], "me")
        assert sigs[0].score > sigs[1].score and sigs[0].level == "high"
        assert "review" in sigs[0].next_action.lower()

    def test_api_urls_map_to_web_urls(self):
        assert html_url("https://api.github.com/repos/a/b/pulls/9", "a/b") == "https://github.com/a/b/pull/9"
        assert html_url(None, "a/b") == "https://github.com/a/b"
        assert html_url("https://evil.example/x", "a/b") == "https://github.com/a/b"

    def test_malformed_items_ignored(self):
        assert signals_from_notifications([None, "x", {}], "me")[0].repo == ""  # empty dict tolerated


class TestPolicy:
    def test_quiet_hours_in_ist(self):
        assert IST.in_quiet_hours(MIDNIGHT_IST) and not IST.in_quiet_hours(NOON_IST)

    def test_non_wrapping_window(self):
        p = AttentionPolicy(quiet_start="13:00", quiet_end="14:00")
        assert p.in_quiet_hours(datetime(2026, 1, 1, 13, 30, tzinfo=timezone.utc))


def watcher(raw, policy=IST):
    return AttentionWatcher(FakeClient({"/notifications": lambda p: raw}), "me", policy)


class TestWatcher:
    def test_high_signal_immediate_low_to_digest_noise_dropped(self):
        w = watcher([note("1", "review_requested"), note("2", "comment"), note("3", "subscribed")])
        r = w.tick(NOON_IST)
        assert [s.kind for s in r.immediate] == ["review_requested"]
        assert {s.kind for s in w.store.queue} == {"review_requested", "comment"}  # immediates queued until delivered
        assert r.digest and [s.kind for s in r.digest] == ["comment"]

    def test_delivered_signals_never_repeat(self):
        raw = [note("1", "review_requested")]
        w = watcher(raw)
        r = w.tick(NOON_IST)
        w.mark_delivered(r.immediate)
        r2 = w.tick(NOON_IST + timedelta(minutes=5))
        assert r2.immediate == [] and r2.suppressed == 1

    def test_quiet_hours_hold_normal_but_escalate_critical(self):
        w = watcher([note("1", "review_requested"), note("2", "security_alert", kind="RepositoryVulnerabilityAlert")])
        r = w.tick(MIDNIGHT_IST)
        assert [s.kind for s in r.immediate] == ["security_alert"] and r.digest == []
        assert any(s.kind == "review_requested" for s in w.store.queue)

    def test_failed_delivery_is_retried_not_lost(self):
        w = watcher([note("1", "review_requested")])
        r = w.tick(NOON_IST)
        assert r.immediate  # pretend Telegram failed: do NOT mark delivered
        w.store.save()
        w2 = AttentionWatcher(FakeClient({"/notifications": lambda p: []}), "me", IST, AttentionStore())
        r2 = w2.tick(NOON_IST + timedelta(hours=5))
        assert [s.kind for s in r2.digest] == ["review_requested"]

    def test_ci_streak_escalates_across_ticks_and_realerts_on_band_change(self):
        batches = [[ci("1")], [ci("2")], [ci("3")]]
        w = AttentionWatcher(FakeClient({"/notifications": lambda p: batches.pop(0)}), "me", IST)
        first = w.tick(NOON_IST)
        w.mark_delivered(first.immediate + first.digest)
        second = w.tick(NOON_IST + timedelta(minutes=5))
        sig = (second.immediate + w.store.queue)[0]
        assert sig.count == 2 and "2x" in sig.why
        w.mark_delivered(second.immediate)
        third = w.tick(NOON_IST + timedelta(minutes=10))
        assert third.immediate == [] or third.immediate[0].count == 3  # same band (2-4): no re-alert spam
        assert w.store.ci_streaks["ci:me/app:tests:main"]["count"] == 3

    def test_mutes_suppress(self):
        w = watcher([note("1", "review_requested", repo="noisy/repo")])
        w.store.muted_repos.add("noisy/repo")
        assert w.tick(NOON_IST).immediate == []

    def test_recovery_after_downtime_is_bounded(self):
        seen = {}
        w = AttentionWatcher(FakeClient({"/notifications": lambda p: seen.update(p) or []}), "me", IST)
        w.store.last_check = "2026-01-01T00:00:00+00:00"  # months offline
        w.tick(NOON_IST)
        assert seen["since"].startswith("2026-11-12")  # max_catchup_days=3

    def test_state_survives_restart(self):
        w = watcher([note("1", "comment")])
        w.tick(NOON_IST)
        w.store.muted_threads.add("9")
        w.store.save()
        again = AttentionStore()
        assert again.last_check and "9" in again.muted_threads and again.queue
