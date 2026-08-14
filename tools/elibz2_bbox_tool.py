#!/usr/bin/env python3
"""Batch-export EasyEDA Pro V3 .elibz2 footprint bounding boxes.

The implementation is independent from the EasyEDA application at runtime.  It
reads the public V3 library format and derives the same zero-degree footprint
box that is used by a PCB component.  Unsupported or incomplete footprint
documents fail closed: no estimated row is emitted for them.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import queue
import re
import sys
import threading
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence


MIL_TO_MM = 0.0254
CSV_HEADER = [
    "Designator",
    "Footprint",
    "X-Length of Bottom Edge on Board (Spacing Line)",
    "Y-Width",
    "Z-Height",
]
AUDIT_HEADER = [
    "Footprint",
    "X-Length (mm)",
    "Y-Width (mm)",
    "Z-Height (mm)",
    "Footprint UUID",
    "Format Version",
    "Primitive Types",
    "Input Path",
    "Error Code",
    "Error Description",
    "Status",
]

MAX_MEMBERS = 4096
MAX_MEMBER_SIZE = 64 * 1024 * 1024
MAX_TOTAL_SIZE = 256 * 1024 * 1024
SUPPORTED_EDIT_VERSION = re.compile(r"^3\.2(?:\.|$)")
IGNORED_LAYER_TYPES = {
    "COMPONENT_MODEL",
    "COMPONENT_SHAPE",
    "COMPONENT_MARKING",
    "PIN_SOLDERING",
    "PIN_FLOATING",
}

# A V3 STRING can omit width/height. Exported FONT documents are authoritative;
# these compact built-in-font right-edge metrics cover older archives that omit
# their cache without bundling any font outlines.
DEFAULT_STROKE_FONT_RIGHT = {
    "!": 2, '"': 8, "#": 15, "$": 14, "%": 18, "&": 20, "'": 2,
    "`": 4, "(": 7, ")": 7, "*": 10, "+": 18, ",": 2, "-": 18,
    ".": 2, "/": 18, "0": 14, "1": 5, "2": 14, "3": 14, "4": 15,
    "5": 14, "6": 13, "7": 14, "8": 14, "9": 13, ":": 2, ";": 2,
    "<": 16, "=": 18, ">": 16, "?": 12, "@": 21, "A": 16, "B": 14,
    "C": 15, "D": 14, "E": 13, "F": 13, "G": 15, "H": 14, "I": 0,
    "J": 10, "K": 14, "L": 12, "M": 16, "N": 14, "O": 16, "P": 14,
    "Q": 16, "R": 14, "S": 14, "T": 14, "U": 14, "V": 16, "W": 20,
    "X": 14, "Y": 16, "Z": 14, "[": 7, "\\": 14, "]": 7, "^": 16,
    "_": 18, "a": 12, "b": 12, "c": 12, "d": 12, "e": 12, "f": 8,
    "g": 12, "h": 11, "i": 2, "j": 6, "k": 11, "l": 0, "m": 22,
    "n": 11, "o": 13, "p": 12, "q": 12, "r": 8, "s": 11, "t": 8,
    "u": 11, "v": 12, "w": 16, "x": 11, "y": 13, "z": 11,
    "{": 5, "|": 0, "}": 5, "~": 18, "°": 6, "Ω": 20, "μ": 12,
}
NON_GEOMETRY_TYPES = {
    "ACTIVE_LAYER",
    "CANVAS",
    "CONNECT",
    "DOCHEAD",
    "ELE_PLACEHOLDER",
    "GROUP",
    "LAYER",
    "META",
    "NET",
    "PRIMITIVE",
    "PROP",
}
GEOMETRY_TYPES = {
    "ARC",
    "CARC",
    "FILL",
    "IMAGE",
    "LINE",
    "OBJ",
    "PAD",
    "POLY",
    "POUR",
    "REGION",
    "STRING",
    "VIA",
}


class ToolError(Exception):
    """An expected archive, format, or geometry error with a stable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class BBox:
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    @classmethod
    def from_points(cls, points: Iterable[tuple[float, float]]) -> "BBox":
        values = list(points)
        if not values:
            raise ToolError("MISSING_GEOMETRY", "几何路径不包含任何坐标。")
        xs, ys = zip(*values)
        return cls(min(xs), min(ys), max(xs), max(ys))

    def union(self, other: "BBox") -> "BBox":
        return BBox(
            min(self.min_x, other.min_x),
            min(self.min_y, other.min_y),
            max(self.max_x, other.max_x),
            max(self.max_y, other.max_y),
        )

    def expand(self, amount: float) -> "BBox":
        if amount < 0:
            raise ToolError("INVALID_GEOMETRY", "描边宽度不能为负数。")
        return BBox(
            self.min_x - amount,
            self.min_y - amount,
            self.max_x + amount,
            self.max_y + amount,
        )


@dataclass(frozen=True)
class Record:
    type: str
    id: str
    ticket: float
    client: str
    data: dict[str, Any] | None


@dataclass
class FootprintDocument:
    uuid: str
    edit_version: str
    records: dict[tuple[str, str], Record] = field(default_factory=dict)
    deleted: bool = False

    def merge(self, record: Record) -> None:
        key = (record.type, record.id)
        previous = self.records.get(key)
        # V3 uses ticket as the logical clock.  Equal-ticket client conflicts
        # are made deterministic by choosing the lexicographically smaller id.
        if (
            previous is None
            or record.ticket > previous.ticket
            or (record.ticket == previous.ticket and record.client < previous.client)
        ):
            self.records[key] = record


@dataclass(frozen=True)
class ExportRow:
    input_path: Path
    footprint_uuid: str
    footprint: str
    edit_version: str
    primitive_types: tuple[str, ...]
    bbox: BBox
    x_mm: float
    y_mm: float
    z_mm: float | None
    warning: str = ""


@dataclass(frozen=True)
class AuditRow:
    input_path: Path
    footprint_uuid: str = ""
    footprint: str = ""
    edit_version: str = ""
    primitive_types: tuple[str, ...] = ()
    status: str = "FAILED"
    error_code: str = ""
    description: str = ""
    bbox: BBox | None = None
    x_mm: float | None = None
    y_mm: float | None = None
    z_mm: float | None = None


@dataclass(frozen=True)
class BatchResult:
    rows: tuple[ExportRow, ...]
    audits: tuple[AuditRow, ...]

    @property
    def success_count(self) -> int:
        return sum(a.status == "SUCCESS" for a in self.audits)

    @property
    def failure_count(self) -> int:
        return sum(a.status == "FAILED" for a in self.audits)



def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ToolError("MISSING_GEOMETRY", f"缺少或无效的几何字段：{name}。")
    return float(value)


def _optional_number(value: Any, default: float = 0.0) -> float:
    return default if value is None else _number(value, "optional number")


def _point(x: Any, y: Any, prefix: str = "point") -> tuple[float, float]:
    return _number(x, f"{prefix}.x"), _number(y, f"{prefix}.y")


def _rotate(point: tuple[float, float], angle_deg: float) -> tuple[float, float]:
    angle = math.radians(angle_deg)
    c, s = math.cos(angle), math.sin(angle)
    return point[0] * c - point[1] * s, point[0] * s + point[1] * c


def _transform(
    point: tuple[float, float],
    angle_deg: float = 0.0,
    offset: tuple[float, float] = (0.0, 0.0),
) -> tuple[float, float]:
    x, y = _rotate(point, angle_deg)
    return x + offset[0], y + offset[1]


def _rectangle_bbox(
    center: tuple[float, float], width: float, height: float, angle: float
) -> BBox:
    if width < 0 or height < 0:
        raise ToolError("INVALID_GEOMETRY", "矩形宽高不能为负数。")
    corners = [
        _transform((sx * width / 2, sy * height / 2), angle, center)
        for sx in (-1, 1)
        for sy in (-1, 1)
    ]
    return BBox.from_points(corners)


def _oval_bbox(center: tuple[float, float], width: float, height: float, angle: float) -> BBox:
    if width < 0 or height < 0:
        raise ToolError("INVALID_GEOMETRY", "长圆宽高不能为负数。")
    radius = min(width, height) / 2
    segment = abs(width - height) / 2
    axis_angle = angle + (90 if height > width else 0)
    radians = math.radians(axis_angle)
    half_x = radius + segment * abs(math.cos(radians))
    half_y = radius + segment * abs(math.sin(radians))
    return BBox(center[0] - half_x, center[1] - half_y, center[0] + half_x, center[1] + half_y)


def _angle_on_sweep(candidate: float, start: float, sweep: float) -> bool:
    tau = 2 * math.pi
    if sweep >= 0:
        return (candidate - start) % tau <= sweep + 1e-12
    return (start - candidate) % tau <= -sweep + 1e-12


def _arc_points(
    start: tuple[float, float], end: tuple[float, float], angle_deg: float
) -> list[tuple[float, float]]:
    sweep = math.radians(angle_deg)
    chord_x, chord_y = end[0] - start[0], end[1] - start[1]
    chord = math.hypot(chord_x, chord_y)
    if chord == 0 or abs(math.sin(sweep / 2)) < 1e-12:
        raise ToolError("INVALID_GEOMETRY", "圆弧端点重合或圆心角无效。")
    if abs(sweep) > 2 * math.pi + 1e-9:
        raise ToolError("INVALID_GEOMETRY", "圆弧角超过 360°。")
    midpoint = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
    offset = chord / (2 * math.tan(sweep / 2))
    left = (-chord_y / chord, chord_x / chord)
    center = (midpoint[0] + left[0] * offset, midpoint[1] + left[1] * offset)
    radius = math.hypot(start[0] - center[0], start[1] - center[1])
    start_angle = math.atan2(start[1] - center[1], start[0] - center[0])
    points = [start, end]
    for candidate in (0.0, math.pi / 2, math.pi, 3 * math.pi / 2):
        if _angle_on_sweep(candidate, start_angle, sweep):
            points.append((center[0] + radius * math.cos(candidate), center[1] + radius * math.sin(candidate)))
    return points


def _quadratic_roots(a: float, b: float, c: float) -> list[float]:
    if abs(a) < 1e-12:
        return [] if abs(b) < 1e-12 else [-c / b]
    discriminant = b * b - 4 * a * c
    if discriminant < 0:
        return []
    root = math.sqrt(max(0.0, discriminant))
    return [(-b - root) / (2 * a), (-b + root) / (2 * a)]


def _cubic_points(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
) -> list[tuple[float, float]]:
    values = [p0, p3]
    candidates: set[float] = set()
    for axis in (0, 1):
        a = -p0[axis] + 3 * p1[axis] - 3 * p2[axis] + p3[axis]
        b = 2 * (p0[axis] - 2 * p1[axis] + p2[axis])
        c = p1[axis] - p0[axis]
        candidates.update(root for root in _quadratic_roots(3 * a, 3 * b, 3 * c) if 0 < root < 1)
    for t in candidates:
        u = 1 - t
        values.append(tuple(
            u**3 * p0[axis] + 3 * u * u * t * p1[axis] + 3 * u * t * t * p2[axis] + t**3 * p3[axis]
            for axis in (0, 1)
        ))
    return values


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def path_bbox(
    path: Any,
    *,
    angle: float = 0.0,
    offset: tuple[float, float] = (0.0, 0.0),
) -> BBox:
    if not isinstance(path, list) or not path:
        raise ToolError("MISSING_GEOMETRY", "路径为空或不是数组。")
    # Complex polygons are arrays of paths.  Holes still contribute to the
    # runtime primitive's path box, although normally contained by the shell.
    if isinstance(path[0], list):
        boxes = [path_bbox(item, angle=angle, offset=offset) for item in path]
        return union_boxes(boxes)
    if path[0] == "R":
        if len(path) < 6:
            raise ToolError("MISSING_GEOMETRY", "R 路径字段不足。")
        x, y, width, height, rotation = map(lambda v: _number(v, "R path"), path[1:6])
        center = _transform((x + width / 2, y + height / 2), angle, offset)
        return _rectangle_bbox(center, width, height, angle + rotation)
    if path[0] == "CIRCLE":
        if len(path) < 4:
            raise ToolError("MISSING_GEOMETRY", "CIRCLE 路径字段不足。")
        center = _transform(_point(path[1], path[2], "circle"), angle, offset)
        radius = _number(path[3], "circle.radius")
        if radius < 0:
            raise ToolError("INVALID_GEOMETRY", "圆半径不能为负数。")
        return BBox(center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius)
    if len(path) < 2 or not _is_number(path[0]) or not _is_number(path[1]):
        raise ToolError("UNKNOWN_PATH", "无法识别路径起点。")

    current = _point(path[0], path[1], "path start")
    points = [_transform(current, angle, offset)]
    index = 2
    command = "L"
    while index < len(path):
        if isinstance(path[index], str):
            command = path[index].upper()
            index += 1
        if command == "L":
            if index + 1 >= len(path):
                raise ToolError("UNKNOWN_PATH", "L 路径字段不足。")
            end = _point(path[index], path[index + 1], "line end")
            points.append(_transform(end, angle, offset))
            current, index = end, index + 2
        elif command in {"ARC", "CARC"}:
            if index + 2 >= len(path):
                raise ToolError("UNKNOWN_PATH", "ARC 路径字段不足。")
            arc_angle = _number(path[index], "arc.angle")
            end = _point(path[index + 1], path[index + 2], "arc end")
            local_points = _arc_points(current, end, arc_angle)
            points.extend(_transform(item, angle, offset) for item in local_points)
            current, index = end, index + 3
        elif command == "C":
            if index + 5 >= len(path):
                raise ToolError("UNKNOWN_PATH", "C 路径字段不足。")
            p1 = _point(path[index], path[index + 1], "bezier control 1")
            p2 = _point(path[index + 2], path[index + 3], "bezier control 2")
            end = _point(path[index + 4], path[index + 5], "bezier end")
            points.extend(_transform(item, angle, offset) for item in _cubic_points(current, p1, p2, end))
            current, index = end, index + 6
        else:
            raise ToolError("UNKNOWN_PATH", f"不支持的路径命令：{command}。")
    return BBox.from_points(points)


def union_boxes(boxes: Iterable[BBox]) -> BBox:
    iterator = iter(boxes)
    try:
        result = next(iterator)
    except StopIteration as error:
        raise ToolError("MISSING_GEOMETRY", "封装不包含可合并的几何图元。") from error
    for box in iterator:
        result = result.union(box)
    return result


def _pad_shape_bbox(shape: Any, center: tuple[float, float], angle: float) -> BBox:
    if not isinstance(shape, dict):
        raise ToolError("MISSING_GEOMETRY", "焊盘形状不是对象。")
    pad_type = str(shape.get("padType", "")).upper()
    if pad_type in {"ROUND", "OVAL", "ELLIPSE", "SLOT"}:
        return _oval_bbox(center, _number(shape.get("width"), "pad.width"), _number(shape.get("height"), "pad.height"), angle)
    if pad_type == "RECT":
        return _rectangle_bbox(center, _number(shape.get("width"), "pad.width"), _number(shape.get("height"), "pad.height"), angle)
    if pad_type == "NGON":
        diameter = _number(shape.get("diameter", shape.get("width")), "pad.diameter")
        sides = int(_number(shape.get("edges", shape.get("sides")), "pad.edges"))
        if sides < 3:
            raise ToolError("INVALID_GEOMETRY", "正多边形焊盘边数小于 3。")
        radius = diameter / 2
        return BBox.from_points([
            _transform((radius * math.cos(2 * math.pi * i / sides), radius * math.sin(2 * math.pi * i / sides)), angle, center)
            for i in range(sides)
        ])
    if pad_type in {"POLY", "POLYGON"}:
        path = shape.get("path", shape.get("paths", shape.get("polygon")))
        return path_bbox(path, angle=angle, offset=center)
    raise ToolError("UNSUPPORTED_PAD", f"不支持的焊盘形状：{pad_type or '<empty>'}。")


FontMetricKey = tuple[str, str, float, float, bool, bool, bool, float]


def _font_metric_key(
    text: str,
    font_family: str,
    font_size_tenths: float,
    stroke_width: float,
    bold: bool,
    italic: bool,
    reverse: bool,
    expansion: float,
) -> FontMetricKey:
    return (
        text, font_family, round(font_size_tenths, 6), round(stroke_width, 6),
        bold, italic, reverse, round(expansion, 6),
    )


def _default_string_dimensions(text: str, font_size: float, stroke_width: float) -> tuple[float, float]:
    if font_size <= 0 or stroke_width < 0:
        raise ToolError("INVALID_GEOMETRY", "默认字体字号或线宽无效。")
    # V3's cached default stroke-font boxes use a 22-unit horizontal grid.
    # Inter-character spacing is the line width plus one tenth of the font size.
    scale = font_size / 22
    line_widths: list[float] = []
    for line in text.split("\n"):
        cursor = 0.0
        right_edge = 0.0
        for character in line:
            if character == " ":
                cursor += font_size * 0.7
                continue
            glyph_right = DEFAULT_STROKE_FONT_RIGHT.get(character)
            if glyph_right is None:
                raise ToolError("UNSUPPORTED_FONT_GLYPH", f"默认字体不支持字符：{character!r}。")
            right_edge = max(right_edge, cursor + glyph_right * scale)
            cursor = right_edge + stroke_width + font_size * 0.1
        line_widths.append(right_edge)
    return max(line_widths, default=0.0), font_size * max(1, len(line_widths))


def _string_origin_factors(origin: Any) -> tuple[float, float]:
    names = {
        "LEFT_TOP": 1, "LEFT_MIDDLE": 2, "LEFT_CENTER": 2, "LEFT_BOTTOM": 3,
        "CENTER_TOP": 4, "CENTER": 5, "CENTER_CENTER": 5, "CENTER_MIDDLE": 5, "CENTER_BOTTOM": 6,
        "RIGHT_TOP": 7, "RIGHT_MIDDLE": 8, "RIGHT_CENTER": 8, "RIGHT_BOTTOM": 9,
    }
    if isinstance(origin, str):
        origin = names.get(origin.upper())
    if not isinstance(origin, int) or not 1 <= origin <= 9:
        raise ToolError("INVALID_GEOMETRY", "文字 origin 无效。")
    column = (origin - 1) // 3
    row = (origin - 1) % 3
    return (-column / 2, -(2 - row) / 2)


def _string_bbox(
    data: dict[str, Any],
    angle: float,
    position: tuple[float, float],
    font_metrics: Mapping[FontMetricKey, tuple[float, float]] | None,
) -> BBox:
    font_family = str(data.get("fontFamily", "default") or "default")
    if data.get("mirror"):
        raise ToolError("UNSUPPORTED_TEXT_MIRROR", "暂不支持缺少显式宽高的镜像文字。")
    text = str(data.get("text", ""))
    font_size = _number(data.get("fontSize"), "STRING.fontSize")
    stroke_width = _number(data.get("strokeWidth"), "STRING.strokeWidth")
    metric_key = _font_metric_key(
        text, font_family, font_size / 10, stroke_width,
        bool(data.get("bold")), bool(data.get("italic")), bool(data.get("reverse")),
        _optional_number(data.get("expansion")),
    )
    dimensions = font_metrics.get(metric_key) if font_metrics else None
    if dimensions is not None:
        width, height = dimensions
    elif font_family == "default":
        width, height = _default_string_dimensions(text, font_size, stroke_width)
    else:
        raise ToolError("UNSUPPORTED_FONT", f"缺少可移植字体度量：{font_family}。")
    offset_x, offset_y = _string_origin_factors(data.get("origin", 3))
    local_center = ((offset_x + 0.5) * width, (offset_y + 0.5) * height)
    center = _transform(local_center, angle, position)
    return _rectangle_bbox(center, width, height, angle)


def primitive_bbox(
    record: Record,
    font_metrics: Mapping[FontMetricKey, tuple[float, float]] | None = None,
) -> BBox | None:
    data = record.data
    if data is None:
        return None
    kind = record.type
    if kind == "ATTR" and str(data.get("key", "")):
        return None
    if kind in NON_GEOMETRY_TYPES:
        return None
    if kind in {"LINE", "ARC", "CARC"}:
        start = _point(data.get("startX"), data.get("startY"), "start")
        end = _point(data.get("endX"), data.get("endY"), "end")
        points = [start, end] if kind == "LINE" else _arc_points(start, end, _number(data.get("angle"), "arc.angle"))
        return BBox.from_points(points).expand(_number(data.get("width"), "width") / 2)
    if kind == "VIA":
        center = _point(data.get("centerX"), data.get("centerY"), "via.center")
        diameter = max(_number(data.get("viaDiameter"), "viaDiameter"), _number(data.get("holeDiameter"), "holeDiameter"))
        return BBox(center[0] - diameter / 2, center[1] - diameter / 2, center[0] + diameter / 2, center[1] + diameter / 2)
    if kind == "PAD":
        center = _point(data.get("centerX"), data.get("centerY"), "pad.center")
        angle = _optional_number(data.get("padAngle"))
        boxes = [_pad_shape_bbox(data.get("defaultPad"), center, angle)]
        special = data.get("specialPad", [])
        if special is not None:
            if not isinstance(special, list):
                raise ToolError("MISSING_GEOMETRY", "specialPad 不是数组。")
            for entry in special:
                if not isinstance(entry, (list, tuple)) or len(entry) < 3:
                    raise ToolError("MISSING_GEOMETRY", "specialPad 条目字段不足。")
                boxes.append(_pad_shape_bbox(entry[2], center, angle))
        # The runtime merges holes only for multi-layer pads.  Layer 12 is the
        # V3 multi-layer id used in library documents.
        hole = data.get("hole")
        if hole is not None and data.get("layerId") == 12:
            hole_center = (
                center[0] + _optional_number(data.get("padOffsetX")),
                center[1] + _optional_number(data.get("padOffsetY")),
            )
            hole_angle = _optional_number(data.get("relativeAngle"))
            if isinstance(hole, dict):
                normalized = dict(hole)
                # Current V3 archives name the discriminator `holeType`, while
                # the common pad geometry helper consumes `padType`.
                if "padType" not in normalized and "holeType" in normalized:
                    normalized["padType"] = normalized["holeType"]
            elif isinstance(hole, list) and len(hole) >= 3:
                normalized = {"padType": hole[0], "width": hole[1], "height": hole[2]}
            else:
                raise ToolError("MISSING_GEOMETRY", "孔形状字段不足。")
            boxes.append(_pad_shape_bbox(normalized, hole_center, hole_angle))
        return union_boxes(boxes)
    if kind in {"POLY", "FILL", "REGION", "POUR"}:
        result = path_bbox(data.get("path"))
        # POLY is a stroked path in the PCB runtime.  Filled/constraint paths
        # use their geometric shell; their width controls fill/grid behavior.
        if kind == "POLY":
            result = result.expand(_number(data.get("width"), "poly.width") / 2)
        return result
    if kind in {"IMAGE", "OBJ", "STRING", "ATTR"}:
        path = data.get("path")
        angle = _optional_number(data.get("angle", data.get("rotation")))
        # Exported IMAGE paths use document coordinates and may omit a separate
        # position.  In that representation the path itself is authoritative.
        if kind == "IMAGE" and path and data.get("positionX") is None and data.get("x") is None and data.get("centerX") is None:
            return path_bbox(path)
        position = _point(
            data.get("positionX", data.get("x", data.get("centerX"))),
            data.get("positionY", data.get("y", data.get("centerY"))),
            f"{kind}.position",
        )
        if path:
            return path_bbox(path, angle=angle, offset=position)
        if kind == "STRING":
            return _string_bbox(data, angle, position, font_metrics)
        width = _number(data.get("width"), f"{kind}.width")
        height = _number(data.get("height"), f"{kind}.height")
        # V3 text alignment uses the public 1..9 left/center/right and
        # top/middle/bottom enum. Other image/OBJ records use an explicit center.
        if kind == "ATTR":
            offset_x, offset_y = _string_origin_factors(data.get("origin", 3))
            center = _transform(((offset_x + 0.5) * width, (offset_y + 0.5) * height), angle, position)
        else:
            center = position
        return _rectangle_bbox(center, width, height, angle)
    raise ToolError("UNSUPPORTED_PRIMITIVE", f"不支持的图元类型：{kind}。")


def _iter_json_objects(content: str) -> Iterator[Any]:
    decoder = json.JSONDecoder()
    index = 0
    while index < len(content):
        while index < len(content) and (content[index].isspace() or content[index] == "|"):
            index += 1
        if index >= len(content):
            break
        try:
            value, index = decoder.raw_decode(content, index)
        except json.JSONDecodeError as error:
            raise ToolError("INVALID_ELIBU", f".elibu JSON 日志损坏（字符 {error.pos}）。") from error
        yield value


def _record_from_pair(header: Any, data: Any) -> Record:
    if not isinstance(header, dict) or not isinstance(header.get("type"), str):
        raise ToolError("INVALID_ELIBU", "日志记录头无效。")
    kind = header["type"].upper()
    record_id = str(header.get("id", kind))
    ticket = _optional_number(header.get("ticket"))
    client = str(header.get("client", ""))
    # Empty data is an explicit tombstone in the V3 append-only log.
    payload = None if data == "" else data
    if payload is not None and not isinstance(payload, dict):
        raise ToolError("INVALID_ELIBU", f"{kind}/{record_id} 的数据不是对象。")
    return Record(kind, record_id, ticket, client, payload)


def parse_elibu(content: str, font_records: dict[str, Record] | None = None) -> list[FootprintDocument]:
    objects = list(_iter_json_objects(content))
    if len(objects) % 2:
        raise ToolError("INVALID_ELIBU", ".elibu 日志记录头和数据没有成对出现。")
    documents: dict[str, FootprintDocument] = {}
    current: FootprintDocument | None = None
    in_font_document = False
    for header, data in zip(objects[::2], objects[1::2]):
        if isinstance(header, dict) and str(header.get("type", "")).upper() == "DOCHEAD":
            if not isinstance(data, dict):
                raise ToolError("INVALID_ELIBU", "DOCHEAD 数据无效。")
            document_type = str(data.get("docType", "")).upper()
            in_font_document = document_type == "FONT"
            if document_type != "FOOTPRINT":
                current = None
                continue
            uuid = str(data.get("uuid", ""))
            if not uuid:
                raise ToolError("INVALID_ELIBU", "FOOTPRINT DOCHEAD 缺少 UUID。")
            edit_version = str(data.get("editVersion", ""))
            current = documents.setdefault(uuid, FootprintDocument(uuid, edit_version))
            if edit_version:
                current.edit_version = edit_version
            continue
        if in_font_document:
            record = _record_from_pair(header, data)
            if font_records is not None and record.type == "FONT":
                previous = font_records.get(record.id)
                if (
                    previous is None
                    or record.ticket > previous.ticket
                    or (record.ticket == previous.ticket and record.client < previous.client)
                ):
                    font_records[record.id] = record
            continue
        if current is None:
            continue
        record = _record_from_pair(header, data)
        if record.type == "DELETE_DOC":
            current.deleted = record.data is not None
            continue
        current.merge(record)
    return [document for document in documents.values() if not document.deleted]


def _font_metrics(records: Mapping[str, Record]) -> dict[FontMetricKey, tuple[float, float]]:
    metrics: dict[FontMetricKey, tuple[float, float]] = {}
    for record in records.values():
        if record.data is None:
            continue
        try:
            identity = json.loads(record.id)
            if not isinstance(identity, list) or len(identity) < 8:
                continue
            width = _number(record.data.get("width"), "FONT.width")
            height = _number(record.data.get("height"), "FONT.height")
            if width < 0 or height < 0:
                continue
            key = _font_metric_key(
                str(identity[0]), str(identity[1]), _number(identity[2], "FONT.fontSize"),
                _number(identity[3], "FONT.strokeWidth"), bool(identity[4]), bool(identity[5]),
                bool(identity[6]), _optional_number(identity[7]),
            )
        except (ToolError, TypeError, ValueError, json.JSONDecodeError):
            continue
        metrics[key] = (width, height)
    return metrics


def _safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    if len(members) > MAX_MEMBERS:
        raise ToolError("UNSAFE_ZIP", f"ZIP 成员数超过限制 {MAX_MEMBERS}。")
    total = 0
    for member in members:
        path = PurePosixPath(member.filename.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise ToolError("UNSAFE_ZIP", f"ZIP 含不安全路径：{member.filename}。")
        if member.file_size > MAX_MEMBER_SIZE:
            raise ToolError("UNSAFE_ZIP", f"ZIP 成员过大：{member.filename}。")
        total += member.file_size
        if total > MAX_TOTAL_SIZE:
            raise ToolError("UNSAFE_ZIP", "ZIP 解压总大小超过安全限制。")
    return members


def _read_metadata(archive: zipfile.ZipFile, members: Sequence[zipfile.ZipInfo]) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for member in members:
        if not member.filename.lower().endswith(".json"):
            continue
        try:
            value = json.loads(archive.read(member).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ToolError("INVALID_METADATA", f"无法读取元数据 {member.filename}。") from error
        if isinstance(value, dict):
            documents.append(value)
    return documents


def _metadata_for_uuid(metadata: Sequence[dict[str, Any]], uuid: str) -> tuple[str, float | None, str]:
    names: list[str] = []
    heights: set[float] = set()
    for document in metadata:
        footprints = document.get("footprints", {})
        if isinstance(footprints, dict) and isinstance(footprints.get(uuid), dict):
            item = footprints[uuid]
            name = item.get("display_title") or item.get("title")
            if name:
                names.append(str(name))
        devices = document.get("devices", {})
        if not isinstance(devices, dict):
            continue
        for device in devices.values():
            attributes = device.get("attributes", {}) if isinstance(device, dict) else {}
            if not isinstance(attributes, dict) or str(attributes.get("Footprint", "")) != uuid:
                continue
            title = str(attributes.get("3D Model Title", ""))
            match = re.search(r"(?:^|[_\s-])H\s*(\d+(?:\.\d+)?)(?:\s*mm)?(?:$|[_\s-])", title, re.I)
            if match:
                heights.add(round(float(match.group(1)), 4))
    warning = ""
    height = None
    if len(heights) == 1:
        height = next(iter(heights))
    elif len(heights) > 1:
        warning = "关联设备包含互相冲突的 3D Model Title 高度，Z-Height 已留空。"
    return (names[0] if names else ""), height, warning


def _rounded_dimensions(bbox: BBox) -> tuple[float, float]:
    # The PCB runtime reports a component's translated min/max values.  Their
    # subtraction is the exact template span; computing that span first avoids
    # making the result depend on where a temporary truth component was placed.
    return (
        round((bbox.max_x - bbox.min_x) * MIL_TO_MM, 4),
        round((bbox.max_y - bbox.min_y) * MIL_TO_MM, 4),
    )


def _layer_types(records: Iterable[Record]) -> dict[int, str]:
    result: dict[int, str] = {}
    for record in records:
        if record.type != "LAYER" or record.data is None:
            continue
        try:
            decoded = json.loads(record.id)
            layer_id = int(decoded[1])
        except (ValueError, TypeError, IndexError, json.JSONDecodeError):
            continue
        result[layer_id] = str(record.data.get("layerType", "")).upper()
    return result


def _document_row(
    path: Path,
    document: FootprintDocument,
    metadata: Sequence[dict[str, Any]],
    font_metrics: Mapping[FontMetricKey, tuple[float, float]],
) -> ExportRow:
    if not document.edit_version or not SUPPORTED_EDIT_VERSION.match(document.edit_version):
        raise ToolError("UNSUPPORTED_VERSION", f"不支持的格式版本：{document.edit_version or '<empty>'}。")
    live_records = [record for record in document.records.values() if record.data is not None]
    unknown = sorted({record.type for record in live_records if record.type not in NON_GEOMETRY_TYPES | GEOMETRY_TYPES | {"ATTR"}})
    if unknown:
        raise ToolError("UNSUPPORTED_PRIMITIVE", f"存在未知图元：{', '.join(unknown)}。")
    layers = _layer_types(live_records)
    boxes: list[BBox] = []
    primitive_types: set[str] = set()
    for record in live_records:
        data = record.data or {}
        layer_id = data.get("layerId")
        if isinstance(layer_id, int) and layers.get(layer_id) in IGNORED_LAYER_TYPES:
            continue
        if record.type == "ATTR" and str(data.get("key", "")):
            continue
        box = primitive_bbox(record, font_metrics)
        if box is not None:
            boxes.append(box)
            primitive_types.add(record.type)
    bbox = union_boxes(boxes)
    meta_record = document.records.get(("META", "META"))
    meta_title = str((meta_record.data or {}).get("title", "")) if meta_record else ""
    metadata_title, z_mm, warning = _metadata_for_uuid(metadata, document.uuid)
    title = metadata_title or meta_title or document.uuid
    x_mm, y_mm = _rounded_dimensions(bbox)
    return ExportRow(path, document.uuid, title, document.edit_version, tuple(sorted(primitive_types)), bbox, x_mm, y_mm, z_mm, warning)


def process_archive(path: Path) -> tuple[list[ExportRow], list[AuditRow]]:
    rows: list[ExportRow] = []
    audits: list[AuditRow] = []
    try:
        if path.suffix.lower() != ".elibz2":
            raise ToolError("INVALID_INPUT", "输入文件扩展名不是 .elibz2。")
        with zipfile.ZipFile(path) as archive:
            members = _safe_members(archive)
            elibu_members = [member for member in members if member.filename.lower().endswith(".elibu")]
            if not elibu_members:
                raise ToolError("NO_ELIBU", "归档中没有 .elibu 文件。")
            metadata = _read_metadata(archive, members)
            documents: list[FootprintDocument] = []
            font_records: dict[str, Record] = {}
            for member in elibu_members:
                try:
                    content = archive.read(member).decode("utf-8")
                except UnicodeDecodeError as error:
                    raise ToolError("INVALID_ELIBU", f"{member.filename} 不是 UTF-8。") from error
                documents.extend(parse_elibu(content, font_records))
            font_metrics = _font_metrics(font_records)
        if not documents:
            raise ToolError("NO_FOOTPRINT", "归档中没有有效的 FOOTPRINT 文档。")
        # Different .elibu members may carry successive chunks for one UUID.
        merged: dict[str, FootprintDocument] = {}
        for document in documents:
            target = merged.setdefault(document.uuid, FootprintDocument(document.uuid, document.edit_version))
            target.edit_version = document.edit_version or target.edit_version
            target.deleted = target.deleted or document.deleted
            for record in document.records.values():
                target.merge(record)
        for document in merged.values():
            try:
                row = _document_row(path, document, metadata, font_metrics)
                rows.append(row)
                audits.append(AuditRow(
                    input_path=path,
                    footprint_uuid=row.footprint_uuid,
                    footprint=row.footprint,
                    edit_version=row.edit_version,
                    primitive_types=row.primitive_types,
                    status="SUCCESS",
                    description=row.warning,
                    bbox=row.bbox,
                    x_mm=row.x_mm,
                    y_mm=row.y_mm,
                    z_mm=row.z_mm,
                ))
            except ToolError as error:
                meta_record = document.records.get(("META", "META"))
                title = str((meta_record.data or {}).get("title", "")) if meta_record else ""
                types = tuple(sorted({r.type for r in document.records.values() if r.data is not None and r.type in GEOMETRY_TYPES}))
                audits.append(AuditRow(path, document.uuid, title, document.edit_version, types, "FAILED", error.code, str(error)))
    except (OSError, zipfile.BadZipFile, ToolError) as error:
        code = error.code if isinstance(error, ToolError) else "INVALID_ZIP" if isinstance(error, zipfile.BadZipFile) else "IO_ERROR"
        audits.append(AuditRow(path, status="FAILED", error_code=code, description=str(error)))
    return rows, audits


def discover_inputs(paths: Sequence[Path]) -> list[Path]:
    result: dict[str, Path] = {}
    for supplied in paths:
        path = supplied.expanduser()
        if path.is_dir():
            candidates = path.rglob("*.elibz2")
        else:
            candidates = [path]
        for candidate in candidates:
            try:
                key = os.path.normcase(str(candidate.resolve()))
            except OSError:
                key = os.path.normcase(str(candidate.absolute()))
            result.setdefault(key, candidate.absolute())
    return sorted(result.values(), key=lambda item: os.path.normcase(str(item)))


def process_batch(paths: Sequence[Path], progress: Callable[[int, int, Path], None] | None = None, cancel: threading.Event | None = None) -> BatchResult:
    inputs = discover_inputs(paths)
    if not inputs:
        return BatchResult((), (AuditRow(Path(""), status="FAILED", error_code="NO_INPUT", description="没有找到 .elibz2 输入文件。"),))
    rows: list[ExportRow] = []
    audits: list[AuditRow] = []
    for index, path in enumerate(inputs, 1):
        if cancel and cancel.is_set():
            audits.append(AuditRow(path, status="FAILED", error_code="CANCELLED", description="用户取消处理。"))
            continue
        if progress:
            progress(index, len(inputs), path)
        archive_rows, archive_audits = process_archive(path)
        rows.extend(archive_rows)
        audits.extend(archive_audits)
    rows.sort(key=lambda row: (os.path.normcase(str(row.input_path)), row.footprint.casefold(), row.footprint_uuid))
    audits.sort(key=lambda row: (os.path.normcase(str(row.input_path)), row.footprint.casefold(), row.footprint_uuid, row.status))
    return BatchResult(tuple(rows), tuple(audits))


def audit_path_for(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.stem}-audit{output_path.suffix or '.csv'}")


def _format_number(value: float | None) -> str:
    if value is None:
        return ""
    text = f"{value:.4f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def gui_result_values(audit: AuditRow) -> tuple[str, str, str, str, str, str, str]:
    """Return the user-facing table row with key dimensions first and status last."""
    return (
        audit.footprint,
        _format_number(audit.x_mm),
        _format_number(audit.y_mm),
        _format_number(audit.z_mm),
        audit.input_path.name,
        audit.description or audit.error_code,
        "成功" if audit.status == "SUCCESS" else "失败",
    )


def write_results(result: BatchResult, output_path: Path, *, force: bool = False) -> tuple[Path, Path]:
    audit_path = audit_path_for(output_path)
    if not force:
        existing = [path for path in (output_path, audit_path) if path.exists()]
        if existing:
            raise ToolError("OUTPUT_EXISTS", f"输出已存在：{existing[0]}（使用 --force 覆盖）。")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream, lineterminator="\r\n")
        writer.writerow(CSV_HEADER)
        for row in result.rows:
            writer.writerow(["", row.footprint, _format_number(row.x_mm), _format_number(row.y_mm), _format_number(row.z_mm)])
    with audit_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream, lineterminator="\r\n")
        writer.writerow(AUDIT_HEADER)
        for row in result.audits:
            writer.writerow([
                row.footprint, _format_number(row.x_mm), _format_number(row.y_mm), _format_number(row.z_mm),
                row.footprint_uuid, row.edit_version, ";".join(row.primitive_types),
                str(row.input_path), row.error_code, row.description, row.status,
            ])
    return output_path, audit_path


def _exit_code(result: BatchResult) -> int:
    if result.success_count and not result.failure_count:
        return 0
    if result.success_count:
        return 1
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="独立批量读取 .elibz2 并导出 PCB 0° 封装 BBox。")
    parser.add_argument("inputs", type=Path, nargs="*", help="一个或多个 .elibz2 文件或文件夹（文件夹递归）")
    parser.add_argument("-o", "--output", type=Path, help="汇总 CSV 输出路径")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的主 CSV 和审计 CSV")
    parser.add_argument("--gui", action="store_true", help="打开 Tkinter 图形界面")
    parser.add_argument("--smoke", action="store_true", help="执行无文件、无 GUI 的跨平台冒烟检查")
    parser.add_argument("--simulate-platform", choices=("win32", "darwin", "linux"), help=argparse.SUPPRESS)
    return parser


def platform_smoke_check(platform_name: str | None = None) -> tuple[bool, str]:
    """Exercise platform-neutral pieces without opening a GUI or an archive."""
    try:
        box = path_bbox([0, 0, "ARC", 180, 10, 0]).expand(1)
        dimensions = _rounded_dimensions(box)
        if dimensions[0] <= 0 or dimensions[1] <= 0:
            raise ToolError("SMOKE_FAILED", "几何尺寸无效。")
        with io.StringIO(newline="") as stream:
            csv.writer(stream).writerow(CSV_HEADER)
            if not stream.getvalue():
                raise ToolError("SMOKE_FAILED", "CSV 写入失败。")
        platform_name = platform_name or sys.platform
        if platform_name not in {"win32", "darwin", "linux"}:
            raise ToolError("SMOKE_FAILED", f"不支持的平台：{platform_name}。")
        # Path parsing and BOM encoding are the only platform-sensitive CLI
        # pieces; neither requires a GUI or an actual foreign host to verify.
        path_class = PureWindowsPath if platform_name == "win32" else PurePosixPath
        sample = path_class("C:/fixtures/a.elibz2" if platform_name == "win32" else "/fixtures/a.elibz2")
        if sample.suffix.lower() != ".elibz2" or not CSV_HEADER[0].encode("utf-8-sig").startswith(b"\xef\xbb\xbf"):
            raise ToolError("SMOKE_FAILED", "路径或 UTF-8 BOM 检查失败。")
        return True, f"Python {sys.version_info.major}.{sys.version_info.minor} / {platform_name}"
    except Exception as error:  # pragma: no cover - last-resort CLI diagnostic
        return False, str(error)


def run_cli(args: argparse.Namespace) -> int:
    if not args.inputs:
        print("错误：命令行模式需要至少一个输入文件或文件夹。", file=sys.stderr)
        return 2
    if args.output is None:
        print("错误：命令行模式必须通过 -o 指定输出 CSV。", file=sys.stderr)
        return 2
    result = process_batch(args.inputs)
    try:
        output, audit = write_results(result, args.output, force=args.force)
    except (OSError, ToolError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 2
    print(f"成功 {result.success_count}，失败 {result.failure_count}")
    print(f"汇总：{output}")
    print(f"审计：{audit}")
    return _exit_code(result)


def run_gui(initial_inputs: Sequence[Path] = ()) -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except ImportError:
        print("错误：当前 Python 没有 tkinter；请使用 CLI，Linux 可安装 python3-tk。", file=sys.stderr)
        return 2

    root = tk.Tk()
    root.title(".elibz2 BBox 批量导出工具")
    root.geometry("1080x680")
    root.minsize(900, 560)
    root.columnconfigure(0, weight=1)
    root.rowconfigure(1, weight=2)
    root.rowconfigure(4, weight=3)

    input_paths: list[Path] = discover_inputs(list(initial_inputs))
    output_var = tk.StringVar(value=str(Path.home() / "Downloads" / "elibz2-bbox.csv"))
    status_var = tk.StringVar(value="添加文件或文件夹后开始导出。")
    progress_var = tk.DoubleVar(value=0)
    events: queue.Queue[tuple[str, Any]] = queue.Queue()
    cancel_event = threading.Event()
    worker: threading.Thread | None = None

    toolbar = ttk.Frame(root, padding=(12, 12, 12, 6))
    toolbar.grid(row=0, column=0, sticky="ew")
    for column in range(6):
        toolbar.columnconfigure(column, weight=0)
    toolbar.columnconfigure(6, weight=1)

    files_box = tk.Listbox(root, selectmode=tk.EXTENDED, activestyle="dotbox")
    files_box.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 8))

    def refresh_files() -> None:
        files_box.delete(0, tk.END)
        for path in input_paths:
            files_box.insert(tk.END, str(path))
        status_var.set(f"已选择 {len(input_paths)} 个不重复的 .elibz2 文件。")

    def merge_inputs(paths: Sequence[Path]) -> None:
        nonlocal input_paths
        input_paths = discover_inputs([*input_paths, *paths])
        refresh_files()

    def add_files() -> None:
        names = filedialog.askopenfilenames(title="选择 .elibz2 文件", filetypes=[("EasyEDA library", "*.elibz2"), ("All files", "*.*")])
        merge_inputs([Path(name) for name in names])

    def add_folder() -> None:
        name = filedialog.askdirectory(title="选择文件夹（递归扫描）")
        if name:
            merge_inputs([Path(name)])

    def remove_selected() -> None:
        selected = set(files_box.curselection())
        input_paths[:] = [path for index, path in enumerate(input_paths) if index not in selected]
        refresh_files()

    ttk.Button(toolbar, text="添加文件", command=add_files).grid(row=0, column=0, padx=(0, 6))
    ttk.Button(toolbar, text="添加文件夹", command=add_folder).grid(row=0, column=1, padx=6)
    ttk.Button(toolbar, text="删除所选", command=remove_selected).grid(row=0, column=2, padx=6)
    ttk.Button(toolbar, text="清空", command=lambda: (input_paths.clear(), refresh_files())).grid(row=0, column=3, padx=6)

    output_frame = ttk.Frame(root, padding=(12, 0, 12, 8))
    output_frame.grid(row=2, column=0, sticky="ew")
    output_frame.columnconfigure(1, weight=1)
    ttk.Label(output_frame, text="汇总 CSV：").grid(row=0, column=0, sticky="w")
    ttk.Entry(output_frame, textvariable=output_var).grid(row=0, column=1, sticky="ew", padx=6)

    def choose_output() -> None:
        name = filedialog.asksaveasfilename(title="保存汇总 CSV", defaultextension=".csv", filetypes=[("CSV", "*.csv")], initialfile=Path(output_var.get()).name)
        if name:
            output_var.set(name)

    ttk.Button(output_frame, text="选择位置", command=choose_output).grid(row=0, column=2)

    controls = ttk.Frame(root, padding=(12, 0, 12, 8))
    controls.grid(row=3, column=0, sticky="ew")
    controls.columnconfigure(0, weight=1)
    ttk.Progressbar(controls, variable=progress_var, maximum=100).grid(row=0, column=0, sticky="ew", padx=(0, 8))
    ttk.Label(controls, textvariable=status_var).grid(row=1, column=0, sticky="w", pady=(4, 0))

    columns = ("footprint", "x", "y", "z", "file", "message", "status")
    results = ttk.Treeview(root, columns=columns, show="headings")
    results.grid(row=4, column=0, sticky="nsew", padx=12, pady=(0, 8))
    headings = {
        "footprint": "封装",
        "x": "X 长度 (mm)",
        "y": "Y 宽度 (mm)",
        "z": "Z 高度 (mm)",
        "file": "输入文件",
        "message": "错误/说明",
        "status": "状态",
    }
    widths = {"footprint": 220, "x": 105, "y": 105, "z": 105, "file": 190, "message": 260, "status": 70}
    for column in columns:
        results.heading(column, text=headings[column])
        results.column(column, width=widths[column], stretch=column in {"file", "footprint", "message"})

    button_frame = ttk.Frame(root, padding=(12, 0, 12, 12))
    button_frame.grid(row=5, column=0, sticky="e")
    cancel_button = ttk.Button(button_frame, text="取消", state=tk.DISABLED, command=cancel_event.set)
    cancel_button.grid(row=0, column=0, padx=6)

    def progress(done: int, total: int, path: Path) -> None:
        events.put(("progress", (done, total, path)))

    def work(paths: list[Path], output: Path) -> None:
        result = process_batch(paths, progress, cancel_event)
        try:
            written = write_results(result, output, force=False)
            events.put(("done", (result, written, None)))
        except (OSError, ToolError) as error:
            events.put(("done", (result, None, error)))

    def start() -> None:
        nonlocal worker
        if worker and worker.is_alive():
            return
        if not input_paths:
            messagebox.showerror("无法开始", "请先添加至少一个 .elibz2 文件。")
            return
        output = Path(output_var.get()).expanduser()
        if not output.name:
            messagebox.showerror("无法开始", "请选择汇总 CSV 保存位置。")
            return
        audit = audit_path_for(output)
        if output.exists() or audit.exists():
            if not messagebox.askyesno("覆盖确认", f"输出文件已存在，是否覆盖？\n{output}\n{audit}"):
                return
            force = True
        else:
            force = False
        results.delete(*results.get_children())
        cancel_event.clear()
        start_button.configure(state=tk.DISABLED)
        cancel_button.configure(state=tk.NORMAL)
        progress_var.set(0)

        def gui_work() -> None:
            result = process_batch(list(input_paths), progress, cancel_event)
            try:
                written = write_results(result, output, force=force)
                events.put(("done", (result, written, None)))
            except (OSError, ToolError) as error:
                events.put(("done", (result, None, error)))

        worker = threading.Thread(target=gui_work, daemon=True)
        worker.start()

    start_button = ttk.Button(button_frame, text="开始导出", command=start)
    start_button.grid(row=0, column=1, padx=(6, 0))

    def poll_events() -> None:
        try:
            while True:
                kind, payload = events.get_nowait()
                if kind == "progress":
                    done, total, path = payload
                    progress_var.set(done / total * 100)
                    status_var.set(f"正在处理 {done}/{total}：{path.name}")
                elif kind == "done":
                    result, written, error = payload
                    for audit in result.audits:
                        results.insert("", tk.END, values=gui_result_values(audit))
                    start_button.configure(state=tk.NORMAL)
                    cancel_button.configure(state=tk.DISABLED)
                    if error:
                        status_var.set(f"导出失败：{error}")
                        messagebox.showerror("导出失败", str(error))
                    else:
                        status_var.set(f"完成：成功 {result.success_count}，失败 {result.failure_count}。")
                        messagebox.showinfo("导出完成", f"成功 {result.success_count}，失败 {result.failure_count}\n汇总：{written[0]}\n审计：{written[1]}")
        except queue.Empty:
            pass
        root.after(100, poll_events)

    refresh_files()
    root.after(100, poll_events)
    root.mainloop()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.smoke:
        success, message = platform_smoke_check(args.simulate_platform)
        print(("OK: " if success else "FAILED: ") + message)
        return 0 if success else 2
    if args.gui or (not args.inputs and args.output is None):
        return run_gui(args.inputs)
    return run_cli(args)


if __name__ == "__main__":
    raise SystemExit(main())
