
from flask import Flask, render_template, request
import json
import os

file = "api.json"

def post_api(api_name, file, api):
    if not os.path.exists(file):
        with open(file, "w", encoding="utf-8") as f:
            json.dump({}, f)

    with open(file, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            data = {}

    data[api_name] = api

    with open(file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

    print(f"{api_name} successfully saved.")


app = Flask(__name__)

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":

        providers = ["openai", "gemini", "claude"]

        for provider in providers:
            api = request.form.get(provider, "").strip()

            if api:
                post_api(provider, file, api)

    return render_template("index.html")


if __name__ == "__main__":
    app.run(debug=True)
