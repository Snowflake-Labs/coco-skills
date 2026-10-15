"""Render architecture-diagram.png for the guide.

Run from this folder:  python architecture_diagram.py
"""

import math

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
from matplotlib.transforms import Bbox

# Object type -> (fill, edge). Add types as needed; the legend shows only types used.
STYLE = {
    "SPCS Service":        ("#FFE0B2", "#F57C00"),
    "Stored Procedure":    ("#F8BBD0", "#C2185B"),
    "Table":               ("#B2EBF2", "#0097A7"),
    "Interactive Warehouse": ("#D1C4E9", "#5E35B1"),
    "Standard Warehouse":  ("#E0E0E0", "#616161"),
    "Task":                ("#FFCDD2", "#D32F2F"),
    "Streamlit in Snowflake": ("#C8E6C9", "#388E3C"),
}

PAD_PX = 28     # padding between text and box border, equal on all four sides
GAP_PX = 6      # gap between title and subtitle
LABEL_CLEAR_PX = 6  # minimum gap between an edge label and any box

# key: (x, y, type, title, subtitle)
NODES = {
    "svc":     (2.0, 4.4, "SPCS Service", "INTERACTIVE_ANALYTICS_SVC", "Generates synthetic orders"),
    "sp":      (7.0, 6.9, "Stored Procedure", "SP_SIMULATE_TICK", "Writes orders each tick"),
    "orders":  (12.0, 6.9, "Table", "ORDERS", "Current tick snapshot\n(clustered)"),
    "iwh":     (17.0, 6.9, "Interactive Warehouse", "INTERACTIVE_ANALYTICS_IWH", "Zero-copy, sub-second reads"),
    "history": (12.0, 4.4, "Table", "ORDERS_HISTORY", "Accumulates all ticks"),
    "control": (12.0, 1.9, "Table", "SIMULATION_CONTROL", "Start / Stop & config"),
    "cleanup": (17.0, 4.4, "Task", "HISTORY_CLEANUP", "Purges old rows\ndaily 2am UTC"),
    "autostop": (17.0, 1.9, "Task", "AUTOSTOP", "Stops idle simulation\nevery 15 min"),
    "wh":      (21.6, 4.4, "Standard Warehouse", "INTERACTIVE_ANALYTICS_WH", "DDL, writes, tasks,\nhistory queries"),
    "dash":    (7.0, 1.9, "Streamlit in Snowflake", "LIVE_ORDERS_DASHBOARD", "Live charts & controls"),
}

# (src, dst, label, color, dashed, rad)
EDGES = [
    ("svc", "sp", "CALL every 1s", "#F57C00", False, 0.0),
    ("control", "svc", "Polls config", "#F57C00", True, 0.0),
    ("sp", "orders", "INSERT\nOVERWRITE", "#5E35B1", False, 0.0),
    ("sp", "history", "INSERT INTO", "#0097A7", False, 0.0),
    ("sp", "control", "UPDATE", "#0097A7", False, 0.0),
    ("iwh", "orders", "Reads in place", "#5E35B1", True, 0.0),
    ("orders", "dash", "Low-latency reads\n(via IWH)", "#5E35B1", False, 0.0),
    ("history", "dash", "History queries\n(via WH)", "#0097A7", False, 0.0),
    ("dash", "control", "Start / Stop\nconfig", "#388E3C", False, 0.0),
    ("dash", "svc", "Resume service", "#388E3C", True, 0.0),
    ("cleanup", "history", "DELETE", "#D32F2F", False, 0.0),
    ("autostop", "control", "UPDATE", "#D32F2F", False, 0.0),
    ("wh", "cleanup", "Runs", "#616161", True, 0.0),
    ("iwh", "wh", "Fallback\n(>5 s)", "#616161", True, -0.25),
]

fig, ax = plt.subplots(figsize=(19, 7.6), dpi=160)
ax.set_xlim(0.1, 23.5)
ax.set_ylim(1.2, 7.5)
ax.axis("off")
renderer = fig.canvas.get_renderer()
to_data = ax.transData.inverted()


def px_to_data(dx, dy):
    """Convert a pixel offset to a data-coordinate offset."""
    (x0, y0), (x1, y1) = to_data.transform([(0, 0), (dx, dy)])
    return x1 - x0, y1 - y0


# Draw text first, then size each box to its text with equal pixel padding.
boxes = {}  # key -> Bbox in data coords
for key, (x, y, kind, title, sub) in NODES.items():
    t = ax.text(x, y, title, ha="center", va="bottom", fontsize=9,
                fontweight="bold", color="#212121", zorder=4)
    s = ax.text(x, y, sub, ha="center", va="top", fontsize=7.5,
                color="#424242", zorder=4, linespacing=1.15, multialignment="center")
    tb, sb = t.get_window_extent(renderer), s.get_window_extent(renderer)
    # Stack title above subtitle around (x, y), then re-centre the pair vertically.
    total = tb.height + GAP_PX + sb.height
    _, cy = ax.transData.transform((x, y))
    top = cy + total / 2
    t.set_position((x, to_data.transform((0, top - tb.height))[1]))
    s.set_position((x, to_data.transform((0, top - tb.height - GAP_PX))[1]))
    bb = Bbox.union([t.get_window_extent(renderer), s.get_window_extent(renderer)])
    bb = Bbox.from_extents(bb.x0 - PAD_PX, bb.y0 - PAD_PX, bb.x1 + PAD_PX, bb.y1 + PAD_PX)
    boxes[key] = Bbox(to_data.transform(bb.get_points()))
    fill, edge = STYLE[kind]
    b = boxes[key]
    ax.add_patch(FancyBboxPatch((b.x0, b.y0), b.width, b.height,
                                boxstyle="square,pad=0",
                                fc=fill, ec=edge, lw=2, zorder=3))


def border_point(key, toward):
    """Point on the box border of `key` in the direction of `toward`."""
    b = boxes[key]
    x, y = (b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2
    dx, dy = toward[0] - x, toward[1] - y
    sx = (b.width / 2) / abs(dx) if dx else float("inf")
    sy = (b.height / 2) / abs(dy) if dy else float("inf")
    s = min(sx, sy)
    return x + dx * s, y + dy * s


def center(key):
    b = boxes[key]
    return (b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2



LABEL_GAP_PX = 7  # gap between a label and its own arrow
LINE_CLEAR_PX = LABEL_GAP_PX  # same gap from every other line


def rect_poly(b, pad=0):
    """Display-space corners of a data Bbox, grown by `pad` pixels."""
    (x0, y0), (x1, y1) = ax.transData.transform(b.get_points())
    return [(x0 - pad, y0 - pad), (x1 + pad, y0 - pad), (x1 + pad, y1 + pad), (x0 - pad, y1 + pad)]


def polys_overlap(a, b):
    """Separating-axis test for two convex polygons."""
    for poly in (a, b):
        for i in range(len(poly)):
            (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
            nx, ny = y1 - y2, x2 - x1
            pa = [nx * x + ny * y for x, y in a]
            pb = [nx * x + ny * y for x, y in b]
            if max(pa) < min(pb) or max(pb) < min(pa):
                return False
    return True


box_polys = [rect_poly(b, LABEL_CLEAR_PX) for b in boxes.values()]
placed = []  # rotated outlines of labels already placed


def bezier(P0, P1, P2, t):
    """Point and tangent of a quadratic Bezier at t (display coords)."""
    pt = tuple((1 - t) ** 2 * a + 2 * (1 - t) * t * b + t ** 2 * c for a, b, c in zip(P0, P1, P2))
    tan = tuple(2 * (1 - t) * (b - a) + 2 * t * (c - b) for a, b, c in zip(P0, P1, P2))
    return pt, tan


# Pass 1: draw every arrow and record its path, so labels can avoid all lines.
paths = []  # (D1, C, D2, segments) in display coords
for src, dst, label, color, dashed, rad in EDGES:
    p1, p2 = border_point(src, center(dst)), border_point(dst, center(src))
    ax.add_patch(FancyArrowPatch(p1, p2, arrowstyle="-|>", mutation_scale=14,
                                 color=color, lw=1.6, ls="--" if dashed else "-",
                                 connectionstyle=f"arc3,rad={rad}",
                                 shrinkA=4, shrinkB=4, zorder=2))
    # Same control point matplotlib's arc3 uses, so labels follow curved edges too.
    D1, D2 = ax.transData.transform(p1), ax.transData.transform(p2)
    dx, dy = D2[0] - D1[0], D2[1] - D1[1]
    C = ((D1[0] + D2[0]) / 2 + rad * dy, (D1[1] + D2[1]) / 2 - rad * dx)
    pts = [bezier(D1, C, D2, i / 30)[0] for i in range(31)]
    paths.append((D1, C, D2, list(zip(pts, pts[1:]))))

all_segments = [(i, seg) for i, (*_, segs) in enumerate(paths) for seg in segs]

# Pass 2: place each label clear of boxes, other labels, and every line.
for idx, ((src, dst, label, color, dashed, rad), (D1, C, D2, _)) in enumerate(zip(EDGES, paths)):
    txt = ax.text(0, 0, label, ha="center", va="bottom", fontsize=7.5, color=color,
                  zorder=5, linespacing=1.1, multialignment="center",
                  rotation_mode="anchor")
    ext = txt.get_window_extent(renderer)
    w, h = ext.width, ext.height
    # The text box includes room for descenders below the baseline, which reads as
    # extra gap when the label sits above its line. Add it to labels below so the
    # visible gap is the same on both sides.
    txt.set_va("baseline")
    descent = ext.y1 - txt.get_window_extent(renderer).y1
    txt.set_va("bottom")

    def outline_at(t, side, pad):
        """Rotated label outline beside the edge at t; side=+1 above, -1 below."""
        (px, py), (tx, ty) = bezier(D1, C, D2, t)
        ang = math.degrees(math.atan2(ty, tx))
        if ang > 90:
            ang -= 180
        elif ang <= -90:
            ang += 180
        a = math.radians(ang)
        ux, uy = math.cos(a), math.sin(a)
        nx, ny = -math.sin(a) * side, math.cos(a) * side
        gap = LABEL_GAP_PX + (descent if side < 0 else 0)
        bx, by = px + nx * gap, py + ny * gap
        poly = [(bx + ux * sx * (w / 2 + pad) + nx * (sy * (h + 2 * pad) - pad),
                 by + uy * sx * (w / 2 + pad) + ny * (sy * (h + 2 * pad) - pad))
                for sx, sy in ((-1, 0), (1, 0), (1, 1), (-1, 1))]
        return poly, (bx, by), ang

    candidates = sorted(((i / 40, side) for i in range(4, 37) for side in (1, -1)),
                        key=lambda c: (abs(c[0] - 0.5), -c[1]))
    for t, side in candidates:
        test, anchor, ang = outline_at(t, side, LINE_CLEAR_PX)
        if (not any(polys_overlap(test, p) for p in box_polys)
                and not any(polys_overlap(test, p) for p in placed)
                and not any(polys_overlap(test, list(s)) for i, s in all_segments if i != idx)):
            break
    else:
        print(f"warning: label {label!r} could not be placed cleanly")
    txt.set_position(to_data.transform(anchor))
    txt.set_rotation(ang)
    txt.set_va("bottom" if side > 0 else "top")
    # Store a grown outline so neighbouring labels keep a visible gap.
    placed.append(outline_at(t, side, LABEL_CLEAR_PX)[0])

handles = [Line2D([], [], marker="s", ls="", markersize=11, markerfacecolor=f,
                  markeredgecolor=e, markeredgewidth=1.8, label=k)
           for k, (f, e) in STYLE.items() if k in {n[2] for n in NODES.values()}]
ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.04), ncol=len(handles),
          frameon=False, fontsize=8.5, handletextpad=0.4, columnspacing=1.4)

fig.savefig("architecture-diagram.png", dpi=160, facecolor="white",
            bbox_inches="tight", pad_inches=0.2)
