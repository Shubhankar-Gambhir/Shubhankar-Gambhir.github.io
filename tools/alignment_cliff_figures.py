#!/usr/bin/env python3
"""Generate the two figures for the Alignment Cliff post (Part 7).

Writes light and dark variants of:
  assets/img/alignment-cliff-sweep-{light,dark}.svg      ns/call vs entry offset (GCC fine pass)
  assets/img/alignment-cliff-placement-{light,dark}.svg  where work() lands

Data is copied from the post's own tables; rerun after changing them.
"""
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "assets" / "img"

# 4-byte fine pass, GCC 15.2.0
GCC_FINE = {0: 2.3948, 4: 2.3924, 8: 2.3926, 12: 2.3920, 16: 2.3920, 20: 2.3919,
            24: 2.3920, 28: 2.8701, 32: 2.8702, 36: 2.8702, 40: 2.8701,
            44: 2.8723, 48: 2.8712, 52: 2.8704, 56: 2.8705, 60: 2.8702}

FUNC_BYTES = 38
PLACEMENTS = [0, 24, 28, 32, 60]

# Chirpy 7 surfaces; series colors are dataviz reference slots 1-2.
THEMES = {
    "light": dict(surface="#ffffff", text="#191919", text2="#666666",
                  grid="#e4e4e4", rule="#191919", s1="#257de0", s2="#f47230"),
    "dark": dict(surface="#1b1b1e", text="#efefef", text2="#9c9c9c",
                 grid="#38383c", rule="#efefef", s1="#338ced", s2="#e76624"),
}
FONT = "font-family=\"-apple-system,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif\""


def svg_open(w, h, t, label):
    return [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
            f'width="{w}" height="{h}" role="img" aria-label="{label}" {FONT}>',
            f'<rect width="{w}" height="{h}" fill="{t["surface"]}"/>']


def sweep(t):
    w, h = 720, 360
    l, r, top, bot = 64, 24, 56, 56
    pw, ph = w - l - r, h - top - bot
    y0, y1 = 2.30, 2.95
    x = lambda v: l + v / 64 * pw
    y = lambda v: top + (y1 - v) / (y1 - y0) * ph
    s = svg_open(w, h, t, "ns per call against entry offset of work() within a "
                 "64-byte cache line: 2.392 ns at offsets 0 to 24, 2.870 ns at 28 to 60, "
                 "GCC 15.2.0, 4-byte sweep")
    for v in (2.4, 2.5, 2.6, 2.7, 2.8, 2.9):
        s.append(f'<line x1="{l}" x2="{l + pw}" y1="{y(v):.1f}" y2="{y(v):.1f}" '
                 f'stroke="{t["grid"]}" stroke-width="1"/>')
        s.append(f'<text x="{l - 8}" y="{y(v) + 4:.1f}" text-anchor="end" '
                 f'font-size="12" fill="{t["text2"]}">{v:.1f}</text>')
    for v in range(0, 65, 8):
        s.append(f'<text x="{x(v):.1f}" y="{top + ph + 18}" text-anchor="middle" '
                 f'font-size="12" fill="{t["text2"]}">{v}</text>')
    s.append(f'<line x1="{l}" x2="{l + pw}" y1="{top + ph}" y2="{top + ph}" '
             f'stroke="{t["text2"]}" stroke-width="1"/>')
    s.append(f'<text x="{l + pw / 2}" y="{h - 12}" text-anchor="middle" font-size="13" '
             f'fill="{t["text2"]}">entry offset of work() within the 64-byte line (bytes)</text>')
    s.append(f'<text x="16" y="{top + ph / 2}" text-anchor="middle" font-size="13" '
             f'fill="{t["text2"]}" transform="rotate(-90 16 {top + ph / 2})">ns per call</text>')
    # the cliff lies somewhere between the last fast and first slow sample
    s.append(f'<rect x="{x(24):.1f}" y="{top}" width="{x(28) - x(24):.1f}" height="{ph}" '
             f'fill="{t["grid"]}"/>')
    s.append(f'<text x="{x(26):.1f}" y="{top - 8}" text-anchor="middle" font-size="12" '
             f'fill="{t["text"]}">cliff between 24 and 28</text>')
    for lo, hi in ((0, 24), (28, 60)):
        pts = " ".join(f"{x(k):.1f},{y(v):.1f}" for k, v in GCC_FINE.items() if lo <= k <= hi)
        s.append(f'<polyline points="{pts}" fill="none" stroke="{t["s1"]}" stroke-width="2" '
                 f'stroke-linejoin="round" stroke-linecap="round"/>')
    for k, v in GCC_FINE.items():
        s.append(f'<circle cx="{x(k):.1f}" cy="{y(v):.1f}" r="4" fill="{t["s1"]}" '
                 f'stroke="{t["surface"]}" stroke-width="2"/>')
    s.append(f'<text x="{x(12):.1f}" y="{y(2.392) - 14:.1f}" text-anchor="middle" '
             f'font-size="13" fill="{t["text"]}">2.392 ns</text>')
    s.append(f'<text x="{x(44):.1f}" y="{y(2.870) - 14:.1f}" text-anchor="middle" '
             f'font-size="13" fill="{t["text"]}">2.870 ns</text>')
    s.append("</svg>")
    return "\n".join(s)


def placement(t):
    rows = len(PLACEMENTS)
    w = 720
    l, bx0, bx1 = 16, 96, 520          # label column, byte axis span
    top, rh = 70, 38
    h = top + rows * rh + 44
    x = lambda b: bx0 + b / 128 * (bx1 - bx0)
    s = svg_open(w, h, t, "work(), 38 bytes, placed at offsets 0, 24, 28, 32 and 60. "
                 "The fast placements stay inside one 64-byte line; every slow one "
                 "crosses into the next. Window counts do not separate them.")
    # 32-byte window hairlines and labels
    for i, b in enumerate((0, 32, 64, 96)):
        s.append(f'<text x="{x(b + 16):.1f}" y="{top - 10}" text-anchor="middle" '
                 f'font-size="11" fill="{t["text2"]}">window {i}</text>')
    for b in (32, 96):
        s.append(f'<line x1="{x(b):.1f}" x2="{x(b):.1f}" y1="{top - 4}" y2="{top + rows * rh}" '
                 f'stroke="{t["grid"]}" stroke-width="1"/>')
    # 64-byte line boundaries, heavier
    for b in (0, 64, 128):
        s.append(f'<line x1="{x(b):.1f}" x2="{x(b):.1f}" y1="{top - 30}" y2="{top + rows * rh}" '
                 f'stroke="{t["rule"]}" stroke-width="2"/>')
    for b, name in ((32, "cache line A"), (96, "cache line A + 64")):
        s.append(f'<text x="{x(b):.1f}" y="{top - 34}" text-anchor="middle" font-size="12" '
                 f'font-weight="600" fill="{t["text"]}">{name}</text>')
    # right-hand columns
    cx = {"win": 566, "lines": 628, "ns": 690}
    for key, name in (("win", "windows"), ("lines", "lines"), ("ns", "ns/call")):
        s.append(f'<text x="{cx[key]}" y="{top - 10}" text-anchor="middle" font-size="12" '
                 f'font-weight="600" fill="{t["text"]}">{name}</text>')
    for i, n in enumerate(PLACEMENTS):
        cy = top + i * rh + rh / 2
        end = n + FUNC_BYTES - 1
        windows = end // 32 - n // 32 + 1
        lines = end // 64 - n // 64 + 1
        slow = lines > 1
        col = t["s2"] if slow else t["s1"]
        s.append(f'<text x="{l}" y="{cy + 4:.1f}" font-size="12" fill="{t["text"]}">offset {n}</text>')
        s.append(f'<rect x="{x(n):.1f}" y="{cy - 8:.1f}" width="{x(n + FUNC_BYTES) - x(n):.1f}" '
                 f'height="16" rx="3" fill="{col}"/>')
        for key, val in (("win", windows), ("lines", lines),
                         ("ns", "2.87" if slow else "2.39")):
            s.append(f'<text x="{cx[key]}" y="{cy + 4:.1f}" text-anchor="middle" font-size="12" '
                     f'fill="{t["text"]}">{val}</text>')
    by = top + rows * rh
    for b in (0, 32, 64, 96, 128):
        s.append(f'<text x="{x(b):.1f}" y="{by + 18}" text-anchor="middle" font-size="11" '
                 f'fill="{t["text2"]}">{b}</text>')
    s.append(f'<text x="{(bx0 + bx1) / 2}" y="{by + 36}" text-anchor="middle" font-size="12" '
             f'fill="{t["text2"]}">byte offset from A (64-byte aligned)</text>')
    s.append("</svg>")
    return "\n".join(s)


def main():
    for mode, t in THEMES.items():
        (OUT / f"alignment-cliff-sweep-{mode}.svg").write_text(sweep(t) + "\n")
        (OUT / f"alignment-cliff-placement-{mode}.svg").write_text(placement(t) + "\n")


if __name__ == "__main__":
    main()
