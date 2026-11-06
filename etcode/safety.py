"""Decides which shell commands are harmless enough to run without asking.

The rule is deliberately narrow: only a few well-known read-only commands, with plain
arguments. Anything with quotes, pipes, redirects, variables or wildcards still asks."""
from __future__ import annotations

import re

from . import config
from .tools import is_sensitive

_UNSAFE_CHARS = set(";&|<>$`()\n\r%^!{}*?[]~\"'\\")
_GIT_READ_ONLY = {"status", "log", "diff", "show", "ls-files", "rev-parse", "blame", "shortlog", "describe"}
_GIT_BRANCH_FLAGS = {"-a", "-v", "-vv", "-r", "--list", "--show-current"}
_GIT_BAD_FLAGS = ("--output", "--ext-diff", "--textconv", "--open-files-in-pager", "--no-index", "--exec", "-O")
_VERSION_PROGRAMS = {"python", "python3", "py", "node", "npm", "pip", "pip3", "git", "java", "go", "cargo",
                     "rustc", "dotnet", "ruby", "php", "gcc", "clang", "make", "cmake", "docker"}


def _path_ok(arg: str) -> bool:
    """A path argument must stay inside the folder and must not name a secrets file."""
    if arg.startswith("/") or re.match(r"[A-Za-z]:", arg) or ".." in arg:
        return False
    return not any(part and is_sensitive(part) for part in re.split(r"[:/\\]", arg))


def is_read_only(command: str) -> bool:
    if not config.SAFE_COMMANDS or not isinstance(command, str):
        return False
    text = command.strip()
    if not text or len(text) > 300 or any(ch in _UNSAFE_CHARS for ch in text):
        return False
    program, *args = text.split()
    program = program.lower()

    if program in _VERSION_PROGRAMS and args in (["--version"], ["-V"]):
        return True
    if program in ("pwd", "whoami"):
        return not args
    if program in ("ls", "dir"):
        flags = [a for a in args if a.startswith("-")]
        paths = [a for a in args if not a.startswith("-")]
        return all(re.fullmatch(r"-{1,2}[A-Za-z][A-Za-z-]*", f) for f in flags) and all(_path_ok(p) for p in paths)
    if program == "git":
        if not args or args[0].startswith("-"):
            return False
        sub, rest = args[0], args[1:]
        if sub in _GIT_READ_ONLY:
            if any(a.startswith(_GIT_BAD_FLAGS) for a in rest):
                return False
            return all(a.startswith("-") or _path_ok(a) for a in rest)
        if sub == "branch":
            return all(a in _GIT_BRANCH_FLAGS for a in rest)
        if sub == "remote":
            return rest in ([], ["-v"])
        return False
    return False
