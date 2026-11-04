
from rich.console import Console
from rich.rule import Rule

console = Console()

def prompt():
    console.print("\nPrompt", style="bold dark_orange")
    console.print(Rule(style="dark_orange"))

    user_prompt = console.input("[bold dark_orange]> [/bold dark_orange]")

    console.print(Rule(style="dark_orange"))

    return user_prompt
