import time

from src.agent.memory import ConversationStore
from src.agent.render import (
    LIMIT,
    github_links,
    render_messages,
    split_markdown,
    strip_html,
    to_html,
)


class TestMemory:
    def test_persists_across_instances(self):
        ConversationStore().append("1:1", [{"role": "user", "content": "hi"}])
        assert ConversationStore().history("1:1") == [{"role": "user", "content": "hi"}]

    def test_trims_whole_turns_never_orphaning_tool_messages(self):
        store = ConversationStore(max_messages=4)
        for i in range(3):
            store.append("k", [{"role": "user", "content": f"q{i}"},
                               {"role": "assistant", "content": None, "tool_calls": [{"id": "c", "function": {"arguments": "{}"}}]},
                               {"role": "tool", "tool_call_id": "c", "content": "r"},
                               {"role": "assistant", "content": f"a{i}"}])
        hist = store.history("k")
        assert hist[0] == {"role": "user", "content": "q2"} and len(hist) == 4

    def test_char_budget(self):
        store = ConversationStore(max_chars=50)
        store.append("k", [{"role": "user", "content": "x" * 40}, {"role": "assistant", "content": "y"}])
        store.append("k", [{"role": "user", "content": "z" * 40}, {"role": "assistant", "content": "w"}])
        assert store.history("k")[0]["content"] == "z" * 40

    def test_expiry_and_reset(self, monkeypatch):
        store = ConversationStore(ttl_hours=1)
        store.append("k", [{"role": "user", "content": "x"}])
        store.append("j", [{"role": "user", "content": "y"}])
        store.reset("j")
        assert store.history("j") == []
        real = time.time
        monkeypatch.setattr(time, "time", lambda: real() + 7200)
        assert store.history("k") == []


class TestRender:
    def test_untrusted_html_is_escaped(self):
        out = to_html('Issue title: <script>alert(1)</script> & <b>x</b>')
        assert "<script>" not in out and "&lt;script&gt;" in out and "&amp;" in out

    def test_markdown_subset(self):
        out = to_html("# Title\n**bold** and *it* and `a<b`\n- item\n[PR #3](https://github.com/a/b/pull/3)")
        assert "<b>Title</b>" in out and "<b>bold</b>" in out and "<i>it</i>" in out
        assert "<code>a&lt;b</code>" in out and "• item" in out
        assert '<a href="https://github.com/a/b/pull/3">PR #3</a>' in out

    def test_javascript_links_are_not_rendered_as_links(self):
        out = to_html("[click](javascript:alert(1))")
        assert "<a " not in out

    def test_link_with_quote_cannot_break_attribute(self):
        out = to_html('[x](https://github.com/a"onmouseover="y)')
        assert 'onmouseover="' not in out

    def test_code_blocks_preserved_and_escaped(self):
        out = to_html("```python\nif a < b:\n    **not bold**\n```")
        assert out == "<pre>if a &lt; b:\n    **not bold**</pre>"

    def test_snake_case_is_not_italicised(self):
        assert "<i>" not in to_html("call get_pull_request and my_var_name")

    def test_long_answers_split_under_limit_without_breaking_fences(self):
        md = "\n\n".join([f"Paragraph {i} " + "word " * 120 for i in range(20)] + ["```\n" + "code line\n" * 300 + "```"])
        parts = render_messages(md)
        assert len(parts) > 1 and all(len(p) <= LIMIT for p in parts)
        assert all(p.count("<pre>") == p.count("</pre>") for p in parts)

    def test_split_keeps_short_message_whole(self):
        assert split_markdown("a\n\nb") == ["a\n\nb"]

    def test_escape_heavy_text_still_fits(self):
        parts = render_messages("&" * 3400)
        assert all(len(p) <= LIMIT for p in parts) and "".join(strip_html(p) for p in parts) == "&" * 3400

    def test_github_links_become_labelled_buttons(self):
        md = "See https://github.com/a/b/pull/12 and https://github.com/a/b/pull/12 and https://github.com/a/b/actions/runs/99."
        assert github_links(md) == [("↗ b #12", "https://github.com/a/b/pull/12"),
                                    ("↗ b run", "https://github.com/a/b/actions/runs/99")]
