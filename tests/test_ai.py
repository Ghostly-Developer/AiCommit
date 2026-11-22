from aicommit.ai import build_prompt, clean_message, truncate_diff


def test_clean_message_strips_fences():
    assert clean_message("```\nfeat: add x\n```") == "feat: add x"
    assert clean_message("```text\nfix: y\n\n- body\n```") == "fix: y\n\n- body"


def test_clean_message_strips_quotes_and_blank_lines():
    assert clean_message('"chore: bump deps"') == "chore: bump deps"
    assert clean_message("feat: a\n\n\n\n- b") == "feat: a\n\n- b"


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
                     recent=["feat: one"], hint="because", commit_type="fix", one_line=True)
    for s in ("DIFF", "file.py", "feature/x", "feat: one", "because", "`fix`", "subject line"):
        assert s in p
