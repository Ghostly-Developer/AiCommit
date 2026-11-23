"""Command-line entry point."""

from __future__ import annotations

import argparse
import os
import sys

from aicommit import __version__, ai, git, schema
from aicommit.config import DEFAULT_GROQ_MODEL, DEFAULT_MODEL, Config, ConfigError, load_config
from aicommit.usage import format_usage

PROVIDERS = ("gemini", "groq")

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
        description="Generate a commit message or PR description for your changes with Google Gemini "
        "(falling back to Groq if Gemini fails). "
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
    p.add_argument("--provider", choices=PROVIDERS,
                   help="provider to try first (default: gemini, falling back to groq)")
    p.add_argument("--no-fallback", action="store_true",
                   help="use only the first provider; don't fall back to the other one if it fails")
    p.add_argument("--model", help=f"Gemini model to use (default: $AICOMMIT_MODEL or {DEFAULT_MODEL})")
    p.add_argument("--groq-model",
                   help=f"Groq model to use (default: $AICOMMIT_GROQ_MODEL or {DEFAULT_GROQ_MODEL})")
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


class Streamer:
    """Echoes streamed text to stderr and remembers whether a line is left open."""

    def __init__(self, enabled: bool):
        self.enabled = enabled
        self._open_line = False

    def __call__(self, text: str) -> None:
        sys.stderr.write(dim(text))
        sys.stderr.flush()
        self._open_line = not text.endswith("\n")

    def end_line(self) -> None:
        if self._open_line:
            sys.stderr.write("\n")
            self._open_line = False

    @property
    def callback(self):
        return self if self.enabled else None


def build_llms(config: Config, provider: str | None, fallback: bool) -> list[ai.LLM]:
    """The providers to try, in order, limited to those with an API key."""
    makers = {
        "gemini": (config.gemini_api_key, "GEMINI_API_KEY",
                   lambda: ai.Gemini(config.gemini_api_key, config.model)),
        "groq": (config.groq_api_key, "GROQ_API_KEY",
                 lambda: ai.Groq(config.groq_api_key, config.groq_model)),
    }
    if provider and not makers[provider][0]:
        raise ConfigError(f"--provider {provider} needs {makers[provider][1]} to be set")
    order = [provider] if provider else []
    order += [p for p in PROVIDERS if p not in order]
    available = [p for p in order if makers[p][0]]
    if not fallback:
        available = available[:1]
    return [makers[p][2]() for p in available]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        git.ensure_repo()
        config = load_config(args.model, args.groq_model)

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

        llms = build_llms(config, args.provider, fallback=not args.no_fallback)
        status(dim("providers: " + " -> ".join(f"{m.provider} ({m.model})" for m in llms)))

        # Context budget: make sure the request fits before paying for it.
        fitted = ai.fit_prompt(build, raw_diff, system, config.max_input_tokens, llms[0].count_tokens)
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

        streamer = Streamer(not args.no_stream and sys.stderr.isatty())
        what = "PR description" if args.pr else "commit message"

        def on_start(llm: ai.LLM) -> None:
            status(dim(f"Generating {what} with {llm.provider} ({llm.model})..."))

        def on_retry(n: int, err: str) -> None:
            streamer.end_line()
            status(yellow(f"attempt {n} rejected: {err}; retrying..."))

        def on_fallback(failed: ai.LLM, err: ai.AIError, nxt: ai.LLM) -> None:
            streamer.end_line()
            status(yellow(f"{failed.provider} failed: {err}"))
            if err.usage and err.usage.calls:
                status(dim(f"{failed.provider} " + format_usage(err.usage, failed.model, failed.price_env)))
            status(yellow(f"falling back to {nxt.provider}..."))

        temperature = 0.4
        while True:
            result = ai.generate_with_fallback(
                llms, system, fitted.text, temperature,
                on_chunk=streamer.callback, on_retry=on_retry,
                on_start=on_start, on_fallback=on_fallback,
            )
            streamer.end_line()
            status(dim(format_usage(result.usage, result.model, result.price_env)))
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
