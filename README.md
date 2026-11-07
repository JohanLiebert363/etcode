# Etcode
[![Tests](https://github.com/JohanLiebert363/etcode/actions/workflows/tests.yml/badge.svg)](https://github.com/JohanLiebert363/etcode/actions/workflows/tests.yml)

An AI coding assistant for your terminal. It works with OpenAI, Gemini, and Claude, and can read and change files in the folder you run it from. You approve every change first.
<img width="1218" height="612" alt="Screenshot 2026-11-05 231259" src="https://github.com/user-attachments/assets/e2357b1e-30a4-4f00-a236-3b352a48dc58" />

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
| `/provider` | pick another AI from a menu (or type `/provider claude`) |
| `/stream on\|off` | show replies as they are written, or only when complete |
| `/save name`, `/load name`, `/sessions` | save, restore and list conversations |
| `/init` | have the AI write an `ETCODE.md` for the current project |
| `/effort` | how hard the AI thinks: low, medium, high or default (menu, or `/effort high`) |
| `/skills` | list skills; run one with `/name`, create one with `/skills new name` |
| `/review`, `/explain`, `/fix`, `/test`, `/commit`, `/plan` | built-in skills, see below |
| `/status`, `/clear`, `/help`, `/exit` | the rest of the commands |

Options: `etcode --continue` (or `-c`) picks up your last conversation, `etcode --allow-commands` lets the AI run commands without asking (risky), `etcode -w FOLDER` works on another folder, `etcode -p openai|gemini|claude` skips the provider question, `etcode --auto` starts with auto-approve on, `etcode --version`.

## Skills

A skill is a saved prompt you run with a slash. Type what you want after the name and it goes where `$ARGS` appears:

| Skill | What it does |
|---|---|
| `/review` | reviews your uncommitted changes (bugs, risks, missing tests) without editing |
| `/explain [file or topic]` | explains the project, a file or a function |
| `/fix <problem>` | finds and fixes a problem with the smallest change, then checks it |
| `/test [what]` | writes or updates tests and runs them |
| `/commit` | drafts a commit message from your changes (it does not commit) |
| `/plan <task>` | plans a task and lists the files it would touch, without changing anything |

Make your own with `/skills new deploy`. This creates `.etcode/skills/deploy.md` in the project (commit it to share it with your team). Put skills in your user folder under `skills` to have them in every project. A file looks like this:

```
---
description: Check the release notes
---
Read CHANGELOG.md and tell me what is missing for version $ARGS.
```

Project skills override global ones, which override built-in ones. A skill cannot replace a built-in command like `/help`. Skills are instructions to the AI, so only use skill files you trust, especially ones that came with someone else's repository. The AI itself cannot edit the skills folder.

## Effort

`/effort low|medium|high` sets how much the AI thinks before answering: higher is slower and more careful. `/effort default` lets the model decide, and `ETCODE_EFFORT` sets a starting level. It works with Gemini and OpenAI models that support thinking and is ignored by others. Claude is not supported yet.

## Project instructions (ETCODE.md)

Put a file called `ETCODE.md` in a project folder and Etcode reads it on every request: how to build and test, coding style, files never to touch. Run `/init` and the AI writes a first version for you (you approve it like any file). A global `ETCODE.md` in your user folder (`%APPDATA%\etcode` on Windows, `~/.config/etcode` elsewhere) applies to every project. Only keep instructions you trust in these files, and be careful with an `ETCODE.md` that came from someone else's repository.

## Replies, tokens and cost

Replies appear as they are written. After each one you see the tokens used and, when the model has a known price, an estimate at list price, for this turn and for the session. `~` marks numbers the provider did not report and Etcode estimated. Free plans are not billed, so treat the cost as what the same use would cost on a paid plan.

## Saving conversations

Every reply is autosaved, so `etcode --continue` resumes where you stopped. `/save name` and `/load name` keep named conversations. Files live in your user folder (`sessions`), readable only by you, and contain everything in the conversation, including anything you pasted. `/clear` also deletes the autosave. Turn autosave off with `ETCODE_AUTOSAVE=0`.

## When the AI hits a rate limit

If a provider's limit is reached, Etcode offers to carry on with another provider that has a key. Set `ETCODE_AUTO_FALLBACK=1` to switch without asking.

## How file changes work

The AI can list, search, read, create, edit, and delete files. Every write, edit, and delete shows a diff and waits for you: `y` applies, `n` skips (you can tell the AI what to do instead), `a` applies this and all later changes.

- The AI can only reach files inside the folder where you started Etcode. `..` and symlinks pointing outside are blocked.
- `.env`, `*.pem`, `*.key`, `api.json`, SSH keys, `.git`, and `.etcode` are blocked, so secrets are never sent to a provider.
- The original of every changed file is copied to `.etcode/backups/` in that folder, and `/undo` restores it. Add `.etcode/backups/` to the project's `.gitignore` (but keep `.etcode/skills/` if you want to share skills).
- Writes are atomic and keep your line endings (LF or CRLF).

## Commands the AI runs

The AI can run shell commands (tests, builds, git) with a `run_command` tool. Every command is shown and needs your `y`. `/auto` only covers file edits, never commands.

A short list of read-only commands runs without asking and is shown with a note: `git status`, `log`, `diff`, `show`, `ls-files`, `branch` (listing only), `ls`/`dir` inside the folder, and `--version` checks. Anything with pipes, redirects, quotes, variables or paths outside the folder still asks. Set `ETCODE_SAFE_COMMANDS=0` to be asked every time. Running git in a repository you do not trust can run helpers that repository configures, so turn this off there.

Commands are **not sandboxed**: once you approve one, it can do anything you can do in that terminal. Read it before you press `y`. Each command runs in a fresh shell in the workspace folder, with a time limit (60 seconds by default, 10 minutes at most), and long output is cut.

## Rate limits

Etcode makes one request to the AI per step. Free plans allow few requests per minute (Gemini's free tier: 5), so a task that reads several files can hit the limit. Etcode waits for the time the provider asks for and retries. When the error states a per-minute limit, it learns the number and paces itself just below it for the rest of the session, so the limit stops coming back. The message says whether you hit the per-minute or the daily limit; daily limits do not clear by waiting, so Etcode offers another provider instead. To avoid hitting it at all, set a pace:

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
| `ETCODE_EFFORT` | unset | starting effort: `low`, `medium` or `high` |
| `ETCODE_STREAM` | on | `0` shows replies only when complete |
| `ETCODE_AUTO_FALLBACK` | off | `1` switches AI on a rate limit without asking |
| `ETCODE_SAFE_COMMANDS` | on | `0` asks before every command |
| `ETCODE_AUTOSAVE` | on | `0` stops saving conversations |

## Develop

```
git clone https://github.com/JohanLiebert363/etcode
cd etcode
pip install -e .
python -m unittest discover -s tests -v
```
