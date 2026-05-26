from __future__ import annotations

import re
from typing import Any

import fitz

from model.elements import ClipPathDefinition, VectorElement
from model.geometry import BBox
from model.styles import VectorStyle
from resolver.color_resolver import tuple_color_to_css


def _xy(point: Any) -> tuple[float, float]:
    if hasattr(point, "x") and hasattr(point, "y"):
        return float(point.x), float(point.y)
    return float(point[0]), float(point[1])


def _rect_path(rect: fitz.Rect) -> str:
    x0, y0, x1, y1 = float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)
    return (
        f"M {x0:.3f} {y0:.3f} "
        f"L {x1:.3f} {y0:.3f} "
        f"L {x1:.3f} {y1:.3f} "
        f"L {x0:.3f} {y1:.3f} Z"
    )


def items_to_path(items: list, close_path: bool = False) -> str:
    """Convert PyMuPDF drawing items to an SVG path string.

    Supports line ('l'), cubic Bézier ('c'), rect ('re'), quad ('qu') and the
    explicit close marker ('h') if present. Each disconnected segment opens a
    new subpath with M.
    """
    commands: list[str] = []
    current: tuple[float, float] | None = None
    subpath_started_at: tuple[float, float] | None = None
    for item in items:
        op = item[0]
        if op == "l":
            p1 = _xy(item[1])
            p2 = _xy(item[2])
            if current != p1:
                commands.append(f"M {p1[0]:.3f} {p1[1]:.3f}")
                subpath_started_at = p1
            commands.append(f"L {p2[0]:.3f} {p2[1]:.3f}")
            current = p2
        elif op == "c":
            p1 = _xy(item[1])
            c1 = _xy(item[2])
            c2 = _xy(item[3])
            p2 = _xy(item[4])
            if current != p1:
                commands.append(f"M {p1[0]:.3f} {p1[1]:.3f}")
                subpath_started_at = p1
            commands.append(
                f"C {c1[0]:.3f} {c1[1]:.3f} "
                f"{c2[0]:.3f} {c2[1]:.3f} "
                f"{p2[0]:.3f} {p2[1]:.3f}"
            )
            current = p2
        elif op == "re":
            commands.append(_rect_path(item[1]))
            current = None
            subpath_started_at = None
        elif op == "qu":
            quad = item[1]
            points = [_xy(quad.ul), _xy(quad.ur), _xy(quad.lr), _xy(quad.ll)]
            commands.append(f"M {points[0][0]:.3f} {points[0][1]:.3f}")
            for point in points[1:]:
                commands.append(f"L {point[0]:.3f} {point[1]:.3f}")
            commands.append("Z")
            current = None
            subpath_started_at = None
        elif op == "h":
            if subpath_started_at:
                commands.append("Z")
                current = subpath_started_at
    if close_path and commands and not commands[-1].endswith("Z"):
        commands.append("Z")
    return " ".join(commands)


def parse_vectors(
    page: fitz.Page,
    page_index: int,
    diagnostics: list[dict] | None = None,
) -> tuple[list[VectorElement], list[ClipPathDefinition]]:
    diagnostics = diagnostics if diagnostics is not None else []
    drawings = _safe_get_drawings(page, diagnostics, page_index)
    if not drawings:
        return [], []

    clip_definitions: list[ClipPathDefinition] = []
    # Stack of (clip_id, level). We push when we see a "clip" entry and pop when
    # the next entry's level is shallower or equal.
    clip_stack: list[tuple[str, int]] = []
    # Stack of (group_info, level) for transparency groups.
    group_stack: list[tuple[dict, int]] = []

    elements: list[VectorElement] = []
    draw_index = 0
    clip_index = 0

    for entry in drawings:
        entry_type = entry.get("type")
        level = int(entry.get("level", 0) or 0)
        _trim_stack(clip_stack, level)
        _trim_stack(group_stack, level)

        if entry_type == "clip":
            clip_path_data = items_to_path(
                entry.get("items", []), close_path=bool(entry.get("closePath"))
            )
            if not clip_path_data:
                scissor = entry.get("scissor") or page.rect
                clip_path_data = _rect_path(scissor)
            scissor = entry.get("scissor") or page.rect
            clip_bbox = BBox(
                float(scissor.x0), float(scissor.y0),
                float(scissor.x1), float(scissor.y1)
            )
            clip_id = f"pdf-page-{page_index + 1}-clip-{clip_index}"
            clip_definitions.append(
                ClipPathDefinition(
                    stable_id=clip_id,
                    path_data=clip_path_data,
                    bbox=clip_bbox,
                    even_odd=bool(entry.get("even_odd", False)),
                    level=level,
                )
            )
            clip_stack.append((clip_id, level))
            clip_index += 1
            continue

        if entry_type == "group":
            group_stack.append(
                (
                    {
                        "blend_mode": entry.get("blendmode") or "Normal",
                        "opacity": _opacity(entry.get("opacity"), 1.0),
                        "isolated": bool(entry.get("isolated", False)),
                        "knockout": bool(entry.get("knockout", False)),
                    },
                    level,
                )
            )
            blend_mode = group_stack[-1][0]["blend_mode"]
            if blend_mode and blend_mode not in {"Normal", ""}:
                diagnostics.append(
                    {
                        "severity": "info",
                        "code": "vector-blendmode",
                        "page": page_index + 1,
                        "blend_mode": blend_mode,
                    }
                )
            continue

        if entry_type not in {"f", "s", "fs", "F", "S"}:
            continue

        path_data = items_to_path(
            entry.get("items", []), close_path=bool(entry.get("closePath"))
        )
        if not path_data:
            continue

        rect = entry.get("rect") or page.rect
        style = _build_style(entry, diagnostics, page_index)
        clip_id = clip_stack[-1][0] if clip_stack else None
        group_blend = group_stack[-1][0]["blend_mode"] if group_stack else None
        group_opacity = group_stack[-1][0]["opacity"] if group_stack else 1.0

        seqno_raw = entry.get("seqno")
        try:
            seqno_int = int(seqno_raw) if seqno_raw is not None else draw_index
        except (TypeError, ValueError):
            seqno_int = draw_index
        elements.append(
            VectorElement(
                stable_id=f"pdf-page-{page_index + 1}-path-{draw_index}",
                type="vector",
                page_index=page_index + 1,
                bbox=BBox(float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)),
                z_index=0,
                source_index=draw_index,
                render_mode="semantic-svg",
                editable=False,
                source={
                    "source": "page.get_drawings(extended=True)",
                    "draw_type": entry_type,
                    "seqno": seqno_int,
                    "layer": entry.get("layer", ""),
                    "level": level,
                },
                diagnostics=[],
                path_data=path_data,
                style=style,
                even_odd=bool(entry.get("even_odd", False)),
                clip_path_id=clip_id,
                blend_mode=group_blend if group_blend not in {None, "Normal"} else None,
                group_opacity=group_opacity,
                paint_seqno=seqno_int,
            )
        )
        draw_index += 1

    return elements, clip_definitions


def _safe_get_drawings(
    page: fitz.Page, diagnostics: list[dict], page_index: int
) -> list[dict]:
    try:
        return page.get_drawings(extended=True)
    except TypeError:
        pass
    except Exception as exc:
        diagnostics.append(
            {
                "severity": "warning",
                "code": "drawings-extended-failed",
                "page": page_index + 1,
                "message": str(exc),
            }
        )
    try:
        return page.get_drawings()
    except Exception as exc:
        diagnostics.append(
            {
                "severity": "warning",
                "code": "drawings-failed",
                "page": page_index + 1,
                "message": str(exc),
            }
        )
        return []


def _build_style(entry: dict, diagnostics: list[dict], page_index: int) -> VectorStyle:
    fill = tuple_color_to_css(
        entry.get("fill"),
        diagnostics=diagnostics,
        context=f"page{page_index + 1}.vector.fill",
    )
    stroke = tuple_color_to_css(
        entry.get("color"),
        diagnostics=diagnostics,
        context=f"page{page_index + 1}.vector.stroke",
    )
    dash_value = entry.get("dashes")
    dash_array, dash_offset = _parse_dash(dash_value)
    return VectorStyle(
        stroke=stroke,
        fill=fill,
        stroke_width=float(entry.get("width") or 0),
        # IMPORTANT: do **not** use ``entry.get(...) or 1.0`` for opacities.
        # PDF can legitimately set opacity to 0.0, which is the falsy form of
        # float; ``0.0 or 1.0`` evaluates to 1.0 and paints a transparent
        # object as fully opaque. Use ``_opacity()`` which only falls back to
        # the default when the value is None.
        stroke_opacity=_opacity(entry.get("stroke_opacity"), 1.0),
        fill_opacity=_opacity(entry.get("fill_opacity"), 1.0),
        dash=dash_array,
        dash_offset=dash_offset,
        line_cap=_line_cap(entry.get("lineCap")),
        line_join=_line_join(entry.get("lineJoin")),
        miter_limit=_safe_float(entry.get("miterLimit")),
    )


def _opacity(value: Any, default: float) -> float:
    if value is None:
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


_DASH_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _parse_dash(value: Any) -> tuple[str | None, float]:
    """Normalize PDF dash spec to (svg dasharray, dashoffset).

    PDF dash is `"[a b c ...] offset"` or a list/tuple. SVG wants commas.
    """
    if not value:
        return None, 0.0
    if isinstance(value, (list, tuple)):
        array = [float(v) for v in value]
        offset = 0.0
    else:
        nums = [float(s) for s in _DASH_RE.findall(str(value))]
        if not nums:
            return None, 0.0
        if str(value).rstrip().endswith("]"):
            array = nums
            offset = 0.0
        elif "]" in str(value):
            split = str(value).split("]")
            array_part = _DASH_RE.findall(split[0])
            offset_part = _DASH_RE.findall(split[1])
            array = [float(s) for s in array_part]
            offset = float(offset_part[0]) if offset_part else 0.0
        else:
            array = nums
            offset = 0.0
    array = [v for v in array if v >= 0]
    if not array or all(v == 0 for v in array):
        return None, 0.0
    return ",".join(f"{v:.3f}" for v in array), offset


def _safe_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except Exception:
        return None


def _line_cap(value: Any) -> str | None:
    if isinstance(value, (list, tuple)) and value:
        value = value[0]
    if isinstance(value, int):
        return {0: "butt", 1: "round", 2: "square"}.get(value)
    return None


def _line_join(value: Any) -> str | None:
    if isinstance(value, (list, tuple)) and value:
        value = value[0]
    if isinstance(value, int):
        return {0: "miter", 1: "round", 2: "bevel"}.get(value)
    return None


def _trim_stack(stack: list[tuple], level: int) -> None:
    while stack and stack[-1][1] >= level:
        stack.pop()
