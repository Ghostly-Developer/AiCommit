"""Configuration loading: environment variables and .env files."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_MODEL = "gemini-3.8-flash"
# Groq fallback: a model with strict (constrained) JSON-schema output.
DEFAULT_GROQ_MODEL = "qwen/qwen3.8-27b"
# Cap on input tokens per request (system prompt + diff). A cost guard, well under the
# model's real context window; large diffs are trimmed to fit.
DEFAULT_MAX_INPUT_TOKENS = 16_000

# Global, per-user config file so the key works in every repository.
USER_ENV_FILE = Path.home() / ".aicommit.env"


class ConfigError(Exception):
    pass


@dataclass
class Config:
    gemini_api_key: str | None
    model: str                   # Gemini model
    groq_api_key: str | None
    groq_model: str
    max_input_tokens: int


def load_config(model_override: str | None = None, groq_model_override: str | None = None) -> Config:
    # Precedence: real env vars > ./.env (current dir) > ~/.aicommit.env
    load_dotenv(Path.cwd() / ".env", override=False)
    load_dotenv(USER_ENV_FILE, override=False)

    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    groq_key = os.getenv("GROQ_API_KEY")
    if not gemini_key and not groq_key:
        raise ConfigError(
            "No API key found. Get a Gemini key at https://aistudio.google.com/apikey\n"
            "(and optionally a Groq key at https://console.groq.com/keys as a fallback), then set:\n"
            "  - environment variables GEMINI_API_KEY / GROQ_API_KEY, or\n"
            f"  - lines 'GEMINI_API_KEY=...' / 'GROQ_API_KEY=...' in {USER_ENV_FILE}, or\n"
            "  - a .env file in the current directory"
        )

    raw = os.getenv("AICOMMIT_MAX_INPUT_TOKENS", str(DEFAULT_MAX_INPUT_TOKENS))
    try:
        max_input_tokens = int(raw)
    except ValueError:
        raise ConfigError(f"AICOMMIT_MAX_INPUT_TOKENS must be an integer, got {raw!r}")
    if max_input_tokens < 1_000:
        raise ConfigError("AICOMMIT_MAX_INPUT_TOKENS must be at least 1000")

    return Config(
        gemini_api_key=gemini_key,
        model=model_override or os.getenv("AICOMMIT_MODEL") or DEFAULT_MODEL,
        groq_api_key=groq_key,
        groq_model=groq_model_override or os.getenv("AICOMMIT_GROQ_MODEL") or DEFAULT_GROQ_MODEL,
        max_input_tokens=max_input_tokens,
    )
