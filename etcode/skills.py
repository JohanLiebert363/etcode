"""Skills: reusable prompts you run with /name.

Built-in skills ship with Etcode. You can add your own as Markdown files:
  <project>/.etcode/skills/NAME.md   only for that project (can be committed)
  <your user folder>/etcode/skills/NAME.md   for every project
A file may start with a header:
  ---
  description: Short text shown in /skills
  ---
and then the prompt. $ARGS is replaced by whatever you type after the name."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import config

NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,29}")
MAX_SKILL_CHARS = 20_000

BUILTIN = {
    "review": ("Review my uncommitted changes",
               "Run `git diff` and `git diff --staged` to see my uncommitted changes, then review them. "
               "Look for bugs, risky changes, missing error handling, missing tests and unclear names. "
               "Be specific: give the file and what to change. Do not edit any files. $ARGS"),
    "explain": ("Explain the project, a file, or a function",
                "Explain $ARGS in plain language. If I gave no target, explain what this project does and "
                "how it is organized: list the files and read the important ones first. Do not edit any files."),
    "fix": ("Find and fix a problem",
            "Find and fix this problem: $ARGS. Read the relevant files first, make the smallest change that "
            "fixes it, and run the project's tests or a quick check if there is one."),
    "test": ("Write or run tests",
             "Look at how this project is tested. Then write or update tests for: $ARGS. If I gave no target, "
             "cover the most recently changed code (check `git diff`). Run the tests and report the result."),
    "commit": ("Draft a commit message for my changes",
               "Run `git status` and `git diff` (and `git diff --staged`), then write a clear commit message: "
               "a summary line under 72 characters and a short body explaining why. Show it to me. "
               "Do not run `git commit` unless I ask. $ARGS"),
    "plan": ("Plan a task step by step without changing anything",
             "Make a step-by-step plan for: $ARGS. Read whatever files you need first. List the files you "
             "would change and why, and the risks. Do not edit any files or run changing commands."),
}


@dataclass
class Skill:
    name: str
    description: str
    prompt: str
    source: str      # "built-in", "global" or "project"


def parse(text: str) -> tuple[str, str]:
    """Split an optional '---' header from the prompt. Returns (description, prompt)."""
    lines = text.lstrip("\ufeff").splitlines()
    if lines and lines[0].strip() == "---":
        for end in range(1, len(lines)):
            if lines[end].strip() == "---":
                description = ""
                for line in lines[1:end]:
                    key, _, value = line.partition(":")
                    if key.strip().lower() == "description":
                        description = value.strip()
                return description, "\n".join(lines[end + 1:]).strip()
    body = text.strip()
    return (body.splitlines()[0][:70] if body else ""), body


def load_all(root: Path, reserved: set[str]) -> dict[str, Skill]:
    """Built-in skills, then global ones, then project ones (later ones win)."""
    skills = {name: Skill(name, desc, prompt, "built-in") for name, (desc, prompt) in BUILTIN.items()}
    for source, folder in (("global", config.config_dir() / "skills"), ("project", root / ".etcode" / "skills")):
        if not folder.is_dir():
            continue
        for file in sorted(folder.glob("*.md")):
            name = file.stem.lower()
            if not NAME_RE.fullmatch(name) or name in reserved:
                continue            # a skill cannot replace a built-in command like /help
            try:
                text = file.read_text(encoding="utf-8")[:MAX_SKILL_CHARS]
            except (OSError, UnicodeDecodeError):
                continue
            description, prompt = parse(text)
            if prompt:
                skills[name] = Skill(name, description or "(no description)", prompt, source)
    return skills


def render(skill: Skill, args: str) -> str:
    args = args.strip()
    if "$ARGS" in skill.prompt:
        return skill.prompt.replace("$ARGS", args or "(nothing specific)").strip()
    return skill.prompt + (f"\n\nAdditional details from me: {args}" if args else "")


TEMPLATE = """---
description: Say what this skill does in one line
---
Write the instructions for the AI here. Use $ARGS where the text you type after the
skill name should go. Example: /{name} the login page
"""


def new_skill(root: Path, name: str, reserved: set[str]) -> Path:
    name = name.strip().lower()
    if not NAME_RE.fullmatch(name):
        raise ValueError("Use 1 to 30 lowercase letters, numbers, dashes or underscores for the name.")
    if name in reserved:
        raise ValueError(f"/{name} is already a built-in command.")
    path = root / ".etcode" / "skills" / f"{name}.md"
    if path.exists():
        raise ValueError(f"{path} already exists.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TEMPLATE.format(name=name), encoding="utf-8")
    return path
