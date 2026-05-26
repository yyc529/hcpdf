from __future__ import annotations

from html import escape
import re

from core.units import bbox_to_px
from model.elements import ImageElement
from renderer.css_renderer import css_rule


def _common_attrs(element: ImageElement) -> str:
    return f'id="{escape(element.stable_id)}"'


def _needs_svg(element: ImageElement) -> bool:
    return bool(
        element.mask_asset_path
        or (element.clip_path_data and not _css_clip_shape(element, 1.0))
        or element.filters
    )


def render_image(element: ImageElement, scale: float) -> str:
    common = _common_attrs(element)
    if not element.asset_path:
        return (
            f'<div {common} class="pdf-image pdf-diagnostic-placeholder" '
            f'aria-hidden="true" title="image unavailable"></div>'
        )

    css_clip = _css_clip_shape(element, scale)
    if css_clip:
        return _render_css_clipped_image(element, common)

    if _needs_svg(element):
        return _render_svg_image(element, scale, common)

    src = element.derived_asset_path or element.asset_path
    return (
        f'<img {common} class="pdf-image" src="{escape(src)}" alt="" />'
    )


def _render_css_clipped_image(element: ImageElement, common: str) -> str:
    src = element.derived_asset_path or element.asset_path
    return (
        f'<div {common} class="pdf-image pdf-image-css-clip">'
        f'<img class="pdf-image-content" src="{escape(src)}" alt="" />'
        f'</div>'
    )


def _render_svg_image(element: ImageElement, scale: float, common: str) -> str:
    """Render an image inside an SVG so we can attach mask / clip / filter.

    The SVG uses **PDF point space** as its viewBox so that clip paths produced
    by the vector parser (also in PDF point space, in absolute page coords)
    map directly without translation/scale arithmetic. The host ``<svg>``
    element's CSS box is sized in pixels so that absolute positioning from
    ``render_image_css`` keeps working unchanged.
    """
    box = bbox_to_px(element.bbox, scale)
    width_px = max(0.0, box.width)
    height_px = max(0.0, box.height)
    width_pt = max(0.0, element.bbox.width)
    height_pt = max(0.0, element.bbox.height)

    base = element.stable_id
    mask_id = f"{base}-mask"
    clip_id = f"{base}-clip"
    filter_id = f"{base}-filter"

    # ViewBox sits at the image's PDF-coord origin so that an absolute-coord
    # clip path (e.g. the "01" stencil curves anchored to page coords) maps
    # straight in. ``<image>`` itself is placed at that same origin.
    view_x0 = element.bbox.x0
    view_y0 = element.bbox.y0

    defs_parts: list[str] = []
    image_attrs: list[str] = [
        'preserveAspectRatio="none"',
        f'x="{view_x0:.3f}"',
        f'y="{view_y0:.3f}"',
        f'width="{width_pt:.3f}"',
        f'height="{height_pt:.3f}"',
    ]
    src = element.derived_asset_path or element.asset_path
    image_attrs.append(f'href="{escape(src)}"')

    if element.mask_asset_path and not element.derived_asset_path:
        defs_parts.append(
            f'<mask id="{escape(mask_id)}" maskUnits="userSpaceOnUse" '
            f'x="{view_x0:.3f}" y="{view_y0:.3f}" '
            f'width="{width_pt:.3f}" height="{height_pt:.3f}">'
            f'<image href="{escape(element.mask_asset_path)}" '
            f'x="{view_x0:.3f}" y="{view_y0:.3f}" '
            f'width="{width_pt:.3f}" height="{height_pt:.3f}" '
            f'preserveAspectRatio="none" /></mask>'
        )
        image_attrs.append(f'mask="url(#{escape(mask_id)})"')

    if element.clip_path_data:
        clip_content = _render_clip_content(element.clip_path_data)
        defs_parts.append(
            f'<clipPath id="{escape(clip_id)}" clipPathUnits="userSpaceOnUse">'
            f'{clip_content}</clipPath>'
        )
        image_attrs.append(f'clip-path="url(#{escape(clip_id)})"')

    if element.filters:
        defs_parts.append(_render_filter_def(filter_id, element.filters,
                                             view_x0, view_y0,
                                             width_pt, height_pt))
        image_attrs.append(f'filter="url(#{escape(filter_id)})"')

    defs = f"<defs>{''.join(defs_parts)}</defs>" if defs_parts else ""
    image_tag = f"<image {' '.join(image_attrs)} />"
    return (
        f'<svg {common} class="pdf-image pdf-image-svg" '
        f'width="{width_px:.3f}" height="{height_px:.3f}" '
        f'viewBox="{view_x0:.3f} {view_y0:.3f} {width_pt:.3f} {height_pt:.3f}" '
        f'preserveAspectRatio="none" '
        f'xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink">{defs}{image_tag}</svg>'
    )


def _render_filter_def(
    filter_id: str,
    filters: list[dict],
    view_x0: float,
    view_y0: float,
    width_pt: float,
    height_pt: float,
) -> str:
    primitives: list[str] = []
    for entry in filters:
        kind = entry.get("kind")
        if kind == "grayscale":
            primitives.append(
                '<feColorMatrix type="matrix" '
                'values="0.2126 0.7152 0.0722 0 0 '
                '0.2126 0.7152 0.0722 0 0 '
                '0.2126 0.7152 0.0722 0 0 '
                '0 0 0 1 0" />'
            )
        elif kind == "brightness":
            amount = float(entry.get("amount", 1.0))
            primitives.append(
                '<feComponentTransfer>'
                f'<feFuncR type="linear" slope="{amount:.3f}" />'
                f'<feFuncG type="linear" slope="{amount:.3f}" />'
                f'<feFuncB type="linear" slope="{amount:.3f}" /></feComponentTransfer>'
            )
        elif kind == "contrast":
            amount = float(entry.get("amount", 1.0))
            intercept = (1.0 - amount) / 2.0
            primitives.append(
                '<feComponentTransfer>'
                f'<feFuncR type="linear" slope="{amount:.3f}" intercept="{intercept:.3f}" />'
                f'<feFuncG type="linear" slope="{amount:.3f}" intercept="{intercept:.3f}" />'
                f'<feFuncB type="linear" slope="{amount:.3f}" intercept="{intercept:.3f}" /></feComponentTransfer>'
            )
    body = "".join(primitives) or '<feColorMatrix type="matrix" values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 1 0" />'
    return (
        f'<filter id="{escape(filter_id)}" filterUnits="userSpaceOnUse" '
        f'x="{view_x0:.3f}" y="{view_y0:.3f}" '
        f'width="{width_pt:.3f}" height="{height_pt:.3f}">{body}</filter>'
    )


def _render_clip_content(path_data: str) -> str:
    simple = _simplify_clip_path(path_data)
    if simple:
        return simple
    return f'<path d="{escape(path_data)}" />'


def _css_clip_shape(element: ImageElement, scale: float) -> dict[str, str] | None:
    if not element.clip_path_data or element.mask_asset_path or element.filters:
        return None
    parsed = _parse_absolute_path(element.clip_path_data)
    if parsed is None:
        return None
    commands, points = parsed
    if not points:
        return None

    x0 = min(x for x, _ in points)
    y0 = min(y for _, y in points)
    x1 = max(x for x, _ in points)
    y1 = max(y for _, y in points)
    if not _bbox_matches_image(element, x0, y0, x1, y1):
        return None

    if _is_ellipse_path(commands, points, x0, y0, x1, y1):
        return {"border-radius": "50%"}

    radius = _rounded_rect_radius(commands, points, x0, y0, x1, y1)
    if radius:
        rx, ry = radius
        return {
            "border-radius": f"{max(0.0, rx * scale):.3f}px / {max(0.0, ry * scale):.3f}px"
        }

    if commands in (["M", "L", "L", "L"], ["M", "L", "L", "L", "Z"]):
        return {"border-radius": "0"}

    return None


def _bbox_matches_image(
    element: ImageElement,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
) -> bool:
    tolerance = max(element.bbox.width, element.bbox.height) * 0.01 + 0.5
    return (
        abs(x0 - element.bbox.x0) <= tolerance
        and abs(y0 - element.bbox.y0) <= tolerance
        and abs(x1 - element.bbox.x1) <= tolerance
        and abs(y1 - element.bbox.y1) <= tolerance
    )


def _simplify_clip_path(path_data: str) -> str | None:
    parsed = _parse_absolute_path(path_data)
    if parsed is None:
        return None
    commands, points = parsed
    if not points:
        return None

    x0 = min(x for x, _ in points)
    y0 = min(y for _, y in points)
    x1 = max(x for x, _ in points)
    y1 = max(y for _, y in points)
    width = x1 - x0
    height = y1 - y0
    if width <= 0 or height <= 0:
        return None

    if commands in (["M", "L", "L", "L"], ["M", "L", "L", "L", "Z"]):
        return _svg_rect(x0, y0, width, height)

    if _is_ellipse_path(commands, points, x0, y0, x1, y1):
        return (
            f'<ellipse cx="{(x0 + x1) / 2:.3f}" cy="{(y0 + y1) / 2:.3f}" '
            f'rx="{width / 2:.3f}" ry="{height / 2:.3f}" />'
        )

    radius = _rounded_rect_radius(commands, points, x0, y0, x1, y1)
    if radius:
        rx, ry = radius
        return _svg_rect(x0, y0, width, height, rx, ry)

    return None


def _svg_rect(
    x: float,
    y: float,
    width: float,
    height: float,
    rx: float | None = None,
    ry: float | None = None,
) -> str:
    attrs = [
        f'x="{x:.3f}"',
        f'y="{y:.3f}"',
        f'width="{width:.3f}"',
        f'height="{height:.3f}"',
    ]
    if rx is not None and ry is not None:
        attrs.append(f'rx="{rx:.3f}"')
        attrs.append(f'ry="{ry:.3f}"')
    return f"<rect {' '.join(attrs)} />"


def _parse_absolute_path(path_data: str) -> tuple[list[str], list[tuple[float, float]]] | None:
    tokens = re.findall(r"[A-Za-z]|[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", path_data)
    commands: list[str] = []
    points: list[tuple[float, float]] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if not token.isalpha():
            return None
        command = token
        commands.append(command)
        i += 1
        if command in {"M", "L"}:
            if i + 1 >= len(tokens):
                return None
            points.append((float(tokens[i]), float(tokens[i + 1])))
            i += 2
        elif command == "C":
            if i + 5 >= len(tokens):
                return None
            points.extend(
                [
                    (float(tokens[i]), float(tokens[i + 1])),
                    (float(tokens[i + 2]), float(tokens[i + 3])),
                    (float(tokens[i + 4]), float(tokens[i + 5])),
                ]
            )
            i += 6
        elif command == "Z":
            continue
        else:
            return None
    return commands, points


def _is_ellipse_path(
    commands: list[str],
    points: list[tuple[float, float]],
    x0: float,
    y0: float,
    x1: float,
    y1: float,
) -> bool:
    if commands not in (["M", "C", "C", "C", "C"], ["M", "C", "C", "C", "C", "Z"]):
        return False
    anchors = [points[0], points[3], points[6], points[9], points[12]]
    expected = [
        ((x0 + x1) / 2, y0),
        (x1, (y0 + y1) / 2),
        ((x0 + x1) / 2, y1),
        (x0, (y0 + y1) / 2),
        ((x0 + x1) / 2, y0),
    ]
    tolerance = max(x1 - x0, y1 - y0) * 0.03 + 0.25
    return all(_close_point(a, b, tolerance) for a, b in zip(anchors, expected))


def _rounded_rect_radius(
    commands: list[str],
    points: list[tuple[float, float]],
    x0: float,
    y0: float,
    x1: float,
    y1: float,
) -> tuple[float, float] | None:
    if commands.count("C") != 4 or commands.count("M") != 1:
        return None
    if any(command not in {"M", "L", "C", "Z"} for command in commands):
        return None

    anchors = _path_anchors(commands, points)
    if len(anchors) < 8:
        return None
    tolerance = max(x1 - x0, y1 - y0) * 0.025 + 0.25
    if not all(_point_on_rect_edge(point, x0, y0, x1, y1, tolerance) for point in anchors):
        return None

    xs = sorted({round(point[0], 3) for point in anchors})
    ys = sorted({round(point[1], 3) for point in anchors})
    inner_xs = [x for x in xs if abs(x - x0) > tolerance and abs(x - x1) > tolerance]
    inner_ys = [y for y in ys if abs(y - y0) > tolerance and abs(y - y1) > tolerance]
    if not inner_xs or not inner_ys:
        return None

    rx = max(min(inner_xs) - x0, x1 - max(inner_xs))
    ry = max(min(inner_ys) - y0, y1 - max(inner_ys))
    if rx <= 0 or ry <= 0:
        return None
    if rx > (x1 - x0) / 2 + tolerance or ry > (y1 - y0) / 2 + tolerance:
        return None
    return rx, ry


def _path_anchors(
    commands: list[str], points: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    anchors: list[tuple[float, float]] = []
    index = 0
    for command in commands:
        if command in {"M", "L"}:
            anchors.append(points[index])
            index += 1
        elif command == "C":
            anchors.append(points[index + 2])
            index += 3
    return anchors


def _point_on_rect_edge(
    point: tuple[float, float],
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    tolerance: float,
) -> bool:
    x, y = point
    on_vertical = abs(x - x0) <= tolerance or abs(x - x1) <= tolerance
    on_horizontal = abs(y - y0) <= tolerance or abs(y - y1) <= tolerance
    within = x0 - tolerance <= x <= x1 + tolerance and y0 - tolerance <= y <= y1 + tolerance
    return within and (on_vertical or on_horizontal)


def _close_point(
    a: tuple[float, float], b: tuple[float, float], tolerance: float
) -> bool:
    return abs(a[0] - b[0]) <= tolerance and abs(a[1] - b[1]) <= tolerance


def render_image_css(element: ImageElement, scale: float) -> str:
    box = bbox_to_px(element.bbox, scale)
    props = {
        "left": f"{box.x0:.3f}px",
        "top": f"{box.y0:.3f}px",
        "width": f"{max(0, box.width):.3f}px",
        "height": f"{max(0, box.height):.3f}px",
    }
    if element.blend_mode:
        props["mix-blend-mode"] = element.blend_mode
    css_clip = _css_clip_shape(element, scale)
    if css_clip:
        props["overflow"] = "hidden"
        props.update(css_clip)
    return css_rule(f"#{element.stable_id}", props)
