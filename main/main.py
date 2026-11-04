"""Etcode: an AI coding assistant for your terminal."""
from __future__ import annotations

import argparse
import sys

from rich.markup import escape
from rich.prompt import Prompt

import ai
import config
from agent import Agent
from attach import expand_mentions
from tools import ToolError, Workspace
from ui import ACCENT, HELP_TEXT, TerminalUI

COMMANDS = {"/help", "/exit", "/quit", "/clear", "/provider", "/auto", "/undo", "/status"}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Etcode: an AI coding assistant for your terminal.")
    parser.add_argument("-w", "--workspace", default=".", help="folder the AI may read and change (default: current folder)")
    parser.add_argument("-p", "--provider", choices=list(config.PROVIDERS), help="skip the provider question")
    parser.add_argument("--auto", action="store_true", help="apply file changes without asking (use /undo to revert)")
    return parser.parse_args(argv)


def handle_command(text: str, agent: Agent, ui: TerminalUI) -> bool:
    """Run a slash command. Returns True when the program should exit."""
    command, _, argument = text.partition(" ")
    command, argument = command.lower(), argument.strip()

    if command in ("/exit", "/quit"):
        ui.console.print(f"[bold {ACCENT}]Goodbye![/]")
        return True
    if command == "/help":
        ui.console.print(HELP_TEXT)
    elif command == "/clear":
        agent.clear()
        ui.info("Conversation cleared.")
    elif command == "/provider":
        if argument not in config.PROVIDERS:
            ui.warn("Usage: /provider openai | gemini | claude")
        elif argument not in config.configured_providers():
            ui.warn(f"No API key for {argument}. Add one on the setup page first.")
        else:
            agent.provider = argument
            ui.info(f"Now using {argument} ({config.model_for(argument)}).")
    elif command == "/auto":
        if argument in ("on", "off"):
            agent.auto_approve = argument == "on"
        elif argument == "":
            agent.auto_approve = not agent.auto_approve
        else:
            ui.warn("Usage: /auto on | off")
            return False
        ui.info("Auto-approve is " + ("ON: changes apply without asking." if agent.auto_approve else "OFF: you approve each change."))
    elif command == "/undo":
        try:
            ui.info(agent.ws.undo())
        except ToolError as error:
            ui.warn(str(error))
    elif command == "/status":
        ui.console.print(
            f"[dim]Provider: {agent.provider} ({escape(config.model_for(agent.provider))})\n"
            f"Workspace: {escape(str(agent.ws.root))}\n"
            f"Messages: {len(agent.messages)}, about {agent.estimated_tokens():,} tokens "
            f"(limit {config.MAX_CONTEXT_TOKENS:,})\n"
            f"Files changed: {agent.changes_applied}, auto-approve: {'on' if agent.auto_approve else 'off'}[/dim]"
        )
    return False


def run_once(agent: Agent, ui: TerminalUI, ws: Workspace, text: str) -> None:
    ui.show_received(text)
    text, attached, notes = expand_mentions(text, ws)
    ui.show_attachments(attached, notes)
    estimate = ai.estimate_tokens([{"content": text}])
    if estimate > config.MAX_CONTEXT_TOKENS:
        ui.warn(f"This input is about {estimate:,} tokens, more than the {config.MAX_CONTEXT_TOKENS:,} "
                "limit. The provider may reject it.")
    agent.run_turn(text)


def main(argv=None) -> int:
    args = parse_args(argv)
    interactive = sys.stdin.isatty()
    ui = TerminalUI(interactive)

    try:
        ws = Workspace(args.workspace)
    except ToolError as error:
        ui.error(error)
        return 2

    configured = config.configured_providers()
    if not configured:
        ui.warn("No API keys found. Run `python api-get/app.py` and add a key, "
                "or set OPENAI_API_KEY, GEMINI_API_KEY or ANTHROPIC_API_KEY.")
        return 1
    if args.provider and args.provider not in configured:
        ui.warn(f"No API key for {args.provider}.")
        return 1

    if interactive:
        ui.banner(ws.root)
    provider = args.provider
    if not provider:
        if len(configured) == 1 or not interactive:
            provider = configured[0]
        else:
            provider = Prompt.ask(f"[bold {ACCENT}]Select provider[/]", choices=configured,
                                  default=configured[0], console=ui.console)
    agent = Agent(provider, ws, ui, auto_approve=args.auto)

    # Piped input: `cat big.txt | python main.py`
    if not interactive:
        text = sys.stdin.read()
        if not text.strip():
            ui.warn("No input received.")
            return 1
        try:
            run_once(agent, ui, ws, text)
        except Exception as error:
            ui.error(error)
            return 1
        return 0

    ui.console.print(f"\n[bold {ACCENT}]Using:[/] {provider} ({escape(config.model_for(provider))})")
    ui.info("Enter sends. Alt+Enter adds a line. Attach files with @path. Type /help for commands.")

    def toolbar() -> str:
        auto = "auto-approve ON" if agent.auto_approve else "changes need approval"
        return f" Enter send | Alt+Enter new line | @file attach | /help | {agent.provider}, {auto}"

    while True:
        try:
            text = ui.read_input(toolbar).strip()
        except (EOFError, KeyboardInterrupt):
            ui.console.print(f"\n[bold {ACCENT}]Goodbye![/]")
            return 0
        if not text:
            continue
        first_word = text.split(None, 1)[0].lower()
        if first_word in COMMANDS and "\n" not in text:
            if handle_command(text, agent, ui):
                return 0
            continue
        try:
            run_once(agent, ui, ws, text)
        except KeyboardInterrupt:
            ui.warn("Cancelled.")
        except Exception as error:
            ui.error(error)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nEtcode terminated.")
        sys.exit(130)
