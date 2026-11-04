
import json
import time
from pathlib import Path
from litellm import completion

# Load API keys
api_file = Path(__file__).parent.parent / "api-get" / "api.json"

with open(api_file, "r", encoding="utf-8") as f:
    api_keys = json.load(f)


def ask_ai(provider, messages):
    providers = {
        "openai": ("openai", "gpt-4o"),
        "gemini": ("gemini", "gemini-3.5-flash"),
        "claude": ("anthropic", "claude-sonnet-4-5")
    }

    if provider not in providers:
        raise ValueError("Unknown provider")

    litellm_provider, model = providers[provider]
    api_key = api_keys.get(provider)

    if not api_key:
        raise ValueError(f"Missing API key for {provider}")

    for attempt in range(3):
        try:
            response = completion(
                model=f"{litellm_provider}/{model}",
                api_key=api_key,
                messages=messages,
                timeout=30,
                request_timeout=30,
                num_retries=0
            )

            return response.choices[0].message.content

        except Exception as e:
            error = str(e)

            # Retry temporary Gemini server errors
            if "503" in error and attempt < 2:
                time.sleep(2 ** (attempt + 1))
                continue

            raise
