import json

import pytest

from aicommit.schema import COMMIT_TYPES, OutputInvalid, json_schema, parse, strip_fences


def j(**kw):
    base = {"title": "add x", "summary": "- does x", "type": "feat"}
    base.update(kw)
    return json.dumps({k: v for k, v in base.items() if v is not None})


def test_parse_valid():
    m = parse(j())
    assert (m.title, m.summary, m.type) == ("add x", "- does x", "feat")
    assert m.as_commit_message() == "feat: add x\n\n- does x"
    assert m.as_commit_message(one_line=True) == "feat: add x"
    assert m.as_pr_markdown().startswith("# feat: add x\n\n")


@pytest.mark.parametrize("t", COMMIT_TYPES)
def test_parse_accepts_every_allowed_type(t):
    assert parse(j(type=t)).type == t


def test_parse_strips_fences():
    assert parse("```json\n" + j() + "\n```").title == "add x"
    assert strip_fences("```\n{}\n```") == "{}"


def test_parse_normalises_title_prefix_and_period():
    assert parse(j(title="feat(api): add x.")).title == "add x"


@pytest.mark.parametrize("raw, reason", [
    ("not json", "not valid JSON"),
    ("[]", "does not match schema"),
    (j(type="chore"), "type"),
    (j(title=""), "title"),
    (j(title="a" * 73), "title"),
    (j(title="two\nlines"), "single line"),
    (j(summary=""), "summary"),
    (j(summary=None), "summary"),
    (json.dumps({"title": "a", "summary": "b", "type": "fix", "extra": 1}), "extra"),
])
def test_parse_rejects_invalid(raw, reason):
    with pytest.raises(OutputInvalid, match=reason):
        parse(raw)


def test_json_schema_shape():
    s = json_schema()
    assert set(s["required"]) == {"title", "summary", "type"}
    assert s["properties"]["type"]["enum"] == list(COMMIT_TYPES)
    assert s["additionalProperties"] is False
