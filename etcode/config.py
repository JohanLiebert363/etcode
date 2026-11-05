"""Settings and API keys for the Etcode terminal app."""
from __future__ import annotations

import json
import os
from pathlib import Path

def config_dir() -> Path:
    """Per-user folder for settings: %APPDATA%\\etcode on Windows, ~/.config/etcode elsewhere."""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "etcode"


KEY_FILE = Path(os.environ.get("ETCODE_KEY_FILE") or config_dir() / "api.json")

# Model names can be changed without editing code, for example:
#   ETCODE_MODEL_CLAUDE=anthropic/claude-opus-5-5
PROVIDERS = {
    "openai": {"label": "OpenAI", "env": "OPENAI_API_KEY", "model": "openai/gpt-4o"},
    "gemini": {"label": "Google Gemini", "env": "GEMINI_API_KEY", "model": "gemini/gemini-3.5-flash"},
    "claude": {"label": "Anthropic Claude", "env": "ANTHROPIC_API_KEY", "model": "anthropic/claude-sonnet-5-5"},
}


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


REQUEST_TIMEOUT = _int_env("ETCODE_TIMEOUT", 120)            # seconds per request
MAX_CONTEXT_TOKENS = _int_env("ETCODE_MAX_CONTEXT_TOKENS", 120_000)  # history budget
MAX_STEPS = _int_env("ETCODE_MAX_STEPS", 25)                  # tool calls per turn


def model_for(provider: str) -> str:
    return os.environ.get(f"ETCODE_MODEL_{provider.upper()}") or PROVIDERS[provider]["model"]


def load_key_file() -> dict:
    """Read the key file. A missing or damaged file counts as empty."""
    try:
        data = json.loads(KEY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def get_key(provider: str) -> str:
    """Environment variables win over the key file. Read on every call, so keys
    saved in the web page work without restarting the terminal app."""
    env_value = os.environ.get(PROVIDERS[provider]["env"], "").strip()
    if env_value:
        return env_value
    value = load_key_file().get(provider, "")
    return value.strip() if isinstance(value, str) else ""


def configured_providers() -> list[str]:
    return [name for name in PROVIDERS if get_key(name)]
