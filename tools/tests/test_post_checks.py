import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import post_checks as pc  # noqa: E402

FENCE = "```"


def test_read_minutes():
    assert pc.read_minutes('<span><em>14 min</em> read</span>') == 14
    assert pc.read_minutes("<p>nothing</p>") is None


def test_prose_violations_flags():
    assert pc.prose_violations("Hello—world\n")
    assert pc.prose_violations("a -- b\n")
    ok = f"{FENCE}mermaid\nA -- B\n{FENCE}\n| a -- b | c |\nplain\n"
    assert pc.prose_violations(ok) == []
    assert pc.prose_violations("| a — b | c |\n")
    assert pc.prose_violations("| a -- b | c |\n") == []
    assert pc.prose_violations(f"{FENCE}asm\nnop\n{FENCE}\n")
    unbalanced = f"{FENCE}cpp\nx\n{FENCE}\n{FENCE}cpp\ny\n"
    assert any("fence" in v for v in pc.prose_violations(unbalanced))
    assert pc.prose_violations("see https://shubhankar-gambhir.github.io/posts/x/\n")
    assert pc.prose_violations("party \U0001F389\n")


def test_cold_refs():
    assert pc.cold_refs("As shown above, x\n")
    assert pc.cold_refs("aboveground\n") == []
    assert pc.cold_refs(f"{FENCE}\nabove\n{FENCE}\n") == []


def test_front_matter():
    full = "---\ntitle: T\ndate: 2026-01-01\ndescription: d\nimage: i.png\n---\nbody\n"
    assert pc.front_matter_violations(full) == []
    v = pc.front_matter_violations(full.replace("image: i.png\n", ""))
    assert len(v) == 1 and "image" in v[0]


def _post(d, name, title, prev=None, nxt=None):
    body = f'---\ntitle: "{title}"\n---\n\ntext\n\n'
    if prev:
        body += f"**Previously:** [{prev[1]}]({{% post_url {prev[0]} %}})\n\n"
    if nxt:
        body += f"**Next:** [{nxt[1]}]({{% post_url {nxt[0]} %}})\n"
    (d / f"{name}.md").write_text(body)


def test_footer_chain(tmp_path):
    series = {"s": ["A", "B", "C"]}
    _post(tmp_path, "A", "Title A", nxt=("B", "Title B"))
    _post(tmp_path, "B", "Title B", prev=("A", "Title A"), nxt=("C", "Title C"))
    _post(tmp_path, "C", "Title C", prev=("B", "Title B"))
    assert pc.footer_violations(tmp_path, series) == []

    _post(tmp_path, "B", "Title B", prev=("A", "Title A"), nxt=("C", "Wrong"))
    assert len(pc.footer_violations(tmp_path, series)) == 1

    _post(tmp_path, "B", "Title B", prev=("A", "Title A"), nxt=("C", "Title C"))
    _post(tmp_path, "A", "Title A", prev=("C", "Title C"), nxt=("B", "Title B"))
    assert len(pc.footer_violations(tmp_path, series)) == 1


def test_missing_items():
    url = "https://godbolt.org/z/abc123"
    row = "| a | 1.0 |"
    old = f"intro {url}\n\n| h | v |\n|---|---|\n{row}\n"
    new1 = "nothing here\n"
    new2 = f"moved {url}\n{row}\n| h | v |\n"
    assert pc.missing_items(old, [new1, new2]) == []
    dropped = pc.missing_items(old, [new1, "| h | v |\n"])
    assert len(dropped) == 2
    assert pc.missing_items(old, [new1, "| h | v |\n"], allow={url, row}) == []


def test_broken_anchors(tmp_path):
    pages = (
        ("a", '<a href="/posts/b/#the-ring">x</a><a href="#top">t</a><h1 id="top">'),
        ("b", '<h2 id="the-ring">r</h2>'),
    )
    for slug, body in pages:
        d = tmp_path / "posts" / slug
        d.mkdir(parents=True)
        (d / "index.html").write_text(body)
    assert pc.broken_anchors(tmp_path) == []
    (tmp_path / "posts" / "b" / "index.html").write_text("<h2>none</h2>")
    assert len(pc.broken_anchors(tmp_path)) == 1


def test_last_post_coming_soon_next(tmp_path):
    series = {"s": ["A", "B"]}
    _post(tmp_path, "A", "Title A", nxt=("B", "Title B"))
    _post(tmp_path, "B", "Title B", prev=("A", "Title A"))
    with open(tmp_path / "B.md", "a") as f:
        f.write("**Next:** Future Post (coming soon)\n")
    assert pc.footer_violations(tmp_path, series) == []

    _post(tmp_path, "B", "Title B", prev=("A", "Title A"), nxt=("A", "Title A"))
    assert len(pc.footer_violations(tmp_path, series)) == 1

    _post(tmp_path, "B", "Title B", prev=("A", "Title A"))
    with open(tmp_path / "B.md", "a") as f:
        f.write("**Next:** Future Post\n")
    assert len(pc.footer_violations(tmp_path, series)) == 1
