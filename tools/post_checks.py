#!/usr/bin/env python3
"""Checks for blog posts: reading time, prose rules, footers, anchors, preservation."""

import argparse
import html
import re
import sys
from pathlib import Path

import yaml

EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
COLD_RE = re.compile(
    r"\b(above|below|earlier in this post|later in this post|as we saw)\b", re.I
)
GODBOLT_RE = re.compile(r"https?://godbolt\.org/[^\s)\]\">]+")
SEPARATOR_RE = re.compile(r"^\|[\s:|-]*$")
MINUTES_RE = re.compile(r"<em>(\d+) min</em>")
PREV_RE = re.compile(r"^\*\*Previously:\*\*(.*)$", re.M)
NEXT_RE = re.compile(r"^\*\*Next:\*\*(.*)$", re.M)
LINK_RE = re.compile(r"\s*\[(.+?)\]\(\{% post_url (\S+) %\}\)")


def read_minutes(html_text):
    m = MINUTES_RE.search(html_text)
    return int(m.group(1)) if m else None


def _prose_lines(md):
    """Yield (lineno, line) for lines outside fenced blocks."""
    in_fence = False
    for n, line in enumerate(md.splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            yield n, line


def prose_violations(md):
    out = []
    for n, line in _prose_lines(md):
        if "—" in line or (" -- " in line and not line.startswith("|")):
            out.append(f"line {n}: em dash or ' -- ' in prose")
        if EMOJI_RE.search(line):
            out.append(f"line {n}: emoji")
        if "shubhankar-gambhir.github.io/posts" in line:
            out.append(f"line {n}: hard-coded post link (use post_url)")
    fences = [
        (n, line.strip())
        for n, line in enumerate(md.splitlines(), 1)
        if line.lstrip().startswith("```")
    ]
    for n, f in fences:
        if f == "```asm":
            out.append(f"line {n}: ```asm fence (use ```nasm)")
    if len(fences) % 2:
        out.append(f"unbalanced ``` fences ({len(fences)})")
    return out


def cold_refs(md):
    return [
        f"line {n}: cold reference '{m.group(1)}'"
        for n, line in _prose_lines(md)
        for m in COLD_RE.finditer(line)
    ]


def _front_matter(md):
    m = re.match(r"---\n(.*?)\n---\n", md, re.S)
    if not m:
        return {}
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def front_matter_violations(md):
    fm = _front_matter(md)
    return [f"front matter missing {k}" for k in ("title", "date", "description", "image") if k not in fm]


def _title(posts_dir, name):
    p = posts_dir / f"{name}.md"
    if not p.exists():
        return None
    return _front_matter(p.read_text()).get("title")


def _check_link(posts_dir, name, label, regex, text, neighbour):
    m = regex.search(text)
    if not m:
        return [f"{name}: missing **{label}:** line"]
    link = LINK_RE.match(m.group(1))
    if not link:
        return [f"{name}: **{label}:** has no post_url link"]
    if link.group(2) != neighbour:
        return [f"{name}: **{label}:** links {link.group(2)}, expected {neighbour}"]
    title = _title(posts_dir, neighbour)
    if title is None:
        return [f"{name}: **{label}:** neighbour {neighbour} not found"]
    if link.group(1) != title:
        return [f"{name}: **{label}:** link text {link.group(1)!r} != title {title!r}"]
    return []


def footer_violations(posts_dir, series):
    posts_dir = Path(posts_dir)
    out = []
    for names in series.values():
        for i, name in enumerate(names):
            p = posts_dir / f"{name}.md"
            if not p.exists():
                out.append(f"{name}: post file not found")
                continue
            text = p.read_text()
            if i == 0:
                if PREV_RE.search(text):
                    out.append(f"{name}: first post of series has a **Previously:** line")
            else:
                out += _check_link(posts_dir, name, "Previously", PREV_RE, text, names[i - 1])
            if i == len(names) - 1:
                if NEXT_RE.search(text):
                    out.append(f"{name}: last post of series has a **Next:** line")
            else:
                out += _check_link(posts_dir, name, "Next", NEXT_RE, text, names[i + 1])
    return out


def _items(md):
    urls = set(GODBOLT_RE.findall(md))
    rows = {
        line.strip()
        for line in md.splitlines()
        if line.startswith("|") and not SEPARATOR_RE.match(line.strip())
    }
    return urls, rows


def missing_items(old_md, new_mds, allow=frozenset()):
    urls, rows = _items(old_md)
    seen_urls, seen_rows = set(), set()
    for md in new_mds:
        u, r = _items(md)
        seen_urls |= u
        seen_rows |= r
    missing = [u for u in sorted(urls) if u not in seen_urls]
    missing += [r for r in sorted(rows) if r not in seen_rows]
    return [f"missing: {x}" for x in missing if x not in allow]


def broken_anchors(site_dir):
    site_dir = Path(site_dir)
    pages = {p.parent.name: p.read_text() for p in sorted(site_dir.glob("posts/*/index.html"))}
    ids = {slug: set(re.findall(r'\bid="([^"]*)"', t)) for slug, t in pages.items()}
    out = []
    for slug, text in pages.items():
        for target, anchor in re.findall(r'href="(?:/posts/([^/"#]+)/)?#([^"]+)"', text):
            anchor = html.unescape(anchor)
            target = target or slug
            if target not in ids:
                out.append(f"{slug}: link to unknown page {target}#{anchor}")
            elif anchor not in ids[target]:
                out.append(f"{slug}: broken anchor {target}#{anchor}")
    return out


def _slug(path):
    return re.sub(r"^\d{4}-\d{2}-\d{2}-", "", Path(path).stem)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True, type=Path)
    ap.add_argument("--max-minutes", type=int, default=12)
    ap.add_argument("--series", type=Path, default=Path(__file__).parent / "series.yml")
    ap.add_argument("files", nargs="+", type=Path)
    args = ap.parse_args(argv)

    fail = False
    for f in args.files:
        md = f.read_text()
        for v in prose_violations(md) + front_matter_violations(md):
            print(f"FAIL {f}: {v}")
            fail = True
        for v in cold_refs(md):
            print(f"WARN {f}: {v}")
    for v in footer_violations(args.files[0].parent, yaml.safe_load(args.series.read_text())):
        print(f"FAIL {v}")
        fail = True
    for v in broken_anchors(args.site):
        print(f"FAIL {args.site}: {v}")
        fail = True
    for f in args.files:
        slug = _slug(f)
        page = args.site / "posts" / slug / "index.html"
        minutes = read_minutes(page.read_text()) if page.exists() else None
        if minutes is None:
            print(f"FAIL {f}: no reading time found at {page}")
            fail = True
            continue
        print(f"minutes {slug} {minutes}")
        if minutes > args.max_minutes:
            print(f"FAIL {f}: {minutes} min > {args.max_minutes}")
            fail = True
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
