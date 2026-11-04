
import json
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode

api_file = Path(__file__).parent.parent / "api-get" / "api.json"

with open(api_file, "r", encoding="utf-8") as f:
    keys = json.load(f)

api_key = keys.get("gemini")

if not api_key:
    raise ValueError("Gemini API key not found")

url = "https://generativelanguage.googleapis.com/v1beta/models"

request_url = url + "?" + urlencode({"key": api_key})
request = Request(request_url)

try:
    with urlopen(request, timeout=30) as response:
        data = json.loads(response.read())

    print("\nAvailable Gemini models:\n")

    for model in data.get("models", []):
        methods = model.get("supportedGenerationMethods", [])

        if "generateContent" in methods:
            print(model["name"])

except Exception as e:
    print(f"\nError: {type(e).__name__}: {e}")
