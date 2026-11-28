#!/usr/bin/env python3
"""Generate the vtable-layout figure for "The Null Slot" (-fno-rtti Part 1).

Writes assets/img/null-slot-vtable-{light,dark}.svg: the 40-byte vtable for Base
with and without RTTI, the vptr pointing at +0x10, and the typeinfo graph that
only exists in the -frtti build.
"""
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "assets" / "img"

THEMES = {
    "light": dict(surface="#ffffff", text="#191919", text2="#666666", line="#8a8a8a",
                  box="#f4f4f4", accent="#257de0", accent_bg="#e3effc", ghost="#b0b0b0"),
    "dark": dict(surface="#1b1b1e", text="#efefef", text2="#9c9c9c", line="#7a7a7a",
                 box="#26262a", accent="#338ced", accent_bg="#1d3350", ghost="#5a5a5e"),
}
SANS = "-apple-system,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif"
MONO = "'SFMono-Regular',Menlo,Consolas,'Liberation Mono',monospace"

ROWS = [("+0x00", "offset-to-top = 0"),
        ("+0x08", None),                      # the typeinfo slot
        ("+0x10", "&Base::~Base() [D1]"),
        ("+0x18", "&Base::~Base() [D0]"),
        ("+0x20", "&Base::f()")]
W, H, PANEL = 720, 330, 360
ROW_H, VT_X, VT_W, VT_Y = 26, 92, 214, 72


def text(x, y, s, t, size=12, color="text", anchor="start", mono=False, weight=None):
    fam = MONO if mono else SANS
    w = f' font-weight="{weight}"' if weight else ""
    s = s.replace("&", "&amp;").replace("<", "&lt;")
    return (f'<text x="{x}" y="{y}" font-family="{fam}" font-size="{size}" '
            f'text-anchor="{anchor}" fill="{t[color]}"{w}>{s}</text>')


def arrow(d, t, color="line"):
    return (f'<path d="{d}" fill="none" stroke="{t[color]}" stroke-width="1.5" '
            f'marker-end="url(#ah-{color})"/>')


def panel(x0, rtti, t):
    s = [text(x0 + 16, 26, "-frtti" if rtti else "-fno-rtti", t, size=14, weight="600", mono=True)]
    # object
    s.append(text(x0 + 16, VT_Y - 8, "Base object", t, size=11, color="text2"))
    s.append(f'<rect x="{x0 + 16}" y="{VT_Y}" width="56" height="{ROW_H}" rx="3" '
             f'fill="{t["box"]}" stroke="{t["line"]}"/>')
    s.append(text(x0 + 44, VT_Y + 17, "vptr", t, anchor="middle", mono=True))
    # vtable
    vx = x0 + VT_X
    s.append(text(vx, VT_Y - 8, "vtable for Base, 40 bytes", t, size=11, color="text2"))
    for i, (off, label) in enumerate(ROWS):
        y = VT_Y + i * ROW_H
        slot = label is None
        fill = t["accent_bg"] if slot else t["box"]
        s.append(f'<rect x="{vx}" y="{y}" width="{VT_W}" height="{ROW_H}" fill="{fill}" '
                 f'stroke="{t["line"]}"/>')
        s.append(text(vx + 8, y + 17, off, t, size=11, color="text2", mono=True))
        if slot:
            label = "&_ZTI4Base" if rtti else "0 (null)"
        s.append(text(vx + 54, y + 17, label, t, size=11, mono=True,
                      weight="600" if slot else None))
    # vptr -> +0x10
    oy, ty = VT_Y + ROW_H / 2, VT_Y + 2 * ROW_H + ROW_H / 2
    s.append(arrow(f"M{x0 + 72},{oy} H{x0 + 82} V{ty} H{vx - 2}", t))
    # typeinfo graph, or its absence
    gy = VT_Y + 5 * ROW_H + 40
    if rtti:
        sy = VT_Y + ROW_H + ROW_H / 2
        s.append(arrow(f"M{vx + VT_W},{sy} H{vx + VT_W + 18} V{gy - 22} H{vx + 40} V{gy - 2}",
                       t, color="accent"))
        s.append(text(x0 + 16, gy + 17, "_ZTI4Base", t, size=11, color="text2", mono=True))
        for j, lab in enumerate(("vptr -> __class_type_info vtable",
                                 '__name -> _ZTS4Base "4Base"')):
            y = gy + j * ROW_H
            s.append(f'<rect x="{vx}" y="{y}" width="{VT_W + 30}" height="{ROW_H}" '
                     f'fill="{t["accent_bg"]}" stroke="{t["accent"]}"/>')
            s.append(text(vx + 8, y + 17, lab, t, size=11, mono=True))
    else:
        s.append(f'<rect x="{vx}" y="{gy}" width="{VT_W}" height="{2 * ROW_H}" rx="3" '
                 f'fill="none" stroke="{t["ghost"]}" stroke-dasharray="4 3"/>')
        s.append(text(vx + VT_W / 2, gy + 22, "no _ZTI4Base, no _ZTS4Base", t, size=11,
                      color="text2", anchor="middle", mono=True))
        s.append(text(vx + VT_W / 2, gy + 38, "(not emitted)", t, size=11,
                      color="text2", anchor="middle"))
    return s


def figure(t):
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" '
         f'height="{H}" role="img" aria-label="The vtable for Base is 40 bytes with or '
         f'without RTTI. With -frtti, slot +0x08 points to the typeinfo object _ZTI4Base; '
         f'with -fno-rtti the slot stays and holds null, and no typeinfo is emitted.">',
         f'<rect width="{W}" height="{H}" fill="{t["surface"]}"/>', "<defs>"]
    for c in ("line", "accent"):
        s.append(f'<marker id="ah-{c}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
                 f'markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" '
                 f'fill="{t[c]}"/></marker>')
    s.append("</defs>")
    s += panel(0, True, t)
    s.append(f'<line x1="{PANEL}" x2="{PANEL}" y1="12" y2="{H - 12}" stroke="{t["ghost"]}" '
             f'stroke-width="1"/>')
    s += panel(PANEL, False, t)
    s.append("</svg>")
    return "\n".join(s)


def main():
    for mode, t in THEMES.items():
        (OUT / f"null-slot-vtable-{mode}.svg").write_text(figure(t) + "\n")


if __name__ == "__main__":
    main()
