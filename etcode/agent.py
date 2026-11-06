"""The conversation loop: ask the AI, run its file tools, repeat until it answers."""
from __future__ import annotations

import json
import platform

from . import ai
from . import config
from . import safety
from . import tools


def new_totals() -> dict:
    return {"prompt": 0, "completion": 0, "cost": 0.0, "cost_known": True, "estimated": False}


def add_usage(totals: dict, usage: dict) -> None:
    totals["prompt"] += usage["prompt"]
    totals["completion"] += usage["completion"]
    totals["estimated"] = totals["estimated"] or usage["estimated"]
    if usage["cost"] is None:
        totals["cost_known"] = False
    else:
        totals["cost"] += usage["cost"]


def describe_usage(totals: dict) -> str:
    mark = "~" if totals["estimated"] else ""
    text = f"{mark}{totals['prompt']:,} in, {mark}{totals['completion']:,} out"
    if totals["cost_known"] and totals["cost"] > 0:
        text += f", about ${totals['cost']:.4f} at list price"
    return text


class Agent:
    def __init__(self, provider: str, workspace: tools.Workspace, ui, auto_approve: bool = False,
                 allow_commands: bool = False, streaming: bool = False):
        self.provider = provider
        self.ws = workspace
        self.ui = ui
        self.auto_approve = auto_approve      # file changes only
        self.allow_commands = allow_commands  # run commands without asking
        self.streaming = streaming            # show replies as they are written
        self.commands_run = 0
        self.usage = new_totals()             # whole session
        self._turn = new_totals()             # current turn
        self.messages: list[dict] = []
        self.changes_applied = 0

    # ---- helpers -----------------------------------------------------------

    def system_prompt(self) -> dict:
        base = (
            "You are Etcode, an AI coding assistant running in the user's terminal.\n"
            f"Workspace folder: {self.ws.root}\n"
            f"Operating system: {platform.system()}\n\n"
            "You can list, search and read files, create, edit or delete them, and run shell commands, all with tools. "
            "All paths are relative to the workspace. The user reviews every change and every command first.\n"
            "Rules:\n"
            "- Each request you make is slow and may count against a rate limit. When you need several files, "
            "ask for all of them in one step (several tool calls at once) instead of one at a time.\n"
            "- Read a file before you edit it.\n"
            "- Prefer edit_file with a small, unique old_text over rewriting a whole file.\n"
            "- Make only the changes the user asked for.\n"
            "- If a tool returns an error, fix the arguments and try again.\n"
            "- If the user declined a change, do not propose the same change again. Follow their note.\n"
            "- Use run_command for tests, builds and git. Each command runs in a fresh shell, so cd does not persist. "
            "Never run destructive commands (deleting, force-pushing, formatting disks) unless the user asked.\n"
            "- After changing files, say briefly what changed and why.\n"
            "- Replies are shown as Markdown in a terminal, so keep them short and clear.\n"
            "The user may attach files with @path; their contents appear in the message."
        )
        extra = self.instructions()
        if extra:
            base += "\n\nProject instructions from the user (follow them):\n" + extra
        return {"role": "system", "content": base}

    def instructions(self) -> str:
        """Text of ETCODE.md in the workspace, plus your global one. Read on every request, so edits apply at once."""
        parts = []
        for label, path in (("your global ETCODE.md", config.config_dir() / "ETCODE.md"),
                            ("ETCODE.md in this folder", self.ws.root / "ETCODE.md")):
            try:
                text = path.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError):
                continue
            if text:
                parts.append(f"From {label}:\n{text[:config.MAX_INSTRUCTION_CHARS]}")
        return "\n\n".join(parts)

    def estimated_tokens(self) -> int:
        return ai.estimate_tokens(self.messages)

    def clear(self) -> None:
        self.messages.clear()
        self.usage = new_totals()

    def _trim(self) -> None:
        self.messages, dropped = ai.trim_history(self.messages, config.MAX_CONTEXT_TOKENS)
        if dropped:
            self.ui.warn(f"Dropped {dropped} old messages to stay within the context limit.")

    # ---- one user turn -----------------------------------------------------

    def run_turn(self, user_text: str) -> None:
        user_message = {"role": "user", "content": user_text}
        self.messages.append(user_message)
        applied_before = self.changes_applied
        self._turn = new_totals()
        try:
            self._loop()
        except BaseException:
            if self.changes_applied == applied_before:
                # Nothing changed on disk: forget this turn so the user can simply retry.
                for index, message in enumerate(self.messages):
                    if message is user_message:
                        del self.messages[index:]
                        break
            else:
                self.messages.append({"role": "assistant", "content":
                                      "(This turn was interrupted after some files were changed.)"})
            raise
        show = getattr(self.ui, "show_usage", None)
        if show and self._turn["prompt"]:
            show(f"This turn: {describe_usage(self._turn)}. Session: {describe_usage(self.usage)}.")

    def _loop(self) -> None:
        for _ in range(config.MAX_STEPS):
            self._trim()
            reply, streamed = self._ask_model()
            usage = getattr(reply, "usage", None)
            if usage:
                add_usage(self._turn, usage)
                add_usage(self.usage, usage)

            text = getattr(reply, "content", None) or ""
            raw_calls = getattr(reply, "tool_calls", None) or []
            calls = [{"id": c.id, "type": "function",
                      "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"}}
                     for c in raw_calls]

            entry = {"role": "assistant", "content": (text or None) if calls else (text or "(empty reply)")}
            if calls:
                entry["tool_calls"] = calls
            self.messages.append(entry)

            if not calls:
                if not streamed:
                    self.ui.show_answer(text or "(The AI sent an empty reply.)")
                return

            if text and not streamed:
                self.ui.show_note(text)
            pending = list(calls)
            try:
                for call in calls:
                    result = self._run_call(call)
                    self.messages.append({"role": "tool", "tool_call_id": call["id"],
                                          "name": call["function"]["name"], "content": result})
                    pending.remove(call)
            except BaseException:
                for call in pending:   # keep the history valid even when interrupted
                    self.messages.append({"role": "tool", "tool_call_id": call["id"],
                                          "name": call["function"]["name"],
                                          "content": "Cancelled before it ran."})
                raise

        self.ui.warn(f"Stopped after {config.MAX_STEPS} steps. Send another message to continue.")

    def _ask_model(self):
        """Ask the AI for the next step. Returns (reply, text_was_already_shown).
        On a rate limit, offers to continue with another configured provider."""
        while True:
            messages = [self.system_prompt()] + self.messages
            try:
                if self.streaming:
                    with self.ui.stream_reply() as stream:
                        reply = ai.chat(self.provider, messages, tools=tools.TOOL_SPECS,
                                        notify=self.ui.warn, on_text=stream.write)
                    return reply, stream.shown
                with self.ui.status("Thinking..."):
                    reply = ai.chat(self.provider, messages, tools=tools.TOOL_SPECS, notify=self.ui.warn)
                return reply, False
            except Exception as error:
                if not ai.is_rate_limit(error):
                    raise
                other = self._fallback_provider()
                if other is None:
                    raise
                self.provider = other

    def _fallback_provider(self):
        others = [name for name in config.configured_providers() if name != self.provider]
        if not others:
            return None
        if config.AUTO_FALLBACK:
            self.ui.warn(f"Rate limit reached. Switching to {others[0]}.")
            return others[0]
        offer = getattr(self.ui, "offer_fallback", None)
        if offer is None:
            return None
        labels = [(name, f"{config.PROVIDERS[name]['label']}  ({config.model_for(name)})") for name in others]
        return offer(config.PROVIDERS[self.provider]["label"], labels) or None

    # ---- tool calls --------------------------------------------------------

    def _run_call(self, call: dict) -> str:
        name = call["function"]["name"]
        raw = call["function"]["arguments"]
        try:
            args = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return "Error: the arguments were not valid JSON."
        if not isinstance(args, dict):
            return "Error: the arguments must be a JSON object."

        self.ui.tool_log(name, args)
        try:
            result = self._dispatch(name, args)
        except tools.ToolError as error:
            result = f"Error: {error}"
        except TypeError as error:
            result = f"Error: invalid arguments for {name}: {error}"
        except Exception as error:  # a bug in a tool must not crash the session
            result = f"Error: {type(error).__name__}: {error}"
        if result.startswith("Error:"):
            self.ui.tool_error(result)
        return result

    def _dispatch(self, name: str, args: dict) -> str:
        allowed = tools.ALLOWED_ARGS.get(name)
        if allowed is None:
            raise tools.ToolError(f"Unknown tool: {name}")
        kwargs = {key: value for key, value in args.items() if key in allowed}

        if name in tools.READ_ONLY:
            return getattr(self.ws, name)(**kwargs)

        if name in tools.COMMAND_TOOLS:
            command = kwargs.get("command")
            if not isinstance(command, str) or not command.strip():
                raise tools.ToolError("The command is empty.")
            if self.allow_commands or safety.is_read_only(command):
                self.ui.show_command(command, auto=not self.allow_commands)
            else:
                decision, note = self.ui.confirm_command(command, str(self.ws.root))
                if decision != "yes":
                    return "The user declined to run this command." + (f" Their note: {note}" if note else "")
            self.commands_run += 1
            return self.ws.run_command(**kwargs)

        prepare = {"write_file": self.ws.prepare_write,
                   "edit_file": self.ws.prepare_edit,
                   "delete_file": self.ws.prepare_delete}[name]
        change = prepare(**kwargs)
        diff = self.ws.diff(change)

        if self.auto_approve:
            decision, note = "yes", ""
            self.ui.show_change(change, diff)
        else:
            decision, note = self.ui.confirm_change(change, diff)
        if decision == "always":
            self.auto_approve = True
            decision = "yes"
        if decision != "yes":
            return "The user declined this change." + (f" Their note: {note}" if note else "")

        self.ws.apply(change)
        self.changes_applied += 1
        self.ui.info(f"Applied: {change.summary}")
        return f"OK: {change.summary}"
