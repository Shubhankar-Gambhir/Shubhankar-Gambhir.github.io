#!/usr/bin/env python3
"""Lint a blog draft against the series' prose rules.

Usage: style_lint.py <draft.md> --sources <sidecar.yml>
Prints one line per violation plus prose_words=N; exits non-zero on any violation.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

import yaml

from verify_sources import normalize

BANNED_PHRASES = [
    "the irony",
    "counterintuitively",
    "interestingly",
    "it's worth noting",
    "it is worth noting",
    "as mentioned earlier",
    "in essence",
    "it's important to note",
]
BANNED_HEADINGS = ["key takeaways", "what this means for you", "what i learned", "conclusion"]
MIN_WORDS, MAX_WORDS = 2500, 3000
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*([^\s`]*)")


def split_fences(lines):
    """Return (prose_lines, cpp_blocks); fenced content is dropped from prose."""
    prose, cpp = [], []
    marker, lang, buf = None, "", []
    for line in lines:
        m = FENCE.match(line)
        if marker is None:
            if m:
                marker, lang, buf = m.group(1), m.group(2).lower(), []
                prose.append("")
            else:
                prose.append(line)
        elif m and m.group(1)[0] == marker[0] and len(m.group(1)) >= len(marker) and not m.group(2):
            if lang in ("cpp", "c++"):
                cpp.append("\n".join(buf))
            marker = None
            prose.append("")
        else:
            buf.append(line)
            prose.append("")
    return prose, cpp


def find_repo_root(draft):
    r = subprocess.run(
        ["git", "-C", str(draft.parent), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True,
    )
    return Path(r.stdout.strip()) if r.returncode == 0 else draft.parent


def squash(s):
    return re.sub(r"\s+", " ", s).strip()


def lint(draft, sidecar):
    violations = []
    text = draft.read_text(encoding="utf-8")
    text = re.sub(r"\A---\n.*?\n---\n", "", text, count=1, flags=re.S)
    root = find_repo_root(draft.resolve())

    # post_url targets must be published (checked over the whole body, as Liquid sees it)
    for slug in re.findall(r"\{%-?\s*post_url\s+([^\s%]+)\s*-?%\}", text):
        if not (root / "_posts" / f"{slug}.md").is_file():
            violations.append(f"post_url target not in _posts/: {slug}")

    lines, cpp_blocks = split_fences(text.split("\n"))
    prose = "\n".join(lines)
    prose = re.sub(r"<!--.*?-->", " ", prose, flags=re.S)
    prose = "\n".join(l for l in prose.split("\n") if not l.lstrip().startswith("|"))
    prose = re.sub(r"(`+)[^`\n]*?\1", " ", prose)
    prose = re.sub(r"\]\([^)]*\)", "]", prose)
    prose = prose.translate({0x201C: '"', 0x201D: '"', 0x2018: "'", 0x2019: "'"})

    words = len(prose.split())
    if not MIN_WORDS <= words <= MAX_WORDS:
        violations.append(f"word count {words} outside {MIN_WORDS}-{MAX_WORDS}")

    if "—" in prose:
        violations.append("em dash in prose")
    if re.search(r"\s--\s", prose):
        violations.append("spaced ' -- ' in prose")

    lower = prose.lower()
    for p in BANNED_PHRASES:
        if re.search(r"\b" + re.escape(p) + r"\b", lower):
            violations.append(f"banned phrase: {p}")
    for line in prose.split("\n"):
        m = re.match(r"^#{1,6}\s+(?:\d+[.)]\s*)?(.*?)\W*$", line)
        if m and m.group(1).lower() in BANNED_HEADINGS:
            violations.append(f"banned closing heading: {m.group(1)}")

    sc = yaml.safe_load(Path(sidecar).read_text()) or {}
    known = [normalize(q["text"]) for q in sc.get("quotes") or []]

    def check_quote(run, kind):
        n = normalize(run)
        if len(n.split()) >= 6 and not any(n in k for k in known):
            violations.append(f"unlisted quote ({kind}): {n[:70]}")

    for m in re.finditer(r'"([^"]{20,})"', prose):
        check_quote(m.group(1), "quoted")
    para = []
    for line in prose.split("\n") + [""]:
        if line.lstrip().startswith(">"):
            para.append(line.lstrip()[1:])
        elif para:
            check_quote(" ".join(para), "blockquote")
            para = []

    for f in sc.get("forbidden") or []:
        for m in re.finditer(f["pattern"], prose):
            near = f.get("near")
            if near and near not in prose[max(0, m.start() - 300): m.end() + 300]:
                continue
            violations.append(f"forbidden pattern {f['pattern']!r}: {f.get('why', '')}")
            break

    if cpp_blocks:
        sketch = sc.get("sketch")
        sketch_path = None
        if sketch:
            for base in (root, Path(sidecar).resolve().parent):
                if (base / sketch).is_file():
                    sketch_path = base / sketch
                    break
        sketch_text = squash(sketch_path.read_text()) if sketch_path else None
        for i, block in enumerate(cpp_blocks, 1):
            if sketch_text is None:
                violations.append(f"cpp block {i}: sidecar has no readable sketch")
            elif squash(block) not in sketch_text:
                violations.append(f"cpp block {i} is not a substring of the sketch")
    return violations, words


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("draft")
    ap.add_argument("--sources", required=True)
    args = ap.parse_args()
    violations, words = lint(Path(args.draft), args.sources)
    for v in violations:
        print(f"VIOLATION: {v}")
    print(f"prose_words={words}")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
