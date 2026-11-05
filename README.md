# Etcode

An AI coding assistant for your terminal. It works with OpenAI, Gemini, and Claude, takes very large input, and can read and change files in the folder you run it from. You approve every change first.

## Install

You need Python 3.9 or newer. On Windows, tick "Add python.exe to PATH" in the Python installer.

```
pip install pipx
pipx ensurepath
```

Close the terminal and open a new one, then:

```
pipx install git+https://github.com/JohanLiebert363/etcode
```

To update later: `pipx upgrade etcode`.

## First run

```
etcode keys
```

This opens a page on your own computer. Paste the API key for any provider you have (OpenAI, Google Gemini, or Anthropic) and save. Keys are stored in your user folder (`%APPDATA%\etcode` on Windows, `~/.config/etcode` elsewhere), not in any project. Never share that file.

Then go to any project folder and start:

```
cd path\to\your\project
etcode
```

You can also set `OPENAI_API_KEY`, `GEMINI_API_KEY`, or `ANTHROPIC_API_KEY` instead of using the page.

## Using it

| Do this | To get this |
|---|---|
| Enter | send your message |
| Alt+Enter (or Ctrl+J) | add a new line |
| Paste anything | big pastes show as `[Pasted text #1: N lines]` and the full text is sent |
| `@path/to/file` | attach that file's contents |
| `cat big.txt \| etcode` | send a huge input without the prompt |
| `!git status` (or `/run git status`) | run a shell command yourself; the output is sent with your next message, handy for failing tests |
| `/cd folder` | change the folder the AI works in (`/cd ..` goes up, `/cd` shows the current one) |
| `/ls [folder]` | list files |
| `/undo` | undo the last file change |
| `/auto on` | apply changes without asking (`/auto off` to stop) |
| `/provider claude` | switch AI mid-session |
| `/status`, `/clear`, `/help`, `/exit` | the rest of the commands |

Options: `etcode --allow-commands` lets the AI run commands without asking (risky), `etcode -w FOLDER` works on another folder, `etcode -p openai|gemini|claude` skips the provider question, `etcode --auto` starts with auto-approve on, `etcode --version`.

## How file changes work

The AI can list, search, read, create, edit, and delete files. Every write, edit, and delete shows a diff and waits for you: `y` applies, `n` skips (you can tell the AI what to do instead), `a` applies this and all later changes.

- The AI can only reach files inside the folder where you started Etcode. `..` and symlinks pointing outside are blocked.
- `.env`, `*.pem`, `*.key`, `api.json`, SSH keys, `.git`, and `.etcode` are blocked, so secrets are never sent to a provider.
- The original of every changed file is copied to `.etcode/backups/` in that folder, and `/undo` restores it. Add `.etcode/` to the project's `.gitignore`.
- Writes are atomic and keep your line endings (LF or CRLF).

## Commands the AI runs

The AI can run shell commands (tests, builds, git) with a `run_command` tool. Every command is shown and needs your `y`. `/auto` only covers file edits, never commands.

Commands are **not sandboxed**: once you approve one, it can do anything you can do in that terminal. Read it before you press `y`. Each command runs in a fresh shell in the workspace folder, with a time limit (60 seconds by default, 10 minutes at most), and long output is cut.

## Rate limits

Etcode makes one request to the AI per step. Free plans allow few requests per minute (Gemini's free tier: 5), so a task that reads several files can hit the limit. Etcode waits for the time the provider asks for and retries. To avoid hitting it at all, set a pace:

```
setx ETCODE_REQUESTS_PER_MINUTE 4        (Windows, then open a new terminal)
export ETCODE_REQUESTS_PER_MINUTE=4      (macOS / Linux)
```

If a message fails, press the up arrow to bring it back. Daily limits and unpaid quotas are not retried, because waiting will not help; switch AI with `/provider` or turn on billing.

## Settings (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `ETCODE_MODEL_OPENAI` / `_GEMINI` / `_CLAUDE` | see `etcode/config.py` | model names in LiteLLM format, like `anthropic/claude-sonnet-5-5` |
| `ETCODE_MAX_CONTEXT_TOKENS` | 120000 | oldest turns are dropped past this size |
| `ETCODE_TIMEOUT` | 120 | seconds to wait for a reply |
| `ETCODE_MAX_STEPS` | 25 | requests the AI may make in one turn |
| `ETCODE_REQUESTS_PER_MINUTE` | 0 (off) | space requests out to stay under a provider limit |
| `ETCODE_KEY_FILE` | user folder | where keys are saved |

## Develop

```
git clone https://github.com/JohanLiebert363/etcode
cd etcode
pip install -e .
python -m unittest discover -s tests -v
```
