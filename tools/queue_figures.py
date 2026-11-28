#!/usr/bin/env python3
"""Generate the ring snapshot for "Taking the Lock Out of the Pool" (Queues post 3).

Writes assets/img/vyukov-ring-stall-{light,dark}.svg: an 8-cell Vyukov ring with
_deq = 10 and _enq = 14, where the producer holding ticket 10 claimed cell 2 and
stalled before publishing. The sequence numbers follow the repo's LockFreeRing:
a free cell holds seq == pos, a full one seq == pos + 1, and pop() frees a cell
for the next lap by storing pos + Capacity.
"""
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "assets" / "img"
CAP, DEQ, ENQ, STALLED = 8, 10, 14, 10

THEMES = {
    "light": dict(surface="#ffffff", text="#191919", text2="#666666", line="#8a8a8a",
                  box="#f4f4f4", full="#257de0", full_bg="#e3effc",
                  stall="#f47230", stall_bg="#fde9de"),
    "dark": dict(surface="#1b1b1e", text="#efefef", text2="#9c9c9c", line="#7a7a7a",
                 box="#26262a", full="#338ced", full_bg="#1d3350",
                 stall="#e76624", stall_bg="#4a2a1a"),
}
SANS = "-apple-system,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif"
MONO = "'SFMono-Regular',Menlo,Consolas,'Liberation Mono',monospace"
W, H = 720, 236
CX, CW, GAP, CY, CH = 28, 78, 6, 92, 50


def cells():
    """(index, pos, seq, state) for the snapshot."""
    out = []
    for i in range(CAP):
        # the ticket that currently maps to cell i: consumers' window first, then producers'
        pos = next(p for p in range(DEQ, DEQ + CAP) if p % CAP == i)
        if pos == STALLED:
            out.append((i, pos, pos, "claimed, unwritten"))
        elif pos < ENQ:
            out.append((i, pos, pos + 1, "full"))
        else:
            out.append((i, pos, pos, "free"))
    return out


def text(x, y, s, t, size=12, color="text", anchor="middle", mono=False, weight=None):
    w = f' font-weight="{weight}"' if weight else ""
    return (f'<text x="{x}" y="{y}" font-family="{MONO if mono else SANS}" font-size="{size}" '
            f'text-anchor="{anchor}" fill="{t[color]}"{w}>{s}</text>')


def figure(t):
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" '
         f'height="{H}" role="img" aria-label="An eight-cell ring. The dequeue ticket is 10 '
         f'and points at cell 2, which a stalled producer claimed but never wrote. Cells 3 to '
         f'5 are full, but the consumer at ticket 10 sees an unwritten cell and returns empty.">',
         f'<rect width="{W}" height="{H}" fill="{t["surface"]}"/>', "<defs>",
         f'<marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
         f'orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="{t["text2"]}"/></marker>', "</defs>"]
    x = lambda i: CX + i * (CW + GAP)
    for i, pos, seq, state in cells():
        if state == "full":
            fill, stroke = t["full_bg"], t["full"]
        elif state == "free":
            fill, stroke = t["box"], t["line"]
        else:
            fill, stroke = t["stall_bg"], t["stall"]
        mid = x(i) + CW / 2
        s.append(text(mid, CY - 8, f"cell {i}", t, size=11, color="text2"))
        s.append(f'<rect x="{x(i)}" y="{CY}" width="{CW}" height="{CH}" rx="4" fill="{fill}" '
                 f'stroke="{stroke}" stroke-width="{2 if state != "free" else 1}"/>')
        s.append(text(mid, CY + 22, f"seq {seq}", t, size=13, mono=True, weight="600"))
        s.append(text(mid, CY + 40, f"pos {pos}", t, size=11, color="text2", mono=True))
        lines = ["claimed,", "unwritten"] if state.startswith("claimed") else [state]
        for k, ln in enumerate(lines):
            s.append(text(mid, CY + CH + 18 + 14 * k, ln, t, size=11))
    # ticket pointers
    for label, idx in ((f"_deq = {DEQ}", DEQ % CAP), (f"_enq = {ENQ}", ENQ % CAP)):
        mid = x(idx) + CW / 2
        s.append(text(mid, 30, label, t, size=12, mono=True, weight="600"))
        s.append(f'<line x1="{mid}" y1="38" x2="{mid}" y2="{CY - 28}" stroke="{t["text2"]}" '
                 f'stroke-width="1.5" marker-end="url(#ah)"/>')
    # what the consumer sees
    ny = CY + CH + 64
    s.append(text(W / 2, ny, "pop() at ticket 10 reads seq 10, not 11, and returns EMPTY,",
                  t, size=12))
    s.append(text(W / 2, ny + 18, "so every consumer waits at cell 2 while cells 3 to 5 sit full.",
                  t, size=12))
    s.append("</svg>")
    return "\n".join(s)


# ---------------------------------------------------------------- post 4: flatness

# Xeon table from "The Ring That Doesn't Allocate", ns per item, median of 5.
MIXES = ["1×1", "2×2", "4×4", "8×8", "16×16", "1×8", "1×15", "8×1", "15×1"]
GROUPS = [("uncontended", 0, 0), ("balanced", 1, 4), ("fan-out", 5, 6), ("fan-in", 7, 8)]
XEON = {
    "Deque + 1 mutex": [379, 611, 596, 593, 576, 2141, 3762, 353, 368],
    "Two locks":       [993, 743, 1104, 1227, 1203, 886, 810, 1009, 994],
    "Ring (1024)":     [90, 424, 472, 505, 598, 433, 460, 429, 450],
}
SERIES = {"light": ["#257de0", "#f47230", "#00b894"], "dark": ["#338ced", "#e76624", "#00a888"]}
GRID = {"light": "#e4e4e4", "dark": "#38383c"}


def marker(shape, x, y, col, ring):
    if shape == 0:
        return f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="{col}" stroke="{ring}" stroke-width="2"/>'
    if shape == 1:
        return (f'<rect x="{x - 5:.1f}" y="{y - 5:.1f}" width="10" height="10" fill="{col}" '
                f'stroke="{ring}" stroke-width="2"/>')
    if shape == 2:
        return (f'<path d="M{x:.1f},{y - 7:.1f} L{x + 7:.1f},{y:.1f} L{x:.1f},{y + 7:.1f} '
                f'L{x - 7:.1f},{y:.1f} z" fill="{col}" stroke="{ring}" stroke-width="2"/>')
    return (f'<path d="M{x:.1f},{y - 7:.1f} L{x + 6.5:.1f},{y + 5:.1f} L{x - 6.5:.1f},{y + 5:.1f} z" '
            f'fill="{col}" stroke="{ring}" stroke-width="2"/>')


def flatness(mode, t):
    import math
    w, h = 720, 380
    l, r, top, bot = 64, 24, 64, 76
    pw, ph = w - l - r, h - top - bot
    lo, hi = math.log10(60), math.log10(5000)
    step = pw / len(MIXES)
    x = lambda i: l + step * (i + 0.5)
    y = lambda v: top + (hi - math.log10(v)) / (hi - lo) * ph
    cols = SERIES[mode]
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" '
         f'height="{h}" role="img" aria-label="Nanoseconds per item on the Xeon across nine '
         f'thread mixes, log scale. The ring stays between 424 and 598 ns across the eight '
         f'contended mixes; the deque behind one mutex ranges from 353 to 3762 ns; the '
         f'two-lock queue stays between 743 and 1227 ns.">',
         f'<rect width="{w}" height="{h}" fill="{t["surface"]}"/>']
    for v in (100, 250, 500, 1000, 2500):
        s.append(f'<line x1="{l}" x2="{l + pw}" y1="{y(v):.1f}" y2="{y(v):.1f}" '
                 f'stroke="{GRID[mode]}" stroke-width="1"/>')
        s.append(text(l - 8, y(v) + 4, f"{v:,}", t, size=12, color="text2", anchor="end"))
    s.append(text(16, top + ph / 2, "ns per item (log)", t, size=12, color="text2"))
    s[-1] = s[-1].replace("<text ", f'<text transform="rotate(-90 16 {top + ph / 2})" ', 1)
    # the ring's band across the contended mixes
    ring = XEON["Ring (1024)"][1:]
    s.append(f'<rect x="{x(1) - step / 2:.1f}" y="{y(max(ring)):.1f}" width="{step * 8:.1f}" '
             f'height="{y(min(ring)) - y(max(ring)):.1f}" fill="{cols[2]}" opacity="0.14"/>')
    s.append(text(x(1) - step / 2 + 6, y(min(ring)) + 15, "ring, contended: 424 to 598 ns (1.4x)",
                  t, size=11, anchor="start"))
    # group dividers and labels
    for name, a, b in GROUPS:
        if a:
            gx = x(a) - step / 2
            s.append(f'<line x1="{gx:.1f}" x2="{gx:.1f}" y1="{top}" y2="{top + ph + 40}" '
                     f'stroke="{GRID[mode]}" stroke-width="1"/>')
        s.append(text((x(a) + x(b)) / 2, top + ph + 38, name, t, size=12, color="text2"))
    for i, m in enumerate(MIXES):
        s.append(text(x(i), top + ph + 18, m, t, size=12))
    s.append(f'<line x1="{l}" x2="{l + pw}" y1="{top + ph}" y2="{top + ph}" '
             f'stroke="{t["text2"]}" stroke-width="1"/>')
    for k, (name, vals) in enumerate(XEON.items()):
        for i, v in enumerate(vals):
            s.append(marker(k, x(i), y(v), cols[k], t["surface"]))
    # legend
    lx = l
    for k, name in enumerate(XEON):
        s.append(marker(k, lx + 6, 30, cols[k], t["surface"]))
        s.append(text(lx + 18, 34, name, t, size=12, anchor="start"))
        lx += 30 + 7.2 * len(name)
    s.append(text(l + pw, 34, "Xeon, producers × consumers", t, size=12, color="text2", anchor="end"))
    s.append("</svg>")
    return "\n".join(s)


# ---------------------------------------------------------------- post 5: payload sweep

# "Where the Copy Happens" table, Xeon, 4P x 4C, ns per item. Up to 512 B from the main
# sweep, 1 KB and up from own-process runs with glibc trimming off (the post explains why).
SIZES = [8, 64, 256, 512, 1024, 2048, 4096]
PAYLOAD = {  # colour slot follows the queue across posts: deque 0, two-lock 1, ring 2
    "MutexQueue":           (0, [530, 1053, 2040, 2896, 3181, 3599, 4698]),
    "TwoLockQueue":         (1, [1200, 1218, 1221, 1317, 1311, 1572, 2059]),
    "Ring":                 (2, [344, 478, 476, 443, 389, 434, 488]),
    "TwoLockQueueDeferred": (3, [1166, 1199, 1160, 1111, 1172, 1229, 1333]),
}
SERIES4 = {"light": SERIES["light"] + ["#f2a600"], "dark": SERIES["dark"] + ["#d28e05"]}


def payload(mode, t):
    import math
    w, h = 720, 380
    l, r, top, bot = 64, 214, 56, 56
    pw, ph = w - l - r, h - top - bot
    x = lambda n: l + (math.log2(n) - 3) / 9 * pw
    y = lambda v: top + (1 - v / 5000) * ph
    cols = SERIES4[mode]
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" '
         f'height="{h}" role="img" aria-label="Nanoseconds per item against payload size from '
         f'8 bytes to 4 KB, Xeon, four producers and four consumers. MutexQueue climbs from 530 '
         f'to 4698 ns, TwoLockQueue from 1200 to 2059, TwoLockQueueDeferred stays between 1111 '
         f'and 1333, and the ring stays between 344 and 488.">',
         f'<rect width="{w}" height="{h}" fill="{t["surface"]}"/>']
    for v in range(0, 5001, 1000):
        s.append(f'<line x1="{l}" x2="{l + pw}" y1="{y(v):.1f}" y2="{y(v):.1f}" '
                 f'stroke="{GRID[mode]}" stroke-width="1"/>')
        s.append(text(l - 8, y(v) + 4, f"{v:,}", t, size=12, color="text2", anchor="end"))
    for n in SIZES:
        lab = f"{n // 1024} KB" if n >= 1024 else f"{n}"
        s.append(text(x(n), top + ph + 18, lab, t, size=12, color="text2"))
    s.append(text(l + pw / 2, h - 10, "payload size (bytes, log scale)", t, size=12, color="text2"))
    s.append(text(16, top + ph / 2, "ns per item", t, size=12, color="text2"))
    s[-1] = s[-1].replace("<text ", f'<text transform="rotate(-90 16 {top + ph / 2})" ', 1)
    # where the data source changes
    bx = (x(512) + x(1024)) / 2
    s.append(f'<line x1="{bx:.1f}" x2="{bx:.1f}" y1="{top - 6}" y2="{top + ph}" '
             f'stroke="{t["text2"]}" stroke-width="1"/>')
    s.append(text(bx - 6, top - 10, "main sweep", t, size=11, color="text2", anchor="end"))
    s.append(text(bx + 6, top - 10, "own process, trimming off", t, size=11, color="text2",
                  anchor="start"))
    # series, then end labels spaced so they never collide
    ends = []
    for name, (k, vals) in PAYLOAD.items():
        pts = " ".join(f"{x(n):.1f},{y(v):.1f}" for n, v in zip(SIZES, vals))
        s.append(f'<polyline points="{pts}" fill="none" stroke="{cols[k]}" stroke-width="2" '
                 f'stroke-linejoin="round" stroke-linecap="round"/>')
        for n, v in zip(SIZES, vals):
            s.append(marker(k, x(n), y(v), cols[k], t["surface"]))
        ends.append([y(vals[-1]), name, vals[-1], k])
    ends.sort()
    for i in range(1, len(ends)):
        ends[i][0] = max(ends[i][0], ends[i - 1][0] + 16)
    for ly, name, v, k in ends:
        s.append(text(l + pw + 12, ly + 4, f"{name} {v:,}", t, size=12, anchor="start"))
    s.append("</svg>")
    return "\n".join(s)


def main():
    for mode, t in THEMES.items():
        (OUT / f"vyukov-ring-stall-{mode}.svg").write_text(figure(t) + "\n")
        (OUT / f"ring-flatness-{mode}.svg").write_text(flatness(mode, t) + "\n")
        (OUT / f"payload-sweep-{mode}.svg").write_text(payload(mode, t) + "\n")


if __name__ == "__main__":
    main()
