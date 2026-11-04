"""Everything the user sees and types in the terminal."""
from __future__ import annotations

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.syntax import Syntax

ACCENT = "dark_orange"
MAX_DIFF_LINES = 150
COLLAPSE_PASTE_LINES = 25      # pastes longer than this show as a short label
COLLAPSE_PASTE_CHARS = 2_000

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
  cat big.txt | python main.py      send a very large input without the prompt

[bold]Commands[/bold]
  /help            show this help
  /clear           forget the conversation
  /provider NAME   switch AI (openai, gemini, claude)
  /auto [on|off]   apply file changes without asking
  /undo            undo the last file change
  /status          show provider, model, workspace and context size
  /exit            quit"""


class TerminalUI:
    def __init__(self, interactive: bool = True):
        self.console = Console()
        self.interactive = interactive
        self._session = None
        self._pastes: dict[str, str] = {}



    def banner(self, workspace) -> None:
        self.console.clear()
        self.console.print(ETCODE_ASCII, style=f"bold {ACCENT}", markup=False)
        self.console.print(f"[bold {ACCENT}]ETCODE AI TERMINAL[/]")
        self.console.print(f"[dim]Workspace: {escape(str(workspace))}[/dim]")
        self.console.print(Rule(style=ACCENT))

    def status(self, text: str):
        return self.console.status(f"[bold {ACCENT}]{escape(text)}[/]", spinner="dots")

    def info(self, text: str) -> None:
        self.console.print(f"[dim]{escape(text)}[/dim]")

    def warn(self, text: str) -> None:
        self.console.print(f"[yellow]{escape(text)}[/yellow]")

    def error(self, error: BaseException) -> None:
        self.console.print(f"\n[bold red]ETCODE ERROR[/bold red]")
        self.console.print(f"[red]{escape(type(error).__name__)}: {escape(str(error))}[/red]")

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
                    label = f"[Pasted text #{len(self._pastes) + 1}: {lines:,} lines]"
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
        self._pastes = {}
        self.console.print()
        self.console.print(Rule(style="bold dark_orange"))
        text = self._session.prompt(bottom_toolbar=toolbar)
        for label, data in self._pastes.items():
            text = text.replace(label, data)
        return text
