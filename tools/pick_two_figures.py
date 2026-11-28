#!/usr/bin/env python3
"""Generate the trade-off triangle for "Pick Two" (HotSpot series).

Writes assets/img/pick-two-triangle-{light,dark}.svg. Corners are the three
guarantees from Xin Liu's March 2021 reply; each edge keeps two of them and lists
the policies and library APIs the post names; numbered markers trace HotSpot's
four answers in order.
"""
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "assets" / "img"

THEMES = {
    "light": dict(surface="#ffffff", text="#191919", text2="#666666", edge="#8a8a8a",
                  accent="#257de0", ring="#ffffff"),
    "dark": dict(surface="#1b1b1e", text="#efefef", text2="#9c9c9c", edge="#7a7a7a",
                 accent="#338ced", ring="#1b1b1e"),
}
SANS = "-apple-system,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif"
MONO = "'SFMono-Regular',Menlo,Consolas,'Liberation Mono',monospace"
W, H = 720, 460
TOP, BL, BR = (360, 64), (210, 312), (510, 312)


def lerp(a, b, f):
    return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)


def text(x, y, s, t, size=12, color="text", anchor="middle", weight=None, mono=False):
    w = f' font-weight="{weight}"' if weight else ""
    s = s.replace("&", "&amp;").replace("<", "&lt;")
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{MONO if mono else SANS}" '
            f'font-size="{size}" text-anchor="{anchor}" fill="{t[color]}"{w}>{s}</text>')


def block(x, y, head, lines, t, anchor):
    """An edge label: bold heading, then policy lines (sans) and API lines (mono)."""
    out = [text(x, y, head, t, size=13, weight="600", anchor=anchor)]
    for i, (s, mono) in enumerate(lines):
        out.append(text(x, y + 18 * (i + 1), s, t, size=11.5, color="text2",
                        anchor=anchor, mono=mono))
    return out


def figure(t):
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" '
         f'height="{H}" role="img" aria-label="A triangle whose corners are non-blocking, '
         f'bounded memory and log fidelity. The edge between non-blocking and bounded memory '
         f'holds the drop policies, the edge between non-blocking and fidelity holds grow, '
         f'and the edge between bounded memory and fidelity holds block. HotSpot moved from '
         f'grow in February 2020 to drop oldest in March 2020, drop newest in May 2021, and '
         f'added stall as an option in February 2025.">',
         f'<rect width="{W}" height="{H}" fill="{t["surface"]}"/>', "<defs>",
         f'<marker id="ah" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" '
         f'orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="{t["accent"]}"/></marker>', "</defs>"]
    s.append(f'<polygon points="{TOP[0]},{TOP[1]} {BL[0]},{BL[1]} {BR[0]},{BR[1]}" fill="none" '
             f'stroke="{t["edge"]}" stroke-width="2" stroke-linejoin="round"/>')
    # corners
    s.append(text(TOP[0], TOP[1] - 16, "non-blocking", t, size=14, weight="600"))
    s.append(text(BL[0] - 10, BL[1] + 26, "bounded memory", t, size=14, weight="600", anchor="end"))
    s.append(text(BR[0] + 10, BR[1] + 26, "log fidelity", t, size=14, weight="600", anchor="start"))
    for (cx, cy) in (TOP, BL, BR):
        s.append(f'<circle cx="{cx}" cy="{cy}" r="5" fill="{t["text"]}"/>')
    # edge labels: each edge keeps its two corners and gives up the third
    s += block(22, 150, "Drop: lose messages", [
        ("drop oldest, drop newest, abort", False),
        ("-Xlog:async (default)", True),
        ("spdlog overrun_oldest, discard_new", True),
        ("moodycamel try_enqueue", True),
        ("boost bounded_push", True)], t, "start")
    s += block(W - 22, 150, "Grow: lose the bound", [
        ("std::queue behind a mutex", True),
        ("moodycamel enqueue", True),
        ("boost push", True)], t, "end")
    s += block(360, 360, "Wait: lose non-blocking", [
        ("block, caller runs", False),
        ("-Xlog:async:stall, spdlog block (default), folly blockingWrite", True)], t, "middle")
    # HotSpot's path: 1 grow -> 2 drop oldest -> 3 drop newest -> 4 stall option
    marks = [lerp(TOP, BR, 0.55), lerp(TOP, BL, 0.38), lerp(TOP, BL, 0.72), lerp(BL, BR, 0.5)]
    for a, b in zip(marks, marks[1:]):
        # stop short of each marker so the arrowhead clears its ring
        dx, dy = b[0] - a[0], b[1] - a[1]
        d = (dx * dx + dy * dy) ** 0.5
        a2 = (a[0] + dx / d * 14, a[1] + dy / d * 14)
        b2 = (b[0] - dx / d * 16, b[1] - dy / d * 16)
        s.append(f'<line x1="{a2[0]:.1f}" y1="{a2[1]:.1f}" x2="{b2[0]:.1f}" y2="{b2[1]:.1f}" '
                 f'stroke="{t["accent"]}" stroke-width="1.5" marker-end="url(#ah)"/>')
    for n, (mx, my) in enumerate(marks, 1):
        s.append(f'<circle cx="{mx:.1f}" cy="{my:.1f}" r="11" fill="{t["accent"]}" '
                 f'stroke="{t["ring"]}" stroke-width="2"/>')
        s.append(text(mx, my + 4.5, str(n), t, size=12, weight="700", color="ring"))
    # legend for the path
    s.append(text(360, H - 22, "HotSpot: 1 grow, Feb 2020 \u00b7 2 drop oldest, Mar 2020 \u00b7 "
                  "3 drop newest, May 2021 \u00b7 4 stall as an option, Feb 2025", t, size=12))
    s.append("</svg>")
    return "\n".join(s)


def main():
    for mode, t in THEMES.items():
        (OUT / f"pick-two-triangle-{mode}.svg").write_text(figure(t) + "\n")


if __name__ == "__main__":
    main()
