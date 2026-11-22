# aicommit: Code Guide

This guide explains what this project does and how every part of the code works.
It's written for developers who know Python and git but haven't worked with AI before.

---

## 1. What the tool does

When you run `aicommit`, it:

1. Reads the changes you've staged with `git add`.
2. Sends those changes as text to Google's Gemini AI model over the internet.
3. Gets back a suggested commit message, for example:
   ```
   fix(auth): handle expired session tokens

   - refresh the token before retrying the request
   - log out the user if the refresh fails
   ```
4. Shows you the message and lets you **accept**, **edit**, **regenerate**, or **cancel**.
5. If you accept it, runs `git commit` with that message.

The tool only ever reads your code and calls one web API. It doesn't change any files except to create the commit you approve.

---

## 2. AI basics (only what you need for this project)

You don't need any machine-learning knowledge to understand this code. From our side, the AI is **a web API that takes text in and returns text out**.

| Term | What it means here |
| --- | --- |
| **LLM (Large Language Model)** | A program, hosted by Google, that has read huge amounts of text and code. Give it text and it writes a plausible continuation or answer. **Gemini** is Google's LLM. |
| **Model name** | Google has several versions (e.g. `gemini-2.5-flash`). "Flash" models are fast and cheap, which suits short tasks like commit messages. We pass the name as a string, like choosing a database version. |
| **Google AI Studio** | Google's website where you get a free **API key**. |
| **API key** | A secret string that identifies you to Google, like a password for the API. Anyone who has it can use your quota, so it's never committed to git. |
| **Prompt** | The text we send to the model. Here, that's the git diff plus some context. |
| **System instruction** | A separate, fixed block of text that tells the model *how to behave* ("you write commit messages, follow these rules…"). Think of it as the job description; the prompt is the specific task. |
| **Temperature** | A number (about 0–2) that controls randomness. Low (0.2) gives predictable, similar answers each time. High (1.0+) gives more varied, creative answers. We use 0.4 and raise it when you click "regenerate" so you get a different message. |
| **Tokens / context limit** | Models measure input in "tokens" (roughly 4 characters each). There's a cost and size limit per request, so we trim very large diffs before sending. |
| **Hallucination** | Models sometimes state things confidently that aren't true. Our instructions tell it to "describe only what the diff shows", but you should still read the message before accepting it. That's why the tool asks you to confirm. |
| **Non-determinism** | The same input can give a slightly different output each run. That's normal, and it's why "regenerate" works. |

**Main takeaway:** most of the "AI work" in this project is **writing good instructions in plain English** (the prompt) and **cleaning up the text that comes back**. The rest is ordinary Python: running git commands, reading config, and handling user input.

---

## 3. Project layout

```
AiCommit/
├── pyproject.toml          # package metadata, dependencies, defines the `aicommit` command
├── README.md               # setup + usage
├── CODE_GUIDE.md           # this file
├── .env.example            # template for the API key file
├── .gitignore              # keeps .env (your key) and .venv out of git
├── src/aicommit/
│   ├── __init__.py         # package marker + version number
│   ├── __main__.py         # lets you run `python -m aicommit`
│   ├── cli.py              # entry point: arguments, main flow, user prompt
│   ├── config.py           # loads API key and settings
│   ├── git.py              # runs git commands
│   └── ai.py               # builds the prompt, calls Gemini, cleans the answer
└── tests/
    └── test_ai.py          # unit tests for the text-processing functions
```

Each module has **one job**, and only `ai.py` knows that an AI is involved. You could swap Gemini for another provider by changing that one file.

---

## 4. The full flow

```
 you run: aicommit
        │
        ▼
 cli.main()
   ├─ git.ensure_repo()          → are we inside a git repo?
   ├─ config.load_config()       → find the API key + settings
   ├─ git.stage_tracked_changes()   (only with -a)
   ├─ git.staged_files()         → "M  src/app.py" list; stop if empty
   ├─ git.staged_diff()          → the actual code changes
   ├─ ai.truncate_diff()         → shrink if too big
   ├─ ai.build_prompt()          → combine diff + branch + recent commits + your hint
   │
   └─ loop:
        ├─ ai.generate_message() → HTTPS call to Gemini → cleaned message text
        ├─ print message
        └─ ask_action():
             y → git.commit(message)           → done
             e → git.commit(message, edit=True) → opens your git editor, then done
             r → raise temperature, loop again
             n → exit, nothing committed
```

---

## 5. File-by-file walkthrough

### 5.1 [pyproject.toml](pyproject.toml): packaging

- **`dependencies`**
  - `google-genai`: Google's official Python SDK for Gemini. It wraps the HTTP calls so we don't have to write them ourselves.
  - `python-dotenv`: reads `KEY=value` lines from `.env` files into environment variables.
- **`[project.scripts] aicommit = "aicommit.cli:main"`**: when installed, pip creates an `aicommit` executable that calls `main()` in `cli.py`. That's how the command appears on your PATH.
- **`[tool.setuptools.packages.find] where = ["src"]`**: uses the "src layout", which prevents tests from accidentally importing the code without it being installed.

### 5.2 [config.py](src/aicommit/config.py): settings

**Constants**
- `DEFAULT_MODEL = "gemini-2.5-flash"`: the model used if you don't choose one.
- `DEFAULT_MAX_DIFF_CHARS = 20_000`: the maximum diff size sent to the AI (about 5,000 tokens).
- `USER_ENV_FILE`: `~/.aicommit.env`, a per-user file so one key works in every repo.

**`class ConfigError(Exception)`**: a custom error type, so `cli.py` can catch configuration problems and print a friendly message instead of a stack trace.

**`@dataclass class Config`**: a simple container with three fields: `api_key`, `model`, `max_diff_chars`. `@dataclass` generates `__init__` and so on for us.

**`load_config(model_override=None) -> Config`**
1. Calls `load_dotenv(...)` on `./.env` and then `~/.aicommit.env`. `override=False` means **already-set values are never replaced**, so the priority order is:
   **real environment variable → `./.env` → `~/.aicommit.env`**.
2. Reads `GEMINI_API_KEY` (or `GOOGLE_API_KEY`, the name Google also accepts). If neither is set, it raises `ConfigError` with instructions on how to set the key.
3. Parses `AICOMMIT_MAX_DIFF_CHARS` as an integer, and raises a clear error if it isn't one.
4. Picks the model in this order: the `--model` flag, then the `AICOMMIT_MODEL` env var, then the default.

### 5.3 [git.py](src/aicommit/git.py): talking to git

All git access goes through the real `git` command-line program via `subprocess`. No git library is needed.

**`EXCLUDED_PATHS`**: lockfiles and minified files (`package-lock.json`, `poetry.lock`, `*.min.js`, …). They're huge, machine-generated, and tell the AI nothing useful, so we don't send their contents. That saves cost and keeps the AI focused.

**`class GitError(Exception)`**: the custom error type for git failures.

**`_run(args, input=None, check=True) -> str`**: the single helper every other function uses.
- Runs `git <args>` and returns its stdout as a string.
- `capture_output=True` collects the output instead of printing it.
- `encoding="utf-8", errors="replace"` avoids crashes on Windows when a diff contains unusual characters.
- If git exits with an error and `check=True`, it raises `GitError` with git's own error message.
- If `git` isn't installed, it turns `FileNotFoundError` into a readable `GitError`.

**`ensure_repo()`**: runs `git rev-parse --is-inside-work-tree`. If it fails, you aren't in a repo, so it raises "Not inside a git repository".

**`stage_tracked_changes()`**: runs `git add -u` (stages modified and deleted files git already tracks; new files are not added). Used by the `-a` flag, and mirrors `git commit -a`.

**`staged_files() -> str`**: runs `git diff --cached --name-status` and returns lines like `M\tsrc/app.py` (M = modified, A = added, D = deleted). The AI gets this as an overview, and `cli.py` uses it to check that something is staged.

**`staged_diff() -> str`**: runs `git diff --cached` with flags:
- `--cached`: staged changes only, which is exactly what will be committed.
- `--no-color`: no terminal color codes in the text.
- `--no-ext-diff`: ignores any custom diff tool you've configured, so we always get standard diff text.
- `":(exclude,glob)**/package-lock.json"` etc.: git "pathspec magic" that excludes the lockfiles in any folder.

**`recent_subjects(n=10) -> list[str]`**: runs `git log -n10 --pretty=format:%s` to get your last 10 commit subject lines. The AI uses them to match your style (e.g. whether you use scopes like `feat(api):`). `check=False` because this fails in a brand-new repo with no commits, which is fine: it just returns an empty list.

**`current_branch() -> str`**: the branch name (e.g. `feature/login-fix`), which often hints at the purpose of the change.

**`commit(message, edit=False)`**
1. Writes the message to a temporary file. A file is safer than passing it as a command-line argument, which has length and quoting problems, especially on Windows.
2. Runs `git commit -F <file>`, plus `-e` if `edit=True`. With `-e`, git opens your normal editor (VS Code, vim, Notepad…) pre-filled with the message.
3. Does **not** capture output, so your editor and any git hooks (pre-commit, etc.) work normally in your terminal.
4. Deletes the temp file in `finally`, even if the commit fails.

### 5.4 [ai.py](src/aicommit/ai.py): the AI part

**`SYSTEM_PROMPT`**: the "job description" sent with every request. It's plain English, and this is where the output quality comes from. It tells the model to:
- use the **Conventional Commits** format: `type(scope): summary`, where type is `feat`, `fix`, `docs`, …
- use imperative mood ("add", not "added"), at most 72 characters (git convention)
- add a bullet-point body only for non-trivial changes
- **not invent** reasons or ticket numbers (to limit hallucination)
- output only the message, without markdown or chatter

> If you want a different style (e.g. no Conventional Commits, or a different language), **this string is the place to change it.** No code changes are needed.

**`build_prompt(diff, files, branch, recent, hint, commit_type, one_line) -> str`**
Joins the pieces of context into one text block. Optional parts are only added when present:
- the branch name (skipped if `HEAD`, which means a detached HEAD)
- recent commit subjects, with "match their style"
- your `--hint` text (e.g. *"fixes timeout reported by QA"*), which tells the AI the *why* that code alone can't show
- `--type` to force a type, and `--one-line` to skip the body
- the file list, then the diff itself

It's ordinary string building. The model reads it like a person would.

**`truncate_diff(diff, max_chars) -> str`**
Keeps the request small. A naive approach would cut the text at 20,000 characters, but then a single huge file could push every other file out entirely, and the AI would never know those files changed. So instead it:
1. Returns the diff unchanged if it already fits.
2. Splits the diff into per-file chunks (each starts with `diff --git `). The regex `(?m)^(?=diff --git )` splits *before* each such line without removing it.
3. Gives each file an equal share of the budget (minimum 400 characters) and cuts any longer chunk, adding a note like `... [5321 chars of this file's diff truncated]` so the model knows content is missing.
4. Applies a final hard cap as a safety net.

**`clean_message(text) -> str`**
Models don't always follow formatting rules, so this fixes the common problems:
- removes markdown code fences (` ```text … ``` `) wrapped around the message
- removes surrounding quotes (`"feat: x"` becomes `feat: x`)
- collapses 3+ blank lines into one blank line

**`class AIError(Exception)`**: the custom error type for API problems.

**`generate_message(api_key, model, prompt, temperature=0.4) -> str`**: the **only function that talks to the AI**.
```python
client = genai.Client(api_key=api_key)
response = client.models.generate_content(
    model=model,                       # e.g. "gemini-2.5-flash"
    contents=prompt,                   # our diff + context
    config=types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,  # the rules
        temperature=temperature,           # randomness
    ),
)
text = response.text
```
- This is one blocking HTTPS request that takes a few seconds and returns `response.text`.
- The import is inside the function so `aicommit --help` starts instantly without loading Google's SDK.
- `errors.APIError` (bad key, quota exceeded, unknown model, network issues) is converted to `AIError` with the HTTP code and message, e.g. `Gemini API error (400): API key not valid`.
- An empty response, which can happen if Google's safety filters block something, raises a clear error.
- The result goes through `clean_message()` before being returned.

### 5.5 [cli.py](src/aicommit/cli.py): the command

**Color helpers** (`dim`, `bold`, `green`, `yellow`, `red`): wrap text in ANSI escape codes for terminal colors.
- Colors are disabled when output isn't a terminal (e.g. piped to a file) or when the standard `NO_COLOR` env var is set.
- `os.system("")` on Windows is a known trick that turns on ANSI color support in older consoles.

**`eprint(...)`**: prints to stderr, so errors don't mix with normal output.

**`parse_args(argv)`**: defines all command-line flags with the standard-library `argparse`:

| Flag | Effect |
| --- | --- |
| `-a / --all` | run `git add -u` first |
| `-y / --yes` | commit without asking |
| `-d / --dry-run` | print the message only |
| `-m / --hint TEXT` | extra context for the AI |
| `-t / --type` | force a commit type (validated against a fixed list) |
| `-1 / --one-line` | subject line only |
| `--model` | choose a model |
| `--show-prompt` | print exactly what is sent to the AI (useful for learning and debugging) |
| `-V / --version` | print the version |

**`ask_action() -> str`**: asks *"Commit with this message? [y]es / [e]dit / [r]egenerate / [n]o"* and loops until it gets a valid answer. Pressing Enter means yes. Ctrl+C or Ctrl+D means no, so nothing is committed by accident.

**`main(argv=None) -> int`**: the program itself. It returns an exit code (0 = success, 1 = error/aborted, 130 = Ctrl+C, the Unix convention), which `raise SystemExit(main())` passes to the shell. Steps:
1. Parse arguments, check we're in a repo, and load config.
2. Optionally stage changes (`-a`).
3. Stop with a hint if nothing is staged.
4. Get and trim the diff. If only lockfiles changed, the diff is empty after exclusion, so it substitutes a placeholder sentence and the AI still gets the file list.
5. Build the prompt, and print it if `--show-prompt`.
6. **Generation loop:** call the AI, then show the message:
   - `--dry-run`: stop here.
   - `y`: commit. `e`: commit via the editor.
   - `r`: increase `temperature` by 0.3 (up to a 1.5 cap) and loop. Since the prompt is identical, more randomness is what produces a *different* message.
   - `n`: abort.
7. **Error handling:** `GitError`, `ConfigError`, and `AIError` are all caught in one place and printed as `error: …` in red, without a Python traceback. Because the three modules raise their own error types, `cli.py` can handle them all the same way.

### 5.6 [\_\_main\_\_.py](src/aicommit/__main__.py) and [\_\_init\_\_.py](src/aicommit/__init__.py)

- `__init__.py` marks the folder as a package and holds `__version__`.
- `__main__.py` makes `python -m aicommit` work. It just calls `main()`.

### 5.7 [tests/test_ai.py](tests/test_ai.py): tests

The tests cover the **pure text functions**, which take input and return output with no network or git involved:
- `clean_message` removes fences, quotes, and extra blank lines.
- `truncate_diff` leaves small diffs alone, and with one huge file plus one small file it **keeps both files visible**.
- `build_prompt` includes every optional piece of context when given.

The actual Gemini call isn't unit-tested, because it needs a real key and network access and its output varies between runs. Testing it means running the tool for real (see `--dry-run` below).

Run with: `pytest`

---

## 6. Design decisions

| Decision | Reason |
| --- | --- |
| Use the `git` CLI via subprocess, not a library | Zero extra dependencies; behaves exactly like git in your terminal. |
| Only staged changes | That's exactly what gets committed; you control the scope with `git add`. |
| Always ask before committing (unless `-y`) | AI output can be wrong; a human checks it. |
| Exclude lockfiles | Saves cost and keeps the model focused on meaningful changes. |
| Per-file truncation | Big diffs can't hide other changed files from the model. |
| Send recent commits + branch | The model matches your team's existing style. |
| Key in `.env` / `~/.aicommit.env`, git-ignored | Secrets never end up in the repo. |
| Custom exception per module | Clean error messages; one place handles them. |
| All AI code in `ai.py` | Easy to switch providers or models later. |

---

## 7. Privacy and cost

- **Your staged diff is sent to Google.** Don't use the tool on code you aren't allowed to share with a third-party service, or on diffs that contain secrets (passwords, keys).
- Google AI Studio has a free tier with rate limits. With the free tier, Google may use the data to improve its products; paid tiers have different terms. Check Google's current terms if this matters for your work.
- Each run is a single request of at most about 20k characters, so the cost per commit is very small on paid plans.

---

## 8. Common changes

| I want to… | Change this |
| --- | --- |
| Change message style or rules | `SYSTEM_PROMPT` in [ai.py](src/aicommit/ai.py) |
| Use a different Gemini model | `AICOMMIT_MODEL=...` in your env file, or `--model` |
| Send bigger or smaller diffs | `AICOMMIT_MAX_DIFF_CHARS` |
| Ignore more generated files | add patterns to `EXCLUDED_PATHS` in [git.py](src/aicommit/git.py) |
| Make output more or less creative | `temperature` in `cli.main()` (starts at 0.4) |
| Add a new CLI flag | `parse_args()` in [cli.py](src/aicommit/cli.py), then pass it to `build_prompt()` |
| Use a different AI provider | rewrite only `generate_message()` in [ai.py](src/aicommit/ai.py) |

---

## 9. Troubleshooting

| Message | Meaning / fix |
| --- | --- |
| `No API key found` | Set `GEMINI_API_KEY` (see README setup). |
| `Gemini API error (400): API key not valid` | The key has a typo or was revoked; get a new one from AI Studio. |
| `Gemini API error (404)` | The model name doesn't exist; check `AICOMMIT_MODEL`. |
| `Gemini API error (429)` | Rate limit or quota hit; wait a minute or check your plan. |
| `No staged changes.` | Run `git add <files>` first, or use `aicommit -a`. |
| `Not inside a git repository` | `cd` into a repo first. |
| Message is vague | Add context: `aicommit -m "why I made this change"`. |
| Want to see what the AI receives | `aicommit -d --show-prompt` |
