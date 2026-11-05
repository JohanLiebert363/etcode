"""Everything the user sees and types in the terminal."""
from __future__ import annotations

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.syntax import Syntax
from rich.text import Text

ACCENT = "dark_orange"
MAX_DIFF_LINES = 150
COLLAPSE_PASTE_LINES = 25      # pastes longer than this show as a short label
COLLAPSE_PASTE_CHARS = 2_000
BOLD = "bold dark_orange"

ETCODE_ASCII = """
███████╗████████╗ ██████╗  ██████╗ ██████╗ ███████╗
██╔════╝╚══██╔══╝██╔════╝ ██╔═══██╗██╔══██╗██╔════╝
█████╗     ██║   ██║      ██║   ██║██║  ██║█████╗
██╔══╝     ██║   ██║      ██║   ██║██║  ██║██╔══╝
███████╗   ██║   ╚██████╗ ╚██████╔╝██████╔╝███████╗
╚══════╝   ╚═╝    ╚═════╝  ╚═════╝ ╚══════╝
"""

HELP_TEXT = """\
[bold]Typing[/bold]
  Enter            send your message
  Alt+Enter        new line (Ctrl+J also works)
  Paste            any amount of text is fine and nothing is sent until you press Enter.
                   Big pastes show as [Pasted text #1: N lines]; the full text is sent.
  @path/to/file    attach a file's contents to your message
  cat big.txt | etcode      send a very large input without the prompt

[bold]Commands[/bold]
  /help            show this help
  /clear           forget the conversation
  /provider NAME   switch AI (openai, gemini, claude)
  /auto [on|off]   apply file changes without asking (commands still ask)
  /undo            undo the last file change
  /cd FOLDER       change the folder the AI works in
  /ls [FOLDER]     list files
  !COMMAND         run a shell command yourself; its output goes with your next message
                   (/run COMMAND does the same)
  /status          show provider, model, workspace and context size
  /exit            quit"""


class TerminalUI:
    def __init__(self, interactive: bool = True):
        self.console = Console()
        self.interactive = interactive
        self._session = None
        self._pastes: dict[str, str] = {}
        self._paste_count = 0

    # ---- output ------------------------------------------------------------

    def banner(self, workspace) -> None:
        self.console.clear()
        self.console.print(ETCODE_ASCII, style=f"bold {ACCENT}", markup=False)
        self.console.print(f"[bold {ACCENT}]In terminal ai provider[/]")
        self.console.print(f"[dim]Made by Edin Tahiri.[/dim]")
        self.console.print(f"[dim]Workspace: {escape(str(workspace))}[/dim]")
        self.console.print(Rule(style=ACCENT))

    def status(self, text: str):
        return self.console.status(f"[bold {ACCENT}]{escape(text)}[/]", spinner="dots")

    def info(self, text: str) -> None:
        self.console.print(f"[dim]{escape(text)}[/dim]")

    def warn(self, text: str) -> None:
        self.console.print(f"[yellow]{escape(text)}[/yellow]")

    def error(self, error: BaseException, message: str | None = None) -> None:
        self.console.print("\n[bold red]ETCODE ERROR[/bold red]")
        text = message or f"{type(error).__name__}: {error}"
        self.console.print(f"[red]{escape(text)}[/red]")

    def show_answer(self, text: str) -> None:
        self.console.print()
        self.console.print(Rule(f"[bold {ACCENT}]ETCODE[/]", style=ACCENT, align="left"))
        self.console.print(Markdown(text))
        self.console.print(Rule(style=ACCENT))

    def show_note(self, text: str) -> None:
        self.console.print(Markdown(text), style="dim")

    def show_received(self, text: str) -> None:
        lines = text.count("\n") + 1
        if lines > 1 or len(text) > 500:
            self.info(f"Received {lines:,} lines, {len(text):,} characters.")

    def show_attachments(self, attached, notes) -> None:
        for path, chars in attached:
            self.info(f"Attached {path} ({chars:,} characters)")
        for note in notes:
            self.warn(note)

    def tool_log(self, name: str, args: dict) -> None:
        target = args.get("path") or args.get("pattern") or ""
        self.console.print(f"[dim]  > {escape(name)} {escape(str(target))}[/dim]")

    def tool_error(self, text: str) -> None:
        self.console.print(f"[red]    {escape(text)}[/red]")

    # ---- commands ----------------------------------------------------------

    def show_command(self, command: str) -> None:
        self.console.print(f"[dim]  $ {escape(command)}[/dim]")

    def show_command_output(self, command: str, output: str) -> None:
        self.console.print(Panel(Text(output.rstrip() or "(no output)"), title=Text(f"$ {command}"),
                                 title_align="left", border_style="dim"))

    def confirm_command(self, command: str, folder: str):
        """Return ("yes" | "no", note). Commands are never auto-approved by /auto."""
        body = Syntax(command, "bash", theme="ansi_dark", word_wrap=True)
        self.console.print(Panel(body, title="Run command", title_align="left", border_style=ACCENT,
                                 subtitle=escape(f"in {folder}. Commands are not sandboxed."), subtitle_align="right"))
        if not self.interactive:
            self.warn("Not run: no terminal to ask for approval. Use --allow-commands to allow commands.")
            return "no", "Approval was not possible in non-interactive mode."
        answer = Prompt.ask(f"[bold {ACCENT}]Run this command?[/]", choices=["y", "n"], default="n",
                            console=self.console)
        if answer == "y":
            return "yes", ""
        note = Prompt.ask("What should the AI do instead? (Enter to skip)", default="",
                          show_default=False, console=self.console)
        return "no", note.strip()

    # ---- file changes ------------------------------------------------------

    def show_change(self, change, diff: str) -> None:
        title = {"create": "Create file", "edit": "Edit file", "delete": "Delete file"}[change.kind]
        lines = diff.splitlines()
        shown = "\n".join(lines[:MAX_DIFF_LINES])
        if len(lines) > MAX_DIFF_LINES:
            shown += f"\n... {len(lines) - MAX_DIFF_LINES} more diff lines not shown"
        body = Syntax(shown, "diff", theme="ansi_dark", word_wrap=True)
        self.console.print(Panel(body, title=f"{title}: {escape(change.rel)}", title_align="left",
                                 border_style=ACCENT, subtitle=escape(change.summary), subtitle_align="right"))

    def confirm_change(self, change, diff: str):
        """Return ("yes" | "no" | "always", note)."""
        self.show_change(change, diff)
        if not self.interactive:
            self.warn("Not applied: no terminal to ask for approval. Use --auto to allow changes.")
            return "no", "Approval was not possible in non-interactive mode."
        self.console.print("[dim]y = apply   n = skip   a = apply this and all later changes[/dim]")
        answer = Prompt.ask(f"[bold {ACCENT}]Apply this change?[/]", choices=["y", "n", "a"],
                            default="n", console=self.console)
        if answer == "y":
            return "yes", ""
        if answer == "a":
            self.warn("Auto-approve is on for this session. Use /undo to revert changes.")
            return "always", ""
        note = Prompt.ask("What should the AI do instead? (Enter to skip)", default="",
                          show_default=False, console=self.console)
        return "no", note.strip()

    # ---- input -------------------------------------------------------------

    def pick_provider(self, options: list[tuple[str, str]], default: str | None = None) -> str:
        """Arrow-key menu. options is [(value, label), ...]. If the menu cannot run
        (library missing), fall back to typing the name."""
        try:
            from InquirerPy import inquirer
            from InquirerPy.base.control import Choice
            from InquirerPy.utils import get_style
        except ImportError:
            return Prompt.ask(f"[bold {ACCENT}]Select provider[/]", choices=[v for v, _ in options],
                              default=default or options[0][0], console=self.console)

        style = get_style({"questionmark": "#ff8c00", "pointer": "#ff8c00 bold",
                           "answer": "#ff8c00 bold", "message": "#efb679"}, style_override=False)
        return inquirer.select(
            message="Select provider:",
            choices=[Choice(value=value, name=label) for value, label in options],
            default=default,
            pointer=">",
            instruction="(arrow keys, Enter to confirm)",
            style=style,
        ).execute()

    def read_input(self, toolbar=None) -> str:
        """Multi-line prompt. Enter sends, Alt+Enter adds a line. Big pastes are collapsed to a
        label while typing (redrawing thousands of lines freezes the terminal) and expanded on send."""
        if self._session is None:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.keys import Keys
            from prompt_toolkit.key_binding import KeyBindings
            from prompt_toolkit.styles import Style

            keys = KeyBindings()

            @keys.add("enter")
            def _send(event):
                event.current_buffer.validate_and_handle()

            @keys.add("escape", "enter")
            @keys.add("c-j")
            def _newline(event):
                event.current_buffer.insert_text("\n")

            @keys.add(Keys.BracketedPaste)
            def _paste(event):
                data = event.data.replace("\r\n", "\n").replace("\r", "\n")
                lines = data.count("\n") + 1
                if lines > COLLAPSE_PASTE_LINES or len(data) > COLLAPSE_PASTE_CHARS:
                    self._paste_count += 1
                    label = f"[Pasted text #{self._paste_count}: {lines:,} lines]"
                    self._pastes[label] = data
                    event.current_buffer.insert_text(label)
                else:
                    event.current_buffer.insert_text(data)

            self._session = PromptSession(
                message=[("fg:#ff8c00 bold", "> ")],
                multiline=True,
                key_bindings=keys,
                prompt_continuation=lambda width, line, wrap: "  ",
                style=Style.from_dict({"bottom-toolbar": "noreverse fg:#8a7a6a bg:default"}),
                mouse_support=False,
            )
        self.console.print()
        self.console.print(Rule(style=BOLD))
        text = self._session.prompt(bottom_toolbar=toolbar)
        for label, data in self._pastes.items():
            text = text.replace(label, data)
        return text
