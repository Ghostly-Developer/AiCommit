# aicommit

Generate git commit messages and pull request descriptions from your diff using Google AI Studio (Gemini).

The model returns structured JSON, `{"title", "summary", "type"}` with `type` one of `feat | fix | refactor`.
That JSON is constrained by a schema on the API side **and** validated locally, and the tool retries with feedback
if a reply is invalid. Responses stream as they're generated, and every call reports its token usage and estimated cost.

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

### Commit message

```bash
git add .
aicommit
```

```
prompt: ~1,240 tokens (limit 16,000)
Generating commit message with gemini-3.8-flash...
{"title": "add interactive regenerate option", "summary": "- let users ...", "type": "feat"}
tokens: 1,262 in / 61 out / 310 thinking | est. cost $0.00141

feat: add interactive regenerate option

- let users request a new message without re-running the tool
- raise temperature on each retry so results differ

Commit with this message? [y]es / [e]dit / [r]egenerate / [n]o:
```

### Raw JSON

```bash
aicommit --json > message.json      # status goes to stderr, so stdout is pure JSON
```

```json
{
  "title": "add interactive regenerate option",
  "summary": "- let users request a new message without re-running the tool
- raise temperature on each retry so results differ",
  "type": "feat"
}
```

### Pull request description

```bash
aicommit --pr                 # current branch vs. origin's default branch (or main/master)
aicommit --pr --base develop  # explicit target
aicommit --pr --json          # same JSON shape; summary is GitHub markdown
```

With `gh`: `gh pr create --title "$(aicommit --pr --json | jq -r '.type + ": " + .title')" ...`

### Options

| Option | Description |
| --- | --- |
| `--pr` | Describe the current branch as a pull request instead of writing a commit |
| `--base BRANCH` | With `--pr`: the target branch |
| `--json` | Print the validated JSON to stdout and exit (never commits) |
| `-a`, `--all` | Stage modified/deleted tracked files first (like `git commit -a`) |
| `-y`, `--yes` | Commit without asking |
| `-d`, `--dry-run` | Only print the message |
| `-m`, `--hint "TEXT"` | Give the AI extra context, e.g. why you made the change |
| `-t`, `--type fix` | Force the type (`feat`, `fix` or `refactor`) |
| `-1`, `--one-line` | Commit with the subject line only |
| `--no-stream` | Don't echo the response while it streams |
| `--model NAME` | Override the Gemini model |
| `--show-prompt` | Print the system and user prompts that are sent |

You can also run it as `python -m aicommit`.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | — | Google AI Studio key (required) |
| `AICOMMIT_MODEL` | `gemini-3.8-flash` | Model name |
| `AICOMMIT_MAX_INPUT_TOKENS` | `16000` | Input token budget per request; large diffs are trimmed per file to fit |
| `AICOMMIT_PRICE_INPUT` / `AICOMMIT_PRICE_OUTPUT` | built-in table | USD per 1M tokens, for the cost estimate |

Lockfiles (`package-lock.json`, `poetry.lock`, etc.) are excluded from the diff sent to the model.
Cost estimates assume paid-tier pricing; the free AI Studio tier costs $0.

## Development

See [CODE_GUIDE.md](CODE_GUIDE.md) for a walkthrough of every module and function.

```bash
pytest
```

Layout:

- `src/aicommit/cli.py`: argument parsing and the interactive loop
- `src/aicommit/git.py`: git commands (staged diff, branch diff for PRs, log, commit)
- `src/aicommit/ai.py`: prompts, token budgeting, streaming Gemini call, validate-and-retry
- `src/aicommit/schema.py`: the `{title, summary, type}` schema and its validation
- `src/aicommit/usage.py`: token usage and cost estimates
- `src/aicommit/config.py`: API key and settings loading
