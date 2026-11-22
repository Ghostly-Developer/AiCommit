"""Configuration loading: environment variables and .env files."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_MAX_DIFF_CHARS = 20_000

# Global, per-user config file so the key works in every repository.
USER_ENV_FILE = Path.home() / ".aicommit.env"


class ConfigError(Exception):
    pass


@dataclass
class Config:
    api_key: str
    model: str
    max_diff_chars: int


def load_config(model_override: str | None = None) -> Config:
    # Precedence: real env vars > ./.env (current dir) > ~/.aicommit.env
    load_dotenv(Path.cwd() / ".env", override=False)
    load_dotenv(USER_ENV_FILE, override=False)

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ConfigError(
            "No API key found. Get one at https://aistudio.google.com/apikey and set it via:\n"
            "  - environment variable GEMINI_API_KEY, or\n"
            f"  - a line 'GEMINI_API_KEY=...' in {USER_ENV_FILE}, or\n"
            "  - a .env file in the current directory"
        )

    max_chars_raw = os.getenv("AICOMMIT_MAX_DIFF_CHARS", str(DEFAULT_MAX_DIFF_CHARS))
    try:
        max_diff_chars = int(max_chars_raw)
    except ValueError:
        raise ConfigError(f"AICOMMIT_MAX_DIFF_CHARS must be an integer, got {max_chars_raw!r}")

    return Config(
        api_key=api_key,
        model=model_override or os.getenv("AICOMMIT_MODEL") or DEFAULT_MODEL,
        max_diff_chars=max_diff_chars,
    )
