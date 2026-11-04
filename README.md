# Etcode

An AI coding assistant for your terminal. It works with OpenAI, Gemini, and Claude, takes very large input, and can read and change files in a folder you choose. You approve every change first.

## Set up

```bash
pip install -r requirements.txt
python api-get/app.py        # opens http://127.0.0.1:5000 where you save your API keys
python main/main.py          # start the terminal assistant in the current folder
```

You can also set `OPENAI_API_KEY`, `GEMINI_API_KEY`, or `ANTHROPIC_API_KEY` instead of using the web page. Environment variables win over saved keys.

## Using it

| Do this | To get this |
|---|---|
| Enter | send your message |
| Alt+Enter (or Ctrl+J) | add a new line |
| Paste anything | big pastes show as `[Pasted text #1: N lines]` and the full text is sent |
| `@path/to/file` | attach that file's contents |
| `cat big.txt \| python main/main.py` | send a huge input without the prompt |
| `/undo` | undo the last file change |
| `/auto on` | apply changes without asking (`/auto off` to stop) |
| `/provider claude` | switch AI mid-session |
| `/status`, `/clear`, `/help`, `/exit` | the rest of the commands |

Options: `-w FOLDER` sets the folder the AI may touch (default: current folder), `-p openai|gemini|claude` skips the provider question, `--auto` starts with auto-approve on.

## How file changes work

The AI has six tools: `list_files`, `read_file`, `search_files`, `write_file`, `edit_file`, `delete_file`. Every write, edit, and delete shows a diff and waits for you. Answer `y` to apply, `n` to skip (you can tell the AI what to do instead), or `a` to apply this and all later changes.

Safety rules:
- The AI can only reach files inside the workspace folder. `..` and symlinks that point outside are blocked.
- `.env`, `*.pem`, `*.key`, `api.json`, SSH keys, `.git`, and `.etcode` are blocked, so secrets are never sent to a provider.
- The original of every changed file is copied to `.etcode/backups/` in the workspace, and `/undo` restores it. Add `.etcode/` to that project's `.gitignore`.
- Writes are atomic and keep your file's line endings (LF or CRLF).

## Settings (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `ETCODE_MODEL_OPENAI` / `_GEMINI` / `_CLAUDE` | see `main/config.py` | model names, in LiteLLM format like `anthropic/claude-sonnet-5-5` |
| `ETCODE_MAX_CONTEXT_TOKENS` | 120000 | oldest turns are dropped past this size |
| `ETCODE_TIMEOUT` | 120 | seconds to wait for a reply |
| `ETCODE_MAX_STEPS` | 25 | tool calls the AI may make in one turn |
| `ETCODE_KEY_FILE` | `api-get/api.json` | where keys are saved |

## Tests

```bash
python -m unittest discover -s tests -v
```
