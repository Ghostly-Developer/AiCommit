"""The structured output contract: what the model must return, and how we check it."""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# Single source of truth for allowed types. Extend this tuple (and the Literal) to allow more.
COMMIT_TYPES = ("feat", "fix", "refactor")
CommitType = Literal["feat", "fix", "refactor"]

TITLE_MAX = 72

# Matches a leading "feat: " / "fix(api): " prefix the model may add to the title by habit.
_TYPE_PREFIX = re.compile(r"^\s*[a-z]+(\([^)]*\))?!?:\s*")


class GeneratedMessage(BaseModel):
    """JSON shape shared by commit messages and PR descriptions: {title, summary, type}."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(
        min_length=1,
        max_length=TITLE_MAX,
        description="Short imperative summary, lowercase start, no trailing period, "
        "WITHOUT the type prefix (e.g. 'add retry to upload client').",
    )
    summary: str = Field(
        min_length=1,
        description="What changed and why. Markdown '- ' bullet points.",
    )
    type: CommitType = Field(description="Kind of change: feat, fix or refactor.")

    @field_validator("title", mode="before")
    @classmethod
    def _normalise_title(cls, v: object) -> object:
        if not isinstance(v, str):
            return v
        v = _TYPE_PREFIX.sub("", v.strip()).rstrip(".").strip()
        if "\n" in v:
            raise ValueError("title must be a single line")
        return v

    @field_validator("summary", mode="before")
    @classmethod
    def _normalise_summary(cls, v: object) -> object:
        return re.sub(r"\n{3,}", "\n\n", v.strip()) if isinstance(v, str) else v

    # --- rendering -----------------------------------------------------------

    @property
    def subject(self) -> str:
        return f"{self.type}: {self.title}"

    def as_commit_message(self, one_line: bool = False) -> str:
        return self.subject if one_line else f"{self.subject}\n\n{self.summary}"

    def as_pr_markdown(self) -> str:
        return f"# {self.subject}\n\n{self.summary}"


def json_schema() -> dict:
    """JSON Schema sent to Gemini so it constrains its output to this shape."""
    return GeneratedMessage.model_json_schema()


# Keywords some providers' strict modes don't document. Dropping them only loosens what the
# API enforces; parse() still checks lengths locally, so nothing invalid gets through.
_NON_PORTABLE_KEYS = {"title", "minLength", "maxLength"}


def portable_json_schema() -> dict:
    """The same schema reduced to the core keywords strict modes support (used for Groq)."""

    def strip(node: object) -> object:
        if isinstance(node, dict):
            return {
                k: (strip(v) if k != "properties" else {name: strip(p) for name, p in v.items()})
                for k, v in node.items()
                if k not in _NON_PORTABLE_KEYS
            }
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node

    return strip(json_schema())  # type: ignore[return-value]


class OutputInvalid(Exception):
    """The model's reply wasn't valid JSON or didn't match the schema."""


def strip_fences(text: str) -> str:
    """Remove ```json fences some models wrap around JSON despite instructions."""
    text = text.strip()
    m = re.match(r"^```[\w-]*\n(.*?)\n?```$", text, re.DOTALL)
    return m.group(1).strip() if m else text


def parse(text: str) -> GeneratedMessage:
    """Parse and validate raw model output. Raises OutputInvalid with a readable reason."""
    raw = strip_fences(text)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise OutputInvalid(f"not valid JSON ({e.msg} at line {e.lineno} col {e.colno})") from e
    try:
        return GeneratedMessage.model_validate(data)
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}" for err in e.errors()
        )
        raise OutputInvalid(f"JSON does not match schema ({problems})") from e
