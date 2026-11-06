"""Tests for commands, directory changes, and rate-limit handling."""
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console  # noqa: E402

from etcode import agent as agent_module  # noqa: E402
from etcode import ai, cli, config, tools  # noqa: E402
from etcode.ui import TerminalUI  # noqa: E402

PY = f'"{sys.executable}"'


def workspace():
    tmp = tempfile.TemporaryDirectory()
    return tmp, tools.Workspace(tmp.name)


class RateLimitError(Exception):
    """Same class name as litellm's, so it is detected the same way."""


class RunCommandTests(unittest.TestCase):
    def test_success_and_failure(self):
        tmp, ws = workspace()
        ok = ws.run_command(f'{PY} -c "print(42)"')
        self.assertIn("exit code: 0", ok)
        self.assertIn("42", ok)
        bad = ws.run_command(f'{PY} -c "import sys; print(\'oops\', file=sys.stderr); sys.exit(3)"')
        self.assertIn("exit code: 3", bad)
        self.assertIn("--- stderr ---", bad)

    def test_runs_in_workspace_and_timeout(self):
        tmp, ws = workspace()
        out = ws.run_command(f'{PY} -c "import os; print(os.getcwd())"')
        self.assertIn(Path(tmp.name).resolve().name, out)
        slow = ws.run_command(f'{PY} -c "import time; time.sleep(5)"', timeout_seconds=1)
        self.assertIn("timed out after 1s", slow)

    def test_long_output_is_cut_and_empty_rejected(self):
        tmp, ws = workspace()
        out = ws.run_command(f'{PY} -c "print(\'x\' * 100000)"')
        self.assertIn("characters cut", out)
        self.assertLess(len(out), 25_000)
        with self.assertRaises(tools.ToolError):
            ws.run_command("   ")


class FakeUI:
    def __init__(self, answers=()):
        self.answers = list(answers)
        self.shown = []

    def status(self, text): return mock.MagicMock()
    def info(self, t): pass
    def warn(self, t): pass
    def show_answer(self, t): pass
    def show_note(self, t): pass
    def tool_log(self, n, a): pass
    def tool_error(self, t): pass
    def show_change(self, c, d): pass
    def show_command(self, c, auto=False): self.shown.append((c, auto))
    def confirm_command(self, command, folder): return self.answers.pop(0)


def call(name, args):
    return SimpleNamespace(id="c1", function=SimpleNamespace(name=name, arguments=__import__("json").dumps(args)))


class AgentCommandTests(unittest.TestCase):
    def run_agent(self, ui, allow=False):
        tmp, ws = workspace()
        agent = agent_module.Agent("claude", ws, ui, allow_commands=allow)
        replies = [SimpleNamespace(content="", tool_calls=[call("run_command", {"command": f'{PY} -c "print(7)"'})]),
                   SimpleNamespace(content="done", tool_calls=None)]
        with mock.patch.object(ai, "chat", side_effect=replies):
            agent.run_turn("run it")
        return agent

    def tool_result(self, agent):
        return [m["content"] for m in agent.messages if m["role"] == "tool"][0]

    def test_approved_command_runs(self):
        agent = self.run_agent(FakeUI([("yes", "")]))
        self.assertIn("exit code: 0", self.tool_result(agent))
        self.assertEqual(agent.commands_run, 1)

    def test_declined_command_does_not_run(self):
        agent = self.run_agent(FakeUI([("no", "use pytest")]))
        result = self.tool_result(agent)
        self.assertIn("declined", result)
        self.assertIn("use pytest", result)
        self.assertEqual(agent.commands_run, 0)

    def test_allow_commands_skips_the_question(self):
        ui = FakeUI([])        # would raise if it asked
        agent = self.run_agent(ui, allow=True)
        self.assertEqual(len(ui.shown), 1)
        self.assertIn("exit code: 0", self.tool_result(agent))

    def test_auto_approve_files_does_not_approve_commands(self):
        tmp, ws = workspace()
        ui = FakeUI([("no", "")])
        agent = agent_module.Agent("claude", ws, ui, auto_approve=True)
        replies = [SimpleNamespace(content="", tool_calls=[call("run_command", {"command": "echo hi"})]),
                   SimpleNamespace(content="ok", tool_calls=None)]
        with mock.patch.object(ai, "chat", side_effect=replies):
            agent.run_turn("x")
        self.assertEqual(agent.commands_run, 0)


class RateLimitTests(unittest.TestCase):
    GEMINI = ('litellm.RateLimitError: geminiException - {"error": {"code": 429, "message": '
              '"You exceeded your current quota. Please retry in 18.417945127s.", "status": "RESOURCE_EXHAUSTED"}}')

    def test_uses_the_wait_the_provider_asked_for(self):
        error = RateLimitError(self.GEMINI)
        self.assertTrue(ai.is_rate_limit(error))
        self.assertAlmostEqual(ai.retry_delay(error, 0), 19.417945127)

    def test_no_hint_waits_longer_each_time_and_caps(self):
        error = RateLimitError("429 too many requests")
        self.assertEqual([ai.retry_delay(error, n) for n in range(3)], [10, 20, 30])
        self.assertEqual(ai.retry_delay(RateLimitError("429 retry in 500s"), 0), ai.MAX_WAIT)

    def test_daily_or_unpaid_limits_are_not_retried(self):
        for text in ("429 GenerateRequestsPerDayPerProject quota", "429 insufficient_quota", "429 limit: 0"):
            self.assertIsNone(ai.retry_delay(RateLimitError(text), 0))

    def test_other_errors(self):
        self.assertEqual(ai.retry_delay(Exception("503 overloaded"), 1), 4)
        self.assertIsNone(ai.retry_delay(Exception("401 bad key"), 0))

    def test_chat_waits_then_succeeds(self):
        import litellm
        message = SimpleNamespace(content="hello", tool_calls=None)
        results = [RateLimitError(self.GEMINI), SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)]

        def fake(**kw):
            r = results.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        sleeps, notes = [], []
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k"}), \
                mock.patch.object(litellm, "completion", side_effect=fake), \
                mock.patch.object(ai.time, "sleep", side_effect=sleeps.append):
            self.assertEqual(ai.chat("claude", [], notify=notes.append).content, "hello")
        self.assertAlmostEqual(sleeps[0], 19.417945127)
        self.assertIn("Rate limit reached", notes[0])

    def test_pacing(self):
        sleeps = []
        with mock.patch.object(config, "REQUESTS_PER_MINUTE", 6), \
                mock.patch.object(ai.time, "monotonic", return_value=100.0), \
                mock.patch.object(ai.time, "sleep", side_effect=sleeps.append):
            ai._last_request[0] = 95.0      # last request 5s ago, spacing is 10s
            ai._pace(None)
        self.assertEqual(sleeps, [5.0])

    def test_friendly_message(self):
        text = cli.explain_error(RateLimitError(self.GEMINI), "gemini")
        self.assertIn("Google Gemini", text)
        self.assertIn("18 seconds", text)
        self.assertIn("ETCODE_REQUESTS_PER_MINUTE", text)
        self.assertNotIn("RESOURCE_EXHAUSTED", text)
        self.assertIn("rejected your API key", cli.explain_error(Exception("API key not valid"), "gemini"))


class CliCommandTests(unittest.TestCase):
    def make(self):
        tmp, ws = workspace()
        (Path(tmp.name) / "sub").mkdir()
        (Path(tmp.name) / "sub" / "f.txt").write_text("hi")
        ui = TerminalUI(interactive=False)
        ui.console = Console(file=io.StringIO(), width=100)
        return tmp, agent_module.Agent("claude", ws, ui), ui

    def test_cd_and_ls(self):
        tmp, agent, ui = self.make()
        pending = []
        cli.handle_command("/cd sub", agent, ui, pending)
        self.assertEqual(agent.ws.root, (Path(tmp.name) / "sub").resolve())
        cli.handle_command("/ls", agent, ui, pending)
        self.assertIn("f.txt", ui.console.file.getvalue())
        cli.handle_command("/cd ..", agent, ui, pending)
        self.assertEqual(agent.ws.root, Path(tmp.name).resolve())
        cli.handle_command("/cd does-not-exist", agent, ui, pending)
        self.assertEqual(agent.ws.root, Path(tmp.name).resolve())     # unchanged

    def test_user_command_output_is_queued_for_next_message(self):
        tmp, agent, ui = self.make()
        pending = []
        cli.run_user_command(f'{PY} -c "print(\'from-shell\')"', agent, ui, pending)
        self.assertIn("from-shell", ui.console.file.getvalue())
        self.assertEqual(len(pending), 1)
        sent = []
        with mock.patch.object(agent, "run_turn", side_effect=sent.append):
            cli.run_once(agent, ui, "why?", pending)
        self.assertTrue(sent[0].startswith("why?"))
        self.assertIn("from-shell", sent[0])
        self.assertEqual(pending, [])

    def test_non_interactive_never_runs_commands(self):
        tmp, agent, ui = self.make()
        self.assertEqual(ui.confirm_command("rm -rf /", str(tmp.name))[0], "no")


if __name__ == "__main__":
    unittest.main()


class ProviderMenuTests(unittest.TestCase):
    OPTIONS = [("openai", "OpenAI  (openai/gpt-4o)"), ("gemini", "Google Gemini"), ("claude", "Anthropic Claude")]

    def pick(self, keys, default=None):
        import threading
        from prompt_toolkit.application import create_app_session
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output import DummyOutput
        ui = TerminalUI(interactive=True)
        ui.console = Console(file=io.StringIO())
        with create_pipe_input() as pipe:
            with create_app_session(input=pipe, output=DummyOutput()):
                threading.Thread(target=lambda: pipe.send_text(keys), daemon=True).start()
                return ui.pick_provider(self.OPTIONS, default=default)

    def test_enter_picks_the_default(self):
        self.assertEqual(self.pick("\r", default="gemini"), "gemini")

    def test_arrow_keys_move_the_choice(self):
        self.assertEqual(self.pick("\x1b[B\x1b[B\r"), "claude")      # down, down, enter
        self.assertEqual(self.pick("\x1b[B\x1b[A\r"), "openai")      # down, up, enter

    def test_falls_back_to_typing_without_the_library(self):
        ui = TerminalUI(interactive=True)
        ui.console = Console(file=io.StringIO())
        with mock.patch.dict(sys.modules, {"InquirerPy": None}), \
                mock.patch("etcode.ui.Prompt.ask", return_value="claude") as ask:
            self.assertEqual(ui.pick_provider(self.OPTIONS), "claude")
        self.assertEqual(ask.call_args.kwargs["choices"], ["openai", "gemini", "claude"])

    def test_provider_command_without_argument_opens_the_menu(self):
        tmp, ws = workspace()
        ui = TerminalUI(interactive=False)
        ui.console = Console(file=io.StringIO())
        agent = agent_module.Agent("openai", ws, ui)
        with mock.patch.object(config, "configured_providers", return_value=["openai", "claude"]), \
                mock.patch.object(ui, "pick_provider", return_value="claude") as pick:
            cli.handle_command("/provider", agent, ui, [])
        self.assertEqual(agent.provider, "claude")
        self.assertEqual([v for v, _ in pick.call_args.args[0]], ["openai", "claude"])
