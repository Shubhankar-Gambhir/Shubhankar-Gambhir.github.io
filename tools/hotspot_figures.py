#!/usr/bin/env python3
"""Generate the figures for "The Queue Inside a Java Pointer Store" (HotSpot primer part 2).

Writes, each in light and dark:
  assets/img/satb-race-{mode}.svg     the lost-object race, in four frames
  assets/img/satb-buffers-{mode}.svg  the SATB buffer lifecycle (jdk-27-ga)
"""
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "assets" / "img"

THEMES = {
    "light": dict(surface="#ffffff", text="#191919", text2="#666666", line="#8a8a8a",
                  box="#f4f4f4", found="#b9b9b9", scanned="#3a3a3a", scanned_text="#ffffff",
                  accent="#257de0", accent_bg="#e3effc", warn="#f47230"),
    "dark": dict(surface="#1b1b1e", text="#efefef", text2="#9c9c9c", line="#7a7a7a",
                 box="#26262a", found="#6a6a6e", scanned="#d8d8d8", scanned_text="#1b1b1e",
                 accent="#338ced", accent_bg="#1d3350", warn="#e76624"),
}
SANS = "-apple-system,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif"
MONO = "'SFMono-Regular',Menlo,Consolas,'Liberation Mono',monospace"


def text(x, y, s, t, size=12, color="text", anchor="middle", weight=None, mono=False):
    w = f' font-weight="{weight}"' if weight else ""
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{MONO if mono else SANS}" '
            f'font-size="{size}" text-anchor="{anchor}" fill="{t[color]}"{w}>{s}</text>')


def markers(t, names):
    out = ["<defs>"]
    for n in names:
        out.append(f'<marker id="ah-{n}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
                   f'markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" '
                   f'fill="{t[n]}"/></marker>')
    out.append("</defs>")
    return out


# ---------------------------------------------------------------- the race

RACE_FRAMES = [
    # (caption line 1, line 2, colors {A,B,C}, edges [(src, dst, kind)])
    ("1. Marker has", "scanned A",
     dict(A="scanned", B="found", C="unfound"), [("B", "C", "plain")]),
    ("2. Java thread:", "A.field = B.field",
     dict(A="scanned", B="found", C="unfound"), [("B", "C", "plain"), ("A", "C", "new")]),
    ("3. Java thread:", "B.field = null",
     dict(A="scanned", B="found", C="unfound"), [("A", "C", "plain")]),
    ("4. Marker scans B,", "finds nothing",
     dict(A="scanned", B="scanned", C="lost"), [("A", "C", "plain")]),
]


def race(t):
    W, H, FW, R = 720, 276, 180, 19
    pos = {"A": (46, 70), "B": (134, 70), "C": (90, 150)}
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" '
         f'height="{H}" role="img" aria-label="Four frames. The marker has scanned A; B is '
         f'found but not scanned and points to C. A Java thread copies B.field into A.field, '
         f'then nulls B.field. The marker scans B and finds nothing, so C is never marked '
         f'although A still points to it.">',
         f'<rect width="{W}" height="{H}" fill="{t["surface"]}"/>']
    s += markers(t, ["line", "accent", "warn"])
    for i, (c1, c2, colors, edges) in enumerate(RACE_FRAMES):
        x0 = i * FW
        if i:
            s.append(f'<line x1="{x0}" x2="{x0}" y1="24" y2="{H - 60}" stroke="{t["found"]}" '
                     f'stroke-width="1"/>')
        for a, b, kind in edges:
            (ax, ay), (bx, by) = pos[a], pos[b]
            ax, ay, bx, by = x0 + ax, ay, x0 + bx, by
            dx, dy = bx - ax, by - ay
            d = (dx * dx + dy * dy) ** 0.5
            sx, sy = ax + dx / d * R, ay + dy / d * R
            ex, ey = bx - dx / d * (R + 3), by - dy / d * (R + 3)
            col = "accent" if kind == "new" else "line"
            s.append(f'<line x1="{sx:.1f}" y1="{sy:.1f}" x2="{ex:.1f}" y2="{ey:.1f}" '
                     f'stroke="{t[col]}" stroke-width="{2 if kind == "new" else 1.5}" '
                     f'marker-end="url(#ah-{col})"/>')
        for name, (cx, cy) in pos.items():
            state = colors[name]
            cx += x0
            if state == "scanned":
                fill, stroke, tc = t["scanned"], t["scanned"], "scanned_text"
            elif state == "found":
                fill, stroke, tc = t["found"], t["found"], "text"
            elif state == "lost":
                fill, stroke, tc = t["surface"], t["warn"], "text"
            else:
                fill, stroke, tc = t["surface"], t["line"], "text"
            sw = 2.5 if state == "lost" else 1.5
            s.append(f'<circle cx="{cx}" cy="{cy}" r="{R}" fill="{fill}" stroke="{stroke}" '
                     f'stroke-width="{sw}"/>')
            s.append(text(cx, cy + 5, name, t, size=14, weight="700", color=tc))
        s.append(text(x0 + 90, 206, c1, t, size=12))
        s.append(text(x0 + 90, 222, c2, t, size=12, mono=c2.startswith(("A.", "B."))))
    s.append(text(630, 182, "C never marked", t, size=11, color="text2"))
    # legend
    ly, items = H - 22, [("scanned", "scanned"), ("found", "found, not yet scanned"),
                         ("unfound", "not found")]
    x = 150
    for state, label in items:
        fill = {"scanned": t["scanned"], "found": t["found"], "unfound": t["surface"]}[state]
        s.append(f'<circle cx="{x}" cy="{ly - 4}" r="7" fill="{fill}" stroke="'
                 f'{t["line"] if state == "unfound" else fill}" stroke-width="1.5"/>')
        s.append(text(x + 13, ly, label, t, size=12, anchor="start"))
        x += 30 + 7 * len(label)
    s.append("</svg>")
    return "\n".join(s)


# ---------------------------------------------------------------- buffer lifecycle

def box(x, y, w, h, t, fill="box", stroke="line", rx=6, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{t[fill]}" '
            f'stroke="{t[stroke]}" stroke-width="1.5"{d}/>')


def arrow(d, t, col="line", dash=None):
    ds = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<path d="{d}" fill="none" stroke="{t[col]}" stroke-width="1.5"{ds} '
            f'marker-end="url(#ah-{col})"/>')


def buffers(t):
    W, H = 720, 360
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" '
         f'height="{H}" role="img" aria-label="Each Java thread writes old pointer values into '
         f'its own buffer with no atomics, filling it downward. When the buffer is full the '
         f'thread filters it and keeps writing if 60 percent or fewer entries survive; '
         f'otherwise it pushes the buffer onto the shared list with a compare-and-swap and takes '
         f'a fresh one from the buffer allocator. GC marking threads pop buffers from the list, '
         f'process them and return them to the allocator. Partially filled buffers are collected '
         f'from every thread in the stop-the-world pause at the end of marking.">',
         f'<rect width="{W}" height="{H}" fill="{t["surface"]}"/>']
    s += markers(t, ["line", "accent"])
    # Java threads, each with its own buffer filling downward
    for k in range(3):
        y = 40 + k * 64
        s.append(box(20, y, 190, 48, t))
        s.append(text(32, y + 20, f"Java thread {k + 1}", t, size=12, anchor="start", weight="600"))
        s.append(text(32, y + 37, "SATB buffer", t, size=11, color="text2", anchor="start"))
        # buffer bar: slots fill from the right (high index) toward 0
        bx, bw = 118, 80
        s.append(f'<rect x="{bx}" y="{y + 26}" width="{bw}" height="12" fill="none" '
                 f'stroke="{t["line"]}"/>')
        filled = (0.35, 0.7, 1.0)[k]
        s.append(f'<rect x="{bx + bw * (1 - filled):.1f}" y="{y + 26}" width="{bw * filled:.1f}" '
                 f'height="12" fill="{t["accent"]}" opacity="0.55"/>')
    s.append(text(115, 30, "writes: no atomics, no fence", t, size=11, color="text2"))
    # filter loop on thread 3 (the full one)
    s.append(arrow("M120,216 C120,250 60,250 60,220", t))
    s.append(text(20, 268, "full: filter it; if 60% or", t, size=11, color="text2", anchor="start"))
    s.append(text(20, 283, "fewer survive, keep writing", t, size=11, color="text2", anchor="start"))
    # shared list (lock-free stack)
    s.append(box(290, 120, 150, 84, t, fill="accent_bg", stroke="accent"))
    s.append(text(365, 144, "shared list", t, size=12, weight="600"))
    s.append(text(365, 161, "of full buffers", t, size=11, color="text2"))
    s.append(text(365, 178, "(lock-free stack)", t, size=11, color="text2"))
    s.append(arrow("M210,192 C250,192 250,170 286,170", t, col="accent"))
    s.append(text(260, 150, "push: one CAS", t, size=11, color="text2"))
    s.append(text(260, 165, "per buffer", t, size=11, color="text2"))
    # GC marking threads
    s.append(box(520, 120, 180, 84, t))
    s.append(text(610, 152, "GC marking threads", t, size=12, weight="600"))
    s.append(text(610, 170, "mark what the logged", t, size=11, color="text2"))
    s.append(text(610, 185, "pointers refer to", t, size=11, color="text2"))
    s.append(arrow("M440,162 H516", t, col="accent"))
    s.append(text(478, 152, "pop", t, size=11, color="text2"))
    # buffer allocator, bottom
    s.append(box(290, 280, 150, 52, t))
    s.append(text(365, 302, "buffer allocator", t, size=12, weight="600"))
    s.append(text(365, 319, "(lock-free stack)", t, size=11, color="text2"))
    s.append(arrow("M610,204 V306 H444", t))
    s.append(text(560, 296, "return for reuse", t, size=11, color="text2"))
    s.append(arrow("M290,306 H170 V220", t))
    s.append(text(230, 298, "fresh buffer", t, size=11, color="text2"))
    # partial buffers at the pause
    s.append(arrow("M210,72 C380,40 560,40 600,116", t, dash="5 4"))
    s.append(text(420, 36, "partly filled buffers: collected at the pause that ends marking",
                  t, size=11, color="text2"))
    s.append("</svg>")
    return "\n".join(s)


def main():
    for mode, t in THEMES.items():
        (OUT / f"satb-race-{mode}.svg").write_text(race(t) + "\n")
        (OUT / f"satb-buffers-{mode}.svg").write_text(buffers(t) + "\n")


if __name__ == "__main__":
    main()
