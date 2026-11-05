"""Turns @path mentions in a prompt into attached file contents."""
from __future__ import annotations

import re

from .tools import ToolError, Workspace

MENTION = re.compile(r"(?<![\w@/.])@([^\s@]+)")
TRAILING = ".,;:!?)]}\"'"
MAX_FILE_CHARS = 200_000
MAX_TOTAL_CHARS = 800_000


def _fence(content: str) -> str:
    longest = max((len(run) for run in re.findall(r"`+", content)), default=0)
    return "`" * max(3, longest + 1)


def expand_mentions(text: str, ws: Workspace):
    """Return (text with file contents appended, [(path, chars)], [notes])."""
    attached, notes, blocks, seen, total = [], [], [], set(), 0
    for match in MENTION.finditer(text):
        token = match.group(1)
        for candidate in dict.fromkeys([token, token.rstrip(TRAILING)]):
            if not candidate:
                continue
            try:
                path = ws.resolve(candidate, must_exist=True)
            except ToolError as error:
                if "No such file" in str(error):
                    continue          # probably not a file at all (an email, a handle)
                notes.append(f"@{candidate}: {error}")
                break
            if not path.is_file():
                continue
            rel = ws.rel(path)
            if rel in seen:
                break
            seen.add(rel)
            try:
                content, _ = ws.load(path)
            except ToolError as error:
                notes.append(f"@{candidate}: {error}")
                break
            if total >= MAX_TOTAL_CHARS:
                notes.append(f"@{candidate}: skipped, too much attached already.")
                break
            if len(content) > MAX_FILE_CHARS:
                content = content[:MAX_FILE_CHARS] + "\n... (file cut here)"
                notes.append(f"@{candidate}: only the first {MAX_FILE_CHARS:,} characters were attached.")
            total += len(content)
            fence = _fence(content)
            blocks.append(f"[Attached file: {rel}]\n{fence}\n{content}\n{fence}")
            attached.append((rel, len(content)))
            break
    if blocks:
        text = text + "\n\n" + "\n\n".join(blocks)
    return text, attached, notes
