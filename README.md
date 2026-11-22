# aicommit

Generate git commit messages from your staged changes using Google AI Studio (Gemini).
Messages follow the [Conventional Commits](https://www.conventionalcommits.org/) format.

## Setup

1. Get an API key at <https://aistudio.google.com/apikey>.
2. Install (from this folder):

   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1        # macOS/Linux: source .venv/bin/activate
   pip install -e ".[dev]"
   ```

   To use `aicommit` in every repo without activating the venv, install it globally instead:
   `pipx install .` (or `pip install --user .`).

3. Save your key. Pick one:
   - **Global (recommended):** create `~/.aicommit.env` (`C:\Users\<you>\.aicommit.env` on Windows) containing
     `GEMINI_API_KEY=your-key`
   - **Environment variable:** `setx GEMINI_API_KEY "your-key"` (Windows, restart terminal after) or `export GEMINI_API_KEY=...`
   - **Per-project:** copy `.env.example` to `.env` in the repo you run it from (`.env` is git-ignored).

## Usage

```bash
git add .
aicommit
```

```
Generating commit message with gemini-2.5-flash...

feat(cli): add interactive regenerate option

- let users request a new message without re-running the tool
- raise temperature on each retry so results differ

Commit with this message? [y]es / [e]dit / [r]egenerate / [n]o:
```

| Option | Description |
| --- | --- |
| `-a`, `--all` | Stage modified/deleted tracked files first (like `git commit -a`) |
| `-y`, `--yes` | Commit without asking |
| `-d`, `--dry-run` | Only print the message |
| `-m`, `--hint "TEXT"` | Give the AI extra context, e.g. why you made the change |
| `-t`, `--type fix` | Force a commit type |
| `-1`, `--one-line` | Subject line only |
| `--model NAME` | Override the Gemini model |
| `--show-prompt` | Print the prompt that is sent |

You can also run it as `python -m aicommit`.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | — | Google AI Studio key (required) |
| `AICOMMIT_MODEL` | `gemini-2.5-flash` | Model name |
| `AICOMMIT_MAX_DIFF_CHARS` | `20000` | Diff size limit; large diffs are trimmed per file so every file stays visible |

Lockfiles (`package-lock.json`, `poetry.lock`, etc.) are excluded from the diff sent to the model.

## Development

See [CODE_GUIDE.md](CODE_GUIDE.md) for a walkthrough of every module and function.

```bash
pytest
```

Layout:

- `src/aicommit/cli.py`: argument parsing and the interactive loop
- `src/aicommit/git.py`: git commands (diff, log, commit)
- `src/aicommit/ai.py`: prompt, diff truncation, Gemini call
- `src/aicommit/config.py`: API key and settings loading
