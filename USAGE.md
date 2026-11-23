# Using aicommit

`aicommit` is a command-line tool. You install it once and give it an API key. After that, you run it in any git repository and it writes your commit message for you.

For every option and setting, see the [README](README.md).

## 1. One-time setup

1. **Get an API key.** Get a free Gemini key at <https://aistudio.google.com/apikey>.
   A Groq key from <https://console.groq.com/keys> is optional. With one set, the tool falls back to Groq when Gemini fails.
2. **Install** (from this folder):
   - To use it in every repo: `pipx install .` (or `pip install --user .`). This puts the `aicommit` command on your PATH.
   - For development, use a virtual environment:

     ```powershell
     python -m venv .venv
     .venv\Scripts\Activate.ps1        # macOS/Linux: source .venv/bin/activate
     pip install -e ".[dev]"
     ```
3. **Save your key.** Pick one place:
   - `~/.aicommit.env` (recommended), containing `GEMINI_API_KEY=your-key`. Add `GROQ_API_KEY=your-groq-key` on its own line if you have one.
   - An environment variable: `setx GEMINI_API_KEY "your-key"` on Windows (restart the terminal afterwards), or `export GEMINI_API_KEY=...` on macOS/Linux.
   - A `.env` file in the repo you run it from. Copy it from `.env.example`. It is git-ignored.

## 2. Daily use: commit messages

```bash
git add .
aicommit
```

The tool reads your staged diff and recent commit history and sends them to Gemini. While it waits, it shows the reply as it streams in. Then it prints the token count and estimated cost, followed by the proposed message:

```
feat: add interactive regenerate option

- let users request a new message without re-running the tool
- raise temperature on each retry so results differ

Commit with this message? [y]es / [e]dit / [r]egenerate / [n]o:
```

| Key | What it does |
| --- | --- |
| `y` | Commits with this message |
| `e` | Opens git's editor with the message filled in, so you can change it before committing |
| `r` | Asks the AI for a different message |
| `n` | Cancels without committing |

If nothing is staged, the tool tells you to stage files with `git add` or to use `aicommit -a`.

### Common variations

| Command | When to use it |
| --- | --- |
| `aicommit -a` | Stage changes to tracked files first, like `git commit -a` |
| `aicommit -m "why I did this"` | Give the AI context the diff can't show |
| `aicommit -t fix` | Force the type (`feat`, `fix` or `refactor`) |
| `aicommit -y` | Commit without asking |
| `aicommit -d` | Only print the message (dry run) |
| `aicommit -1` | Commit with the subject line only |

## 3. Pull request descriptions

```bash
aicommit --pr                 # current branch vs. origin's default branch (or main/master)
aicommit --pr --base develop  # choose the target branch
```

This writes a title and a description in GitHub markdown for all the changes on your branch.

## 4. Scripts and automation

```bash
aicommit --json > message.json
```

`--json` prints only the validated JSON, `{"title", "summary", "type"}`, to stdout, and it never commits. Status messages go to stderr, so you can pipe the output straight into other tools. For example, with the GitHub CLI:

```bash
gh pr create --title "$(aicommit --pr --json | jq -r '.type + ": " + .title')" ...
```

## 5. What happens automatically

- **Fallback to Groq.** If Gemini returns an error, is rate-limited or down, or returns invalid JSON three times in a row, the tool switches to Groq. This needs `GROQ_API_KEY` to be set. Use `--provider groq` to try Groq first, or `--no-fallback` to turn the fallback off.
- **Large diffs are trimmed** to fit the token budget, and lockfiles such as `package-lock.json` and `poetry.lock` are left out.
- **Replies are validated.** The JSON must match the schema. If a reply is invalid, the tool retries and tells the model what was wrong.
