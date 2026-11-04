import json
from pathlib import Path

file = Path(__file__).parent.parent / "api-get" / "api.json"

with open(file, "r", encoding="utf-8") as f:
    data = json.load(f)

api1 = data.get("api1")
api2 = data.get("api2")
api3 = data.get("api3")