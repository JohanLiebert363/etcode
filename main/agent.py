"""The conversation loop: ask the AI, run its file tools, repeat until it answers."""
from __future__ import annotations

import json
import platform

import ai
import config
import tools


class Agent:
    def __init__(self, provider: str, workspace: tools.Workspace, ui, auto_approve: bool = False):
        self.provider = provider
        self.ws = workspace
        self.ui = ui
        self.auto_approve = auto_approve
        self.messages: list[dict] = []
        self.changes_applied = 0

    # ---- helpers -----------------------------------------------------------

    def system_prompt(self) -> dict:
        return {"role": "system", "content": (
            "You are Etcode, an AI coding assistant running in the user's terminal.\n"
            f"Workspace folder: {self.ws.root}\n"
            f"Operating system: {platform.system()}\n\n"
            "You can list, search and read files, and create, edit or delete them with tools. "
            "All paths are relative to the workspace. The user reviews every change before it is applied.\n"
            "Rules:\n"
            "- Read a file before you edit it.\n"
            "- Prefer edit_file with a small, unique old_text over rewriting a whole file.\n"
            "- Make only the changes the user asked for.\n"
            "- If a tool returns an error, fix the arguments and try again.\n"
            "- If the user declined a change, do not propose the same change again. Follow their note.\n"
            "- After changing files, say briefly what changed and why.\n"
            "- Replies are shown as Markdown in a terminal, so keep them short and clear.\n"
            "The user may attach files with @path; their contents appear in the message."
        )}

    def estimated_tokens(self) -> int:
        return ai.estimate_tokens(self.messages)

    def clear(self) -> None:
        self.messages.clear()

    def _trim(self) -> None:
        self.messages, dropped = ai.trim_history(self.messages, config.MAX_CONTEXT_TOKENS)
        if dropped:
            self.ui.warn(f"Dropped {dropped} old messages to stay within the context limit.")

    # ---- one user turn -----------------------------------------------------

    def run_turn(self, user_text: str) -> None:
        user_message = {"role": "user", "content": user_text}
        self.messages.append(user_message)
        applied_before = self.changes_applied
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

    def _loop(self) -> None:
        for _ in range(config.MAX_STEPS):
            self._trim()
            with self.ui.status("Thinking..."):
                reply = ai.chat(self.provider, [self.system_prompt()] + self.messages,
                                tools=tools.TOOL_SPECS, notify=self.ui.warn)

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
                self.ui.show_answer(text or "(The AI sent an empty reply.)")
                return

            if text:
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
