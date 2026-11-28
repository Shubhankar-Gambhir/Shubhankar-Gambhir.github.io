#!/usr/bin/env python3
"""Generate the call-path figure for "Why std::visit May Be Slower Than a Vtable" (Part 3).

Writes assets/img/visit-vs-vtable-{light,dark}.svg: the GCC 11.4 hot loop and G1
callee for virtual dispatch beside the same for std::visit, with the lambda-capture
round trip through the stack highlighted. Steps follow the post's own listings.
"""
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "assets" / "img"

THEMES = {
    "light": dict(surface="#ffffff", text="#191919", text2="#666666", line="#8a8a8a",
                  box="#f4f4f4", mem="#ececec", warn="#f47230", warn_bg="#fde9de"),
    "dark": dict(surface="#1b1b1e", text="#efefef", text2="#9c9c9c", line="#7a7a7a",
                 box="#26262a", mem="#303034", warn="#e76624", warn_bg="#4a2a1a"),
}
SANS = "-apple-system,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif"
MONO = "'SFMono-Regular',Menlo,Consolas,'Liberation Mono',monospace"
W, H = 720, 400
SW, SH, GAP, Y0 = 200, 46, 22, 66

VIRTUAL = [("load BarrierSet* from stack", "movq 8(%rsp), %rdi", False),
           ("load vptr from the object", "movq (%rdi), %rax", False),
           ("indirect call through vtable[2]", "call *16(%rax)", False),
           ("G1BS::store", "args in registers: 5 instructions", False)]
VISIT = [("spill 2 locals to the capture", "movq %rbp, 16(%rsp) ...", True),
         ("load the variant's index byte", "movzbl 7(%rsp), %eax", False),
         ("indexed call into _S_vtable", "call *(%r14,%rax,8)", False),
         ("__visit_invoke<2>", "reload 2 captures: 15 instructions", True)]


def text(x, y, s, t, size=12, color="text", anchor="start", weight=None, mono=False):
    w = f' font-weight="{weight}"' if weight else ""
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{MONO if mono else SANS}" '
            f'font-size="{size}" text-anchor="{anchor}" fill="{t[color]}"{w}>{s}</text>')


def lane(x0, title, steps, t):
    s = [text(x0, 34, title, t, size=14, weight="600")]
    for i, (what, asm, extra) in enumerate(steps):
        y = Y0 + i * (SH + GAP)
        fill, stroke = (t["warn_bg"], t["warn"]) if extra else (t["box"], t["line"])
        s.append(f'<rect x="{x0}" y="{y}" width="{SW}" height="{SH}" rx="5" fill="{fill}" '
                 f'stroke="{stroke}" stroke-width="{2 if extra else 1.2}"/>')
        s.append(text(x0 + 10, y + 19, what, t, size=12, weight="600" if i == 3 else None))
        s.append(text(x0 + 10, y + 36, asm, t, size=10.5, color="text2", mono=True))
        if i < len(steps) - 1:
            ax = x0 + SW / 2
            s.append(f'<line x1="{ax}" y1="{y + SH}" x2="{ax}" y2="{y + SH + GAP - 3}" '
                     f'stroke="{t["line"]}" stroke-width="1.5" marker-end="url(#ah-line)"/>')
    return s


def mem(x, y, w, h, title, rows, t, highlight=False):
    stroke = t["warn"] if highlight else t["line"]
    s = [text(x, y - 8, title, t, size=11, color="text2")]
    for i, r in enumerate(rows):
        s.append(f'<rect x="{x}" y="{y + i * h}" width="{w}" height="{h}" fill="{t["mem"]}" '
                 f'stroke="{stroke}" stroke-width="{1.5 if highlight else 1}"/>')
        s.append(text(x + 8, y + i * h + h / 2 + 4, r, t, size=10.5, mono=True))
    return s


def figure(t):
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" '
         f'height="{H}" role="img" aria-label="Two call paths compiled by GCC 11.4. Virtual '
         f'dispatch reloads the object pointer, loads the vptr, calls through vtable slot 2, '
         f'and the callee receives its arguments in registers and runs 5 instructions. '
         f'std::visit first stores two locals into a lambda capture struct on the stack, loads '
         f'the variant index byte, calls through the library table, and the callee reads both '
         f'captures back from the stack and runs 15 instructions.">',
         f'<rect width="{W}" height="{H}" fill="{t["surface"]}"/>', "<defs>"]
    for c in ("line", "warn"):
        s.append(f'<marker id="ah-{c}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
                 f'markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" '
                 f'fill="{t[c]}"/></marker>')
    s.append("</defs>")
    # virtual lane and its vtable
    s += lane(20, "virtual dispatch", VIRTUAL, t)
    vy = Y0 + 1 * (SH + GAP) + 6
    s += mem(244, vy, 96, 22, "vtable for G1BS", ["+0", "+8", "+16 store"], t)
    s.append(f'<line x1="{20 + SW}" y1="{Y0 + 2 * (SH + GAP) + SH / 2}" x2="241" '
             f'y2="{vy + 2 * 22 + 11}" stroke="{t["line"]}" stroke-width="1.2" '
             f'marker-end="url(#ah-line)"/>')
    # divider
    s.append(f'<line x1="360" x2="360" y1="16" y2="{H - 16}" stroke="{t["mem"]}" stroke-width="1"/>')
    # visit lane, its capture struct and its table
    s += lane(380, "std::visit (libstdc++ 11.4)", VISIT, t)
    # capture struct on the stack: the two stores in, the two loads back out
    cy, rh = 112, 24
    s += mem(604, cy, 100, rh, "", ["16(%rsp)", "24(%rsp)"], t, highlight=True)
    s.append(text(604, cy + 2 * rh + 16, "capture, on stack", t, size=11, color="text2"))
    s.append(f'<path d="M{380 + SW},{Y0 + SH / 2} H660 V{cy - 3}" fill="none" '
             f'stroke="{t["warn"]}" stroke-width="1.8" marker-end="url(#ah-warn)"/>')
    s.append(text(596, Y0 + SH / 2 - 7, "2 stores", t, size=11, color="text2"))
    ly = Y0 + 3 * (SH + GAP) + SH / 2
    s.append(f'<path d="M704,{cy + rh} H714 V{ly} H{380 + SW + 3}" fill="none" '
             f'stroke="{t["warn"]}" stroke-width="1.8" marker-end="url(#ah-warn)"/>')
    s.append(text(704, ly - 7, "2 loads", t, size=11, color="text2", anchor="end"))
    # the library's function-pointer table, beside the indexed call
    ty = Y0 + 2 * (SH + GAP) + 18
    s += mem(604, ty, 100, 18, "_S_vtable", ["[0] Epsilon", "[1] Serial", "[2] G1"], t)
    s.append(f'<line x1="{380 + SW}" y1="{ty + SH / 2}" x2="601" y2="{ty + 2 * 18 + 9}" '
             f'stroke="{t["line"]}" stroke-width="1.2" marker-end="url(#ah-line)"/>')
    s.append("</svg>")
    return "\n".join(s)


# ---------------------------------------------------------------- Part 5: workloads

# "The Full Picture" tables from "When Dispatch Mechanism Choice Stops Mattering", ns/call.
WORKLOADS = ["mono", "round-robin", "90/10", "random"]
POLY = {
    "GCC 11.4.0": {"virtual": [2.90, 3.05, 5.60, 17.61], "fn ptr": [2.43, 2.88, 4.67, 14.11],
                   "variant": [3.71, 4.65, 7.08, 18.89], "CRTP": [2.42, 2.87, 4.65, 14.11]},
    "GCC 15.2.0": {"virtual": [2.42, 3.05, 5.58, 17.57], "fn ptr": [2.42, 2.88, 4.63, 14.15],
                   "variant": [1.47, 2.24, 4.31, 13.95], "CRTP": [2.41, 2.88, 4.63, 14.15]},
}
COLS = {"light": ["#257de0", "#f47230", "#00b894", "#f2a600"],
        "dark": ["#338ced", "#e76624", "#00a888", "#d28e05"]}
GRIDC = {"light": "#e4e4e4", "dark": "#38383c"}
DRAW_ORDER = ["CRTP", "virtual", "variant", "fn ptr"]   # fn ptr on top of the CRTP it hides
SLOT = {"virtual": 0, "fn ptr": 1, "variant": 2, "CRTP": 3}


def pmark(k, x, y, col, ring):
    if k == 0:
        return f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="{col}" stroke="{ring}" stroke-width="2"/>'
    if k == 1:
        return (f'<rect x="{x - 4.5:.1f}" y="{y - 4.5:.1f}" width="9" height="9" fill="{col}" '
                f'stroke="{ring}" stroke-width="2"/>')
    if k == 2:
        return (f'<path d="M{x:.1f},{y - 6.5:.1f} L{x + 6.5:.1f},{y:.1f} L{x:.1f},{y + 6.5:.1f} '
                f'L{x - 6.5:.1f},{y:.1f} z" fill="{col}" stroke="{ring}" stroke-width="2"/>')
    return (f'<path d="M{x:.1f},{y - 9:.1f} L{x + 8:.1f},{y + 5:.1f} L{x - 8:.1f},{y + 5:.1f} z" '
            f'fill="{col}" stroke="{ring}" stroke-width="2"/>')


def workloads(mode, t):
    import math
    w, h = 720, 380
    top, ph = 64, 240
    panels = [(60, 300), (392, 300)]
    lo, hi = math.log10(1.2), math.log10(25)
    y = lambda v: top + (hi - math.log10(v)) / (hi - lo) * ph
    cols = COLS[mode]
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" '
         f'height="{h}" role="img" aria-label="Nanoseconds per call on a log scale for four '
         f'dispatch mechanisms across four workloads, monomorphic, round-robin, 90/10 and '
         f'random, on GCC 11 and GCC 15. All four climb five to ten times from monomorphic to '
         f'random. Function pointer and CRTP coincide everywhere. Virtual sits about 1 ns above '
         f'function pointer at 90/10 and 3.4 ns above at random. Variant is the slowest on GCC 11 '
         f'and the fastest on GCC 15 at every workload.">',
         f'<rect width="{w}" height="{h}" fill="{t["surface"]}"/>']
    s.append(text(16, top + ph / 2, "ns per call (log)", t, size=12, color="text2"))
    s[-1] = s[-1].replace("<text ", f'<text transform="rotate(-90 16 {top + ph / 2})" ', 1)
    for (px, pw), (title, data) in zip(panels, POLY.items()):
        step = pw / len(WORKLOADS)
        x = lambda i: px + step * (i + 0.5)
        for v in (2, 5, 10, 20):
            s.append(f'<line x1="{px}" x2="{px + pw}" y1="{y(v):.1f}" y2="{y(v):.1f}" '
                     f'stroke="{GRIDC[mode]}" stroke-width="1"/>')
            if px == panels[0][0]:
                s.append(text(px - 8, y(v) + 4, str(v), t, size=12, color="text2", anchor="end"))
        s.append(f'<line x1="{px}" x2="{px + pw}" y1="{top + ph}" y2="{top + ph}" '
                 f'stroke="{t["text2"]}" stroke-width="1"/>')
        s.append(text(px + pw / 2, top - 14, title, t, size=13, weight="600", anchor="middle"))
        for i, wl in enumerate(WORKLOADS):
            s.append(text(x(i), top + ph + 18, wl, t, size=12, color="text2", anchor="middle"))
        for name in DRAW_ORDER:
            k, vals = SLOT[name], data[name]
            pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vals))
            s.append(f'<polyline points="{pts}" fill="none" stroke="{cols[k]}" stroke-width="2" '
                     f'stroke-linejoin="round" stroke-linecap="round"/>')
            for i, v in enumerate(vals):
                s.append(pmark(k, x(i), y(v), cols[k], t["surface"]))
    # legend, one row
    lx, ly = 60, h - 22
    for name in ("virtual", "fn ptr", "variant", "CRTP"):
        k = SLOT[name]
        s.append(f'<line x1="{lx}" x2="{lx + 22}" y1="{ly - 4}" y2="{ly - 4}" stroke="{cols[k]}" '
                 f'stroke-width="2"/>')
        s.append(pmark(k, lx + 11, ly - 4, cols[k], t["surface"]))
        label = "CRTP (under fn ptr)" if name == "CRTP" else name
        s.append(text(lx + 30, ly, label, t, size=12))
        lx += 44 + 7.2 * len(label)
    s.append("</svg>")
    return "\n".join(s)


def main():
    for mode, t in THEMES.items():
        (OUT / f"visit-vs-vtable-{mode}.svg").write_text(figure(t) + "\n")
        (OUT / f"dispatch-workloads-{mode}.svg").write_text(workloads(mode, t) + "\n")


if __name__ == "__main__":
    main()
