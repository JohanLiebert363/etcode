"""File tools the AI can call. Everything stays inside one workspace folder."""
from __future__ import annotations

import difflib
import fnmatch
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

IGNORED_DIRS = {".git", ".etcode", "node_modules", "__pycache__", ".venv", "venv", "dist",
                "build", ".idea", ".vscode", ".mypy_cache", ".pytest_cache"}
BLOCKED_DIRS = {".git", ".etcode"}
SENSITIVE_PATTERNS = (".env", ".env.*", "*.pem", "*.key", "*.pfx", "id_rsa*", "id_ed25519*",
                      ".netrc", "api.json", "credentials.json")
SAFE_SUFFIXES = (".example", ".sample", ".template")

MAX_FILE_BYTES = 2_000_000
MAX_OUTPUT_CHARS = 50_000
MAX_LIST = 400
MAX_SEARCH = 100
MAX_COMMAND_OUTPUT = 20_000


class ToolError(Exception):
    """A problem the AI can read and fix (bad path, text not found, and so on)."""


def is_sensitive(name: str) -> bool:
    lowered = name.lower()
    if lowered.endswith(SAFE_SUFFIXES):
        return False
    return any(fnmatch.fnmatch(lowered, pattern) for pattern in SENSITIVE_PATTERNS)


def _int(value, default, name):
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ToolError(f"{name} must be a whole number.")


def _bool(value, default=False):
    if value is None or value == "":
        return default
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


def _tool(name, description, properties, required):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required}}}


TOOL_SPECS = [
    _tool("list_files",
          "List files and folders in the workspace. Use it first to learn the project layout.",
          {"path": {"type": "string", "description": "Folder to list, relative to the workspace. Default '.'"},
           "max_depth": {"type": "integer", "description": "How many folder levels to show. Default 4."}}, []),
    _tool("read_file",
          "Read a text file with line numbers. Always read a file before you edit it.",
          {"path": {"type": "string"},
           "start_line": {"type": "integer", "description": "First line to show. Default 1."},
           "end_line": {"type": "integer", "description": "Last line to show. Default: end of file."}}, ["path"]),
    _tool("search_files",
          "Search file contents with a regular expression. Returns path:line: text.",
          {"pattern": {"type": "string", "description": "Regular expression."},
           "path": {"type": "string", "description": "Folder or file to search. Default '.'"},
           "ignore_case": {"type": "boolean", "description": "Default true."},
           "file_glob": {"type": "string", "description": "Only search file names matching this, like '*.py'."}},
          ["pattern"]),
    _tool("write_file",
          "Create a new file, or replace a whole file. For small changes to an existing file use "
          "edit_file instead. The user reviews the change before it is applied.",
          {"path": {"type": "string"}, "content": {"type": "string", "description": "The complete new file content."}},
          ["path", "content"]),
    _tool("edit_file",
          "Change part of an existing file by replacing exact text. old_text must match the file "
          "exactly (including spaces and indentation) and appear only once, unless replace_all is true. "
          "The user reviews the change before it is applied.",
          {"path": {"type": "string"},
           "old_text": {"type": "string", "description": "Exact text to replace. Include enough surrounding lines to be unique."},
           "new_text": {"type": "string", "description": "Replacement text. May be empty to delete old_text."},
           "replace_all": {"type": "boolean", "description": "Replace every match. Default false."}},
          ["path", "old_text", "new_text"]),
    _tool("run_command",
          "Run a shell command in the workspace folder and return its output. Use it for tests, builds, "
          "git, and similar. The user approves every command. Each command runs in a fresh shell, so "
          "'cd' does not carry over to the next command.",
          {"command": {"type": "string"},
           "timeout_seconds": {"type": "integer", "description": "Default 60, maximum 600."}}, ["command"]),
    _tool("delete_file",
          "Delete one file. The user must approve it, and it can be undone with a backup.",
          {"path": {"type": "string"}}, ["path"]),
]

ALLOWED_ARGS = {s["function"]["name"]: set(s["function"]["parameters"]["properties"]) for s in TOOL_SPECS}
READ_ONLY = {"list_files", "read_file", "search_files"}
COMMAND_TOOLS = {"run_command"}


def _text(value) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value


def _clip(text: str) -> str:
    if len(text) <= MAX_COMMAND_OUTPUT:
        return text
    half = MAX_COMMAND_OUTPUT // 2
    return f"{text[:half]}\n... {len(text) - MAX_COMMAND_OUTPUT:,} characters cut ...\n{text[-half:]}"


def _format_output(stdout, stderr) -> str:
    out, err = _clip(_text(stdout)).rstrip(), _clip(_text(stderr)).rstrip()
    parts = []
    if out:
        parts.append(f"--- stdout ---\n{out}")
    if err:
        parts.append(f"--- stderr ---\n{err}")
    return "\n".join(parts) if parts else "(no output)"


def diff_stats(old: str | None, new: str | None) -> tuple[int, int]:
    added = removed = 0
    for line in difflib.unified_diff((old or "").splitlines(), (new or "").splitlines(), lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


@dataclass
class Change:
    """A pending or applied file change. Text is stored with \\n line endings."""
    path: Path
    rel: str
    old: str | None   # None: the file did not exist
    new: str | None   # None: the file will be deleted
    crlf: bool = False

    @property
    def kind(self) -> str:
        if self.old is None:
            return "create"
        if self.new is None:
            return "delete"
        return "edit"

    @property
    def summary(self) -> str:
        added, removed = diff_stats(self.old, self.new)
        verb = {"create": "Created", "delete": "Deleted", "edit": "Edited"}[self.kind]
        return f"{verb} {self.rel} (+{added} -{removed})"


class Workspace:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise ToolError(f"Workspace is not a folder: {self.root}")
        self.undo_stack: list[Change] = []
        self._session = time.strftime("%Y%m%d-%H%M%S")
        self._backup_count = 0

    # ---- paths -------------------------------------------------------------

    def rel(self, path: Path) -> str:
        return "." if path == self.root else path.relative_to(self.root).as_posix()

    def resolve(self, path, *, must_exist=False, allow_root=False) -> Path:
        if not isinstance(path, str) or not path.strip():
            raise ToolError("The path is empty.")
        target = (self.root / path.strip()).resolve()
        if not target.is_relative_to(self.root):
            raise ToolError(f"The path is outside the workspace: {path}")
        if target == self.root:
            if not allow_root:
                raise ToolError("That path is the workspace folder itself. Give a file path.")
            return target
        parts = target.relative_to(self.root).parts
        if any(part in BLOCKED_DIRS for part in parts):
            raise ToolError("Access to .git and .etcode folders is blocked.")
        if is_sensitive(target.name):
            raise ToolError(f"{target.name} looks like a secrets file, so it is blocked.")
        if must_exist and not target.exists():
            raise ToolError(f"No such file or folder: {path}")
        return target

    # ---- reading -----------------------------------------------------------

    def load(self, path: Path) -> tuple[str, bool]:
        """Return (text with \\n endings, file used CRLF)."""
        if not path.is_file():
            raise ToolError(f"Not a file: {self.rel(path)}")
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ToolError(f"{self.rel(path)} is too large ({size:,} bytes, limit {MAX_FILE_BYTES:,}).")
        data = path.read_bytes()
        if b"\0" in data[:8192]:
            raise ToolError(f"{self.rel(path)} is a binary file.")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise ToolError(f"{self.rel(path)} is not valid UTF-8 text.")
        crlf = "\r\n" in text
        return (text.replace("\r\n", "\n") if crlf else text), crlf

    def _walk_files(self, base: Path):
        for folder, dirs, files in os.walk(base):
            dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS and not (Path(folder) / d).is_symlink())
            for name in sorted(files):
                full = Path(folder) / name
                if full.is_symlink() or is_sensitive(name):
                    continue
                yield full

    def list_files(self, path=".", max_depth=4) -> str:
        base = self.resolve(path, must_exist=True, allow_root=True)
        if not base.is_dir():
            raise ToolError(f"Not a folder: {path}")
        depth_limit = max(1, _int(max_depth, 4, "max_depth"))
        lines: list[str] = []
        truncated = False

        def walk(folder: Path, depth: int):
            nonlocal truncated
            try:
                entries = sorted(folder.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
            except OSError:
                return
            for entry in entries:
                if len(lines) >= MAX_LIST:
                    truncated = True
                    return
                if entry.is_symlink() or is_sensitive(entry.name):
                    continue
                if entry.is_dir():
                    if entry.name in IGNORED_DIRS:
                        continue
                    lines.append(self.rel(entry) + "/")
                    if depth < depth_limit:
                        walk(entry, depth + 1)
                else:
                    lines.append(f"{self.rel(entry)}  ({entry.stat().st_size:,} bytes)")

        walk(base, 1)
        if not lines:
            return "The folder is empty."
        if truncated:
            lines.append(f"... list cut at {MAX_LIST} entries. List a subfolder to see more.")
        return "\n".join(lines)

    def read_file(self, path, start_line=1, end_line=None) -> str:
        target = self.resolve(path, must_exist=True)
        text, _ = self.load(target)
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        total = len(lines)
        rel = self.rel(target)
        if total == 0:
            return f"{rel} is empty."
        start = max(1, _int(start_line, 1, "start_line"))
        end = total if end_line in (None, "") else min(total, _int(end_line, total, "end_line"))
        if start > total:
            raise ToolError(f"start_line {start} is past the end of the file ({total} lines).")
        if end < start:
            raise ToolError("end_line is before start_line.")
        out, used = [], 0
        for n in range(start, end + 1):
            line = f"{n:>5}| {lines[n - 1]}"
            if used + len(line) + 1 > MAX_OUTPUT_CHARS:
                if not out:
                    out.append(line[:MAX_OUTPUT_CHARS] + "...")
                else:
                    out.append(f"... output cut after line {n - 1}. Call read_file again with start_line={n}.")
                break
            out.append(line)
            used += len(line) + 1
        return f"{rel}: {total} lines\n" + "\n".join(out)

    def search_files(self, pattern, path=".", ignore_case=True, file_glob=None) -> str:
        base = self.resolve(path, must_exist=True, allow_root=True)
        try:
            rx = re.compile(pattern, re.IGNORECASE if _bool(ignore_case, True) else 0)
        except (re.error, TypeError) as error:
            raise ToolError(f"Invalid regular expression: {error}")
        files = [base] if base.is_file() else self._walk_files(base)
        results, truncated = [], False
        for file in files:
            if file_glob and not fnmatch.fnmatch(file.name, file_glob):
                continue
            try:
                text, _ = self.load(file)
            except ToolError:
                continue
            for number, line in enumerate(text.split("\n"), 1):
                if rx.search(line):
                    if len(results) >= MAX_SEARCH:
                        truncated = True
                        break
                    results.append(f"{self.rel(file)}:{number}: {line.strip()[:200]}")
            if truncated:
                break
        if not results:
            return "No matches."
        if truncated:
            results.append(f"... stopped at {MAX_SEARCH} matches. Narrow the pattern or path.")
        return "\n".join(results)

    # ---- running commands --------------------------------------------------

    def run_command(self, command, timeout_seconds=60) -> str:
        """Run a shell command in the workspace. NOT sandboxed: the caller must get approval first."""
        if not isinstance(command, str) or not command.strip():
            raise ToolError("The command is empty.")
        timeout = min(max(_int(timeout_seconds, 60, "timeout_seconds"), 1), 600)
        try:
            done = subprocess.run(command, shell=True, cwd=self.root, stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired as error:
            return (f"The command timed out after {timeout}s and was stopped.\n"
                    f"{_format_output(error.stdout, error.stderr)}")
        except OSError as error:
            raise ToolError(f"Could not run the command: {error}")
        return f"exit code: {done.returncode}\n{_format_output(done.stdout, done.stderr)}"

    # ---- changing files ----------------------------------------------------

    def prepare_write(self, path, content) -> Change:
        if not isinstance(content, str):
            raise ToolError("content must be text.")
        target = self.resolve(path)
        old, crlf = (None, False)
        if target.exists():
            if not target.is_file():
                raise ToolError(f"Not a file: {path}")
            old, crlf = self.load(target)
        new = content.replace("\r\n", "\n")
        if old == new:
            raise ToolError("The file already has exactly this content.")
        return Change(target, self.rel(target), old, new, crlf)

    def prepare_edit(self, path, old_text, new_text, replace_all=False) -> Change:
        if not isinstance(old_text, str) or not isinstance(new_text, str):
            raise ToolError("old_text and new_text must be text.")
        target = self.resolve(path, must_exist=True)
        text, crlf = self.load(target)
        old_text = old_text.replace("\r\n", "\n")
        new_text = new_text.replace("\r\n", "\n")
        if not old_text:
            raise ToolError("old_text is empty. Use write_file to create or replace a whole file.")
        count = text.count(old_text)
        if count == 0:
            raise ToolError("old_text was not found. Read the file again and copy the text exactly, "
                            "including spaces and indentation.")
        replace_all = _bool(replace_all)
        if count > 1 and not replace_all:
            raise ToolError(f"old_text matches {count} places. Add more surrounding lines to make it "
                            "unique, or set replace_all to true.")
        new = text.replace(old_text, new_text) if replace_all else text.replace(old_text, new_text, 1)
        if new == text:
            raise ToolError("That edit would not change the file.")
        return Change(target, self.rel(target), text, new, crlf)

    def prepare_delete(self, path) -> Change:
        target = self.resolve(path, must_exist=True)
        if not target.is_file():
            raise ToolError(f"Not a file: {path}. Only single files can be deleted.")
        old, crlf = self.load(target)
        return Change(target, self.rel(target), old, None, crlf)

    def diff(self, change: Change) -> str:
        old = (change.old or "").splitlines(keepends=True)
        new = (change.new or "").splitlines(keepends=True)
        from_name = "/dev/null" if change.old is None else f"a/{change.rel}"
        to_name = "/dev/null" if change.new is None else f"b/{change.rel}"
        text = "".join(difflib.unified_diff(old, new, from_name, to_name, n=3))
        return text if text.endswith("\n") or not text else text + "\n"

    def apply(self, change: Change) -> None:
        try:
            if change.path.exists():
                self._backup(change.path)
            if change.new is None:
                change.path.unlink()
            else:
                self._write(change.path, change.new, change.crlf)
        except OSError as error:
            raise ToolError(f"Could not write {change.rel}: {error}")
        self.undo_stack.append(change)

    def undo(self) -> str:
        if not self.undo_stack:
            raise ToolError("Nothing to undo.")
        change = self.undo_stack.pop()
        try:
            if change.old is None:
                if change.path.exists():
                    change.path.unlink()
                return f"Undid: removed {change.rel}"
            self._write(change.path, change.old, change.crlf)
        except OSError as error:
            self.undo_stack.append(change)
            raise ToolError(f"Could not undo {change.rel}: {error}")
        return f"Undid: restored {change.rel}"

    def _backup(self, path: Path) -> None:
        self._backup_count += 1
        target = self.root / ".etcode" / "backups" / self._session / str(self._backup_count) / self.rel(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)

    @staticmethod
    def _write(path: Path, text: str, crlf: bool) -> None:
        """Write atomically: a crash cannot leave a half-written file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = (text.replace("\n", "\r\n") if crlf else text).encode("utf-8")
        handle = tempfile.NamedTemporaryFile(dir=path.parent, delete=False)
        try:
            with handle:
                handle.write(data)
            if path.exists():
                shutil.copymode(path, handle.name)
            os.replace(handle.name, path)
        except BaseException:
            try:
                os.unlink(handle.name)
            except OSError:
                pass
            raise
