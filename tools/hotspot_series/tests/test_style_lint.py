import subprocess
import sys
from pathlib import Path

import pytest
import yaml

TOOL = Path(__file__).resolve().parent.parent / "style_lint.py"

FM = "---\ntitle: T\n---\n"


def words(n):
    return " ".join(["word"] * n) + "\n"


@pytest.fixture
def env(tmp_path):
    """A temp git repo with _posts/ and helpers that lint a draft against a sidecar."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "_posts").mkdir()
    (tmp_path / "_drafts").mkdir()

    def run(text, sc):
        (tmp_path / "side.yml").write_text(yaml.safe_dump(sc))
        d = tmp_path / "_drafts" / "d.md"
        d.write_text(text)
        return subprocess.run(
            [sys.executable, str(TOOL), str(d), "--sources", str(tmp_path / "side.yml")],
            capture_output=True,
            text=True,
        )

    class E:
        root = tmp_path

        def lint(self, body, sidecar=None, sketch_text=None):
            sc = dict(sidecar or {})
            sc.setdefault("post", "_drafts/d.md")
            if sketch_text is not None:
                (tmp_path / "sketch.cpp").write_text(sketch_text)
                sc["sketch"] = "sketch.cpp"
            return run(FM + words(2600) + body, sc)

        def lint_raw(self, text):
            return run(text, {"post": "_drafts/d.md"})

    return E()


def test_clean_passes(env):
    r = env.lint("A clean closing sentence.\n")
    assert r.returncode == 0, r.stdout
    assert "prose_words=" in r.stdout


def test_em_dash_fails(env):
    r = env.lint("This is wrong — very wrong.\n")
    assert r.returncode == 1 and "em dash" in r.stdout


def test_spaced_double_hyphen_in_prose_fails_but_mermaid_ok(env):
    assert env.lint("a -- b\n").returncode == 1
    ok = env.lint("```mermaid\nA -- label --> B\n```\n")
    assert ok.returncode == 0, ok.stdout
    code = env.lint("see `a -- b` here\n")
    assert code.returncode == 0, code.stdout


@pytest.mark.parametrize(
    "phrase",
    [
        "The irony",
        "Counterintuitively",
        "Interestingly",
        "It's worth noting",
        "It is worth noting",
        "As mentioned earlier",
        "In essence",
        "It's important to note",
    ],
)
def test_banned_phrase_fails(env, phrase):
    r = env.lint(f"{phrase}, the queue was empty.\n")
    assert r.returncode == 1 and "banned phrase" in r.stdout


@pytest.mark.parametrize("h", ["Key Takeaways", "What This Means for You", "What I Learned", "Conclusion"])
def test_banned_closing_heading_fails(env, h):
    r = env.lint(f"\n## {h}\n\nbye\n")
    assert r.returncode == 1 and "closing heading" in r.stdout


def test_word_count_bounds(env):
    def count(n, extra=""):
        return env.lint_raw(FM + words(n) + extra)

    assert count(2499).returncode == 1
    assert "prose_words=2499" in count(2499).stdout
    assert count(2500).returncode == 0
    assert count(3000).returncode == 0
    assert count(3001).returncode == 1
    # exclusions: fences, tables, comments, link targets (link text "[x]" still counts as 1 word)
    extra = (
        "```cpp\n" + words(500) + "```\n"
        + "| " + " | ".join(["cell"] * 400) + " |\n"
        + "<!-- AUTHOR: " + words(300).strip() + " -->\n"
        + "[x](https://example.com/" + "a" * 50 + " " + "b" * 50 + ")\n"
    )
    r = count(2499, extra)
    assert "prose_words=2500" in r.stdout


def test_unlisted_quote_fails(env):
    q = 'He wrote "we cannot have all three guarantees at the same time" once.\n'
    r = env.lint(q)
    assert r.returncode == 1 and "unlisted quote" in r.stdout
    ok = env.lint(
        q,
        {"quotes": [{"id": "q", "text": "we cannot have all three guarantees at the same time: non-blocking", "url": "u"}]},
    )
    assert ok.returncode == 0, ok.stdout


def test_short_quote_and_code_quote_ignored(env):
    r = env.lint('He said "blocked" and `"this is a long string in code that is ignored"` fine.\n')
    assert r.returncode == 0, r.stdout


def test_blockquote_must_match(env):
    r = env.lint("> we cannot have all three guarantees here\n")
    assert r.returncode == 1 and "unlisted quote" in r.stdout
    ok = env.lint(
        "> we cannot have all three guarantees here\n",
        {"quotes": [{"id": "q", "text": "we cannot have all three guarantees here, really", "url": "u"}]},
    )
    assert ok.returncode == 0, ok.stdout


def test_forbidden_near_pattern_fails(env):
    sc = {"forbidden": [{"pattern": "(?i)\\bcrash", "near": "8391060", "why": "no field crash"}]}
    assert env.lint("Commit 8391060 caused a crash.\n", sc).returncode == 1
    far = "Commit 8391060 is here.\n" + words(200) + "A crash elsewhere.\n"
    assert env.lint(far, sc).returncode == 0
    sc2 = {"forbidden": [{"pattern": "lock-free proposed three times", "why": "inference"}]}
    r = env.lint("They had lock-free proposed three times.\n", sc2)
    assert r.returncode == 1 and "forbidden" in r.stdout


def test_post_url_to_draft_fails(env):
    (env.root / "_posts" / "2026-05-07-real.md").write_text("x")
    assert env.lint("see {% post_url 2026-05-07-real %}\n").returncode == 0
    r = env.lint("see {% post_url 2026-11-30-unpublished %}\n")
    assert r.returncode == 1 and "post_url" in r.stdout


def test_cpp_block_not_in_sketch_fails(env):
    sketch = "int main() {\n  int x = 1;\n  return x - 1;\n}\n"
    ok = env.lint("```cpp\nint main() {\n    int x = 1;\n  return x - 1;\n}\n```\n", sketch_text=sketch)
    assert ok.returncode == 0, ok.stdout
    bad = env.lint("```cpp\nint main() { return 7; }\n```\n", sketch_text=sketch)
    assert bad.returncode == 1 and "cpp block" in bad.stdout
    nosketch = env.lint("```cpp\nint x;\n```\n")
    assert nosketch.returncode == 1 and "cpp block" in nosketch.stdout
