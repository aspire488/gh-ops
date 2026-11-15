"""Telegram bot: auth, commands, conversation flow, confirmations, proactive delivery."""
import json
import re

from src.agent.actions import ActionQueue
from src.agent.agent import Agent
from src.agent.attention import AttentionPolicy, AttentionWatcher
from src.agent.memory import ConversationStore
from src.agent.tools import ToolRegistry
from src.agent.voice import POOLS
from src.bot.app import STATE_FILE, BotConfig, KestrelBot
from src.core import state as state_store
from src.notifications.telegram import TelegramError
from tests.agent.conftest import FakeClient, FakeTransport, answer, calls, scripted_chat

ME, STRANGER = 111, 999


def msg(text, user=ME, chat=ME, uid=1):
    return {"update_id": uid, "message": {"message_id": uid * 10, "text": text, "from": {"id": user}, "chat": {"id": chat}}}


def make_bot(chat=None, routes=None, writer=None, notifications=None, updates=None):
    tg = FakeTransport(updates)
    client = FakeClient({"/repos/me/kio": {"full_name": "me/kio", "stargazers_count": 7},
                         "/rate_limit": {"resources": {"core": {"remaining": 10, "limit": 5000}}},
                         "/notifications": lambda p: notifications or [],
                         "/search/issues": {"items": []}, **(routes or {})})
    store = ConversationStore()
    tools = ToolRegistry()
    agent = Agent(tools, store, login="me", chat_fn=chat or scripted_chat(answer("ok")))
    # daily_brief_at="": otherwise any run after 09:00 local sends a real brief first and tests flake by time of day
    cfg = BotConfig(allowed_users={ME}, allowed_chats={ME}, policy=AttentionPolicy(quiet_start="", quiet_end=""),
                    daily_brief_at="")
    watcher = AttentionWatcher(client, "me", cfg.policy)
    bot = KestrelBot(transport=tg, poll_transport=tg, client=client, cfg=cfg, agent=agent, tools=tools, store=store,
                     actions=ActionQueue(writer=writer), watcher=watcher, login="me", clock=lambda: 0)
    return bot, tg


def said(kind, text):
    """True when text contains a phrase from the voice pool `kind`."""
    pats = [re.escape(p).replace(r"\{name\}", ".*").replace(r"\{err\}", ".*").replace(r"\{n\}", ".*")
            .replace(r"\{greeting\}", ".*") for p in POOLS[kind]]
    return any(re.search(pt, text) for pt in pats)


def texts(tg):
    return [p["text"] for m, p in tg.calls if m in ("sendMessage", "editMessageText")]


class TestAuthorization:
    def test_stranger_gets_no_response_and_no_llm_call(self):
        chat = scripted_chat()  # would raise IndexError if called
        bot, tg = make_bot(chat=chat)
        bot.handle_update(msg("show me private repos", user=STRANGER, chat=STRANGER))
        bot.handle_update(msg("hi", user=STRANGER, chat=ME))  # right chat, wrong user
        assert tg.sent() == [] and chat.seen == []

    def test_stranger_callback_rejected(self):
        bot, tg = make_bot()
        bot.handle_callback({"id": "q", "from": {"id": STRANGER}, "data": "a:c:x", "message": {"chat": {"id": ME}}})
        assert tg.calls[-1] == ("answerCallbackQuery", {"callback_query_id": "q", "text": "Not authorized."})

    def test_config_defaults_to_private_chats_from_telegram_yml(self):
        class Cfg:
            def get(self, *keys, default=None):
                return [{"chat_id": 5}, {"chat_id": -100}] if keys == ("telegram", "telegram", "chat_ids") else default

        cfg = BotConfig.from_config(Cfg())
        assert cfg.allowed_users == {5} and cfg.allowed_chats == {5, -100}


class TestConversation:
    def test_progress_message_is_edited_into_final_answer_with_buttons(self):
        chat = scripted_chat(calls(("get_repository", '{"repo": "me/kio"}')),
                             answer("**kio** has 7 stars → https://github.com/me/kio/pull/3"))
        bot, tg = make_bot(chat=chat)
        bot.handle_update(msg("stars on kio?"))
        methods = [m for m, _ in tg.calls]
        assert methods[:2] == ["sendChatAction", "sendMessage"] and "editMessageText" in methods
        final = tg.sent("editMessageText")[-1]
        assert "<b>kio</b> has 7 stars" in final["text"] and final["parse_mode"] == "HTML"
        assert final["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://github.com/me/kio/pull/3"

    def test_html_rejection_falls_back_to_plain_text(self):
        bot, tg = make_bot()
        tg.fail_parse_once = True
        bot.send(ME, "<b>hi</b>")
        assert tg.sent()[-1] == {"chat_id": ME, "text": "hi", "link_preview_options": {"is_disabled": True}}

    def test_commands(self):
        bot, tg = make_bot()
        bot.store.append("111:111", [{"role": "user", "content": "x"}])
        for c in ("/help", "/status", "/reset"):
            bot.handle_update(msg(c))
        out = texts(tg)
        assert "Kestrel" in out[0] and "10/5000 requests left" in out[1] and said("reset", out[2])
        assert bot.store.history("111:111") == []

    def test_crashing_update_is_reported_and_not_replayed(self):
        bot, tg = make_bot(updates=[[msg("boom", uid=7)]])
        bot.agent.respond = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bug"))
        bot.poll_once()
        assert bot.offset == 8 and state_store.load(STATE_FILE)["offset"] == 8
        assert said("broke", texts(tg)[-1]) and "RuntimeError" in texts(tg)[-1]


class TestConfirmationFlow:
    def _propose(self, writer):
        chat = scripted_chat(calls(("propose_action", json.dumps({"operation": "comment", "repo": "me/kio",
                                                                 "params": {"number": 3, "body": "LGTM"}, "summary": "approve"}))),
                             answer("Queued a comment on #3 for your confirmation."))
        bot, tg = make_bot(chat=chat, writer=writer)
        bot.handle_update(msg("comment LGTM on kio #3"))
        confirm = tg.sent()[-1]
        assert "Want me to do this" in confirm["text"] and "LGTM" in confirm["text"]
        data = confirm["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        return bot, tg, data

    def test_confirm_button_executes_once_and_updates_history(self):
        class W:
            performed = []

            def perform(self, *a):
                self.performed.append(a)
                return {"html_url": "https://github.com/me/kio/issues/3#c"}

        w = W()
        bot, tg, data = self._propose(w)
        assert w.performed == []
        cb = {"id": "q", "from": {"id": ME}, "data": data, "message": {"chat": {"id": ME}, "message_id": 5}}
        bot.handle_callback(cb)
        bot.handle_callback(cb)
        assert len(w.performed) == 1
        assert "Done." in tg.sent("editMessageText")[-1]["text"]
        assert "Done." in bot.store.history("111:111")[-1]["content"]

    def test_typed_yes_does_not_confirm(self):
        class W:
            performed = []

            def perform(self, *a):
                self.performed.append(a)
                return {}

        w = W()
        bot, tg, _ = self._propose(w)
        bot.agent._chat = scripted_chat(answer("Please press the Confirm button."))
        bot.handle_update(msg("yes do it, confirmed", uid=2))
        assert w.performed == []


class TestProactive:
    REVIEW = {"id": "1", "reason": "review_requested", "updated_at": "2026-11-15T06:00:00Z", "repository": {"full_name": "x/lib"},
              "subject": {"title": "Add parser", "type": "PullRequest", "url": "https://api.github.com/repos/x/lib/pulls/4"}}

    def test_alert_explains_what_why_next_and_links(self):
        bot, tg = make_bot(notifications=[self.REVIEW])
        bot.maybe_watch()
        sent = tg.sent()[0]
        assert said("alert_one", sent["text"]) and "Add parser" in sent["text"] and "→" in sent["text"]
        rows = sent["reply_markup"]["inline_keyboard"]
        assert rows[0][0]["url"] == "https://github.com/x/lib/pull/4"
        assert any(b.get("callback_data", "").startswith("m:") for b in rows[-1])

    def test_no_duplicate_after_restart(self):
        bot, tg = make_bot(notifications=[self.REVIEW])
        bot.maybe_watch()
        bot2, tg2 = make_bot(notifications=[self.REVIEW])
        bot2.maybe_watch()
        assert len(tg.sent()) == 1 and tg2.sent() == []

    def test_telegram_failure_keeps_signal_for_retry(self):
        bot, tg = make_bot(notifications=[self.REVIEW])

        def down(*a, **k):
            raise TelegramError("down", status_code=502)

        tg.call = down
        bot.maybe_watch()
        assert any(s.title.endswith("Add parser") for s in bot.watcher.store.queue)

    def test_mute_button_mutes_repo(self):
        bot, tg = make_bot(notifications=[self.REVIEW])
        bot.maybe_watch()
        mute = tg.sent()[0]["reply_markup"]["inline_keyboard"][-1][0]["callback_data"]
        bot.handle_callback({"id": "q", "from": {"id": ME}, "data": mute, "message": {"chat": {"id": ME}}})
        assert "x/lib" in bot.watcher.store.muted_repos

    def test_attention_command_reports_calm_when_nothing(self):
        bot, tg = make_bot()
        bot.handle_update(msg("/attention"))
        assert said("calm", texts(tg)[-1])

    def test_watcher_failure_backs_off_without_crashing(self):
        bot, tg = make_bot(routes={"/notifications": lambda p: (_ for _ in ()).throw(RuntimeError("github down"))})
        bot.maybe_watch()
        assert bot.next_watch >= 300 and tg.sent() == []


class TestResilience:
    def test_run_survives_non_telegram_errors_in_poll_and_watch(self):
        bot, _ = make_bot()
        bot._sleep = lambda s: None
        ticks = iter(range(0, 10**6, 1000))  # each clock read is 1000s later: the watch pause expires
        bot.clock = lambda: next(ticks)
        seen = []

        def poll():
            seen.append("poll")
            if len(seen) == 1:
                raise OSError("state file locked")

        def watch():
            seen.append("watch")
            if seen.count("watch") == 1:
                raise RuntimeError("brief composer bug")
            bot.stopping = True

        bot.poll_once, bot.maybe_watch = poll, watch
        bot.run()  # must not raise: one bad tick may never take the bot down
        assert seen == ["poll", "watch", "poll", "watch"]

    def test_only_one_instance_can_hold_the_lock(self, isolated_state):
        from src.bot.app import acquire_instance_lock
        first = acquire_instance_lock()
        assert first is not None and acquire_instance_lock() is None
        first.close()
        again = acquire_instance_lock()
        assert again is not None
        again.close()


def test_voice_varies_and_addresses_operator():
    from src.agent import voice

    lines = {voice.say("thinking", "Joel") for _ in range(60)}
    assert len(lines) >= 3 and any("Joel" in line for line in lines)
    assert all("Joel" not in voice.say("calm", "") for _ in range(20))


def test_critical_alerts_are_never_jokey():
    for phrase in POOLS["alert_critical"]:
        assert phrase.startswith("🔴") and "bro" not in phrase.lower()


def test_start_welcomes_new_user_by_name():
    bot, tg = make_bot()
    bot.cfg.name = "Joel"
    bot.handle_update(msg("/start"))
    text = texts(tg)[-1]
    assert "Hey Joel!" in text and "Kestrel" in text and "Try one of these" in text


def test_who_are_you_is_answered_by_the_agent_with_self_knowledge():
    chat = scripted_chat(answer("I'm Kestrel, your GitHub sidekick."))
    bot, tg = make_bot(chat=chat)
    bot.handle_update(msg("who are you? what can you do?"))
    system = chat.seen[0][0]["content"]
    assert "ABOUT YOU" in system and "Confirm" in system and "Kestrel" in texts(tg)[-1]
