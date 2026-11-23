# aicommit: Code Guide

This guide explains what this project does and how every part of the code works.
It's written for developers who know Python and git but haven't worked with AI before.

---

## 1. What the tool does

When you run `aicommit`, it:

1. Reads the changes you've staged with `git add` (or, with `--pr`, everything on your branch since it left `main`).
2. Checks how many **tokens** that is, and trims the diff if it's over budget.
3. Sends the changes to Google's Gemini model, asking for **JSON in a fixed shape**:
   ```json
   {
     "title": "handle expired session tokens",
     "summary": "- refresh the token before retrying the request\n- log out the user if the refresh fails",
     "type": "fix"
   }
   ```
4. **Streams** the reply to your terminal as it's generated.
5. **Validates** the JSON against a schema. If it's invalid, it tells the model what was wrong and asks again.
6. Prints the token usage and estimated cost of the call.
7. Turns the JSON into a commit message (`fix: handle expired session tokens` + body) and lets you **accept**, **edit**, **regenerate**, or **cancel**. With `--json` it prints the JSON instead; with `--pr` it prints a PR description.

The tool only reads your code and calls one web API. It doesn't change any files except to create the commit you approve.

---

## 2. AI basics (only what you need for this project)

From our side, the AI is **a web API that takes text in and returns text out**.

| Term | What it means here |
| --- | --- |
| **LLM (Large Language Model)** | A program, hosted by Google, that has read huge amounts of text and code. Give it text and it writes a plausible answer. **Gemini** is Google's LLM. |
| **Model name** | Google has several versions (e.g. `gemini-3.8-flash`). "Flash" models are fast and cheap, which suits short tasks like commit messages. |
| **API key** | A secret string that identifies you to Google, like a password for the API. Anyone who has it can use your quota, so it's never committed to git. |
| **System instruction vs. prompt** | The **system instruction** is a fixed "job description" (rules, output format). The **user prompt** is the specific task (this diff). Keeping them separate means the rules never change between calls; only the task does. |
| **Temperature** | A number (about 0–2) that controls randomness. Low (0.2) gives predictable answers; high (1.0+) gives more varied ones. We start at 0.4 and raise it on "regenerate". |
| **Tokens** | Models read and bill text in **tokens**, pieces of words. English prose is ~4 characters per token; code is often worse (~2–3) because of symbols and indentation. |
| **Context limit** | The maximum tokens a model accepts in one request (about a million for Flash). We use a much smaller self-imposed budget (16k) as a **cost guard**. |
| **Cost per call** | You pay per million input tokens and per million output tokens; output is several times dearer. **Thinking tokens** (the model's hidden reasoning) are billed as output. The free AI Studio tier costs $0 but has rate limits. |
| **Streaming** | Instead of waiting for the whole reply, the API sends it in chunks as it's generated. Same total time, but you see progress immediately. |
| **Structured output** | Asking the API to return JSON that matches a **JSON Schema**. The API constrains generation to that shape, so you get data your code can rely on instead of free text you'd have to parse. |
| **Validation** | Even with structured output, **never trust the model blindly**: check the reply against the schema in your own code (here with Pydantic) and handle failures. |
| **Hallucination** | Models sometimes state things confidently that aren't true. Our instructions say "describe only what the diff shows", but read the message before accepting it. |

**Main takeaway:** most of the "AI work" is **writing good instructions** (the prompts), **defining the output contract** (the schema), and **checking what comes back**. The rest is ordinary Python.

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
│   ├── git.py              # runs git commands (staged diff, branch diff for PRs, commit)
│   ├── schema.py           # the JSON contract {title, summary, type} and its validation
│   ├── usage.py            # token counts and cost estimates
│   └── ai.py               # prompts, token budgeting, streaming Gemini call, retries
└── tests/
    ├── test_ai.py          # generate/retry logic with a fake model, prompt + budget helpers
    ├── test_schema.py      # valid and invalid JSON cases
    └── test_usage.py       # cost maths
```

Only `ai.py` imports the AI SDKs: Google's inside the `Gemini` class and Groq's inside the `Groq` class. Both have the same two methods, so the rest of the code doesn't care which one is answering. That's what makes the fallback cheap to add.

---

## 4. The full flow

```
 you run: aicommit            (or: aicommit --pr)
        │
        ▼
 cli.main()
   ├─ git.ensure_repo(), config.load_config()
   ├─ commit mode: git.staged_files() / git.staged_diff()
   │  PR mode:     git.branch_files(base) / git.branch_diff(base) / git.branch_commits(base)
   ├─ cli.build_llms()           → [Gemini, Groq], limited to providers that have a key
   ├─ ai.fit_prompt()            → build prompt, count tokens, trim the diff until it fits
   │
   └─ loop:
        ├─ ai.generate_with_fallback()     → for each provider in order:
        │  └─ ai.generate()
        │       ├─ llm.stream()        → Gemini: streaming HTTPS call, JSON-schema constrained
        │       │                        Groq: one (non-streaming) call, strict JSON schema
        │       │    (each chunk is echoed to the terminal as it arrives)
        │       ├─ schema.parse()       → json.loads + Pydantic validation
        │       └─ invalid? add the error to the prompt and try again (up to 3 attempts)
        │     AIError (API error, or 3 invalid replies) → warn, try the next provider
        ├─ print token usage + estimated cost
        ├─ --json → print the JSON, exit
        ├─ --pr / --dry-run → print the rendered text, exit
        └─ ask_action():
             y → git.commit(message)            → done
             e → git.commit(message, edit=True) → opens your git editor, then done
             r → raise temperature, loop again
             n → exit, nothing committed
```

---

## 5. File-by-file walkthrough

### 5.1 [pyproject.toml](pyproject.toml): packaging

- **`dependencies`**
  - `google-genai>=1.30`: Google's official Python SDK for Gemini. 1.30+ is needed for `response_json_schema`.
  - `groq`: Groq's official SDK (an OpenAI-style chat API), used for the fallback.
  - `pydantic`: defines the output schema as a Python class and validates data against it.
  - `python-dotenv`: reads `KEY=value` lines from `.env` files into environment variables.
- **`[project.scripts] aicommit = "aicommit.cli:main"`**: when installed, pip creates an `aicommit` executable that calls `main()` in `cli.py`.
- **`[tool.setuptools.packages.find] where = ["src"]`**: the "src layout", which prevents tests from accidentally importing the code without it being installed.

### 5.2 [config.py](src/aicommit/config.py): settings

- `DEFAULT_MODEL = "gemini-3.8-flash"`: the Gemini model used if you don't choose one.
- `DEFAULT_GROQ_MODEL = "qwen/qwen3.8-27b"`: the Groq fallback model. It supports Groq's *strict* JSON-schema mode. Groq lists it as a preview model; `openai/gpt-oss-20b` is a much cheaper strict-mode alternative.
- `DEFAULT_MAX_INPUT_TOKENS = 16_000`: the per-request input budget (system prompt + diff).
- `USER_ENV_FILE`: `~/.aicommit.env`, a per-user file so one key works in every repo.

**`load_config(model_override=None, groq_model_override=None) -> Config`**
1. Loads `./.env` then `~/.aicommit.env`. `override=False` means already-set values win, so the priority is **real environment variable → `./.env` → `~/.aicommit.env`**.
2. Reads `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) and `GROQ_API_KEY`. Each is optional, but at least one is required, otherwise it raises `ConfigError` with setup instructions.
3. Parses `AICOMMIT_MAX_INPUT_TOKENS` (must be an integer ≥ 1000).
4. Picks the models: `--model` / `AICOMMIT_MODEL` / default for Gemini, and `--groq-model` / `AICOMMIT_GROQ_MODEL` / default for Groq.

### 5.3 [git.py](src/aicommit/git.py): talking to git

All git access goes through the real `git` program via `subprocess`.

- **`EXCLUDED_PATHS`**: lockfiles and minified files. They're huge, machine-generated and tell the model nothing, so their contents are never sent. That saves tokens, which saves money.
- **`_run(args, check=True)`**: runs `git <args>` and returns stdout. Raises `GitError` with git's own message on failure. `encoding="utf-8", errors="replace"` avoids crashes on unusual characters on Windows.
- **`ensure_repo()`**, **`stage_tracked_changes()`** (`git add -u`, for `-a`), **`staged_files()`** (`--name-status` list), **`staged_diff()`** (`git diff --cached`, excluding lockfiles via pathspec magic `:(exclude,glob)**/...`).
- **`recent_subjects(n=10)`**: your last 10 commit subjects, so the model can match your tone.
- **`current_branch()`**: the branch name, which often hints at the purpose of a change.
- **`commit(message, edit=False)`**: writes the message to a temp file and runs `git commit -F <file>` (plus `-e` to open your editor). Output isn't captured, so your editor and hooks work normally.

**Pull request helpers** (used by `--pr`):
- **`default_base_branch()`**: the branch a PR would target. It asks git for `origin/HEAD` (the remote's default branch), then falls back to `origin/main`, `origin/master`, `main`, `master`.
- **`ensure_ref(ref)`**: fails clearly if `--base` names a branch that doesn't exist.
- **`branch_files(base)`** / **`branch_diff(base)`**: `git diff base...HEAD`. The **three dots** mean "changes on my branch since it forked from base", ignoring anything merged into base since. That's exactly what a PR shows.
- **`branch_commits(base)`**: `git log --reverse base..HEAD`, the subjects of your branch's commits, oldest first. They're a good summary of intent for the model.

### 5.4 [schema.py](src/aicommit/schema.py): the output contract

This is the heart of "structured output".

**`COMMIT_TYPES` / `CommitType`**: the allowed values, `feat`, `fix`, `refactor`. To allow more (e.g. `docs`), add them to both.

**`class GeneratedMessage(BaseModel)`**: a Pydantic model describing the JSON we require:

| Field | Rule |
| --- | --- |
| `title` | 1–72 characters, single line |
| `summary` | non-empty |
| `type` | exactly one of `COMMIT_TYPES` |

`extra="forbid"` rejects unknown keys. Two **validators** tidy harmless habits instead of failing on them: `_normalise_title` strips a `feat: ` prefix the model may add out of habit, plus a trailing period, and `_normalise_summary` collapses runs of blank lines.

Rendering helpers turn the data into text: `subject` (`"feat: add x"`), `as_commit_message(one_line)`, and `as_pr_markdown()`.

**`json_schema()`**: Pydantic generates a standard JSON Schema from the class. This same schema is sent to Gemini, so **one definition drives both what we ask for and what we accept**.

**`parse(text) -> GeneratedMessage`**: the validation step:
1. `strip_fences()` removes ```` ```json ```` fences if present.
2. `json.loads` checks it's JSON at all.
3. `GeneratedMessage.model_validate` checks the shape and types.
4. Any failure raises `OutputInvalid` with a readable reason such as `JSON does not match schema (type: Input should be 'feat', 'fix' or 'refactor')`. That reason is shown to you **and fed back to the model** on retry.

**`portable_json_schema()`**: the same schema minus the `title`, `minLength` and `maxLength` *keywords*, which Groq's strict mode doesn't document support for. (The *field* called `title` stays.) This only loosens what the API enforces: `parse()` still checks lengths locally.

**Why validate if the API already enforces the schema?** API-side schema support is best-effort: it can differ by model, some JSON Schema keywords may be ignored, and a reply can be cut off mid-JSON (e.g. a safety block or token limit). Checking in your own code turns "usually valid" into "valid every time, or a clear error".

### 5.5 [usage.py](src/aicommit/usage.py): tokens and cost

- **`PRICES`**: USD per 1M tokens `(input, output)` for known Gemini and Groq models. Prices change, so treat this as a convenience. Env vars override it per provider: `AICOMMIT_PRICE_INPUT/OUTPUT` for Gemini and `AICOMMIT_GROQ_PRICE_INPUT/OUTPUT` for Groq. Each client class carries its prefix as `price_env`, so a Groq call is never priced with Gemini's override. The `gemini-3.8-flash` entry is an introductory rate that Google has said will double on 2027-01-01.
- **`Usage`**: input, output and thinking token counts plus a call count. `from_metadata()` reads Gemini's `usage_metadata` (`prompt_token_count`, `candidates_token_count`, `thoughts_token_count`). `+` adds two usages, so retries are summed.
- **`estimate_cost()`**: `input × input_price + (output + thinking) × output_price`, per million. Unknown model → `None`.
- **`format_usage()`**: the line printed after each call, e.g. `tokens: 2,310 in / 84 out / 412 thinking | est. cost $0.00360`.

Watch the thinking count: on "thinking" models it's often bigger than the visible answer, and it's billed at the output rate.

### 5.6 [ai.py](src/aicommit/ai.py): the AI part

**System prompts**: `COMMIT_SYSTEM_PROMPT` and `PR_SYSTEM_PROMPT` share `_OUTPUT_RULES` (the JSON keys, the allowed types and what each means, title style, "don't invent things", "only JSON"). They differ in what `summary` should contain: commit-body bullets vs. a GitHub-markdown PR body with `## Summary`, `## Changes`, `## Testing`. **To change the writing style, edit these strings.**

**`build_prompt(...)`** / **`build_pr_prompt(...)`**: the user prompts. Ordinary string joining of branch, recent commits (or the branch's commits for PRs), your `--hint`, a forced `--type`, the file list, and the diff.

**`truncate_diff(diff, max_chars)`**: shrinks a diff fairly. It splits it per file (`diff --git` boundaries), gives each file an equal share (min 400 chars), and marks cuts with `... [N chars truncated]`. A single huge file can't push every other file out of view.

**`fit_prompt(build, raw_diff, system, max_tokens, count_tokens) -> FittedPrompt`**: **context-limit handling.** Limits are in tokens but truncation works in characters, so:
1. Start with a guess of `max_tokens × 4` characters and build the prompt.
2. If the text is obviously small (fits even at a pessimistic 2 chars/token), skip counting. That saves a round trip.
3. Otherwise ask the API for the exact count (`count_tokens`, a free call).
4. If it's over, shrink the character budget by the overshoot ratio (×0.9 margin) and repeat, up to 4 rounds.

It returns the prompt, its token count, whether that count was exact or estimated, and whether the diff was truncated. The CLI prints this as `prompt: 3,120 tokens (limit 16,000)`.

**`class LLM(Protocol)`**: what the rest of the code needs from a model: the attributes `provider`, `model` and `price_env`, plus two methods, `count_tokens(text)` and `stream(system, prompt, temperature)`. Tests pass a fake object with these, so no network is needed.

**`class AIError`**: raised for any model failure. It carries `usage`, the tokens already spent before the failure, so the CLI can still report what a failed provider cost.

**`class Gemini`**: the real implementation.
```python
config = types.GenerateContentConfig(
    system_instruction=system,               # the rules
    temperature=temperature,                 # randomness
    response_mime_type="application/json",   # reply must be JSON...
    response_json_schema=schema.json_schema()  # ...of exactly this shape
)
for chunk in client.models.generate_content_stream(model=..., contents=prompt, config=config):
    yield chunk.text, usage_from(chunk.usage_metadata)
```
- `generate_content_stream` returns an iterator. Each chunk carries the next bit of text, and usage metadata arrives along the way (we keep the latest report).
- API errors (bad key, quota, unknown model) become `AIError("Gemini API error (400): ...")`. Any other exception (e.g. no network) becomes `AIError("Gemini request failed: ...")`. Every failure has the same type, which is what lets the fallback catch it.
- The SDK is imported inside the methods so `aicommit --help` starts instantly.

**`class Groq`**: the fallback provider, with the same interface:
```python
client.chat.completions.create(
    model="qwen/qwen3.8-27b",
    messages=[{"role": "system", "content": system},   # OpenAI-style: the system prompt
              {"role": "user", "content": prompt}],     # is just the first message
    temperature=temperature,
    response_format={"type": "json_schema", "json_schema": {
        "name": "generated_message",
        "strict": True,                                 # constrained decoding
        "schema": schema.portable_json_schema()}},
)
```
- **Strict mode** (constrained decoding, which can't produce off-schema JSON) is only available on some Groq models, listed in `GROQ_STRICT_MODELS`. Other models get best-effort mode (`strict: False`). Local validation catches anything that slips through either way.
- **No streaming:** Groq doesn't support streaming together with structured outputs, so `stream()` makes one normal request and yields the whole reply as a single chunk. Because the interface is the same, `generate()` doesn't need to know.
- **`count_tokens`:** Groq has no counting endpoint, so it estimates ~3 characters per token.
- **Usage:** Groq's `completion_tokens` *includes* reasoning tokens. `_groq_usage()` splits them out so thinking is shown separately, just like Gemini's.
- `groq.APIStatusError` / `groq.APIError` become `AIError`. The SDK itself already retries 429/5xx responses twice before giving up.

**`generate(llm, system, prompt, temperature, on_chunk, on_retry, max_attempts=3) -> Result`**: the **validate-and-retry loop**:
1. Stream the reply, passing each chunk to `on_chunk` (the CLI prints it dimmed) and collecting it.
2. Add this call's usage to the running total.
3. `schema.parse()` the full text. Valid → return `Result(message, usage, attempts)`.
4. Invalid or empty → call `on_retry`, then re-send the **original prompt plus the rejection reason** ("Your previous reply was rejected: ... Reply again with ONLY a JSON object matching the schema."). Telling the model *what* was wrong fixes it far more often than just asking again.
5. After `max_attempts` failures, raise `AIError`. The tool never prints or commits unvalidated output.

**`generate_with_fallback(llms, ..., on_start, on_fallback) -> Result`**: tries each provider in order with `generate()`.
- Success → return immediately. The `Result` records which `provider`/`model` answered, so usage and cost are priced correctly.
- `AIError` (API error, network failure, or 3 invalid replies) → call `on_fallback(failed, error, next)` and try the next provider. Validation retries happen *within* a provider first; switching providers is the last resort.
- If every provider fails, it raises one `AIError` listing each provider's error. With a single provider, the original error is raised unchanged.

### 5.7 [cli.py](src/aicommit/cli.py): the command

- **Output streams**: status, streaming text, usage and errors go to **stderr**; the message (or JSON) goes to **stdout**. So `aicommit --json > msg.json` captures pure JSON while you still see progress.
- **Colors** are off when the stream isn't a terminal or `NO_COLOR` is set.
- **`parse_args()`** defines the flags (see README) and rejects nonsensical combinations, e.g. `--pr` with `-y` (PR mode never commits) or `--base` without `--pr`.
- **`Streamer`** echoes chunks to stderr and tracks whether a line was left open, so retry/fallback warnings start on a fresh line. Streaming is on when stderr is a terminal, unless `--no-stream`.
- **`build_llms(config, provider, fallback)`** builds the ordered provider list: `--provider` first (default `gemini`), then the other. Providers without a key are skipped, and `--no-fallback` keeps only the first. Asking for `--provider groq` without `GROQ_API_KEY` is an error rather than a silent switch.
- **`main()`**:
  1. Collects files/diff/context for commit mode or PR mode and picks the matching system prompt. `build` is a small closure that turns a (possibly trimmed) diff into the full user prompt; `fit_prompt` calls it repeatedly while trimming.
  2. Builds the provider list (printed as `providers: gemini (...) -> groq (...)`) and fits the prompt to the token budget using the first provider's token counter.
  3. Generates with fallback (printing a warning, plus the failed provider's usage, when it switches), prints usage, then outputs JSON (`--json`), the PR markdown (`--pr`), or the commit message with the y/e/r/n prompt.
  4. "Regenerate" raises temperature by 0.3 (cap 1.5): with an identical prompt, more randomness is what produces a different answer.
  5. `GitError`, `ConfigError` and `AIError` are caught in one place and printed as `error: ...` without a traceback. Exit codes: 0 success, 1 error/aborted, 130 Ctrl+C.

### 5.8 [\_\_main\_\_.py](src/aicommit/__main__.py) and [\_\_init\_\_.py](src/aicommit/__init__.py)

`__init__.py` holds `__version__`; `__main__.py` makes `python -m aicommit` work.

### 5.9 Tests

- **[test_schema.py](tests/test_schema.py)**: valid JSON parses; each allowed type is accepted; fences and `feat:` title prefixes are cleaned; and a table of **invalid** replies (not JSON, a list, wrong type, empty/long/multi-line title, missing summary, extra key) are all rejected with the right reason.
- **[test_ai.py](tests/test_ai.py)**: a `FakeLLM` replays canned replies in 5-char chunks. Tests check that streaming delivers every chunk, that invalid → invalid → valid succeeds on attempt 3 **with the error fed back into the prompt**, that usage sums across retries, that it gives up after `max_attempts`, and that `fit_prompt` trims a code-heavy diff under the limit while keeping every file.
- **[test_ai.py](tests/test_ai.py)**, fallback: a Gemini API error falls back to Groq; three invalid Gemini replies fall back too, and still report Gemini's spent usage; Groq isn't called when Gemini succeeds; when all providers fail, every error is listed. A fake Groq SDK client checks the request shape (strict schema, system message) and the reasoning-token split.
- **[test_cli.py](tests/test_cli.py)**: provider ordering, `--provider`, `--no-fallback`, missing keys.
- **[test_usage.py](tests/test_usage.py)**: thinking tokens billed as output, model-suffix price fallback, env overrides, unknown models.

The real Gemini call isn't unit-tested (it needs a key and its output varies). That's what the one-week real-use check is for.

Run with: `pytest`

---

## 6. Design decisions

| Decision | Reason |
| --- | --- |
| JSON schema output instead of free text | Code can rely on the fields; no fragile text parsing. |
| One Pydantic model for both the API schema and validation | The thing we ask for and the thing we accept can't drift apart. |
| Validate locally even with API-enforced schema | Turns "usually valid" into "always valid or a clear error". |
| Retry with the error message | Models fix a specific complaint far more reliably than a blind retry. |
| Token budget checked before the call | Big diffs get trimmed instead of producing surprise bills or errors. |
| Streaming on by default | Immediate feedback; you can Ctrl+C a bad answer early. |
| Status on stderr, result on stdout | `--json` output can be piped into other tools. |
| `LLM` protocol + fake in tests | The retry/validation logic is tested without network or keys. |
| Groq as automatic fallback | A different company's infrastructure, so a Gemini outage or quota limit doesn't block your commit. |
| Fall back only after in-provider retries | Most invalid replies are fixed by one retry; switching provider is the last resort. |
| Only staged changes (commit) / `base...HEAD` (PR) | Exactly what will be committed / exactly what the PR shows. |
| Always ask before committing (unless `-y`) | AI output can be wrong; a human checks it. |
| Exclude lockfiles; per-file truncation | Saves tokens; big files can't hide other changes. |

---

## 7. Privacy and cost

- **Your diff is sent to Google**, and also to **Groq** if the fallback kicks in (only when `GROQ_API_KEY` is set). Don't use the tool on code you aren't allowed to share with a third-party service, or on diffs containing secrets.
- On the free AI Studio tier, Google may use the data to improve its products; paid tiers have different terms.
- The usage line after each call shows what that call cost on paid pricing. A typical commit is a few thousand input tokens, a fraction of a cent. `AICOMMIT_MAX_INPUT_TOKENS` caps the worst case.

---

## 8. Common changes

| I want to… | Change this |
| --- | --- |
| Change message style or rules | `COMMIT_SYSTEM_PROMPT` / `PR_SYSTEM_PROMPT` in [ai.py](src/aicommit/ai.py) |
| Allow more types (e.g. `docs`, `chore`) | `COMMIT_TYPES` and `CommitType` in [schema.py](src/aicommit/schema.py) |
| Add a JSON field | a new field on `GeneratedMessage`; update the prompts and rendering helpers |
| Use a different Gemini model | `AICOMMIT_MODEL=...` or `--model` |
| Send bigger or smaller diffs | `AICOMMIT_MAX_INPUT_TOKENS` |
| Fix cost estimates | `PRICES` in [usage.py](src/aicommit/usage.py), or `AICOMMIT_PRICE_INPUT`/`OUTPUT` |
| Ignore more generated files | `EXCLUDED_PATHS` in [git.py](src/aicommit/git.py) |
| Use a different Groq model | `AICOMMIT_GROQ_MODEL=...` or `--groq-model`; add it to `GROQ_STRICT_MODELS` if Groq supports strict mode for it |
| Try Groq first / disable fallback | `--provider groq` / `--no-fallback` |
| Add another AI provider | write a class with `provider`, `model`, `price_env`, `count_tokens()` and `stream()` like `Groq` in [ai.py](src/aicommit/ai.py), then register it in `build_llms()` in [cli.py](src/aicommit/cli.py) |

---

## 9. Troubleshooting

| Message | Meaning / fix |
| --- | --- |
| `No API key found` | Set `GEMINI_API_KEY` (see README setup). |
| `Gemini API error (400): API key not valid` | The key has a typo or was revoked; get a new one from AI Studio. |
| `Gemini API error (400)` mentioning the schema | The model doesn't support `response_json_schema`; try another model. |
| `Gemini API error (404)` | The model name doesn't exist; check `AICOMMIT_MODEL`. |
| `Gemini API error (429)` | Rate limit or quota hit; wait a minute or check your plan. |
| `attempt 1 rejected: ...; retrying...` | The reply failed validation; the tool is retrying with the error. Occasional is fine. |
| `Model output was invalid after 3 attempts` | Persistent failure; run with `--show-prompt` and check the model. Worth noting in your week of testing. |
| `diff truncated to fit` | The diff exceeded the token budget; raise `AICOMMIT_MAX_INPUT_TOKENS` or commit in smaller pieces. |
| `cost: unknown for <model>` | Model not in `PRICES`; set the price env vars the message names. |
| `gemini failed: ...` / `falling back to groq...` | Gemini errored or kept returning bad JSON; Groq answered instead. Nothing to do unless it happens every time. |
| `Groq API error (400)` mentioning `json_schema` / `response_format` | That Groq model doesn't support structured outputs; use `qwen/qwen3.8-27b` or `openai/gpt-oss-20b`/`120b`. |
| `All providers failed:` | Both failed; each provider's error is listed underneath. |
| `--provider groq needs GROQ_API_KEY to be set` | Add the Groq key (see README setup). |
| `No staged changes.` | Run `git add <files>` first, or use `aicommit -a`. |
| `Could not detect a base branch` | Pass `--base main` (or whatever your PR targets). |
| Want to see what the AI receives | `aicommit -d --show-prompt` |
