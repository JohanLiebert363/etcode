"""Saving and restoring conversations."""
from __future__ import annotations

import json
import os
import re
import tempfile
import time

from . import config

VERSION = 1


class SessionError(Exception):
    pass


def _path(name: str):
    if not re.fullmatch(r"[\w.-]{1,60}", name or ""):
        raise SessionError("Use 1 to 60 letters, numbers, dots, dashes or underscores for the name.")
    return config.sessions_dir() / f"{name}.json"


def save(name: str, agent) -> None:
    """Write the conversation to disk, readable only by you. It can contain anything you pasted."""
    path = _path(name)
    data = {"version": VERSION, "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "workspace": str(agent.ws.root), "provider": agent.provider,
            "messages": agent.messages, "usage": agent.usage}
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False)
    try:
        with handle:
            json.dump(data, handle)
        try:
            os.chmod(handle.name, 0o600)
        except OSError:
            pass
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def load(name: str) -> dict:
    path = _path(name)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SessionError(f"No saved session called {name}.")
    except (OSError, json.JSONDecodeError):
        raise SessionError(f"The saved session {name} is damaged.")
    messages = data.get("messages") if isinstance(data, dict) else None
    if (not isinstance(messages, list) or not messages
            or not all(isinstance(m, dict) and m.get("role") in ("user", "assistant", "tool") for m in messages)):
        raise SessionError(f"The saved session {name} is damaged.")
    return data


def restore(agent, data: dict) -> None:
    agent.messages = data["messages"]
    usage = data.get("usage")
    if isinstance(usage, dict):
        agent.usage.update({k: usage[k] for k in agent.usage if k in usage})


def delete(name: str) -> None:
    try:
        _path(name).unlink()
    except (OSError, SessionError):
        pass


def list_sessions() -> list[tuple[str, str, int]]:
    """[(name, saved_at, message_count)], newest first."""
    found = []
    folder = config.sessions_dir()
    if folder.is_dir():
        for file in folder.glob("*.json"):
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
                found.append((file.stem, str(data.get("saved_at", "")), len(data.get("messages", []))))
            except (OSError, json.JSONDecodeError, AttributeError):
                continue
    return sorted(found, key=lambda item: item[1], reverse=True)
