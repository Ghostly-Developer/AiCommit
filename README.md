# aicommit

Generate git commit messages and pull request descriptions from your diff using Google AI Studio (Gemini).

The model returns structured JSON, `{"title", "summary", "type"}` with `type` one of `feat | fix | refactor`.
That JSON is constrained by a schema on the API side **and** validated locally, and the tool retries with feedback
if a reply is invalid. Responses stream as they're generated, and every call reports its token usage and estimated cost.

If Gemini fails (API error, rate limit, outage, or invalid JSON 3 times in a row), the tool falls back to **Groq**
automatically when a Groq key is set.

New here? [USAGE.md](USAGE.md) walks through setup and day-to-day use step by step.

## Setup

1. Get an API key at <https://aistudio.google.com/apikey>.
   Optional: also get a Groq key at <https://console.groq.com/keys> to enable the fallback.
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
     `GEMINI_API_KEY=your-key` (and, optionally, `GROQ_API_KEY=your-groq-key` on its own line)
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
| `--provider gemini\|groq` | Which provider to try first (default: `gemini`) |
| `--no-fallback` | Use only the first provider; don't fall back if it fails |
| `--model NAME` | Override the Gemini model |
| `--groq-model NAME` | Override the Groq model |
| `--show-prompt` | Print the system and user prompts that are sent |

You can also run it as `python -m aicommit`.

### Groq fallback

With both keys set, a run looks like this when Gemini is down:

```
providers: gemini (gemini-3.8-flash) -> groq (openai/gpt-oss-20b)
Generating commit message with gemini (gemini-3.8-flash)...
gemini failed: Gemini API error (503): The model is overloaded.
falling back to groq...
Generating commit message with groq (openai/gpt-oss-20b)...
tokens: 1,310 in / 74 out / 212 thinking | est. cost $0.00018
```

- Fallback happens on any Gemini API/network error, or when Gemini returns invalid JSON 3 times.
- Groq uses the same schema (in **strict** mode for the default `openai/gpt-oss-20b`, plus `openai/gpt-oss-120b` and `qwen/qwen3.8-27b`; best-effort for other models), and the reply is validated locally just the same.
- Groq doesn't support streaming together with structured output, so its reply appears all at once.
- Groq gets its **own, smaller prompt budget** (`AICOMMIT_GROQ_MAX_INPUT_TOKENS`, default 6000), because its free tier rejects any single request over the model's input-tokens-per-minute cap (HTTP 413). If Groq still answers 413, the tool reads the limit from the error, trims the diff to 80% of it, and retries once.
- With only a Groq key set, Groq is used on its own. `aicommit --provider groq` tries Groq first.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | — | Google AI Studio key |
| `GROQ_API_KEY` | — | Groq key; enables the fallback (at least one of the two keys is required) |
| `AICOMMIT_MODEL` | `gemini-3.8-flash` | Gemini model |
| `AICOMMIT_GROQ_MODEL` | `openai/gpt-oss-20b` | Groq model |
| `AICOMMIT_GROQ_MAX_INPUT_TOKENS` | `6000` | Groq prompt budget. Keep it under your Groq tier's input-tokens-per-minute limit |
| `AICOMMIT_MAX_INPUT_TOKENS` | `16000` | Gemini input token budget per request; large diffs are trimmed per file to fit |
| `AICOMMIT_PRICE_INPUT` / `AICOMMIT_PRICE_OUTPUT` | built-in table | Gemini USD per 1M tokens, for the cost estimate |
| `AICOMMIT_GROQ_PRICE_INPUT` / `AICOMMIT_GROQ_PRICE_OUTPUT` | built-in table | Same, for Groq |

Lockfiles (`package-lock.json`, `poetry.lock`, etc.) are excluded from the diff sent to the model.
Cost estimates assume paid-tier pricing; free tiers cost $0.

## Development

See [CODE_GUIDE.md](CODE_GUIDE.md) for a walkthrough of every module and function.

```bash
pytest
```

Layout:

- `src/aicommit/cli.py`: argument parsing and the interactive loop
- `src/aicommit/git.py`: git commands (staged diff, branch diff for PRs, log, commit)
- `src/aicommit/ai.py`: prompts, token budgeting, Gemini (streaming) and Groq clients, validate-and-retry, provider fallback
- `src/aicommit/schema.py`: the `{title, summary, type}` schema and its validation
- `src/aicommit/usage.py`: token usage and cost estimates
- `src/aicommit/config.py`: API key and settings loading
