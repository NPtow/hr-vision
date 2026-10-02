"""Render the CJM board contract as an editable Excalidraw scene and SVG sheets.

Run from any directory: python3 cjm/render_v1.py
Input defaults to cjm/v1-board.json. Coordinates in each board are board-local.
Text is deliberately grouped, not container-bound: Excalidraw otherwise repositions
the title into the centre of the node and overlaps its separate body text.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import sys
import time

try:
    from PIL import ImageFont
except ImportError:
    ImageFont = None

ROOT = Path(__file__).resolve().parent
PAPER = "#f4f1e8"
INK = "#20211f"
MUTED = "#696b65"
RULE = "#d6d4cc"
PAD = 18
FONT_PATHS = (
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)
FONT_PATH = next((p for p in FONT_PATHS if Path(p).is_file()), None)
_fonts = {}


def measure(text: str, size: float) -> float:
    """Arial is Excalidraw fontFamily=2; small safety margin protects imports."""
    if ImageFont is not None and FONT_PATH:
        key = (FONT_PATH, round(size * 4))
        if key not in _fonts:
            _fonts[key] = ImageFont.truetype(*key)
        return float(_fonts[key].getlength(text)) / 4 * 1.025
    # Conservative fallback, usable in an otherwise standard-library runtime.
    return sum(size * (.31 if c.isspace() else .35 if c in "ilI.,:;!'|" else
                       .92 if c in "MWЖШЩЮжшщю" else .66) for c in text)


def wrap(value: str, width: float, size: float) -> str:
    """Respect intentional newlines, including blank lines; wrap long words too."""
    if width <= 0:
        raise ValueError(f"Text width must be positive, got {width}")
    result = []
    for paragraph in str(value).split("\n"):
        words = paragraph.split()
        if not words:
            result.append("")
            continue
        line = ""
        for word in words:
            trial = f"{line} {word}" if line else word
            if measure(trial, size) <= width:
                line = trial
                continue
            if line:
                result.append(line)
                line = ""
            while measure(word, size) > width:
                cut = 1
                while cut < len(word) and measure(word[:cut + 1], size) <= width:
                    cut += 1
                result.append(word[:cut])
                word = word[cut:]
            line = word
        if line:
            result.append(line)
    return "\n".join(result)


def text_height(value: str, size: float, line_height: float) -> float:
    return len(value.split("\n")) * size * line_height


def number(value, description: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"{description}: expected a finite number, got {value!r}")
    return value


def safe_id(value: str) -> str:
    # Explicit input IDs must not be silently altered and accidentally collide.
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError(f"Invalid ID {value!r}; use letters, digits, '-' or '_'")
    return value


class Renderer:
    def __init__(self, data: dict):
        self.data = data
        self.elements = []
        self.by_id = {}
        self.sheets = {}
        self.updated = int(time.time() * 1000)
        self.board = None
        self.svg = []
        self.frame = None

    def uid(self, suffix: str) -> str:
        return self.board["id"] + "--" + suffix

    def add(self, element: dict) -> dict:
        eid = element["id"]
        if eid in self.by_id:
            raise ValueError(f"Duplicate element ID: {eid}")
        self.by_id[eid] = element
        self.elements.append(element)
        return element

    def base(self, eid, kind, x, y, w, h, **extra):
        bx, by = self.board.get("x", 0), self.board.get("y", 0)
        seed = int(hashlib.sha256(eid.encode()).hexdigest()[:8], 16) % (2**30)
        item = dict(id=eid, type=kind, x=x + bx, y=y + by, width=w, height=h,
                    angle=0, strokeColor=INK, backgroundColor="transparent",
                    fillStyle="solid", strokeWidth=1.5, strokeStyle="solid",
                    roughness=0, opacity=100, groupIds=[], frameId=self.frame,
                    roundness=None, seed=seed, version=1, versionNonce=seed,
                    isDeleted=False, boundElements=[], updated=self.updated,
                    link=None, locked=False)
        item.update(extra)
        return item

    def text(self, eid, x, y, value, size=18, width=300, color=INK,
             group=None, line_height=1.25, align="left", do_wrap=True):
        original = str(value)
        value = wrap(original, width, size) if do_wrap else original
        height = text_height(value, size, line_height)
        element = self.base(eid, "text", x, y, width, height,
                            strokeColor=color, text=value, originalText=original,
                            fontSize=size, fontFamily=2, textAlign=align,
                            verticalAlign="top", containerId=None,
                            autoResize=False, lineHeight=line_height)
        if group:
            element["groupIds"] = [group]
        self.add(element)
        tx = x if align == "left" else x + width / 2 if align == "center" else x + width
        anchor = "start" if align == "left" else "middle" if align == "center" else "end"
        self.svg.append(f'<text x="{tx:g}" y="{y:g}" fill="{color}" font-size="{size:g}" '
                        f'text-anchor="{anchor}" font-family="Arial, Helvetica, sans-serif">')
        # A common baseline gives SVG and native text equivalent line-box geometry.
        for i, part in enumerate(value.split("\n")):
            baseline = y + size * .96 + i * size * line_height
            self.svg.append(f'<tspan x="{tx:g}" y="{baseline:g}">{html.escape(part)}</tspan>')
        self.svg.append("</text>")
        return height

    def rule(self, eid, points, color=RULE, dashed=False):
        x, y = points[0]
        local = [[a - x, b - y] for a, b in points]
        w = max(p[0] for p in points) - min(p[0] for p in points)
        h = max(p[1] for p in points) - min(p[1] for p in points)
        element = self.base(eid, "line", x, y, w, h, points=local,
                            strokeColor=color, strokeStyle="dashed" if dashed else "solid",
                            lastCommittedPoint=None, startBinding=None, endBinding=None,
                            startArrowhead=None, endArrowhead=None)
        self.add(element)
        self.svg_polyline(points, color, dashed)

    def svg_polyline(self, points, color=INK, dashed=False):
        """Use explicit dash paths: MuPDF's SVG reader ignores stroke-dasharray."""
        if not dashed:
            ps = " ".join(f"{a:g},{b:g}" for a, b in points)
            self.svg.append(f'<polyline points="{ps}" fill="none" stroke="{color}" '
                            f'stroke-width="1.5" stroke-linejoin="miter"/>')
            return
        phase = 0.0
        dash, period = 8.0, 14.0
        paths = []
        for a, b in zip(points, points[1:]):
            length = math.hypot(b[0] - a[0], b[1] - a[1])
            if length < 1e-8:
                continue
            ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
            travelled = 0.0
            while travelled < length - 1e-8:
                visible = phase < dash - 1e-8
                remaining = (dash if visible else period) - phase
                step = min(remaining, length - travelled)
                if visible and step > 1e-8:
                    x1, y1 = a[0] + ux * travelled, a[1] + uy * travelled
                    x2, y2 = a[0] + ux * (travelled + step), a[1] + uy * (travelled + step)
                    paths.append(f"M{x1:g},{y1:g} L{x2:g},{y2:g}")
                travelled += step
                phase = (phase + step) % period
                if phase < 1e-8 or period - phase < 1e-8:
                    phase = 0.0
        self.svg.append(f'<path d="{" ".join(paths)}" fill="none" stroke="{color}" '
                        f'stroke-width="1.5"/>')

    def svg_arrowhead(self, points, color):
        """An actual open-chevron path survives rasterizers without SVG markers."""
        a, b = points[-2], points[-1]
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
        head_length, half_width = 11.0, 5.25
        x, y = b[0] - ux * head_length, b[1] - uy * head_length
        left = (x - uy * half_width, y + ux * half_width)
        right = (x + uy * half_width, y - ux * half_width)
        self.svg.append(f'<path d="M{left[0]:g},{left[1]:g} L{b[0]:g},{b[1]:g} '
                        f'L{right[0]:g},{right[1]:g}" fill="none" stroke="{color}" '
                        f'stroke-width="1.5" stroke-linejoin="miter" data-arrowhead="true"/>')

    def bridges(self, edges):
        """Small editable hops distinguish route crossings from true junctions.

        Common source/target routes and corners are excluded. Arrow geometries and
        bindings stay intact; the two small decorative elements form their own
        editable group. No spline or Excalidraw type-2 roundness is involved.
        """
        seen = set()
        for i, first in enumerate(edges):
            for second in edges[i + 1:]:
                if {first["source"], first["target"]} & {second["source"], second["target"]}:
                    continue
                for a, b in zip(first["points"], first["points"][1:]):
                    for c, d in zip(second["points"], second["points"][1:]):
                        if a[0] == b[0] and c[1] == d[1]:
                            vertical, horizontal, over = (a, b), (c, d), second
                        elif a[1] == b[1] and c[0] == d[0]:
                            vertical, horizontal, over = (c, d), (a, b), first
                        else:
                            continue
                        x, y = vertical[0][0], horizontal[0][1]
                        if not (min(vertical[0][1], vertical[1][1]) + 14 < y < max(vertical[0][1], vertical[1][1]) - 14 and
                                min(horizontal[0][0], horizontal[1][0]) + 14 < x < max(horizontal[0][0], horizontal[1][0]) - 14):
                            continue
                        if (x, y) in seen:
                            continue
                        seen.add((x, y))
                        eid = self.uid(f"crossing-{len(seen)}")
                        group = eid + "-group"
                        meta = dict(role="crossing-decoration", edges=[first["id"], second["id"]])
                        self.add(self.base(eid + "-mask", "rectangle", x - 10, y - 9, 20, 18,
                                           strokeColor=PAPER, backgroundColor=PAPER, strokeWidth=0,
                                           groupIds=[group], customData=meta))
                        self.svg.append(f'<rect x="{x - 10:g}" y="{y - 9:g}" width="20" height="18" fill="{PAPER}"/>')
                        points = [(x - 11, y), (x - 8, y - 5), (x - 4, y - 7),
                                  (x + 4, y - 7), (x + 8, y - 5), (x + 11, y)]
                        color = MUTED if over.get("returning") else INK
                        self.rule(eid + "-hop", points, color)
                        self.by_id[eid + "-hop"]["groupIds"] = [group]
                        self.by_id[eid + "-hop"]["customData"] = meta

    def binding(self, node, point):
        nx, ny, nw, nh = (node[k] for k in ("x", "y", "w", "h"))
        u, v = (point[0] - nx) / nw, (point[1] - ny) / nh
        # Bind the closest boundary point; preserve the explicit endpoint gap.
        candidates = [(0, min(1, max(0, v))), (1, min(1, max(0, v))),
                      (min(1, max(0, u)), 0), (min(1, max(0, u)), 1)]
        fixed = min(candidates, key=lambda p: math.hypot((p[0] - u) * nw, (p[1] - v) * nh))
        gap = math.hypot((fixed[0] - u) * nw, (fixed[1] - v) * nh)
        if gap > 36:
            raise ValueError(f"{self.board['id']}/{node['id']}: arrow endpoint {point} is {gap:.1f}px from node boundary")
        focus = (fixed[1] - .5) * 2 if fixed[0] in (0, 1) else (fixed[0] - .5) * 2
        return dict(elementId=self.uid("node-" + node["id"]), focus=focus,
                    gap=round(gap, 4), fixedPoint=[round(q, 6) for q in fixed])

    def arrow(self, edge, nodes):
        eid = self.uid("edge-" + safe_id(edge["id"]))
        pts = edge["points"]
        if len(pts) < 2:
            raise ValueError(f"{eid}: arrow needs at least two points")
        for p in pts:
            if len(p) != 2:
                raise ValueError(f"{eid}: invalid point {p}")
            for coordinate in p:
                number(coordinate, eid + " point")
        for a, b in zip(pts, pts[1:]):
            if a == b:
                raise ValueError(f"{eid}: duplicate consecutive points {a}")
            if abs(a[0] - b[0]) > .1 and abs(a[1] - b[1]) > .1:
                raise ValueError(f"{eid}: segment {a} -> {b} must be orthogonal")
        for key in ("source", "target"):
            if edge[key] not in nodes:
                raise ValueError(f"{eid}: missing {key} node {edge[key]}")
        x, y = pts[0]
        w = max(p[0] for p in pts) - min(p[0] for p in pts)
        h = max(p[1] for p in pts) - min(p[1] for p in pts)
        color = MUTED if edge.get("returning") else INK
        element = self.base(eid, "arrow", x, y, w, h,
                            points=[[a - x, b - y] for a, b in pts],
                            strokeColor=color,
                            strokeStyle="dashed" if edge.get("returning") else "solid",
                            startArrowhead=None, endArrowhead="arrow", elbowed=False,
                            startBinding=self.binding(nodes[edge["source"]], pts[0]),
                            endBinding=self.binding(nodes[edge["target"]], pts[-1]),
                            lastCommittedPoint=None, customData=edge.get("meta", {}))
        self.add(element)
        self.svg_polyline(pts, color, bool(edge.get("returning")))
        self.svg_arrowhead(pts, color)

    def node_layout(self, node):
        width = node["w"] - PAD * 2
        labels = []
        if node.get("kind") == "decision":
            labels.append("РЕШЕНИЕ")
        elif node.get("kind") == "solution":
            labels.append("HR VISION")
        if node.get("hypothesis"):
            labels.append("ГИПОТЕЗА")
        parts = []
        y = PAD
        if labels:
            value = wrap(" · ".join(labels), width, 14)
            parts.append(("kind", value, 14, 1.25, MUTED, y))
            y += text_height(value, 14, 1.25) + 8
        title = wrap(node["title"], width, 24)
        parts.append(("title", title, 24, 1.2, INK, y))
        y += text_height(title, 24, 1.2)
        if node.get("body"):
            y += 10
            body = wrap(node["body"], width, 18)
            parts.append(("body", body, 18, 1.3, MUTED, y))
            y += text_height(body, 18, 1.3)
        if node.get("refs"):
            y += 10
            refs = wrap(node["refs"], width, 14)
            parts.append(("refs", refs, 14, 1.25, MUTED, y))
            y += text_height(refs, 14, 1.25)
        required = y + PAD
        if required > node["h"] + .01:
            raise ValueError(f"{self.board['id']}/{node['id']}: text does not fit {node['w']}x{node['h']}; "
                             f"requires h >= {math.ceil(required)} at this width")
        return parts

    def node(self, node, edges):
        eid = self.uid("node-" + node["id"])
        group = self.uid("group-" + node["id"])
        x, y, w, h = (node[k] for k in ("x", "y", "w", "h"))
        kind = node.get("kind", "job")
        dashed = node.get("hypothesis", False) or kind == "solution"
        color = MUTED if kind == "note" else INK
        element = self.base(eid, "rectangle", x, y, w, h,
                            strokeColor=color, backgroundColor=PAPER,
                            strokeStyle="dashed" if dashed else "solid",
                            roundness={"type": 3}, groupIds=[group], customData=node)
        element["boundElements"] = [dict(id=self.uid("edge-" + e["id"]), type="arrow")
                                     for e in edges if node["id"] in (e["source"], e["target"])]
        self.add(element)
        self.svg.append(f'<rect x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" rx="12" '
                        f'fill="{PAPER}" stroke="{"none" if dashed else color}" stroke-width="1.5"/>')
        if dashed:
            radius = 12
            points = []
            for cx, cy, start in [(x + w - radius, y + radius, -90),
                                  (x + w - radius, y + h - radius, 0),
                                  (x + radius, y + h - radius, 90),
                                  (x + radius, y + radius, 180)]:
                for angle in range(start, start + 91, 15):
                    radians = math.radians(angle)
                    points.append((cx + math.cos(radians) * radius, cy + math.sin(radians) * radius))
            points.append(points[0])
            self.svg_polyline(points, color, True)
        for suffix, value, size, lh, text_color, dy in self.node_layout(node):
            self.text(eid + "-" + suffix, x + PAD, y + dy, value, size, w - 2 * PAD,
                      text_color, group, lh, do_wrap=False)

    def render_board(self, board):
        self.board = board
        safe_id(board["id"])
        w, h = board["width"], board["height"]
        for key in ("x", "y", "width", "height"):
            number(board.get(key, 0), board["id"] + "." + key)
        if w <= 100 or h <= 100:
            raise ValueError(f"{board['id']}: board must be larger than 100x100")
        self.frame = None
        self.frame = self.uid("frame")
        self.add(self.base(self.frame, "frame", 0, 0, w, h, frameId=None,
                           name=board["title"], strokeColor=RULE))
        self.svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:g}" height="{h:g}" '
                    f'viewBox="0 0 {w:g} {h:g}">',
                    f'<title>{html.escape(board["title"])}</title>',
                    f'<rect width="{w:g}" height="{h:g}" fill="{PAPER}"/>']
        self.text(self.uid("title"), 32, 28, board["title"], 36, w - 64, line_height=1.15)
        if board.get("subtitle"):
            self.text(self.uid("subtitle"), 32, 90, board["subtitle"], 20, w - 64, MUTED)
        for i, column in enumerate(board.get("columns", [])):
            self.text(self.uid(f"column-{i}"), column["x"], 150, column["label"], 18,
                      column.get("width", 260), MUTED)
        for i, lane in enumerate(board.get("lanes", [])):
            self.rule(self.uid(f"lane-{i}-rule"), [(24, lane["y"]), (w - 24, lane["y"])])
            self.text(self.uid(f"lane-{i}-title"), 32, lane["y"] + 12, lane["label"],
                      21, lane.get("width", w - 64), INK)
        nodes = {}
        for node in board.get("nodes", []):
            safe_id(node["id"])
            if node["id"] in nodes:
                raise ValueError(f"{board['id']}: duplicate node ID {node['id']}")
            for key in ("x", "y", "w", "h"):
                number(node[key], f"{board['id']}/{node['id']}.{key}")
            if node["w"] <= PAD * 2 or node["h"] <= PAD * 2:
                raise ValueError(f"{board['id']}/{node['id']}: node is smaller than its padding")
            if (node["x"] < 0 or node["y"] < 0 or node["x"] + node["w"] > w or
                    node["y"] + node["h"] > h):
                raise ValueError(f"{board['id']}/{node['id']}: node is outside board bounds")
            self.node_layout(node)
            nodes[node["id"]] = node
        edges = board.get("edges", [])
        for edge in edges:
            self.arrow(edge, nodes)
        self.bridges(edges)
        for node in nodes.values():
            self.node(node, edges)
        # Arrow annotations stay above boxes and remain independent editable text.
        for edge in edges:
            if edge.get("label"):
                pts = edge["points"]
                x = edge.get("labelX", (pts[0][0] + pts[-1][0]) / 2)
                y = edge.get("labelY", (pts[0][1] + pts[-1][1]) / 2 - 24)
                self.text(self.uid("edge-" + edge["id"] + "-label"), x, y,
                          edge["label"], 16, edge.get("labelWidth", 260), MUTED)
        for i, note in enumerate(board.get("notes", [])):
            self.text(self.uid(f"note-{i}"), note["x"], note["y"], note["text"],
                      note.get("size", 18), note.get("width", w - note["x"] - 32), MUTED)
        self.svg.append("</svg>")
        self.sheets[board["id"]] = "\n".join(self.svg) + "\n"

    def validate(self):
        for e in self.elements:
            for key in ("x", "y", "width", "height"):
                number(e[key], e["id"] + "." + key)
            if e["width"] < 0 or e["height"] < 0:
                raise ValueError(f"{e['id']}: negative geometry")
            if e.get("frameId") and self.by_id[e["frameId"]]["type"] != "frame":
                raise ValueError(f"{e['id']}: invalid parent frame")
            for key in ("startBinding", "endBinding"):
                if e.get(key):
                    target = self.by_id.get(e[key]["elementId"])
                    if target is None or target["type"] != "rectangle":
                        raise ValueError(f"{e['id']}: invalid {key}")
                    if {"id": e["id"], "type": "arrow"} not in target["boundElements"]:
                        raise ValueError(f"{e['id']}: missing reciprocal node binding")
            for bound in e["boundElements"]:
                if bound["id"] not in self.by_id:
                    raise ValueError(f"{e['id']}: broken boundElements {bound['id']}")
            if e["type"] == "text":
                for line in e["text"].split("\n"):
                    if measure(line, e["fontSize"]) > e["width"] + .5:
                        raise ValueError(f"{e['id']}: wrapped text exceeds width")
                frame = self.by_id.get(e.get("frameId"))
                if frame and (e["x"] < frame["x"] or e["y"] < frame["y"] or
                              e["x"] + e["width"] > frame["x"] + frame["width"] + .5 or
                              e["y"] + e["height"] > frame["y"] + frame["height"] + .5):
                    raise ValueError(f"{e['id']}: text is outside its frame bounds")

    def render(self):
        boards = self.data.get("boards", [])
        if not boards:
            raise ValueError("Input must contain at least one board")
        board_ids = [board["id"] for board in boards]
        if len(set(board_ids)) != len(board_ids):
            raise ValueError("Duplicate board IDs")
        for board in boards:
            self.render_board(board)
        self.validate()
        return dict(type="excalidraw", version=2, source="https://excalidraw.com",
                    elements=self.elements, files={},
                    appState=dict(name=self.data.get("title", "HR Vision — CJM v1"),
                                  viewBackgroundColor=PAPER, theme="light",
                                  frameRendering=dict(enabled=True, clip=False, name=True, outline=True),
                                  currentItemStrokeColor=INK, currentItemBackgroundColor="transparent",
                                  currentItemFillStyle="solid", currentItemStrokeWidth=1.5,
                                  currentItemRoughness=0, currentItemFontFamily=2,
                                  currentItemFontSize=24, currentItemEndArrowhead="arrow",
                                  exportBackground=True, exportWithDarkMode=False,
                                  gridSize=20, gridModeEnabled=False))


def atomic_write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "v1-board.json")
    parser.add_argument("--output", type=Path, default=ROOT / "HR Vision — CJM v1.excalidraw")
    parser.add_argument("--preview-dir", type=Path, default=ROOT.parent / "output" / "cjm-v1")
    parser.add_argument("--check", action="store_true", help="Validate without writing outputs")
    args = parser.parse_args()
    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        renderer = Renderer(data)
        scene = renderer.render()
        if not args.check:
            atomic_write(args.output, json.dumps(scene, ensure_ascii=False, indent=2) + "\n")
            for name, svg in renderer.sheets.items():
                atomic_write(args.preview_dir / (name + ".svg"), svg)
        node_count = sum(len(b.get("nodes", [])) for b in data["boards"])
        edge_count = sum(len(b.get("edges", [])) for b in data["boards"])
        print(f"Validated {len(renderer.sheets)} boards, {node_count} nodes, {edge_count} arrows, "
              f"{len(scene['elements'])} editable elements.")
        if not args.check:
            print(args.output)
            print(f"SVG sheets: {args.preview_dir}")
        if ImageFont is None or FONT_PATH is None:
            print("Note: Pillow/Arial unavailable; used conservative fallback text metrics.", file=sys.stderr)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"CJM renderer: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
