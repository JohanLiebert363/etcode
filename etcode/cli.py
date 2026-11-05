"""Command line entry point: `etcode` and `etcode keys`."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from rich.markup import escape
from rich.prompt import Prompt

from . import __version__, ai, config
from .agent import Agent
from .attach import expand_mentions
from .tools import ToolError, Workspace
from .ui import ACCENT, HELP_TEXT, TerminalUI

COMMANDS = {"/help", "/exit", "/quit", "/clear", "/provider", "/auto", "/undo", "/status",
            "/cd", "/ls", "/run"}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="etcode", description="Etcode: an AI coding assistant for your terminal.")
    parser.add_argument("--version", action="version", version=f"etcode {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="[command]")
    keys = commands.add_parser("keys", help="open the page where you save your API keys")
    keys.add_argument("--port", type=int, default=5000)
    keys.add_argument("--no-browser", action="store_true", help="do not open the browser automatically")
    parser.add_argument("-w", "--workspace", default=".", help="folder the AI may read and change (default: current folder)")
    parser.add_argument("-p", "--provider", choices=list(config.PROVIDERS), help="skip the provider question")
    parser.add_argument("--auto", action="store_true", help="apply file changes without asking (use /undo to revert)")
    parser.add_argument("--allow-commands", action="store_true",
                        help="let the AI run shell commands without asking (risky; commands are not sandboxed)")
    return parser.parse_args(argv)


def explain_error(error: BaseException, provider: str) -> str:
    """Turn a long provider error into something a person can act on."""
    text = str(error)
    lowered = text.lower()
    if ai.is_rate_limit(error):
        label = config.PROVIDERS.get(provider, {}).get("label", provider)
        lines = [f"{label} refused the request: rate limit or quota reached."]
        wait = ai.suggested_wait(error)
        if "perday" in lowered.replace(" ", "") or "insufficient_quota" in lowered:
            lines.append("This looks like a daily limit or an unpaid quota, so waiting a few minutes will not help.")
        elif wait:
            lines.append(f"It asked for a wait of about {wait:.0f} seconds.")
        lines.append("Etcode makes one request per step, and free plans allow only a few per minute "
                     "(Gemini's free tier allows 5).")
        lines.append("Options: wait a minute and press the up arrow to resend; set ETCODE_REQUESTS_PER_MINUTE=4 "
                     "so Etcode paces itself; switch AI with /provider; or turn on billing with the provider.")
        return "\n".join(lines)
    if (type(error).__name__ == "AuthenticationError" or "401" in text
            or "invalid api key" in lowered or "api key not valid" in lowered):
        return "The provider rejected your API key. Run `etcode keys` and paste it again."
    if len(text) > 800:
        text = text[:800] + "..."
    return f"{type(error).__name__}: {text}"


def _fence(text: str) -> str:
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def run_user_command(command: str, agent: Agent, ui: TerminalUI, pending: list) -> None:
    """You typed !command: run it now and send its output with your next message."""
    if not command:
        ui.warn("Usage: !COMMAND  (for example: !git status)")
        return
    try:
        with ui.status("Running..."):
            result = agent.ws.run_command(command, timeout_seconds=120)
    except ToolError as error:
        ui.warn(str(error))
        return
    ui.show_command_output(command, result)
    fence = _fence(result)
    pending.append(f"[Output of a command I ran: {command}]\n{fence}\n{result}\n{fence}")
    ui.info("This output will be sent with your next message.")


def handle_command(text: str, agent: Agent, ui: TerminalUI, pending: list) -> bool:
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
        pending.clear()
        ui.info("Conversation cleared.")
    elif command == "/provider":
        if argument not in config.PROVIDERS:
            ui.warn("Usage: /provider openai | gemini | claude")
        elif argument not in config.configured_providers():
            ui.warn(f"No API key for {argument}. Run `etcode keys` in another terminal to add one.")
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
        ui.info("Auto-approve is " + ("ON: file changes apply without asking (commands still ask)."
                                      if agent.auto_approve else "OFF: you approve each change."))
    elif command == "/undo":
        try:
            ui.info(agent.ws.undo())
        except ToolError as error:
            ui.warn(str(error))
    elif command == "/cd":
        if not argument:
            ui.info(f"Workspace: {agent.ws.root}")
        else:
            target = Path(argument.strip("\"'")).expanduser()
            if not target.is_absolute():
                target = agent.ws.root / target
            try:
                new_ws = Workspace(target)
            except ToolError as error:
                ui.warn(str(error))
            else:
                agent.ws = new_ws
                ui.info(f"Workspace is now {new_ws.root}. Undo history from the old folder is no longer available.")
    elif command == "/ls":
        try:
            ui.console.print(escape(agent.ws.list_files(argument or ".", max_depth=1)))
        except ToolError as error:
            ui.warn(str(error))
    elif command == "/run":
        run_user_command(argument, agent, ui, pending)
    elif command == "/status":
        ui.console.print(
            f"[dim]Provider: {agent.provider} ({escape(config.model_for(agent.provider))})\n"
            f"Workspace: {escape(str(agent.ws.root))}\n"
            f"Messages: {len(agent.messages)}, about {agent.estimated_tokens():,} tokens "
            f"(limit {config.MAX_CONTEXT_TOKENS:,})\n"
            f"Files changed: {agent.changes_applied}, commands run by the AI: {agent.commands_run}\n"
            f"Auto-approve files: {'on' if agent.auto_approve else 'off'}, "
            f"AI commands without asking: {'on' if agent.allow_commands else 'off'}[/dim]"
        )
    return False


def run_once(agent: Agent, ui: TerminalUI, text: str, pending: list | None = None) -> None:
    ui.show_received(text)
    text, attached, notes = expand_mentions(text, agent.ws)
    ui.show_attachments(attached, notes)
    if pending:
        text = text + "\n\n" + "\n\n".join(pending)
    estimate = ai.estimate_tokens([{"content": text}])
    if estimate > config.MAX_CONTEXT_TOKENS:
        ui.warn(f"This input is about {estimate:,} tokens, more than the {config.MAX_CONTEXT_TOKENS:,} "
                "limit. The provider may reject it.")
    agent.run_turn(text)
    if pending:
        pending.clear()


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.command == "keys":
        from .web import serve
        serve(port=args.port, open_browser=not args.no_browser)
        return 0
    interactive = sys.stdin.isatty()
    ui = TerminalUI(interactive)

    try:
        ws = Workspace(args.workspace)
    except ToolError as error:
        ui.error(error)
        return 2

    configured = config.configured_providers()
    if not configured:
        ui.warn("No API keys found. Run `etcode keys` to add one, "
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
    agent = Agent(provider, ws, ui, auto_approve=args.auto, allow_commands=args.allow_commands)
    pending: list[str] = []

    # Piped input: `cat big.txt | etcode`
    if not interactive:
        text = sys.stdin.read()
        if not text.strip():
            ui.warn("No input received.")
            return 1
        try:
            run_once(agent, ui, text)
        except Exception as error:
            ui.error(error, explain_error(error, provider))
            return 1
        return 0

    ui.console.print(f"\n[bold {ACCENT}]Using:[/] {provider} ({escape(config.model_for(provider))})")
    ui.info("Enter sends. Alt+Enter adds a line. @file attaches a file. !command runs a command. /help lists commands.")

    def toolbar() -> str:
        auto = "auto-approve ON" if agent.auto_approve else "changes need approval"
        return f" Enter send | Alt+Enter new line | @file | !cmd | /help | {agent.provider}, {auto}"

    while True:
        try:
            text = ui.read_input(toolbar).strip()
        except (EOFError, KeyboardInterrupt):
            ui.console.print(f"\n[bold {ACCENT}]Goodbye![/]")
            return 0
        if not text:
            continue
        if text.startswith("!") and "\n" not in text:
            run_user_command(text[1:].strip(), agent, ui, pending)
            continue
        first_word = text.split(None, 1)[0].lower()
        if first_word in COMMANDS and "\n" not in text:
            if handle_command(text, agent, ui, pending):
                return 0
            continue
        changes_before = agent.changes_applied
        try:
            run_once(agent, ui, text, pending)
        except KeyboardInterrupt:
            ui.warn("Cancelled.")
        except Exception as error:
            ui.error(error, explain_error(error, agent.provider))
            if agent.changes_applied > changes_before:
                ui.info("Some file changes were applied before the error. Use /undo to revert them.")
            else:
                ui.info("Your message was not sent. Press the up arrow to bring it back.")


def run() -> None:
    """Console-script entry point."""
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nEtcode terminated.")
        sys.exit(130)
