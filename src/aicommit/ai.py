"""Prompt construction and the Gemini API call."""

from __future__ import annotations

import re

SYSTEM_PROMPT = """\
You are an expert software engineer who writes excellent git commit messages.
Given a staged git diff, write ONE commit message following the Conventional Commits spec.

Rules:
- Subject line: `<type>(<optional scope>): <summary>`
  - type is one of: feat, fix, docs, style, refactor, perf, test, build, ci, chore, revert
  - summary in imperative mood ("add", not "added"), lowercase start, no trailing period
  - whole subject line at most 72 characters
- If the change is non-trivial, add a blank line then a short body (wrap at 72 chars)
  explaining WHAT changed and WHY, using "- " bullet points. Skip the body for trivial changes.
- Describe only what the diff actually shows. Do not invent motivation or ticket numbers.
- Output ONLY the raw commit message. No markdown code fences, no quotes, no commentary.
"""


def build_prompt(
    diff: str,
    files: str,
    branch: str = "",
    recent: list[str] | None = None,
    hint: str | None = None,
    commit_type: str | None = None,
    one_line: bool = False,
) -> str:
    parts: list[str] = []
    if branch and branch != "HEAD":
        parts.append(f"Current branch: {branch}")
    if recent:
        parts.append(
            "Recent commit subjects in this repo (match their style where sensible):\n"
            + "\n".join(f"  {s}" for s in recent)
        )
    if hint:
        parts.append(f"Extra context from the author: {hint}")
    if commit_type:
        parts.append(f"Use the commit type `{commit_type}`.")
    if one_line:
        parts.append("Write ONLY the subject line, no body.")
    parts.append(f"Changed files (git name-status):\n{files}")
    parts.append(f"Staged diff:\n{diff}")
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


def clean_message(text: str) -> str:
    """Strip code fences / stray quotes models sometimes add despite instructions."""
    text = text.strip()
    fence = re.match(r"^```[\w-]*\n(.*?)\n?```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'`":
        text = text[1:-1].strip()
    # Normalise excess blank lines.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


class AIError(Exception):
    pass


def generate_message(api_key: str, model: str, prompt: str, temperature: float = 0.4) -> str:
    # Imported lazily so `--help` stays fast.
    from google import genai
    from google.genai import errors, types

    client = genai.Client(api_key=api_key)
    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=temperature,
            ),
        )
    except errors.APIError as e:
        raise AIError(f"Gemini API error ({e.code}): {e.message}") from e

    text = response.text
    if not text or not text.strip():
        raise AIError("Model returned an empty response (it may have been blocked by safety filters)")
    return clean_message(text)
