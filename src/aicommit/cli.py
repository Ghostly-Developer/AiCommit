"""Command-line entry point."""

from __future__ import annotations

import argparse
import os
import sys

from aicommit import __version__, ai, git, schema
from aicommit.config import DEFAULT_MODEL, ConfigError, load_config
from aicommit.usage import format_usage

# --- tiny colour helpers (no extra dependency) -----------------------------
# Status/progress goes to stderr so `--json` leaves stdout as pure JSON.

_NO_COLOR = os.getenv("NO_COLOR") is not None
_OUT_COLOR = sys.stdout.isatty() and not _NO_COLOR
_ERR_COLOR = sys.stderr.isatty() and not _NO_COLOR
if (_OUT_COLOR or _ERR_COLOR) and os.name == "nt":
    os.system("")  # enables ANSI escape processing in Windows consoles


def _c(code: str, text: str, enabled: bool) -> str:
    return f"\033[{code}m{text}\033[0m" if enabled else text


def bold(t: str) -> str: return _c("1", t, _OUT_COLOR)
def green(t: str) -> str: return _c("32", t, _OUT_COLOR)
def dim(t: str) -> str: return _c("2", t, _ERR_COLOR)
def yellow(t: str) -> str: return _c("33", t, _ERR_COLOR)
def red(t: str) -> str: return _c("31", t, _ERR_COLOR)


def status(*args: object) -> None:
    print(*args, file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="aicommit",
        description="Generate a commit message or PR description for your changes with Google Gemini. "
        "The model returns JSON {title, summary, type}, validated against a schema.",
    )
    p.add_argument("--pr", action="store_true",
                   help="write a pull request description for the current branch instead of a commit")
    p.add_argument("--base", metavar="BRANCH",
                   help="with --pr: branch the PR targets (default: origin's default branch, or main/master)")
    p.add_argument("--json", action="store_true",
                   help="print the validated JSON to stdout and exit (never commits)")
    p.add_argument("-a", "--all", action="store_true",
                   help="stage all modified/deleted tracked files first (like `git commit -a`)")
    p.add_argument("-y", "--yes", action="store_true",
                   help="commit immediately without asking for confirmation")
    p.add_argument("-d", "--dry-run", action="store_true",
                   help="print the message only; don't commit")
    p.add_argument("-m", "--hint", metavar="TEXT",
                   help="extra context for the AI, e.g. the reason for the change")
    p.add_argument("-t", "--type", dest="commit_type", choices=schema.COMMIT_TYPES,
                   help="force the change type")
    p.add_argument("-1", "--one-line", action="store_true",
                   help="commit with the subject line only, no body")
    p.add_argument("--no-stream", action="store_true",
                   help="don't show the response as it streams in")
    p.add_argument("--model", help=f"Gemini model to use (default: $AICOMMIT_MODEL or {DEFAULT_MODEL})")
    p.add_argument("--show-prompt", action="store_true",
                   help="print the prompt sent to the model (for debugging)")
    p.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    args = p.parse_args(argv)

    if args.base and not args.pr:
        p.error("--base only applies with --pr")
    if args.pr and (args.all or args.yes or args.one_line):
        p.error("--pr doesn't create a commit, so it can't be combined with -a, -y or -1")
    return args


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


def _streamer(enabled: bool):
    """Return an on_chunk callback that echoes streamed text to stderr, or None."""
    if not enabled:
        return None

    def on_chunk(text: str) -> None:
        sys.stderr.write(dim(text))
        sys.stderr.flush()

    return on_chunk


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        git.ensure_repo()
        config = load_config(args.model)

        if args.pr:
            base = args.base or git.default_base_branch()
            git.ensure_ref(base)
            files = git.branch_files(base)
            if not files:
                status(yellow(f"No changes between {base} and HEAD."))
                return 1
            raw_diff = git.branch_diff(base)
            commits, branch = git.branch_commits(base), git.current_branch()
            system = ai.PR_SYSTEM_PROMPT

            def build(diff: str) -> str:
                return ai.build_pr_prompt(diff, files, commits, base, branch, args.hint, args.commit_type)
        else:
            if args.all:
                git.stage_tracked_changes()
            files = git.staged_files()
            if not files:
                status(yellow("No staged changes.") + " Stage files with `git add` or use `aicommit -a`.")
                return 1
            raw_diff = git.staged_diff()
            branch, recent = git.current_branch(), git.recent_subjects()
            system = ai.COMMIT_SYSTEM_PROMPT

            def build(diff: str) -> str:
                return ai.build_prompt(diff, files, branch, recent, args.hint, args.commit_type)

        llm = ai.Gemini(config.api_key, config.model)

        # Context budget: make sure the request fits before paying for it.
        fitted = ai.fit_prompt(build, raw_diff, system, config.max_input_tokens, llm.count_tokens)
        approx = "" if fitted.exact else "~"
        note = f"prompt: {approx}{fitted.tokens:,} tokens (limit {config.max_input_tokens:,})"
        if fitted.truncated:
            note += yellow(" | diff truncated to fit")
        status(dim(note))

        if args.show_prompt:
            status(dim("----- system -----"))
            status(system)
            status(dim("----- prompt -----"))
            status(fitted.text)
            status(dim("------------------"))

        stream = not args.no_stream and sys.stderr.isatty()
        temperature = 0.4
        while True:
            status(dim(f"Generating {'PR description' if args.pr else 'commit message'} with {config.model}..."))
            result = ai.generate(
                llm, system, fitted.text, temperature,
                on_chunk=_streamer(stream),
                on_retry=lambda n, err: status(
                    ("\n" if stream else "") + yellow(f"attempt {n} rejected: {err}; retrying...")
                ),
            )
            if stream:
                status()
            status(dim(format_usage(result.usage, config.model)))
            msg = result.message

            if args.json:
                print(msg.model_dump_json(indent=2))
                return 0

            print()
            print(bold(msg.as_pr_markdown() if args.pr else msg.as_commit_message(args.one_line)))
            print()

            if args.pr or args.dry_run:
                return 0

            action = "y" if args.yes else ask_action()
            commit_text = msg.as_commit_message(args.one_line)
            if action == "y":
                git.commit(commit_text)
                return 0
            if action == "e":
                git.commit(commit_text, edit=True)
                return 0
            if action == "r":
                # A bit more randomness so regenerations actually differ.
                temperature = min(temperature + 0.3, 1.5)
                continue
            status(dim("Aborted. Nothing was committed."))
            return 1

    except (git.GitError, ConfigError, ai.AIError) as e:
        status(red("error: ") + str(e))
        return 1
    except KeyboardInterrupt:
        status("\n" + dim("Interrupted."))
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
