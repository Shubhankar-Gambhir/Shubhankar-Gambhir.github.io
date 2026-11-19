#!/usr/bin/env python3
"""Verify a sidecar's quotes are present at their URLs and commit first-tags match git.

Usage: verify_sources.py <sidecar.yml> [--repo R] [--cache DIR] [--delay SECONDS]
"""
import argparse
import hashlib
import html
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import yaml

JBS_BROWSE = re.compile(r"^https://bugs\.openjdk\.org/browse/(JDK-\d+)/?$")
JBS_REST_FIELDS = "summary,description,comment"


def rewrite_url(url):
    m = JBS_BROWSE.match(url)
    if m:
        return f"https://bugs.openjdk.org/rest/api/2/issue/{m.group(1)}?fields={JBS_REST_FIELDS}"
    return url


PLAIN_TEXT_PREFIXES = ("https://bugs.openjdk.org/rest/", "https://raw.githubusercontent.com/")
PLAIN_TEXT_EXTS = (".txt", ".patch", ".diff", ".java", ".cpp", ".hpp", ".h", ".c", ".cc")
ENTITY = re.compile(r"&(#\d+|#x[0-9a-fA-F]+|[A-Za-z]+);")


def is_html(url):
    """Decided from the URL alone so cached and fresh fetches behave identically.

    Plain text: JBS REST JSON, raw.githubusercontent.com, source-file extensions, and
    file:// fixtures without an .html/.htm extension. Everything else (pipermail, JEPs,
    GitHub commit pages, .html) is treated as HTML.
    """
    path = url.split("?", 1)[0]
    if url.startswith(PLAIN_TEXT_PREFIXES) or path.endswith(PLAIN_TEXT_EXTS):
        return False
    if url.startswith("file://"):
        return path.endswith((".html", ".htm"))
    return True


def normalize(text, html_mode=False):
    """html_mode=True: unescape entities and strip tags (HTML pages only).

    Otherwise only unescape if real entities are present, and never strip `<...>`.
    Sidecar and draft text must always use html_mode=False.
    """
    if html_mode:
        text = html.unescape(text)
        text = re.sub(r"<[^>]*>", "", text)
    text = text.translate({0x201C: '"', 0x201D: '"', 0x2018: "'", 0x2019: "'"})
    text = text.replace("*", "")
    return re.sub(r"\s+", " ", text).strip()


def extract_text(url, raw):
    """Page text to match against; for JBS REST URLs, description plus all comment bodies."""
    if "/rest/api/2/issue/" in url:
        fields = json.loads(raw).get("fields", {})
        parts = [fields.get("description") or ""]
        parts += [c.get("body") or "" for c in (fields.get("comment") or {}).get("comments", [])]
        return "\n".join(parts)
    return raw


def page_text(url, raw):
    text = extract_text(url, raw)
    if is_html(url):
        return normalize(text, html_mode=True)
    return normalize(html.unescape(text) if ENTITY.search(text) else text)


_last_fetch = [0.0]


def fetch(url, cache_dir, delay):
    if url.startswith("file://"):
        with urllib.request.urlopen(url) as r:
            return r.read().decode("utf-8", "replace")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / hashlib.sha256(url.encode()).hexdigest()
    if cached.exists():
        return cached.read_text(encoding="utf-8")
    wait = _last_fetch[0] + delay - time.monotonic()
    if _last_fetch[0] and wait > 0:
        time.sleep(wait)
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            body = r.read().decode("utf-8", "replace")
    finally:
        _last_fetch[0] = time.monotonic()
    cached.write_text(body, encoding="utf-8")
    return body


def first_tag(repo, commit):
    out = subprocess.run(
        ["git", "-C", str(repo), "tag", "--contains", commit],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    modern = sorted(
        (int(m.group(1)), int(m.group(2)), t)
        for t in out
        if (m := re.fullmatch(r"jdk-(\d+)\+(\d+)", t))
    )
    if modern and modern[0][2] != "jdk-9+95":
        return modern[0][2]
    # jdk-9+95 contains every pre-JDK 9 commit; it is an artifact, so use jdk7/jdk8 build tags.
    legacy = sorted(
        (int(m.group(1)), int(m.group(2)), t)
        for t in out
        if (m := re.fullmatch(r"jdk([78])-b(\d+)", t))
    )
    if legacy:
        return legacy[0][2]
    return modern[0][2] if modern else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sidecar")
    ap.add_argument("--repo", default=str(Path("~/work/git/zulu").expanduser()))
    ap.add_argument("--cache", default=str(Path("~/tmp/hotspot-pc-research/fetch-cache").expanduser()))
    ap.add_argument("--delay", type=float, default=6)
    args = ap.parse_args()

    data = yaml.safe_load(Path(args.sidecar).read_text()) or {}
    ok = True
    for q in data.get("quotes") or []:
        url = rewrite_url(q["url"])
        try:
            page = page_text(url, fetch(url, Path(args.cache), args.delay))
        except Exception as e:
            print(f"MISSING {q['id']} (fetch failed: {e}) {q['url']}")
            ok = False
            continue
        if normalize(q["text"]) in page:
            print(f"FOUND {q['id']}")
        else:
            print(f"MISSING {q['id']} not found at {q['url']}")
            ok = False
    for v in data.get("versions") or []:
        try:
            got = first_tag(args.repo, v["commit"])
        except subprocess.CalledProcessError as e:
            got = f"<git error: {e.stderr.strip()}>"
        if got == v["expect_first_tag"]:
            print(f"TAG-OK {v['id']} {v['commit']} {got}")
        else:
            print(f"TAG-MISMATCH {v['id']} {v['commit']} expected {v['expect_first_tag']} got {got}")
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
