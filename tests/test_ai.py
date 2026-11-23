import json

import pytest

from aicommit import ai
from aicommit.ai import build_pr_prompt, build_prompt, fit_prompt, truncate_diff
from aicommit.usage import Usage

VALID = json.dumps({"title": "add retry to upload", "summary": "- retry on 503", "type": "feat"})


class FakeLLM:
    """Replays canned replies, streamed in small chunks, and records the prompts it got."""

    price_env = "AICOMMIT_PRICE"

    def __init__(self, replies, tokens_per_char=0.25, provider="fake", model="fake-model",
                 max_input_tokens=16_000):
        self.max_input_tokens = max_input_tokens
        self.replies = list(replies)  # str = reply text, Exception = raised by the API call
        self.prompts: list[str] = []
        self.tokens_per_char = tokens_per_char
        self.provider, self.model = provider, model

    def count_tokens(self, text: str) -> int:
        return int(len(text) * self.tokens_per_char)

    def stream(self, system, prompt, temperature):
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
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


# --- provider fallback --------------------------------------------------------

def test_fallback_on_api_error():
    gemini = FakeLLM([ai.AIError("Gemini API error (429): quota")], provider="gemini")
    groq = FakeLLM([VALID], provider="groq", model="openai/gpt-oss-20b")
    events = []
    result = ai.generate_with_fallback(
        [gemini, groq], "sys", "prompt",
        on_start=lambda llm: events.append(("start", llm.provider)),
        on_fallback=lambda f, e, n: events.append(("fallback", f.provider, n.provider)),
    )
    assert result.provider == "groq" and result.model == "openai/gpt-oss-20b"
    assert result.message.type == "feat"
    assert events == [("start", "gemini"), ("fallback", "gemini", "groq"), ("start", "groq")]


def test_fallback_after_repeated_invalid_json_reports_spent_usage():
    gemini = FakeLLM(["nope", "nope", "nope"], provider="gemini")
    groq = FakeLLM([VALID], provider="groq")
    failed = []
    result = ai.generate_with_fallback([gemini, groq], "sys", "prompt",
                                       on_fallback=lambda f, e, n: failed.append(e))
    assert result.provider == "groq"
    assert failed[0].usage.calls == 3  # the tokens Gemini burned are still reported


def test_no_fallback_when_primary_succeeds():
    groq = FakeLLM([], provider="groq")
    result = ai.generate_with_fallback([FakeLLM([VALID], provider="gemini"), groq], "sys", "p")
    assert result.provider == "gemini" and groq.prompts == []


def test_all_providers_failing_lists_every_error():
    llms = [FakeLLM([ai.AIError("boom")], provider="gemini"),
            FakeLLM([ai.AIError("bang")], provider="groq")]
    with pytest.raises(ai.AIError, match="(?s)All providers failed.*gemini: boom.*groq: bang"):
        ai.generate_with_fallback(llms, "sys", "p")


def test_single_provider_error_is_raised_unchanged():
    with pytest.raises(ai.AIError, match="^boom$"):
        ai.generate_with_fallback([FakeLLM([ai.AIError("boom")])], "sys", "p")


# --- Groq request/response handling (fake SDK client) -------------------------

class _FakeGroqClient:
    def __init__(self, content):
        from types import SimpleNamespace as NS
        self.kwargs = None
        usage = NS(prompt_tokens=200, completion_tokens=90,
                   completion_tokens_details=NS(reasoning_tokens=60))
        self._response = NS(choices=[NS(message=NS(content=content))], usage=usage)
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kwargs):
        self.kwargs = kwargs
        return self._response


def test_groq_sends_strict_schema_and_splits_reasoning_tokens():
    client = _FakeGroqClient(VALID)
    llm = ai.Groq("key", "openai/gpt-oss-20b", client=client)
    result = ai.generate(llm, "SYS", "PROMPT")
    assert result.message.title == "add retry to upload"
    assert result.usage.input_tokens == 200
    assert result.usage.output_tokens == 30 and result.usage.thinking_tokens == 60
    fmt = client.kwargs["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert set(fmt["json_schema"]["schema"]["required"]) == {"title", "summary", "type"}
    assert client.kwargs["messages"][0] == {"role": "system", "content": "SYS"}


def test_groq_non_strict_model_uses_best_effort():
    client = _FakeGroqClient(VALID)
    ai.generate(ai.Groq("key", "some/other-model", client=client), "s", "p")
    assert client.kwargs["response_format"]["json_schema"]["strict"] is False


# --- per-provider token budgets / HTTP 413 ------------------------------------

def test_each_provider_gets_a_prompt_for_its_own_budget():
    gemini = FakeLLM([ai.AIError("down")], provider="gemini", max_input_tokens=16_000)
    groq = FakeLLM([VALID], provider="groq", max_input_tokens=6_000)
    asked = []

    def prompt_for(llm, budget):
        asked.append((llm.provider, budget))
        return f"prompt<{budget}>"

    ai.generate_with_fallback([gemini, groq], "sys", prompt_for)
    assert asked == [("gemini", 16_000), ("groq", 6_000)]
    assert groq.prompts == ["prompt<6000>"]


def test_request_too_large_shrinks_prompt_and_retries_once():
    too_big = ai.RequestTooLarge("413", limit=7000, requested=10301)
    groq = FakeLLM([too_big, VALID], provider="groq", max_input_tokens=16_000)
    shrinks = []
    result = ai.generate_with_fallback([groq], "sys", lambda llm, b: f"p<{b}>",
                                       on_shrink=lambda llm, e, b: shrinks.append(b))
    assert result.provider == "groq"
    assert shrinks == [5600]                          # 80% of the reported limit
    assert groq.prompts == ["p<16000>", "p<5600>"]
    assert groq.max_input_tokens == 5600              # remembered for regenerations


def test_request_too_large_twice_gives_up():
    errs = [ai.RequestTooLarge("413", limit=7000), ai.RequestTooLarge("413", limit=7000)]
    with pytest.raises(ai.RequestTooLarge):
        ai.generate_with_fallback([FakeLLM(errs, max_input_tokens=16_000)], "s", lambda l, b: "p")


def test_request_too_large_with_fixed_prompt_is_not_retried():
    llm = FakeLLM([ai.RequestTooLarge("413", limit=7000)])
    with pytest.raises(ai.RequestTooLarge):
        ai.generate_with_fallback([llm], "s", "fixed prompt")


def test_groq_413_is_parsed_into_request_too_large():
    import groq
    import httpx

    class Client413:
        def __init__(self):
            from types import SimpleNamespace as NS
            self.chat = NS(completions=NS(create=self._create))

        def _create(self, **kw):
            resp = httpx.Response(413, request=httpx.Request("POST", "https://api.groq.com"))
            raise groq.APIStatusError(
                "Request too large for model `x` on input tokens per minute (ITPM): "
                "Limit 7000, Requested 10301, please reduce your message size",
                response=resp, body=None)

    llm = ai.Groq("key", "openai/gpt-oss-20b", client=Client413())
    with pytest.raises(ai.RequestTooLarge) as info:
        ai.generate(llm, "s", "p")
    assert (info.value.limit, info.value.requested) == (7000, 10301)
    assert "limit 7,000" in str(info.value)


def test_groq_token_estimate_is_pessimistic():
    assert ai.Groq("k", "m", client=object()).count_tokens("x" * 1000) == 400
