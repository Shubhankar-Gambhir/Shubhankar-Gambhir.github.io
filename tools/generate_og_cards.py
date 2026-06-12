#!/usr/bin/env python3
"""Generate 1200x630 social preview cards (og:image) for each blog post.

For every file in _posts/*.md this:
  1. Renders a branded title card to assets/img/og/<slug>.png
  2. Idempotently adds an og-only `image:` block to the post front matter
     (hero: false keeps it out of the on-page banner; see _layouts/post.html)

Re-run after adding a new post. Requires Pillow.
"""

import os
import re
import glob

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POSTS = os.path.join(ROOT, "_posts")
OUT_DIR = os.path.join(ROOT, "assets", "img", "og")

W, H = 1200, 630
BG = (24, 24, 27)          # #18181b  near-black, matches dark theme
ACCENT = (88, 166, 255)    # #58a6ff  dev-blue
TITLE_FG = (240, 240, 242)
MUTED = (150, 156, 162)
MARGIN = 90
BAR_W = 14                 # left accent bar
TEXT_W = W - MARGIN - 70   # wrap width for the title

DEJAVU = "/usr/share/fonts/truetype/dejavu"
LIBERATION = "/usr/share/fonts/truetype/liberation"


def font(bold, size):
    for path in (
        os.path.join(DEJAVU, "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),
        os.path.join(LIBERATION, "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf"),
    ):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def parse_front_matter(text):
    """Return (title, categories_list, has_image, fm_end_line_index, lines)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("no front matter")
    end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    fm = "\n".join(lines[1:end])
    m = re.search(r"^title:\s*(.+)$", fm, re.M)
    title = m.group(1).strip().strip('"').strip("'") if m else "Untitled"
    cats = []
    cm = re.search(r"^categories:\s*\[(.+?)\]", fm, re.M)
    if cm:
        cats = [c.strip() for c in cm.group(1).split(",")]
    has_image = re.search(r"^image:\s*", fm, re.M) is not None
    return title, cats, has_image, end, lines


def wrap(draw, text, fnt, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=fnt) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def render_card(title, eyebrow, out_path):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # left accent bar
    d.rectangle([0, 0, BAR_W, H], fill=ACCENT)

    # eyebrow (category), uppercase
    eb_font = font(True, 30)
    d.text((MARGIN, 84), eyebrow.upper(), font=eb_font, fill=ACCENT)

    # title: shrink font until it fits in <= 4 lines AND clears the footer
    top, footer_y, gap = 175, H - 92, 34
    avail = footer_y - gap - top
    size = 72
    while size >= 44:
        tf = font(True, size)
        lines = wrap(d, title, tf, TEXT_W)
        ascent, descent = tf.getmetrics()
        lh = int((ascent + descent) * 1.12)
        if len(lines) <= 4 and lh * len(lines) <= avail:
            break
        size -= 3
    block_h = lh * len(lines)
    y = top + (avail - block_h) // 2
    for ln in lines:
        d.text((MARGIN, y), ln, font=tf, fill=TITLE_FG)
        y += lh

    # footer
    ff = font(False, 30)
    d.text((MARGIN, H - 92), "Shubhankar Gambhir  ·  shubhankar-gambhir.github.io",
           font=ff, fill=MUTED)

    img.save(out_path, "PNG")


def add_front_matter_image(path, slug, title, end_idx, lines):
    alt = title.replace('"', '\\"')
    block = [
        "image:",
        f"  path: /assets/img/og/{slug}.png",
        f'  alt: "{alt}"',
        "  hero: false",
    ]
    new_lines = lines[:end_idx] + block + lines[end_idx:]
    with open(path, "w") as f:
        f.write("\n".join(new_lines) + "\n")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for path in sorted(glob.glob(os.path.join(POSTS, "*.md"))):
        slug = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", os.path.basename(path)[:-3])
        with open(path) as f:
            text = f.read()
        title, cats, has_image, end_idx, lines = parse_front_matter(text)
        eyebrow = " ".join(cats) if cats else "C++ Performance"
        out_path = os.path.join(OUT_DIR, slug + ".png")
        render_card(title, eyebrow, out_path)
        status = "card"
        if not has_image:
            add_front_matter_image(path, slug, title, end_idx, lines)
            status += " + front-matter"
        else:
            status += " (front-matter already present)"
        print(f"{status:40s} {slug}")


if __name__ == "__main__":
    main()
