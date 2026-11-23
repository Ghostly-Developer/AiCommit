"""Prompt construction, token budgeting and the Gemini API call."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterator, Protocol

from aicommit import schema
from aicommit.usage import Usage

_TYPES = ", ".join(schema.COMMIT_TYPES)

_OUTPUT_RULES = f"""\
Output format: a single JSON object with exactly these keys:
- "type": one of {_TYPES} (feat = new behaviour, fix = bug fix, refactor = anything else
  that changes code without changing behaviour, including docs, tests and tooling)
- "title": imperative mood ("add", not "added"), lowercase start, no trailing period,
  at most {schema.TITLE_MAX} characters, and WITHOUT the type prefix
- "summary": see below
Describe only what the diff actually shows. Do not invent motivation or ticket numbers.
Output ONLY the JSON object, with no markdown fences and no commentary.
"""

COMMIT_SYSTEM_PROMPT = f"""\
You are an expert software engineer who writes excellent git commit messages.
Given a staged git diff, describe it as ONE commit following Conventional Commits.

{_OUTPUT_RULES}
"summary" is the commit body: 1-5 "- " bullet points explaining WHAT changed and WHY,
each line wrapped at 72 characters. For a trivial change, a single bullet is enough.
"""

PR_SYSTEM_PROMPT = f"""\
You are an expert software engineer who writes clear pull request descriptions.
Given the commits and the combined diff of a branch, describe the whole branch as ONE PR.

{_OUTPUT_RULES}
"summary" is the PR body in GitHub markdown, with these sections:
"## Summary" (1-3 sentences), "## Changes" ("- " bullets grouped by area), and
"## Testing" (how the change was or can be verified, based only on what the diff shows,
e.g. added tests; write "Not covered by this diff." if nothing is shown).
Pick the "type" that best describes the branch overall.
"""

DIFF_OMITTED = "(diff omitted: only lockfiles / generated files changed)"


def build_prompt(
    diff: str,
    files: str,
    branch: str = "",
    recent: list[str] | None = None,
    hint: str | None = None,
    commit_type: str | None = None,
) -> str:
    """User prompt for a commit message."""
    parts: list[str] = []
    if branch and branch != "HEAD":
        parts.append(f"Current branch: {branch}")
    if recent:
        parts.append(
            "Recent commit subjects in this repo (match their tone where sensible):\n"
            + "\n".join(f"  {s}" for s in recent)
        )
    if hint:
        parts.append(f"Extra context from the author: {hint}")
    if commit_type:
        parts.append(f'Use "type": "{commit_type}".')
    parts.append(f"Changed files (git name-status):\n{files}")
    parts.append(f"Staged diff:\n{diff}")
    return "\n\n".join(parts)


def build_pr_prompt(
    diff: str,
    files: str,
    commits: list[str],
    base: str,
    branch: str = "",
    hint: str | None = None,
    commit_type: str | None = None,
) -> str:
    """User prompt for a pull request description."""
    parts = [f"Pull request: {branch or 'HEAD'} -> {base}"]
    if commits:
        parts.append("Commits on this branch (oldest first):\n" + "\n".join(f"  {c}" for c in commits))
    if hint:
        parts.append(f"Extra context from the author: {hint}")
    if commit_type:
        parts.append(f'Use "type": "{commit_type}".')
    parts.append(f"Changed files (git name-status):\n{files}")
    parts.append(f"Combined diff:\n{diff}")
    return "\n\n".join(parts)


def truncate_diff(diff: str, max_chars: int) -> str:
    """Shrink a diff to roughly max_chars, giving every file a fair share.

    Splitting per file (rather than chopping the end) keeps every changed file
    visible to the model, even when one file has a huge diff.
    """
    if len(diff) <= max_chars:
        return diff

    chunks = re.split(r"(?m)^(?=diff --git )", diff)
    chunks = [c for c in chunks if c]
    budget = max(max_chars // max(len(chunks), 1), 400)

    out: list[str] = []
    for chunk in chunks:
        if len(chunk) > budget:
            omitted = len(chunk) - budget
            chunk = chunk[:budget].rstrip() + f"\n... [{omitted} chars of this file's diff truncated]\n"
        out.append(chunk)
    result = "".join(out)

    if len(result) > max_chars:
        result = result[:max_chars] + "\n... [remaining diff truncated]\n"
    return result


@dataclass
class FittedPrompt:
    text: str
    tokens: int          # input tokens (system + user prompt)
    exact: bool          # True if counted by the API, False if estimated from length
    truncated: bool      # True if the diff had to be cut to fit


def fit_prompt(
    build: Callable[[str], str],
    raw_diff: str,
    system: str,
    max_tokens: int,
    count_tokens: Callable[[str], int],
    max_rounds: int = 4,
) -> FittedPrompt:
    """Build a prompt whose token count fits within max_tokens, trimming the diff if needed.

    Tokens depend on the text (code tokenises worse than prose), so we start from a
    ~4 chars/token guess, ask the API for the real count, and shrink proportionally.
    The API count is skipped when the text is obviously small, to save a round trip.
    """
    chars = max_tokens * 4
    prompt, tokens, exact, truncated = "", 0, False, False
    for round_no in range(max_rounds):
        diff = truncate_diff(raw_diff, chars)
        truncated = truncated or diff != raw_diff
        prompt = build(diff if diff.strip() else DIFF_OMITTED)
        full = system + "\n\n" + prompt
        if round_no == 0 and len(full) // 2 <= max_tokens:
            # Even at a pessimistic 2 chars/token this fits; don't bother counting.
            return FittedPrompt(prompt, len(full) // 4, False, truncated)
        tokens, exact = count_tokens(full), True
        if tokens <= max_tokens:
            break
        chars = max(int(chars * max_tokens / tokens * 0.9), 1_000)
    return FittedPrompt(prompt, tokens, exact, truncated)


# --- LLM client --------------------------------------------------------------


class AIError(Exception):
    pass


class LLM(Protocol):
    """What the rest of the code needs from a model. Lets tests plug in a fake."""

    model: str

    def count_tokens(self, text: str) -> int: ...

    def stream(self, system: str, prompt: str, temperature: float) -> Iterator[tuple[str, Usage | None]]:
        """Yield (text_chunk, usage). usage is set only on the chunk that reports it."""
        ...


class Gemini:
    def __init__(self, api_key: str, model: str):
        # Imported lazily so `--help` stays fast.
        from google import genai

        self.model = model
        self._client = genai.Client(api_key=api_key)

    def count_tokens(self, text: str) -> int:
        from google.genai import errors

        try:
            return self._client.models.count_tokens(model=self.model, contents=text).total_tokens or 0
        except errors.APIError:
            return len(text) // 4  # counting is best-effort; the real call will surface errors

    def stream(self, system: str, prompt: str, temperature: float) -> Iterator[tuple[str, Usage | None]]:
        from google.genai import errors, types

        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            # Structured output: the API constrains generation to this JSON Schema.
            response_mime_type="application/json",
            response_json_schema=schema.json_schema(),
        )
        try:
            for chunk in self._client.models.generate_content_stream(
                model=self.model, contents=prompt, config=config
            ):
                meta = chunk.usage_metadata
                yield (chunk.text or ""), (Usage.from_metadata(meta) if meta else None)
        except errors.APIError as e:
            raise AIError(f"Gemini API error ({e.code}): {e.message}") from e


@dataclass
class Result:
    message: schema.GeneratedMessage
    usage: Usage
    attempts: int


def generate(
    llm: LLM,
    system: str,
    prompt: str,
    temperature: float = 0.4,
    on_chunk: Callable[[str], None] | None = None,
    on_retry: Callable[[int, str], None] | None = None,
    max_attempts: int = 3,
) -> Result:
    """Call the model, validate its JSON against the schema, and retry with feedback if invalid."""
    total = Usage()
    current_prompt = prompt
    last_error = ""
    for attempt in range(1, max_attempts + 1):
        text_parts: list[str] = []
        usage: Usage | None = None
        for text, chunk_usage in llm.stream(system, current_prompt, temperature):
            if text:
                text_parts.append(text)
                if on_chunk:
                    on_chunk(text)
            if chunk_usage is not None:
                usage = chunk_usage  # each report is cumulative; keep the latest
        total = total + (usage or Usage(calls=1))

        raw = "".join(text_parts)
        if not raw.strip():
            last_error = "empty response (it may have been blocked by safety filters)"
        else:
            try:
                return Result(schema.parse(raw), total, attempt)
            except schema.OutputInvalid as e:
                last_error = str(e)

        if attempt < max_attempts:
            if on_retry:
                on_retry(attempt, last_error)
            current_prompt = (
                f"{prompt}\n\nYour previous reply was rejected: {last_error}.\n"
                "Reply again with ONLY a JSON object matching the schema."
            )

    raise AIError(f"Model output was invalid after {max_attempts} attempts: {last_error}")
