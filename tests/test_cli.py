import pytest

from aicommit import ai
from aicommit.cli import build_llms
from aicommit.config import Config, ConfigError


def cfg(gemini="g-key", groq="q-key"):
    return Config(gemini, "gemini-x", groq, "openai/gpt-oss-20b", 16_000)


@pytest.fixture(autouse=True)
def fake_clients(monkeypatch):
    # Avoid constructing real SDK clients.
    monkeypatch.setattr(ai.Gemini, "__init__", lambda self, key, model, **kw: setattr(self, "model", model))
    monkeypatch.setattr(ai.Groq, "__init__", lambda self, key, model, **kw: setattr(self, "model", model))


def providers(llms):
    return [m.provider for m in llms]


def test_default_order_is_gemini_then_groq():
    assert providers(build_llms(cfg(), None, fallback=True)) == ["gemini", "groq"]


def test_provider_flag_changes_order():
    assert providers(build_llms(cfg(), "groq", fallback=True)) == ["groq", "gemini"]


def test_no_fallback_uses_only_first():
    assert providers(build_llms(cfg(), None, fallback=False)) == ["gemini"]


def test_missing_keys_are_skipped():
    assert providers(build_llms(cfg(groq=None), None, fallback=True)) == ["gemini"]
    assert providers(build_llms(cfg(gemini=None), None, fallback=True)) == ["groq"]


def test_explicit_provider_without_key_errors():
    with pytest.raises(ConfigError, match="GROQ_API_KEY"):
        build_llms(cfg(groq=None), "groq", fallback=True)


def test_each_provider_gets_its_own_budget(monkeypatch):
    monkeypatch.setattr(ai.Gemini, "__init__",
                        lambda self, key, model, max_input_tokens: setattr(self, "budget", max_input_tokens))
    monkeypatch.setattr(ai.Groq, "__init__",
                        lambda self, key, model, max_input_tokens: setattr(self, "budget", max_input_tokens))
    c = cfg()
    c.groq_max_input_tokens = 5_000
    assert [m.budget for m in build_llms(c, None, fallback=True)] == [16_000, 5_000]
