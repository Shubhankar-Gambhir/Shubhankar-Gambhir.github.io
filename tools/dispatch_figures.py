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


def main():
    for mode, t in THEMES.items():
        (OUT / f"visit-vs-vtable-{mode}.svg").write_text(figure(t) + "\n")


if __name__ == "__main__":
    main()
