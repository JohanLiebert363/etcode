
from rich.console import Console
from rich.rule import Rule
from rich.prompt import Prompt
from rich.text import Text

console = Console()

class CleanPrompt(Prompt):
    def make_prompt(self, console: Console) -> Text:
        # Get the standard prompt Text object from Rich
        prompt = self.render_default(self.prompt)
        # Return it directly without adding a trailing colon
        return prompt

etcode_ascii = """
███████╗████████╗ ██████╗  ██████╗ ██████╗ ███████╗
██╔════╝╚══██╔══╝██╔════╝ ██╔═══██╗██╔══██╗██╔════╝
█████╗     ██║   ██║      ██║   ██║██║  ██║█████╗  
██╔══╝     ██║   ██║      ██║   ██║██║  ██║██╔══╝  
███████╗   ██║   ╚██████╗ ╚██████╔╝██████╔╝███████╗
╚══════╝   ╚═╝    ╚═════╝  ╚═════╝ ╚═════╝ ╚══════╝
"""

def main(etcode_ascii):
    model = "No model available"
    console.print(etcode_ascii, style="bold dark_orange")
    console.print("\n[bold dark_orange]currently using:[/bold dark_orange]", model, style="dark_orange")
    console.print("\nPrompt:", style="bold dark_orange")
    console.print(Rule(style="dark_orange"))
    user_prompt = CleanPrompt.ask("[bold dark_orange]>[/bold dark_orange]")
    console.print(Rule(style="dark_orange"))
if __name__  == "__main__":
    main(etcode_ascii)
