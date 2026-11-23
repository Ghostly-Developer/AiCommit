"""Thin wrappers around the git CLI."""

from __future__ import annotations

import os
import subprocess
import tempfile

# Generated/lock files add noise and tokens without helping the model.
EXCLUDED_PATHS = [
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "uv.lock",
    "Pipfile.lock",
    "Cargo.lock",
    "go.sum",
    "*.min.js",
    "*.min.css",
]


class GitError(Exception):
    pass


def _run(args: list[str], input: str | None = None, check: bool = True) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            input=input,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        raise GitError("git executable not found on PATH")
    if check and result.returncode != 0:
        raise GitError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def ensure_repo() -> None:
    try:
        _run(["rev-parse", "--is-inside-work-tree"])
    except GitError:
        raise GitError("Not inside a git repository")


def stage_tracked_changes() -> None:
    """Equivalent of `git commit -a`: stage modifications/deletions of tracked files."""
    _run(["add", "-u"])


def staged_files() -> str:
    return _run(["diff", "--cached", "--name-status"]).strip()


def _excludes() -> list[str]:
    return [f":(exclude,glob)**/{p}" for p in EXCLUDED_PATHS]


def staged_diff() -> str:
    return _run(["diff", "--cached", "--no-color", "--no-ext-diff", "--", ".", *_excludes()])


# --- pull request helpers ----------------------------------------------------

def _ref_exists(ref: str) -> bool:
    return bool(_run(["rev-parse", "--verify", "--quiet", ref], check=False).strip())


def default_base_branch() -> str:
    """Best guess at the branch a PR targets: origin's default branch, else main/master."""
    origin_head = _run(["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], check=False).strip()
    if origin_head:
        return origin_head
    for candidate in ("origin/main", "origin/master", "main", "master"):
        if _ref_exists(candidate):
            return candidate
    raise GitError("Could not detect a base branch; pass one with --base")


def ensure_ref(ref: str) -> None:
    if not _ref_exists(ref):
        raise GitError(f"Unknown branch or ref: {ref}")


def branch_files(base: str) -> str:
    # `base...HEAD` = changes on this branch since it forked from base.
    return _run(["diff", "--name-status", f"{base}...HEAD"]).strip()


def branch_diff(base: str) -> str:
    return _run(["diff", "--no-color", "--no-ext-diff", f"{base}...HEAD", "--", ".", *_excludes()])


def branch_commits(base: str) -> list[str]:
    out = _run(["log", "--reverse", "--pretty=format:%s", f"{base}..HEAD"])
    return [line for line in out.splitlines() if line.strip()]


def recent_subjects(n: int = 10) -> list[str]:
    # Fails in a repo with no commits yet; that's fine.
    out = _run(["log", f"-n{n}", "--pretty=format:%s"], check=False)
    return [line for line in out.splitlines() if line.strip()]


def current_branch() -> str:
    return _run(["rev-parse", "--abbrev-ref", "HEAD"], check=False).strip()


def commit(message: str, edit: bool = False) -> None:
    """Create a commit. With edit=True, git opens its configured editor first."""
    fd, path = tempfile.mkstemp(prefix="aicommit-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(message.rstrip() + "\n")
        args = ["git", "commit", "-F", path]
        if edit:
            args.append("-e")
        # Inherit stdio so the editor and hook output reach the terminal.
        result = subprocess.run(args)
        if result.returncode != 0:
            raise GitError("git commit failed (see output above)")
    finally:
        os.unlink(path)
