
from prompt import prompt
from ai import ask_ai

from rich.console import Console
from rich.rule import Rule
from rich.prompt import Prompt

console = Console()

etcode_ascii = """
███████╗████████╗ ██████╗  ██████╗ ██████╗ ███████╗
██╔════╝╚══██╔══╝██╔════╝ ██╔═══██╗██╔══██╗██╔════╝
█████╗     ██║   ██║      ██║   ██║██║  ██║█████╗
██╔══╝     ██║   ██║      ██║   ██║██║  ██║██╔══╝
███████╗   ██║   ╚██████╗ ╚██████╔╝██████╔╝███████╗
╚══════╝   ╚═╝    ╚═════╝  ╚═════╝ ╚══════╝
"""

def main():

    console.clear()
    console.print(etcode_ascii, style="bold dark_orange")
    console.print("[bold dark_orange]ETCODE AI TERMINAL[/bold dark_orange]")
    console.print(Rule(style="dark_orange"))

    model = Prompt.ask(
        "[bold dark_orange]Select provider[/bold dark_orange]",
        choices=["openai", "gemini", "claude"],
        default="openai"
    )

    console.print(
        f"\n[bold dark_orange]Currently using:[/bold dark_orange] {model}"
    )

    console.print("[dim]Type /exit to quit or /clear to reset history.[/dim]")

    messages = []

    while True:

        user_prompt = prompt()

        if not user_prompt.strip():
            continue

        if user_prompt.lower() == "/exit":
            console.print("[bold dark_orange]Goodbye![/bold dark_orange]")
            break

        if user_prompt.lower() == "/clear":
            messages.clear()
            console.print("[dim]Conversation history cleared.[/dim]")
            continue

        messages.append({
            "role": "user",
            "content": user_prompt
        })

        try:

            with console.status("[bold dark_orange]Thinking...[/bold dark_orange]", spinner="dots"):
                answer = ask_ai(model, messages)

            messages.append({
                "role": "assistant",
                "content": answer
            })


            console.print("\n[bold dark_orange]AI RESPONSE[/bold dark_orange]")
            console.print(Rule(style="dark_orange"))
            console.print(answer)
            console.print(Rule(style="dark_orange"))

        except Exception as e:
            if messages and messages[-1]["role"] == "user":
                messages.pop()

            console.print("\n[bold red]ETCODE ERROR[/bold red]")
            console.print(f"[red]{type(e).__name__}: {e}[/red]")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[bold dark_orange]Etcode terminated.[/bold dark_orange]")
