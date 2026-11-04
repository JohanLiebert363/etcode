"""Run with:  python -m unittest discover -s tests -v"""
import atexit
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "main"))
sys.path.insert(0, str(ROOT / "api-get"))

import agent as agent_module  # noqa: E402
import ai  # noqa: E402
import attach  # noqa: E402
import tools  # noqa: E402


_DIRS = []
atexit.register(lambda: [d.cleanup() for d in _DIRS])


def make_ws(files=None):
    tmp = tempfile.TemporaryDirectory()
    _DIRS.append(tmp)
    for name, content in (files or {}).items():
        path = Path(tmp.name) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode() if isinstance(content, str) else content)
    return tmp, tools.Workspace(tmp.name)


class WorkspaceTests(unittest.TestCase):
    def test_blocks_paths_outside_workspace(self):
        tmp, ws = make_ws({"a.txt": "hi"})
        for bad in ("../x.txt", "/etc/passwd", "sub/../../x"):
            with self.assertRaises(tools.ToolError):
                ws.prepare_write(bad, "x")

    def test_blocks_symlink_escape(self):
        tmp, ws = make_ws({"a.txt": "hi"})
        outside = tempfile.TemporaryDirectory()
        (Path(outside.name) / "secret.txt").write_text("nope")
        os.symlink(outside.name, Path(tmp.name) / "link")
        with self.assertRaises(tools.ToolError):
            ws.read_file("link/secret.txt")

    def test_blocks_secret_files_and_git(self):
        tmp, ws = make_ws({".env": "KEY=1", ".env.example": "KEY=", "api.json": "{}", ".git/config": "x"})
        for bad in (".env", "api.json", ".git/config"):
            with self.assertRaises(tools.ToolError):
                ws.read_file(bad)
        self.assertIn("KEY=", ws.read_file(".env.example"))
        listing = ws.list_files()
        self.assertNotIn("api.json", listing)
        self.assertNotIn(".git", listing)

    def test_read_file_ranges(self):
        tmp, ws = make_ws({"a.txt": "one\ntwo\nthree\n"})
        out = ws.read_file("a.txt", 2, 3)
        self.assertIn("2| two", out)
        self.assertNotIn("1| one", out)
        with self.assertRaises(tools.ToolError):
            ws.read_file("a.txt", 9)

    def test_binary_and_missing(self):
        tmp, ws = make_ws({"b.bin": b"\x00\x01\x02"})
        with self.assertRaises(tools.ToolError):
            ws.read_file("b.bin")
        with self.assertRaises(tools.ToolError):
            ws.read_file("nope.txt")

    def test_search(self):
        tmp, ws = make_ws({"a.py": "def foo():\n    pass\n", "sub/b.py": "foo = 1\n", "c.txt": "foo"})
        out = ws.search_files("foo", file_glob="*.py")
        self.assertIn("a.py:1", out)
        self.assertIn("sub/b.py:1", out)
        self.assertNotIn("c.txt", out)
        self.assertEqual(ws.search_files("zzz"), "No matches.")
        with self.assertRaises(tools.ToolError):
            ws.search_files("(")

    def test_edit_unique_missing_and_replace_all(self):
        tmp, ws = make_ws({"a.txt": "x = 1\ny = 1\n"})
        with self.assertRaises(tools.ToolError):
            ws.prepare_edit("a.txt", "= 1", "= 2")          # two matches
        with self.assertRaises(tools.ToolError):
            ws.prepare_edit("a.txt", "z = 9", "z = 2")      # no match
        with self.assertRaises(tools.ToolError):
            ws.prepare_edit("a.txt", "", "z")               # empty
        change = ws.prepare_edit("a.txt", "= 1", "= 2", replace_all="true")
        ws.apply(change)
        self.assertEqual((Path(tmp.name) / "a.txt").read_text(), "x = 2\ny = 2\n")

    def test_crlf_is_preserved(self):
        tmp, ws = make_ws({"w.txt": "a\r\nb\r\n"})
        ws.apply(ws.prepare_edit("w.txt", "a\nb", "a\nc"))
        self.assertEqual((Path(tmp.name) / "w.txt").read_bytes(), b"a\r\nc\r\n")

    def test_write_create_undo_and_backup(self):
        tmp, ws = make_ws({"a.txt": "old"})
        new = ws.prepare_write("deep/new.txt", "hello")
        self.assertEqual(new.kind, "create")
        ws.apply(new)
        self.assertEqual((Path(tmp.name) / "deep/new.txt").read_text(), "hello")
        ws.apply(ws.prepare_write("a.txt", "new"))
        backups = list((Path(tmp.name) / ".etcode" / "backups").rglob("a.txt"))
        self.assertEqual(backups[0].read_text(), "old")
        ws.undo()
        self.assertEqual((Path(tmp.name) / "a.txt").read_text(), "old")
        ws.undo()
        self.assertFalse((Path(tmp.name) / "deep/new.txt").exists())
        with self.assertRaises(tools.ToolError):
            ws.undo()

    def test_delete_and_undo(self):
        tmp, ws = make_ws({"a.txt": "keep me"})
        ws.apply(ws.prepare_delete("a.txt"))
        self.assertFalse((Path(tmp.name) / "a.txt").exists())
        ws.undo()
        self.assertEqual((Path(tmp.name) / "a.txt").read_text(), "keep me")

    def test_identical_write_rejected_and_diff(self):
        tmp, ws = make_ws({"a.txt": "same\n"})
        with self.assertRaises(tools.ToolError):
            ws.prepare_write("a.txt", "same\n")
        change = ws.prepare_write("a.txt", "different\n")
        diff = ws.diff(change)
        self.assertIn("-same", diff)
        self.assertIn("+different", diff)
        self.assertEqual(change.summary, "Edited a.txt (+1 -1)")


class AttachTests(unittest.TestCase):
    def test_mentions(self):
        tmp, ws = make_ws({"src/a.py": "print('hi')\n", ".env": "SECRET=1"})
        text, attached, notes = attach.expand_mentions(
            "look at @src/a.py, email me@example.com or @nothing and @.env", ws)
        self.assertEqual([a[0] for a in attached], ["src/a.py"])
        self.assertIn("[Attached file: src/a.py]", text)
        self.assertNotIn("SECRET", text)
        self.assertTrue(any(".env" in n for n in notes))

    def test_fence_grows_for_code_with_backticks(self):
        tmp, ws = make_ws({"r.md": "```py\nx\n```\n"})
        text, _, _ = attach.expand_mentions("see @r.md", ws)
        self.assertIn("````", text)


class HistoryTests(unittest.TestCase):
    def test_trim_drops_whole_old_turns(self):
        msgs = []
        for i in range(5):
            msgs += [{"role": "user", "content": "q" * 3500}, {"role": "assistant", "content": "a" * 3500}]
        kept, dropped = ai.trim_history(msgs, 3000)
        self.assertGreater(dropped, 0)
        self.assertEqual(kept[0]["role"], "user")
        self.assertEqual(kept[-1], msgs[-1])

    def test_trim_keeps_single_huge_turn(self):
        msgs = [{"role": "user", "content": "x" * 100000}]
        kept, dropped = ai.trim_history(msgs, 10)
        self.assertEqual((len(kept), dropped), (1, 0))


class FakeUI:
    def __init__(self, decisions=()):
        self.decisions = list(decisions)
        self.answers, self.logs, self.infos = [], [], []

    def status(self, text):
        return mock.MagicMock()

    def info(self, t): self.infos.append(t)
    def warn(self, t): self.infos.append(t)
    def show_answer(self, t): self.answers.append(t)
    def show_note(self, t): pass
    def tool_log(self, n, a): self.logs.append(n)
    def tool_error(self, t): self.logs.append(t)
    def show_change(self, c, d): pass

    def confirm_change(self, change, diff):
        return self.decisions.pop(0)


def call(name, args, cid="c1"):
    return SimpleNamespace(id=cid, function=SimpleNamespace(name=name, arguments=json.dumps(args)))


def reply(text="", calls=None):
    return SimpleNamespace(content=text, tool_calls=calls)


class AgentTests(unittest.TestCase):
    def run_agent(self, files, replies, decisions=(), auto=False):
        tmp, ws = make_ws(files)
        ui = FakeUI(decisions)
        agent = agent_module.Agent("claude", ws, ui, auto_approve=auto)
        with mock.patch.object(ai, "chat", side_effect=replies) as chat:
            agent.run_turn("do it")
        return tmp, ws, ui, agent, chat

    def test_read_edit_answer(self):
        replies = [
            reply("", [call("read_file", {"path": "a.txt"})]),
            reply("", [call("edit_file", {"path": "a.txt", "old_text": "old", "new_text": "new"}, "c2")]),
            reply("Done."),
        ]
        tmp, ws, ui, agent, chat = self.run_agent({"a.txt": "old\n"}, replies, decisions=[("yes", "")])
        self.assertEqual((Path(tmp.name) / "a.txt").read_text(), "new\n")
        self.assertEqual(ui.answers, ["Done."])
        roles = [m["role"] for m in agent.messages]
        self.assertEqual(roles, ["user", "assistant", "tool", "assistant", "tool", "assistant"])

    def test_declined_change_reaches_the_model(self):
        replies = [
            reply("", [call("write_file", {"path": "n.txt", "content": "x"})]),
            reply("Okay, skipped."),
        ]
        tmp, ws, ui, agent, chat = self.run_agent({}, replies, decisions=[("no", "use b.txt")])
        self.assertFalse((Path(tmp.name) / "n.txt").exists())
        tool_msg = [m for m in agent.messages if m["role"] == "tool"][0]
        self.assertIn("declined", tool_msg["content"])
        self.assertIn("use b.txt", tool_msg["content"])

    def test_always_turns_on_auto(self):
        replies = [
            reply("", [call("write_file", {"path": "1.txt", "content": "a"})]),
            reply("", [call("write_file", {"path": "2.txt", "content": "b"}, "c2")]),
            reply("ok"),
        ]
        tmp, ws, ui, agent, chat = self.run_agent({}, replies, decisions=[("always", "")])
        self.assertTrue(agent.auto_approve)
        self.assertTrue((Path(tmp.name) / "2.txt").exists())

    def test_tool_errors_do_not_crash(self):
        replies = [
            reply("", [call("read_file", {"path": "../x"}),
                       call("nope", {}, "c2"),
                       SimpleNamespace(id="c3", function=SimpleNamespace(name="read_file", arguments="{bad"))]),
            reply("recovered"),
        ]
        tmp, ws, ui, agent, chat = self.run_agent({}, replies)
        results = [m["content"] for m in agent.messages if m["role"] == "tool"]
        self.assertEqual(len(results), 3)
        self.assertTrue(all(r.startswith("Error:") for r in results))
        self.assertEqual(ui.answers, ["recovered"])

    def test_error_rolls_back_turn_when_nothing_changed(self):
        tmp, ws = make_ws({})
        agent = agent_module.Agent("claude", ws, FakeUI())
        with mock.patch.object(ai, "chat", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                agent.run_turn("hi")
        self.assertEqual(agent.messages, [])

    def test_error_after_change_keeps_history_valid(self):
        tmp, ws = make_ws({})
        agent = agent_module.Agent("claude", ws, FakeUI(), auto_approve=True)
        replies = [reply("", [call("write_file", {"path": "a.txt", "content": "x"})]), RuntimeError("boom")]
        with mock.patch.object(ai, "chat", side_effect=replies):
            with self.assertRaises(RuntimeError):
                agent.run_turn("hi")
        self.assertEqual(agent.messages[-1]["role"], "assistant")
        self.assertEqual(agent.messages[-2]["role"], "tool")

    def test_step_limit(self):
        tmp, ws = make_ws({"a.txt": "x"})
        agent = agent_module.Agent("claude", ws, FakeUI())
        loop = [reply("", [call("list_files", {})]) for _ in range(3)]
        with mock.patch.object(agent_module.config, "MAX_STEPS", 3), mock.patch.object(ai, "chat", side_effect=loop):
            agent.run_turn("go")
        self.assertTrue(any("Stopped after 3" in i for i in agent.ui.infos))


class FlaskTests(unittest.TestCase):
    def setUp(self):
        import app as web
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        web.KEY_FILE = Path(self.tmp.name) / "api.json"
        self.web = web
        self.client = web.app.test_client()

    def token(self):
        self.client.get("/")
        with self.client.session_transaction() as s:
            return s["csrf"]

    def test_rejects_missing_csrf(self):
        self.client.post("/", data={"openai": "sk-test"})
        self.assertFalse(self.web.KEY_FILE.exists())

    def test_saves_only_filled_fields_and_never_echoes_keys(self):
        resp = self.client.post("/", data={"csrf_token": self.token(), "openai": " sk-test123 ", "gemini": ""},
                                follow_redirects=True)
        self.assertEqual(json.loads(self.web.KEY_FILE.read_text()), {"openai": "sk-test123"})
        html = resp.get_data(as_text=True)
        self.assertIn("Saved: OpenAI.", html)
        self.assertNotIn("sk-test123", html)
        self.assertIn("Key saved", html)
        if os.name == "posix":
            self.assertEqual(self.web.KEY_FILE.stat().st_mode & 0o777, 0o600)

    def test_merges_with_existing_and_rejects_bad_keys(self):
        self.web.KEY_FILE.write_text(json.dumps({"claude": "keep"}))
        token = self.token()
        self.client.post("/", data={"csrf_token": token, "gemini": "g-1"})
        self.assertEqual(json.loads(self.web.KEY_FILE.read_text()), {"claude": "keep", "gemini": "g-1"})
        resp = self.client.post("/", data={"csrf_token": token, "openai": "has space"}, follow_redirects=True)
        self.assertIn("looks wrong", resp.get_data(as_text=True))
        self.assertNotIn("openai", json.loads(self.web.KEY_FILE.read_text()))

    def test_empty_submit_and_corrupt_file(self):
        self.web.KEY_FILE.write_text("not json")
        resp = self.client.post("/", data={"csrf_token": self.token()}, follow_redirects=True)
        self.assertIn("at least one API key", resp.get_data(as_text=True))
        self.assertEqual(self.web.load_keys(), {})


if __name__ == "__main__":
    unittest.main()
