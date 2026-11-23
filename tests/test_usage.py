from types import SimpleNamespace

from aicommit.usage import Usage, estimate_cost, format_usage


def test_from_metadata_handles_missing_fields():
    u = Usage.from_metadata(SimpleNamespace(prompt_token_count=10, candidates_token_count=None,
                                            thoughts_token_count=3))
    assert (u.input_tokens, u.output_tokens, u.thinking_tokens, u.calls) == (10, 0, 3, 1)


def test_cost_bills_thinking_as_output(monkeypatch):
    monkeypatch.delenv("AICOMMIT_PRICE_INPUT", raising=False)
    monkeypatch.delenv("AICOMMIT_PRICE_OUTPUT", raising=False)
    u = Usage(input_tokens=1_000_000, output_tokens=500_000, thinking_tokens=500_000)
    assert estimate_cost(u, "gemini-2.5-flash") == 0.30 + 2.50
    # Model versions with a suffix fall back to the base model's price.
    assert estimate_cost(u, "gemini-2.5-flash-001") == 0.30 + 2.50


def test_env_price_override_and_unknown_model(monkeypatch):
    u = Usage(input_tokens=1_000_000)
    monkeypatch.delenv("AICOMMIT_PRICE_INPUT", raising=False)
    monkeypatch.delenv("AICOMMIT_PRICE_OUTPUT", raising=False)
    assert estimate_cost(u, "mystery-model") is None
    assert "unknown" in format_usage(u, "mystery-model")
    monkeypatch.setenv("AICOMMIT_PRICE_INPUT", "2")
    monkeypatch.setenv("AICOMMIT_PRICE_OUTPUT", "4")
    assert estimate_cost(u, "mystery-model") == 2.0


def test_format_usage():
    line = format_usage(Usage(1234, 56, 7, calls=2), "gemini-2.5-flash")
    assert "1,234 in" in line and "56 out" in line and "7 thinking" in line
    assert "2 calls" in line and "$" in line
