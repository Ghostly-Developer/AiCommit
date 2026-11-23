import json

import pytest

from aicommit import ai
from aicommit.ai import build_pr_prompt, build_prompt, fit_prompt, truncate_diff
from aicommit.usage import Usage

VALID = json.dumps({"title": "add retry to upload", "summary": "- retry on 503", "type": "feat"})


class FakeLLM:
    """Replays canned replies, streamed in small chunks, and records the prompts it got."""

    model = "fake"

    def __init__(self, replies, tokens_per_char=0.25):
        self.replies = list(replies)
        self.prompts: list[str] = []
        self.tokens_per_char = tokens_per_char

    def count_tokens(self, text: str) -> int:
        return int(len(text) * self.tokens_per_char)

    def stream(self, system, prompt, temperature):
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        for i in range(0, len(reply), 5):
            yield reply[i:i + 5], None
        yield "", Usage(input_tokens=100, output_tokens=20, thinking_tokens=5, calls=1)


# --- generate: streaming + structured output -------------------------------

def test_generate_streams_and_validates():
    chunks: list[str] = []
    result = ai.generate(FakeLLM([VALID]), "sys", "prompt", on_chunk=chunks.append)
    assert "".join(chunks) == VALID
    assert result.message.type == "feat"
    assert result.message.subject == "feat: add retry to upload"
    assert result.attempts == 1
    assert result.usage.input_tokens == 100 and result.usage.thinking_tokens == 5


def test_generate_retries_with_feedback_on_invalid_output():
    bad = json.dumps({"title": "x", "summary": "y", "type": "chore"})
    llm = FakeLLM(["not json at all", bad, VALID])
    retries: list[tuple[int, str]] = []
    result = ai.generate(llm, "sys", "prompt", on_retry=lambda n, e: retries.append((n, e)))
    assert result.attempts == 3
    assert result.usage.calls == 3 and result.usage.input_tokens == 300
    assert "not valid JSON" in retries[0][1]
    assert "type" in retries[1][1]
    # The rejection reason is fed back to the model on the next attempt.
    assert "rejected" in llm.prompts[1] and "not valid JSON" in llm.prompts[1]


def test_generate_gives_up_after_max_attempts():
    with pytest.raises(ai.AIError, match="invalid after 2 attempts"):
        ai.generate(FakeLLM(["{}", ""]), "sys", "prompt", max_attempts=2)


# --- token budget ---------------------------------------------------------------

def test_fit_prompt_small_diff_skips_counting():
    calls = []
    fitted = fit_prompt(lambda d: f"DIFF:{d}", "diff --git a/x b/x\n+hi\n", "sys", 1000,
                        lambda t: calls.append(t) or 0)
    assert not calls and not fitted.exact and not fitted.truncated


def test_fit_prompt_shrinks_until_under_limit():
    raw = "".join(f"diff --git a/f{i} b/f{i}\n" + "+code line\n" * 2000 for i in range(5))
    llm = FakeLLM([], tokens_per_char=0.5)  # code-heavy text: 2 chars per token
    fitted = fit_prompt(lambda d: d, raw, "sys", 4000, llm.count_tokens)
    assert fitted.exact and fitted.truncated
    assert fitted.tokens <= 4000
    assert all(f"a/f{i}" in fitted.text for i in range(5))


# --- prompt building / truncation -------------------------------------------

def test_truncate_diff_noop_when_small():
    d = "diff --git a/x b/x\n+hello\n"
    assert truncate_diff(d, 1000) == d


def test_truncate_diff_keeps_every_file():
    big = "diff --git a/big b/big\n" + "+x\n" * 5000
    small = "diff --git a/small b/small\n+tiny change\n"
    out = truncate_diff(big + small, 2000)
    assert "a/big" in out and "a/small" in out
    assert "+tiny change" in out
    assert len(out) < 2500


def test_build_prompt_includes_options():
    p = build_prompt("DIFF", "M\tfile.py", branch="feature/x",
                     recent=["feat: one"], hint="because", commit_type="fix")
    for s in ("DIFF", "file.py", "feature/x", "feat: one", "because", '"fix"'):
        assert s in p


def test_build_pr_prompt_includes_commits_and_base():
    p = build_pr_prompt("DIFF", "M\ta.py", ["feat: one", "fix: two"], "origin/main", "feature/x")
    for s in ("feature/x -> origin/main", "feat: one", "fix: two", "a.py", "DIFF"):
        assert s in p


def test_system_prompts_list_allowed_types():
    for prompt in (ai.COMMIT_SYSTEM_PROMPT, ai.PR_SYSTEM_PROMPT):
        assert "feat, fix, refactor" in prompt
