"""Local setup page where you save your AI provider API keys."""
from __future__ import annotations

import json
import os
import secrets
import tempfile
import threading
import webbrowser

from flask import Flask, flash, redirect, render_template, request, session, url_for

from . import config

PROVIDERS = {"openai": "OpenAI", "gemini": "Google Gemini", "claude": "Anthropic Claude"}
MAX_KEY_LENGTH = 512

app = Flask(__name__)
app.secret_key = os.environ.get("ETCODE_SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_HTTPONLY=True, MAX_CONTENT_LENGTH=64 * 1024)


def load_keys() -> dict:
    return config.load_key_file()


def save_keys(updates: dict) -> None:
    """Merge new keys into the file. Atomic write, readable only by you."""
    data = load_keys()
    data.update(updates)
    key_file = config.KEY_FILE
    key_file.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=key_file.parent, delete=False)
    try:
        with handle:
            json.dump(data, handle, indent=4)
        try:
            os.chmod(handle.name, 0o600)
        except OSError:
            pass  # not supported on every system (Windows)
        os.replace(handle.name, key_file)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.after_request
def security_headers(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; script-src 'self' 'unsafe-inline'; "
        "frame-ancestors 'none'; form-action 'self'"
    )
    return response


def back_to_form():
    return redirect(url_for("index", _anchor="setup"))


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        sent = request.form.get("csrf_token", "")
        if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
            flash("Your session expired. Reload the page and try again.", "error")
            return back_to_form()

        updates = {}
        for provider, label in PROVIDERS.items():
            value = request.form.get(provider, "").strip()
            if not value:
                continue
            if len(value) > MAX_KEY_LENGTH or any(ch.isspace() or ord(ch) < 32 for ch in value):
                flash(f"The {label} key looks wrong. Paste it again without spaces or line breaks.", "error")
                return back_to_form()
            updates[provider] = value

        if not updates:
            flash("Enter at least one API key before saving.", "error")
        else:
            try:
                save_keys(updates)
            except OSError:
                flash(f"Could not save the keys. Check that the folder {config.KEY_FILE.parent} is writable.", "error")
                return back_to_form()
            flash("Saved: " + ", ".join(PROVIDERS[p] for p in updates) + ".", "success")
        return back_to_form()

    keys = load_keys()
    configured = {name: bool(keys.get(name)) for name in PROVIDERS}
    return render_template("index.html", configured=configured)


def serve(port: int = 5000, open_browser: bool = True) -> None:
    """Run the key page. It only listens on this computer, and debug mode stays off
    because it would let anyone who can reach the page run code here."""
    url = f"http://127.0.0.1:{port}"
    print(f"Etcode key setup: {url}")
    print(f"Keys are saved in {config.KEY_FILE}")
    print("Press Ctrl+C when you are done.")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, debug=False)
