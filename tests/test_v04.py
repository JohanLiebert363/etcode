"""Tests for streaming, ETCODE.md, rate-limit fallback, sessions, safe commands and usage."""
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console  # noqa: E402

from etcode import agent as agent_module  # noqa: E402
from etcode import ai, cli, config, safety, sessions, tools  # noqa: E402
from etcode.ui import TerminalUI  # noqa: E402

_DIRS = []


def tmpdir():
    d = tempfile.TemporaryDirectory()
    _DIRS.append(d)
    return Path(d.name)


def tearDownModule():
    for d in _DIRS:
        try:
            d.cleanup()
        except OSError:
            pass


class RateLimitError(Exception):
    pass


class FakeUI:
    def __init__(self, fallback=None):
        self.fallback = fallback
        self.answers, self.notes, self.usage_lines, self.commands, self.warnings = [], [], [], [], []

    def status(self, text): return mock.MagicMock()
    def info(self, t): pass
    def warn(self, t): self.warnings.append(t)
    def show_answer(self, t): self.answers.append(t)
    def show_note(self, t): self.notes.append(t)
    def tool_log(self, n, a): pass
    def tool_error(self, t): pass
    def show_change(self, c, d): pass
    def show_usage(self, t): self.usage_lines.append(t)
    def show_command(self, c, auto=False): self.commands.append((c, auto))
    def confirm_command(self, command, folder): raise AssertionError("should not have asked")

    def offer_fallback(self, current, options, reason=""):
        self.offered = options
        self.reason = reason
        return self.fallback

    def stream_reply(self):
        ui = self

        class Stream:
            shown = False

            def __enter__(self): return self
            def __exit__(self, *a): return False

            def write(self, piece):
                Stream.shown = True
                ui.streamed = getattr(ui, "streamed", "") + piece
        return Stream()


def make_agent(ui=None, **kw):
    root = tmpdir()
    return root, agent_module.Agent("gemini", tools.Workspace(root), ui or FakeUI(), **kw)


def tool_call(name, args, cid="c1"):
    return NS(id=cid, function=NS(name=name, arguments=json.dumps(args)))


class SafeCommandTests(unittest.TestCase):
    def test_read_only_commands_are_allowed(self):
        for command in ("git status", "git log -5", "git diff --stat", "git show HEAD", "git branch -a",
                        "git remote -v", "git diff src/app.py", "ls", "ls -la src", "dir", "pwd",
                        "python --version", "node -V", "git --version"):
            self.assertTrue(safety.is_read_only(command), command)

    def test_everything_else_still_asks(self):
        for command in ("rm -rf .", "git push", "git commit -am x", "git branch -D main", "git remote add x y",
                        "git -c core.pager=evil status", "git diff --output=out.txt", "ls ..", "ls /etc",
                        "ls C:\\Windows", "cat .env", "git show HEAD:.env", "git diff -- .env",
                        "git status; rm x", "git status && rm x", "git log | head", "ls > out.txt",
                        "echo $HOME", "git status `whoami`", "python script.py", "python -c print(1)",
                        "pytest", "npm test", "evil --version", "", "   ", "git"):
            self.assertFalse(safety.is_read_only(command), command)

    def test_can_be_turned_off(self):
        with mock.patch.object(config, "SAFE_COMMANDS", False):
            self.assertFalse(safety.is_read_only("git status"))


class StreamAssemblyTests(unittest.TestCase):
    @staticmethod
    def chunk(content=None, calls=None, usage=None):
        return NS(choices=[NS(delta=NS(content=content, tool_calls=calls))], usage=usage)

    @staticmethod
    def piece(index, id=None, name=None, args=None):
        return NS(index=index, id=id, function=NS(name=name, arguments=args))

    def test_text_and_split_arguments(self):
        got = []
        reply = ai.collect_stream([
            self.chunk("Hel"), self.chunk("lo"),
            self.chunk(calls=[self.piece(0, "a", "read_file", '{"pa')]),
            self.chunk(calls=[self.piece(0, None, None, 'th": "x.py"}')]),
            NS(choices=[], usage={"prompt_tokens": 10, "completion_tokens": 5}),
        ], got.append)
        self.assertEqual((reply.content, got), ("Hello", ["Hel", "lo"]))
        self.assertEqual([(c.id, c.function.name, c.function.arguments) for c in reply.tool_calls],
                         [("a", "read_file", '{"path": "x.py"}')])
        self.assertEqual(reply.usage, {"prompt_tokens": 10, "completion_tokens": 5})

    def test_parallel_calls_even_when_a_provider_reuses_index_zero(self):
        reply = ai.collect_stream([
            self.chunk(calls=[self.piece(0, "a", "read_file", '{"path":"1"}')]),
            self.chunk(calls=[self.piece(0, "b", "read_file", '{"path":"2"}')]),
            self.chunk(calls=[self.piece(1, "c", "list_files", None)]),
        ], lambda p: None)
        self.assertEqual([(c.id, c.function.arguments) for c in reply.tool_calls],
                         [("a", '{"path":"1"}'), ("b", '{"path":"2"}'), ("c", "{}")])

    def test_works_with_real_litellm_chunks(self):
        import litellm
        chunks = litellm.completion(model="gpt-4o", messages=[{"role": "user", "content": "hi"}],
                                    mock_response="Hello from the mock", stream=True)
        got = []
        reply = ai.collect_stream(chunks, got.append)
        self.assertEqual(reply.content, "Hello from the mock")
        self.assertGreater(len(got), 1)           # arrived in pieces, not all at once


class StreamingChatTests(unittest.TestCase):
    def run_chat(self, completion, **kw):
        import litellm
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k"}), \
                mock.patch.object(litellm, "completion", side_effect=completion), \
                mock.patch.object(ai.time, "sleep"):
            return ai.chat("gemini", [{"role": "user", "content": "hi"}], **kw)

    def test_streams_and_reports_usage(self):
        seen = {}

        def completion(**kw):
            seen.update(kw)
            return iter([StreamAssemblyTests.chunk("Hi "), StreamAssemblyTests.chunk("there"),
                         NS(choices=[], usage={"prompt_tokens": 100, "completion_tokens": 20})])

        pieces = []
        reply = self.run_chat(completion, on_text=pieces.append)
        self.assertTrue(seen["stream"])
        self.assertEqual((reply.content, pieces), ("Hi there", ["Hi ", "there"]))
        self.assertEqual((reply.usage["prompt"], reply.usage["completion"], reply.usage["estimated"]), (100, 20, False))

    def test_usage_is_estimated_when_the_provider_gives_none(self):
        reply = self.run_chat(lambda **kw: iter([StreamAssemblyTests.chunk("x" * 350)]), on_text=lambda p: None)
        self.assertTrue(reply.usage["estimated"])
        self.assertEqual(reply.usage["completion"], 100)

    def test_not_retried_after_text_was_shown(self):
        calls = []

        def completion(**kw):
            calls.append(1)

            def gen():
                yield StreamAssemblyTests.chunk("partial")
                raise RuntimeError("503 overloaded")
            return gen()

        with self.assertRaises(RuntimeError):
            self.run_chat(completion, on_text=lambda p: None)
        self.assertEqual(len(calls), 1)           # a retry would print "partial" twice

    def test_retried_when_nothing_was_shown(self):
        results = [RuntimeError("503 overloaded"), iter([StreamAssemblyTests.chunk("ok")])]

        def completion(**kw):
            r = results.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        self.assertEqual(self.run_chat(completion, on_text=lambda p: None).content, "ok")


class AgentFeatureTests(unittest.TestCase):
    def run_turn(self, agent, replies, **chat_kw):
        with mock.patch.object(ai, "chat", side_effect=replies) as chat:
            agent.run_turn("go")
        return chat

    def test_streamed_text_is_not_printed_twice(self):
        ui = FakeUI()
        _, agent = make_agent(ui, streaming=True)

        def fake_chat(provider, messages, tools=None, notify=None, on_text=None, **kw):
            on_text("Hello")
            return ai.Reply("Hello")

        with mock.patch.object(ai, "chat", side_effect=fake_chat):
            agent.run_turn("hi")
        self.assertEqual(ui.streamed, "Hello")
        self.assertEqual(ui.answers, [])           # already on screen

    def test_not_streaming_shows_the_answer(self):
        ui = FakeUI()
        _, agent = make_agent(ui)
        self.run_turn(agent, [ai.Reply("Plain answer")])
        self.assertEqual(ui.answers, ["Plain answer"])

    def test_etcode_md_reaches_the_system_prompt(self):
        root, agent = make_agent()
        self.assertNotIn("Project instructions", agent.system_prompt()["content"])
        (root / "ETCODE.md").write_text("Always use tabs. Never touch legacy/.")
        text = agent.system_prompt()["content"]
        self.assertIn("Always use tabs", text)
        self.assertIn("Never touch legacy/", text)
        (root / "ETCODE.md").write_text("Use spaces now.")      # edits apply on the next request
        self.assertIn("Use spaces now", agent.system_prompt()["content"])

    def test_global_instructions_and_size_cap(self):
        root, agent = make_agent()
        home = tmpdir()
        (home / "ETCODE.md").write_text("Reply in German.")
        (root / "ETCODE.md").write_text("x" * (config.MAX_INSTRUCTION_CHARS + 5000))
        with mock.patch.object(config, "config_dir", return_value=home):
            text = agent.system_prompt()["content"]
        self.assertIn("Reply in German.", text)
        self.assertLess(text.count("x"), config.MAX_INSTRUCTION_CHARS + 10)

    def test_rate_limit_offers_another_provider(self):
        ui = FakeUI(fallback="claude")
        _, agent = make_agent(ui)
        used = []

        def fake_chat(provider, messages, **kw):
            used.append(provider)
            if provider == "gemini":
                raise RateLimitError("429 quota")
            return ai.Reply("from claude")

        with mock.patch.object(ai, "chat", side_effect=fake_chat), \
                mock.patch.object(config, "configured_providers", return_value=["gemini", "claude"]):
            agent.run_turn("hi")
        self.assertEqual(used, ["gemini", "claude"])
        self.assertEqual(agent.provider, "claude")
        self.assertEqual([name for name, _ in ui.offered], ["claude"])
        self.assertEqual(ui.answers, ["from claude"])

    def test_declining_the_fallback_raises_and_keeps_history_clean(self):
        ui = FakeUI(fallback=None)
        _, agent = make_agent(ui)
        with mock.patch.object(ai, "chat", side_effect=RateLimitError("429 quota")), \
                mock.patch.object(config, "configured_providers", return_value=["gemini", "claude"]):
            with self.assertRaises(RateLimitError):
                agent.run_turn("hi")
        self.assertEqual((agent.provider, agent.messages), ("gemini", []))

    def test_no_other_provider_means_no_offer(self):
        ui = FakeUI(fallback="claude")
        _, agent = make_agent(ui)
        with mock.patch.object(ai, "chat", side_effect=RateLimitError("429")), \
                mock.patch.object(config, "configured_providers", return_value=["gemini"]):
            with self.assertRaises(RateLimitError):
                agent.run_turn("hi")
        self.assertFalse(hasattr(ui, "offered"))

    def test_auto_fallback_switches_without_asking(self):
        ui = FakeUI(fallback=None)
        _, agent = make_agent(ui)
        replies = [RateLimitError("429"), ai.Reply("ok")]
        with mock.patch.object(ai, "chat", side_effect=replies), \
                mock.patch.object(config, "AUTO_FALLBACK", True), \
                mock.patch.object(config, "configured_providers", return_value=["gemini", "openai"]):
            agent.run_turn("hi")
        self.assertEqual(agent.provider, "openai")
        self.assertFalse(hasattr(ui, "offered"))

    def test_read_only_command_runs_without_asking(self):
        ui = FakeUI()
        _, agent = make_agent(ui)
        replies = [ai.Reply("", [tool_call("run_command", {"command": "git --version"})]), ai.Reply("done")]
        self.run_turn(agent, replies)           # FakeUI.confirm_command would raise if asked
        self.assertEqual(ui.commands, [("git --version", True)])
        self.assertEqual(agent.commands_run, 1)

    def test_usage_adds_up_and_is_shown(self):
        ui = FakeUI()
        _, agent = make_agent(ui)
        usage = {"prompt": 1000, "completion": 200, "estimated": False, "cost": 0.01}
        replies = [ai.Reply("", [tool_call("list_files", {})], dict(usage)), ai.Reply("done", [], dict(usage))]
        self.run_turn(agent, replies)
        self.assertEqual((agent.usage["prompt"], agent.usage["completion"]), (2000, 400))
        self.assertAlmostEqual(agent.usage["cost"], 0.02)
        self.assertEqual(len(ui.usage_lines), 1)
        self.assertIn("2,000 in, 400 out", ui.usage_lines[0])
        self.assertIn("$0.0200", ui.usage_lines[0])

    def test_unknown_price_and_estimates_are_labelled(self):
        totals = agent_module.new_totals()
        agent_module.add_usage(totals, {"prompt": 10, "completion": 5, "estimated": True, "cost": None})
        text = agent_module.describe_usage(totals)
        self.assertEqual(text, "~10 in, ~5 out")
        self.assertNotIn("$", text)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tmpdir()
        patcher = mock.patch.object(config, "sessions_dir", return_value=self.folder)
        patcher.start()
        self.addCleanup(patcher.stop)

    def agent_with_history(self):
        root, agent = make_agent()
        agent.messages = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
        agent.usage["prompt"] = 123
        return agent

    def test_round_trip(self):
        sessions.save("work", self.agent_with_history())
        _, fresh = make_agent()
        sessions.restore(fresh, sessions.load("work"))
        self.assertEqual([m["content"] for m in fresh.messages], ["hello", "hi"])
        self.assertEqual(fresh.usage["prompt"], 123)
        if os.name == "posix":
            self.assertEqual((self.folder / "work.json").stat().st_mode & 0o777, 0o600)

    def test_bad_names_and_missing_and_damaged(self):
        for bad in ("../x", "a b", "", "x" * 61):
            with self.assertRaises(sessions.SessionError):
                sessions.save(bad, self.agent_with_history())
        with self.assertRaises(sessions.SessionError):
            sessions.load("nope")
        (self.folder / "broken.json").write_text("{not json")
        (self.folder / "empty.json").write_text(json.dumps({"messages": [{"role": "hacker"}]}))
        for name in ("broken", "empty"):
            with self.assertRaises(sessions.SessionError):
                sessions.load(name)

    def test_list_and_delete(self):
        sessions.save("a", self.agent_with_history())
        sessions.save("b", self.agent_with_history())
        self.assertEqual(sorted(n for n, _, _ in sessions.list_sessions()), ["a", "b"])
        sessions.delete("a")
        sessions.delete("does-not-exist")           # no error
        self.assertEqual([n for n, _, _ in sessions.list_sessions()], ["b"])

    def test_cli_commands(self):
        ui = TerminalUI(interactive=False)
        ui.console = Console(file=io.StringIO(), width=120)
        agent = self.agent_with_history()
        cli.handle_command("/save mine", agent, ui, [])
        cli.handle_command("/clear", agent, ui, [])
        self.assertEqual(agent.messages, [])
        cli.handle_command("/load mine", agent, ui, [])
        self.assertEqual(len(agent.messages), 2)
        cli.handle_command("/sessions", agent, ui, [])
        self.assertIn("mine", ui.console.file.getvalue())
        cli.handle_command("/load ghost", agent, ui, [])
        self.assertIn("No saved session", ui.console.file.getvalue())

    def test_autosave_continue_and_clear(self):
        ui = TerminalUI(interactive=False)
        ui.console = Console(file=io.StringIO(), width=120)
        agent = self.agent_with_history()
        cli.autosave(agent, ui)
        self.assertTrue((self.folder / "last.json").exists())
        cli.handle_command("/clear", agent, ui, [])
        self.assertFalse((self.folder / "last.json").exists())      # clearing also removes the autosave
        with mock.patch.object(config, "AUTOSAVE", False):
            cli.autosave(self.agent_with_history(), ui)
        self.assertFalse((self.folder / "last.json").exists())
        self.assertTrue(cli.parse_args(["-c"]).continue_)
        self.assertTrue(cli.parse_args(["--continue"]).continue_)

    def test_warns_when_restoring_into_another_folder(self):
        ui = TerminalUI(interactive=False)
        ui.console = Console(file=io.StringIO(), width=200)
        sessions.save("x", self.agent_with_history())
        _, other = make_agent()
        cli.restore_session(other, ui, sessions.load("x"))
        self.assertIn("You are in", ui.console.file.getvalue())

    def test_stream_toggle(self):
        ui = TerminalUI(interactive=False)
        ui.console = Console(file=io.StringIO())
        _, agent = make_agent()
        cli.handle_command("/stream on", agent, ui, [])
        self.assertTrue(agent.streaming)
        cli.handle_command("/stream off", agent, ui, [])
        self.assertFalse(agent.streaming)


class StreamViewTests(unittest.TestCase):
    def ui(self):
        ui = TerminalUI(interactive=True)
        ui.console = Console(file=io.StringIO(), width=80)
        return ui

    def test_shows_text_and_frames_it(self):
        ui = self.ui()
        with ui.stream_reply() as stream:
            stream.write("Hello ")
            stream.write("**world**")
        self.assertTrue(stream.shown)
        out = ui.console.file.getvalue()
        self.assertIn("ETCODE", out)
        self.assertIn("Hello", out)
        self.assertIn("world", out)

    def test_silent_when_nothing_arrives(self):
        ui = self.ui()
        with ui.stream_reply() as stream:
            pass
        self.assertFalse(stream.shown)
        self.assertNotIn("ETCODE", ui.console.file.getvalue())

    def test_fallback_menu_declined(self):
        ui = self.ui()
        with mock.patch.object(ui, "pick_provider", return_value=""):
            self.assertIsNone(ui.offer_fallback("Google Gemini", [("claude", "Claude")]))
        with mock.patch.object(ui, "pick_provider", return_value="claude"):
            self.assertEqual(ui.offer_fallback("Google Gemini", [("claude", "Claude")]), "claude")
        ui.interactive = False
        self.assertIsNone(ui.offer_fallback("Google Gemini", [("claude", "Claude")]))


if __name__ == "__main__":
    unittest.main()
