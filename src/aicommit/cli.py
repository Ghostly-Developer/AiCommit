"""Command-line entry point."""

from __future__ import annotations

import argparse
import os
import sys

from aicommit import __version__, ai, git
from aicommit.config import ConfigError, load_config

# --- tiny colour helpers (no extra dependency) -----------------------------

_USE_COLOR = sys.stdout.isatty() and os.getenv("NO_COLOR") is None
if _USE_COLOR and os.name == "nt":
    os.system("")  # enables ANSI escape processing in Windows consoles


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def dim(t: str) -> str: return _c("2", t)
def bold(t: str) -> str: return _c("1", t)
def green(t: str) -> str: return _c("32", t)
def yellow(t: str) -> str: return _c("33", t)
def red(t: str) -> str: return _c("31", t)


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="aicommit",
        description="Generate a commit message for your staged changes with Google Gemini.",
    )
    p.add_argument("-a", "--all", action="store_true",
                   help="stage all modified/deleted tracked files first (like `git commit -a`)")
    p.add_argument("-y", "--yes", action="store_true",
                   help="commit immediately without asking for confirmation")
    p.add_argument("-d", "--dry-run", action="store_true",
                   help="print the message only; don't commit")
    p.add_argument("-m", "--hint", metavar="TEXT",
                   help="extra context for the AI, e.g. the reason for the change")
    p.add_argument("-t", "--type", dest="commit_type",
                   choices=["feat", "fix", "docs", "style", "refactor", "perf",
                            "test", "build", "ci", "chore", "revert"],
                   help="force a Conventional Commit type")
    p.add_argument("-1", "--one-line", action="store_true",
                   help="subject line only, no body")
    p.add_argument("--model", help="Gemini model to use (default: $AICOMMIT_MODEL or gemini-2.5-flash)")
    p.add_argument("--show-prompt", action="store_true",
                   help="print the prompt sent to the model (for debugging)")
    p.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    return p.parse_args(argv)


def ask_action() -> str:
    while True:
        try:
            choice = input(
                f"{bold('Commit with this message?')} "
                f"[{green('y')}]es / [e]dit / [r]egenerate / [n]o: "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return "n"
        if choice in ("", "y", "yes"):
            return "y"
        if choice in ("e", "edit"):
            return "e"
        if choice in ("r", "regen", "regenerate"):
            return "r"
        if choice in ("n", "no", "q", "quit"):
            return "n"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        git.ensure_repo()
        config = load_config(args.model)

        if args.all:
            git.stage_tracked_changes()

        files = git.staged_files()
        if not files:
            eprint(yellow("No staged changes.") + " Stage files with `git add` or use `aicommit -a`.")
            return 1

        raw_diff = git.staged_diff()
        diff = ai.truncate_diff(raw_diff, config.max_diff_chars)
        if not diff.strip():
            # Only excluded files (e.g. lockfiles) changed; the file list still helps.
            diff = "(diff omitted: only lockfiles / generated files changed)"

        prompt = ai.build_prompt(
            diff=diff,
            files=files,
            branch=git.current_branch(),
            recent=git.recent_subjects(),
            hint=args.hint,
            commit_type=args.commit_type,
            one_line=args.one_line,
        )
        if args.show_prompt:
            print(dim("----- prompt -----"))
            print(prompt)
            print(dim("------------------"))

        temperature = 0.4
        while True:
            print(dim(f"Generating commit message with {config.model}..."))
            message = ai.generate_message(config.api_key, config.model, prompt, temperature)

            print()
            print(bold(message))
            print()

            if args.dry_run:
                return 0

            action = "y" if args.yes else ask_action()
            if action == "y":
                git.commit(message)
                return 0
            if action == "e":
                git.commit(message, edit=True)
                return 0
            if action == "r":
                # A bit more randomness so regenerations actually differ.
                temperature = min(temperature + 0.3, 1.5)
                continue
            print(dim("Aborted. Nothing was committed."))
            return 1

    except (git.GitError, ConfigError, ai.AIError) as e:
        eprint(red("error: ") + str(e))
        return 1
    except KeyboardInterrupt:
        eprint("\n" + dim("Interrupted."))
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
