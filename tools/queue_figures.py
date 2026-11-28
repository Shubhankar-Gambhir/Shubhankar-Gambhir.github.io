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


def main():
    for mode, t in THEMES.items():
        (OUT / f"vyukov-ring-stall-{mode}.svg").write_text(figure(t) + "\n")


if __name__ == "__main__":
    main()
