"""Render three CJM scenarios and their editable screen contours.

Input: cjm/v2-board.json, using the v1 board schema plus:
  contours: [{id, label, screenId, nodes: [node IDs]}]
Each contour belongs to one swimlane. Its line is a single editable native
Excalidraw element, not a rectangle, image, group of strokes or embedded SVG.

Run: python3 cjm/render_v2.py [--check] [--png]
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
import json
import math
from pathlib import Path
import sys

# This file lives beside render_v1.py; importing it does not execute its CLI.
from render_v1 import (
    Renderer as BaseRenderer, ROOT, PAPER, INK, MUTED, PAD, FONT_PATH, ImageFont,
    atomic_write, measure, safe_id, text_height, wrap,
)

OUTLINE = "#8b8c85"
CONTOUR_STROKE = 2.5
PADDING_X = 22
PADDING_TOP = 70
PADDING_BOTTOM = 24


def cubic_points(start, control1, control2, end, samples):
    """Sample one cubic, excluding its starting point to avoid duplicate points."""
    points = []
    for step in range(1, samples + 1):
        t = step / samples
        q = 1 - t
        points.append([
            q**3 * start[0] + 3*q*q*t * control1[0] + 3*q*t*t * control2[0] + t**3 * end[0],
            q**3 * start[1] + 3*q*q*t * control1[1] + 3*q*t*t * control2[1] + t**3 * end[1],
        ])
    return points


def organic_outline(left, top, right, bottom):
    """An asymmetrical, softly bowed perimeter with generous corner clearance.

    Dense curve samples keep the same shape in SVG and native line rendering.
    Excalidraw roundness stays null: its sparse-point auto-curves can overshoot
    dramatically on long board contours. The sampled line is visually smooth.
    """
    width, height = right - left, bottom - top
    radius = 36
    start = [left + radius, top + 6]
    segments = [
        # A shallow wave over the screen name, with a slightly higher middle.
        ([left + width * .17, top + 1], [left + width * .32, top - 1], [left + width * .48, top + 3]),
        ([left + width * .65, top + 7], [right - width * .13, top + 1], [right - radius, top + 5]),
        ([right - 10, top + 7], [right + 1, top + 24], [right - 1, top + 52]),
        ([right - 8, top + height * .43], [right + 4, bottom - 65], [right - 3, bottom - 37]),
        ([right - 6, bottom - 10], [right - 24, bottom - 1], [right - 48, bottom - 2]),
        ([left + width * .72, bottom - 6], [left + width * .60, bottom + 1], [left + width * .43, bottom - 3]),
        ([left + width * .25, bottom - 7], [left + width * .12, bottom - 1], [left + 42, bottom - 4]),
        ([left + 11, bottom - 5], [left - 1, bottom - 22], [left + 1, bottom - 47]),
        ([left + 8, top + height * .55], [left - 3, top + 78], [left + 1, top + 45]),
        ([left + 2, top + 19], [left + 14, top + 8], start),
    ]
    result = [start]
    current = start
    for control1, control2, end in segments:
        # Small chords yield a smooth-looking editable line without huge payloads.
        chord = math.hypot(end[0] - current[0], end[1] - current[1])
        samples = max(8, min(80, math.ceil(chord / 20)))
        result.extend(cubic_points(current, control1, control2, end, samples))
        current = end
    result = [[round(x, 4), round(y, 4)] for x, y in result]
    result[-1] = result[0].copy()
    return result


class Renderer(BaseRenderer):
    def text(self, eid, x, y, value, size=18, width=300, color=INK,
             group=None, line_height=1.25, align="left", do_wrap=True):
        needs_mask = (("--edge-" in eid or "--contour-" in eid) and eid.endswith("-label"))
        if needs_mask:
            rendered = wrap(str(value), width, size) if do_wrap else str(value)
            measured_width = max(measure(line, size) for line in rendered.split("\n"))
            height = text_height(rendered, size, line_height)
            left = x if align == "left" else x + (width - measured_width) / 2 if align == "center" else x + width - measured_width
            group = group or eid + "-group"
            self.add(self.base(eid + "-mask", "rectangle", left - 4, y - 3,
                               measured_width + 8, height + 6,
                               strokeColor=PAPER, backgroundColor=PAPER, strokeWidth=0,
                               groupIds=[group], customData=dict(role="text-backdrop", textId=eid)))
            self.svg.append(f'<rect x="{left - 4:g}" y="{y - 3:g}" '
                            f'width="{measured_width + 8:g}" height="{height + 6:g}" '
                            f'fill="{PAPER}" stroke="none" data-label-mask="true"/>')
        return super().text(eid, x, y, value, size, width, color, group, line_height, align, do_wrap)

    def node_layout(self, node):
        parts = super().node_layout(node)
        if node.get("kind") != "external":
            return parts
        width = node["w"] - PAD * 2
        label = "ВНЕ HR VISION" + (" · ГИПОТЕЗА" if node.get("hypothesis") else "")
        label = wrap(label, width, 14)
        label_height = text_height(label, 14, 1.25)
        if parts and parts[0][0] == "kind":
            old_height = text_height(parts[0][1], parts[0][2], parts[0][3])
            shift = label_height - old_height
            rest = parts[1:]
        else:
            shift = label_height + 8
            rest = parts
        result = [("kind", label, 14, 1.25, MUTED, PAD)]
        result.extend((suffix, text, size, lh, MUTED, y + shift)
                      for suffix, text, size, lh, color, y in rest)
        required = max(y + text_height(text, size, lh)
                       for _, text, size, lh, _, y in result) + PAD
        if required > node["h"] + .01:
            raise ValueError(f"{self.board['id']}/{node['id']}: external node text does not fit "
                             f"{node['w']}x{node['h']}; requires h >= {math.ceil(required)}")
        return result

    def node(self, node, edges):
        if node.get("kind") != "external":
            return super().node(node, edges)
        eid = self.uid("node-" + node["id"])
        group = self.uid("group-" + node["id"])
        x, y, w, h = (node[key] for key in ("x", "y", "w", "h"))
        element = self.base(eid, "rectangle", x, y, w, h,
                            strokeColor=MUTED, backgroundColor=PAPER, strokeStyle="dashed",
                            roundness={"type": 3}, groupIds=[group], customData=node)
        element["boundElements"] = [dict(id=self.uid("edge-" + edge["id"]), type="arrow")
                                     for edge in edges if node["id"] in (edge["source"], edge["target"])]
        self.add(element)
        self.svg.append(f'<rect x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" rx="12" '
                        f'fill="{PAPER}" stroke="none"/>')
        points = []
        radius = 12
        for cx, cy, start in [(x + w - radius, y + radius, -90),
                              (x + w - radius, y + h - radius, 0),
                              (x + radius, y + h - radius, 90),
                              (x + radius, y + radius, 180)]:
            for angle in range(start, start + 91, 15):
                radians = math.radians(angle)
                points.append((cx + math.cos(radians) * radius, cy + math.sin(radians) * radius))
        points.append(points[0])
        self.svg_polyline(points, MUTED, True)
        for suffix, value, size, lh, color, dy in self.node_layout(node):
            self.text(eid + "-" + suffix, x + PAD, y + dy, value, size,
                      w - PAD * 2, color, group, lh, do_wrap=False)

    def contour(self, contour, nodes):
        cid = safe_id(contour["id"])
        eid = self.uid("contour-" + cid)
        group = eid + "-group"
        associated = contour.get("nodes", [])
        if not associated or len(set(associated)) != len(associated):
            raise ValueError(f"{eid}: nodes must be a nonempty list of unique job IDs")
        missing = [nid for nid in associated if nid not in nodes]
        if missing:
            raise ValueError(f"{eid}: missing associated jobs {missing}")
        selected = [nodes[nid] for nid in associated]
        external = [node["id"] for node in selected if node.get("kind") == "external"]
        if external:
            raise ValueError(f"{eid}: external steps cannot be enclosed by an HR Vision screen: {external}")

        lane_y = sorted(lane["y"] for lane in self.board.get("lanes", []))
        if lane_y:
            lane_indices = {bisect_right(lane_y, node["y"] + node["h"] / 2) - 1 for node in selected}
            if len(lane_indices) != 1 or -1 in lane_indices:
                raise ValueError(f"{eid}: one contour must belong to exactly one swimlane")
        elif max(node["y"] for node in selected) - min(node["y"] for node in selected) > 60:
            raise ValueError(f"{eid}: selected jobs span multiple rows; split the contour by participant")

        min_x, min_y = min(n["x"] for n in selected), min(n["y"] for n in selected)
        max_x, max_y = max(n["x"] + n["w"] for n in selected), max(n["y"] + n["h"] for n in selected)
        left, top = min_x - PADDING_X, min_y - PADDING_TOP
        right, bottom = max_x + PADDING_X, max_y + PADDING_BOTTOM
        points = organic_outline(left, top, right, bottom)
        bx, by = min(x for x, y in points), min(y for x, y in points)
        bw = max(x for x, y in points) - bx
        bh = max(y for x, y in points) - by
        if bx < 0 or by < 0 or bx + bw > self.board["width"] or by + bh > self.board["height"]:
            raise ValueError(f"{eid}: contour exceeds board bounds; reserve side/top/bottom padding")

        # Noncontiguous membership would visually attribute an unrelated job to this screen.
        accidentally_enclosed = [nid for nid, n in nodes.items() if nid not in associated and
                                min_x <= n["x"] and n["x"] + n["w"] <= max_x and
                                min_y <= n["y"] and n["y"] + n["h"] <= max_y]
        if accidentally_enclosed:
            raise ValueError(f"{eid}: contour also encloses unlisted jobs {accidentally_enclosed}")
        origin_x, origin_y = points[0]
        element = self.base(eid, "line", origin_x, origin_y, bw, bh,
                            strokeColor=OUTLINE, strokeWidth=CONTOUR_STROKE,
                            backgroundColor="transparent", roundness=None,
                            points=[[round(x - origin_x, 4), round(y - origin_y, 4)] for x, y in points],
                            groupIds=[group], lastCommittedPoint=None,
                            startBinding=None, endBinding=None, startArrowhead=None, endArrowhead=None,
                            customData=dict(role="screen-contour", screenId=contour["screenId"],
                                            associatedJobs=list(associated), label=contour["label"]))
        self.add(element)
        coordinates = " ".join(f"{x:g},{y:g}" for x, y in points)
        self.svg.append(f'<polyline points="{coordinates}" fill="none" stroke="{OUTLINE}" '
                        f'stroke-width="{CONTOUR_STROKE:g}" stroke-linejoin="round" '
                        f'data-screen-contour="{cid}"/>')
        screen_id = str(contour["screenId"])
        label = str(contour["label"])
        label = label if label.startswith(screen_id) else f"{screen_id} · {label}"
        label_width = max_x - min_x
        wrapped = wrap(label, label_width, 18)
        if text_height(wrapped, 18, 1.25) > 46:
            raise ValueError(f"{eid}: screen label exceeds two lines; shorten it or enlarge the screen span")
        self.text(eid + "-label", min_x, min_y - 54, wrapped, 18, label_width,
                  MUTED, group, do_wrap=False)

    def render_board(self, board):
        start = len(self.elements)
        super().render_board(board)
        contours = board.get("contours", [])
        if not contours:
            return
        contour_ids = [contour["id"] for contour in contours]
        if len(set(contour_ids)) != len(contour_ids):
            raise ValueError(f"{board['id']}: duplicate contour IDs")
        ordinary_svg = self.svg
        outline_start = len(self.elements)
        nodes = {node["id"]: node for node in board["nodes"]}
        outline_svg, label_svg = [], []
        for contour in contours:
            self.svg = []
            self.contour(contour, nodes)
            # Each contour writes its single line first, then its label backdrop/text.
            outline_svg.append(self.svg[0])
            label_svg.extend(self.svg[1:])
        # Outlines stay behind jobs/arrows; screen labels and their masks stay above.
        contour_elements = self.elements[outline_start:]
        del self.elements[outline_start:]
        outlines = [element for element in contour_elements
                    if element.get("customData", {}).get("role") == "screen-contour"]
        labels = [element for element in contour_elements if element not in outlines]
        self.elements[start + 1:start + 1] = outlines
        self.elements.extend(labels)
        background_index = next(i for i, fragment in enumerate(ordinary_svg) if fragment.startswith("<rect width="))
        ordinary_svg[background_index + 1:background_index + 1] = outline_svg
        ordinary_svg[-1:-1] = label_svg
        self.svg = ordinary_svg
        self.sheets[board["id"]] = "\n".join(self.svg) + "\n"

    def validate(self):
        super().validate()
        for element in self.elements:
            if element.get("customData", {}).get("role") != "screen-contour":
                continue
            if element["type"] != "line" or element["backgroundColor"] != "transparent":
                raise ValueError(f"{element['id']}: screen outline must be one unfilled native line")
            if element["points"][0] != element["points"][-1]:
                raise ValueError(f"{element['id']}: screen contour is not closed")
            if len(element["points"]) < 40:
                raise ValueError(f"{element['id']}: contour is too sparse to remain smooth")


def export_png(svg: str, path: Path, max_width: int):
    try:
        import fitz
    except ImportError as exc:
        raise ValueError("PNG export requires PyMuPDF; omit --png to export native scene and SVG") from exc
    with fitz.open(stream=svg.encode("utf-8"), filetype="svg") as document:
        page = document[0]
        scale = min(1.0, max_width / page.rect.width)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        pixmap.save(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "v2-board.json")
    parser.add_argument("--output", type=Path, default=ROOT / "HR Vision — 3 CJM и интерфейсы v2.excalidraw")
    parser.add_argument("--preview-dir", type=Path, default=ROOT.parent / "output" / "cjm-v2")
    parser.add_argument("--check", action="store_true", help="Validate without creating any output")
    parser.add_argument("--png", action="store_true", help="Also rasterize each SVG with PyMuPDF")
    parser.add_argument("--png-width", type=int, default=2600, help="Maximum PNG width; default 2600")
    args = parser.parse_args()
    try:
        if args.png_width < 100:
            raise ValueError("--png-width must be at least 100")
        data = json.loads(args.input.read_text(encoding="utf-8"))
        renderer = Renderer(data)
        scene = renderer.render()
        scene["appState"]["name"] = data.get("title", "HR Vision — 3 CJM и интерфейсы v2")
        if not args.check:
            atomic_write(args.output, json.dumps(scene, ensure_ascii=False, indent=2) + "\n")
            for board_id, svg in renderer.sheets.items():
                atomic_write(args.preview_dir / (board_id + ".svg"), svg)
                if args.png:
                    export_png(svg, args.preview_dir / (board_id + ".png"), args.png_width)
        boards = data["boards"]
        nodes = sum(len(board.get("nodes", [])) for board in boards)
        edges = sum(len(board.get("edges", [])) for board in boards)
        contours = sum(len(board.get("contours", [])) for board in boards)
        print(f"Validated {len(boards)} boards, {nodes} jobs, {edges} arrows, {contours} screen contours, "
              f"{len(scene['elements'])} editable elements.")
        if not args.check:
            print(args.output)
            print(f"Previews: {args.preview_dir}")
        if ImageFont is None or FONT_PATH is None:
            print("Note: conservative fallback font metrics were used.", file=sys.stderr)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"CJM v2 renderer: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
