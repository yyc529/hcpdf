from __future__ import annotations

from html import escape
import re

from core.units import bbox_to_px
from model.elements import GradientFillElement, ImageElement, VectorElement
from model.geometry import BBox
from model.page import PageModel
from renderer.image_renderer import _render_clip_content


_BLEND_MODE_MAP = {
    "Normal": "normal",
    "Multiply": "multiply",
    "Screen": "screen",
    "Overlay": "overlay",
    "Darken": "darken",
    "Lighten": "lighten",
    "ColorDodge": "color-dodge",
    "ColorBurn": "color-burn",
    "HardLight": "hard-light",
    "SoftLight": "soft-light",
    "Difference": "difference",
    "Exclusion": "exclusion",
    "Hue": "hue",
    "Saturation": "saturation",
    "Color": "color",
    "Luminosity": "luminosity",
}


def _vector_style(element: VectorElement) -> str:
    style = element.style
    attrs = [
        f'stroke="{escape(style.stroke or "none")}"',
        f'fill="{escape(style.fill or "none")}"',
        f'stroke-width="{style.stroke_width:.3f}"',
        f'stroke-opacity="{style.stroke_opacity:.3f}"',
        f'fill-opacity="{style.fill_opacity:.3f}"',
    ]
    if element.even_odd:
        attrs.append('fill-rule="evenodd"')
    if style.line_cap:
        attrs.append(f'stroke-linecap="{escape(style.line_cap)}"')
    if style.line_join:
        attrs.append(f'stroke-linejoin="{escape(style.line_join)}"')
    if style.miter_limit is not None:
        attrs.append(f'stroke-miterlimit="{style.miter_limit:.3f}"')
    if style.dash:
        attrs.append(f'stroke-dasharray="{escape(style.dash)}"')
    if abs(style.dash_offset) > 0:
        attrs.append(f'stroke-dashoffset="{style.dash_offset:.3f}"')
    return " ".join(attrs)


def _path_inline_style(element: VectorElement) -> str:
    parts: list[str] = []
    if element.blend_mode:
        css = _BLEND_MODE_MAP.get(element.blend_mode)
        if css:
            parts.append(f"mix-blend-mode:{css}")
    if element.group_opacity < 1.0:
        parts.append(f"opacity:{element.group_opacity:.3f}")
    return ";".join(parts)


def render_vector_layer(
    page: PageModel, vectors: list[VectorElement], role: str = "background"
) -> str:
    """Render vectors as local CSS/SVG fragments.

    The page still has background/foreground vector layers, but unrelated
    shapes inside each layer are split apart. Simple rectangles and straight
    rules become CSS divs. Remaining complex paths are clustered by spatial
    proximity and emitted as small local SVGs, so a page header and footer do
    not share an SVG unless their geometry is actually connected.
    """
    parts: list[tuple[float, str]] = []
    svg_vectors: list[VectorElement] = []
    for vector in vectors:
        css_html = _render_css_vector(vector, page.scale_factor)
        if css_html is None:
            svg_vectors.append(vector)
        else:
            parts.append((float(vector.paint_seqno), css_html))

    clip_map = {clip.stable_id: clip for clip in page.clip_paths}
    for cluster in _cluster_vectors(svg_vectors, page):
        parts.append(
            (
                min(float(vector.paint_seqno) for vector in cluster),
                _render_svg_cluster(page, cluster, role, clip_map),
            )
        )

    if not parts:
        return ""
    html = "".join(fragment for _seqno, fragment in sorted(parts, key=lambda item: item[0]))
    return (
        f'<div class="pdf-layer vector-layer vector-layer-{escape(role)}">'
        f'{html}</div>'
    )


def render_svg_layer(
    page: PageModel, vectors: list[VectorElement], role: str = "background"
) -> str:
    return render_vector_layer(page, vectors, role)


def _render_svg_cluster(
    page: PageModel,
    vectors: list[VectorElement],
    role: str,
    clip_map,
) -> str:
    bbox = _pad_bbox(_union_bbox([vector.bbox for vector in vectors]), 1.0, page)
    box = bbox_to_px(bbox, page.scale_factor)
    defs: list[str] = []
    for clip_id in sorted({vector.clip_path_id for vector in vectors if vector.clip_path_id}):
        clip = clip_map.get(clip_id)
        if clip is None:
            continue
        rule = 'clip-rule="evenodd"' if clip.even_odd else ""
        clip_content = _render_clip_content(clip.path_data)
        if rule:
            clip_content = clip_content.replace(" />", f" {rule} />")
        defs.append(
            f'<clipPath id="{escape(clip.stable_id)}" clipPathUnits="userSpaceOnUse">'
            f'{clip_content}</clipPath>'
        )
    defs_block = f"<defs>{''.join(defs)}</defs>" if defs else ""

    paths: list[str] = []
    for vector in sorted(vectors, key=lambda item: float(item.paint_seqno)):
        extra_attrs: list[str] = []
        if vector.clip_path_id:
            extra_attrs.append(f'clip-path="url(#{escape(vector.clip_path_id)})"')
        inline_style = _path_inline_style(vector)
        if inline_style:
            extra_attrs.append(f'style="{escape(inline_style)}"')
        paths.append(
            f'<path id="{escape(vector.stable_id)}" class="pdf-vector" '
            f'd="{escape(vector.path_data)}" {_vector_style(vector)} '
            f'{" ".join(extra_attrs)} />'
        )

    style = (
        f"left:{box.x0:.3f}px;top:{box.y0:.3f}px;"
        f"width:{max(0.0, box.width):.3f}px;height:{max(0.0, box.height):.3f}px;"
    )
    return (
        f'<svg class="pdf-vector-svg pdf-vector-svg-{escape(role)}" '
        f'style="{escape(style)}" '
        f'width="{max(0.0, box.width):.3f}" height="{max(0.0, box.height):.3f}" '
        f'viewBox="{bbox.x0:.3f} {bbox.y0:.3f} {max(1.0, bbox.width):.3f} {max(1.0, bbox.height):.3f}" '
        f'xmlns="http://www.w3.org/2000/svg">{defs_block}{"".join(paths)}</svg>'
    )


def _render_css_vector(vector: VectorElement, scale: float) -> str | None:
    if vector.clip_path_id or vector.even_odd or vector.style.dash:
        return None
    if vector.blend_mode:
        return None
    rect = _simple_rect_bbox(vector)
    if rect is not None:
        return _render_css_rect(vector, rect, scale)
    line = _simple_line(vector)
    if line is not None:
        return _render_css_line(vector, line, scale)
    return None


def _render_css_rect(vector: VectorElement, bbox: BBox, scale: float) -> str | None:
    style = vector.style
    if not style.fill and not style.stroke:
        return None
    box = bbox_to_px(bbox, scale)
    props = [
        "position:absolute",
        "box-sizing:border-box",
        f"left:{box.x0:.3f}px",
        f"top:{box.y0:.3f}px",
        f"width:{max(0.0, box.width):.3f}px",
        f"height:{max(0.0, box.height):.3f}px",
        f"background:{_css_color(style.fill, style.fill_opacity) if style.fill else 'transparent'}",
    ]
    if style.stroke:
        props.append(
            f"border:{max(0.0, style.stroke_width * scale):.3f}px solid "
            f"{_css_color(style.stroke, style.stroke_opacity)}"
        )
    if vector.group_opacity < 1.0:
        props.append(f"opacity:{vector.group_opacity:.3f}")
    return (
        f'<div id="{escape(vector.stable_id)}" class="pdf-vector-css pdf-vector-rect" '
        f'style="{escape(";".join(props))}"></div>'
    )


def _render_css_line(
    vector: VectorElement,
    line: tuple[float, float, float, float],
    scale: float,
) -> str | None:
    style = vector.style
    if not style.stroke:
        return None
    x0, y0, x1, y1 = line
    stroke = max(0.5, style.stroke_width * scale)
    if abs(y1 - y0) <= 0.2:
        left = min(x0, x1) * scale
        top = y0 * scale - stroke / 2
        width = abs(x1 - x0) * scale
        height = stroke
    elif abs(x1 - x0) <= 0.2:
        left = x0 * scale - stroke / 2
        top = min(y0, y1) * scale
        width = stroke
        height = abs(y1 - y0) * scale
    else:
        return None
    props = [
        "position:absolute",
        f"left:{left:.3f}px",
        f"top:{top:.3f}px",
        f"width:{max(0.0, width):.3f}px",
        f"height:{max(0.0, height):.3f}px",
        f"background:{_css_color(style.stroke, style.stroke_opacity)}",
    ]
    if style.line_cap == "round":
        props.append("border-radius:999px")
    if vector.group_opacity < 1.0:
        props.append(f"opacity:{vector.group_opacity:.3f}")
    return (
        f'<div id="{escape(vector.stable_id)}" class="pdf-vector-css pdf-vector-line" '
        f'style="{escape(";".join(props))}"></div>'
    )


def _cluster_vectors(vectors: list[VectorElement], page: PageModel) -> list[list[VectorElement]]:
    clusters: list[list[VectorElement]] = []
    pad = max(4.0, min(page.width_pt, page.height_pt) * 0.015)
    for vector in sorted(vectors, key=lambda item: float(item.paint_seqno)):
        matches = [
            idx
            for idx, cluster in enumerate(clusters)
            if any(_bbox_near(vector.bbox, other.bbox, pad) for other in cluster)
        ]
        if not matches:
            clusters.append([vector])
            continue
        target = matches[0]
        clusters[target].append(vector)
        for idx in reversed(matches[1:]):
            clusters[target].extend(clusters.pop(idx))
    return clusters


def _union_bbox(boxes: list[BBox]) -> BBox:
    return BBox(
        min(box.x0 for box in boxes),
        min(box.y0 for box in boxes),
        max(box.x1 for box in boxes),
        max(box.y1 for box in boxes),
    )


def _pad_bbox(bbox: BBox, pad: float, page: PageModel) -> BBox:
    return BBox(
        max(0.0, bbox.x0 - pad),
        max(0.0, bbox.y0 - pad),
        min(page.width_pt, bbox.x1 + pad),
        min(page.height_pt, bbox.y1 + pad),
    )


def _bbox_near(a: BBox, b: BBox, pad: float) -> bool:
    return not (
        a.x1 + pad < b.x0
        or b.x1 + pad < a.x0
        or a.y1 + pad < b.y0
        or b.y1 + pad < a.y0
    )


def _simple_rect_bbox(vector: VectorElement) -> BBox | None:
    if not _is_simple_rect_path(vector.path_data):
        return None
    return vector.bbox


def _simple_line(vector: VectorElement) -> tuple[float, float, float, float] | None:
    if vector.style.fill:
        return None
    matches = re.findall(r"([ML])\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)", vector.path_data)
    if len(matches) != 2 or [cmd for cmd, _x, _y in matches] != ["M", "L"]:
        return None
    x0, y0 = float(matches[0][1]), float(matches[0][2])
    x1, y1 = float(matches[1][1]), float(matches[1][2])
    if abs(x1 - x0) <= 0.2 or abs(y1 - y0) <= 0.2:
        return x0, y0, x1, y1
    return None


def _css_color(color: str | None, opacity: float) -> str:
    if not color:
        return "transparent"
    if opacity >= 0.999:
        return color
    match = re.fullmatch(
        r"rgb\(\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*\)",
        color,
    )
    if not match:
        return color
    r, g, b = (float(value) for value in match.groups())
    return f"rgba({r:.0f}, {g:.0f}, {b:.0f}, {max(0.0, min(1.0, opacity)):.3f})"


def classify_vectors(
    vectors: list[VectorElement], page: PageModel
) -> tuple[list[VectorElement], list[VectorElement]]:
    """Split vectors into ``(background, foreground)`` by PDF paint order."""
    images = _collect_image_metadata(page.elements)
    background: list[VectorElement] = []
    foreground: list[VectorElement] = []
    for vector in vectors:
        if _is_background_vector(vector, images):
            background.append(vector)
        else:
            foreground.append(vector)
    return background, foreground


def _collect_image_metadata(elements) -> list[tuple[int, tuple[float, float, float, float]]]:
    """Return ``(paint_seqno, bbox_tuple)`` for raster/vector fill occluders."""
    out: list[tuple[int, tuple[float, float, float, float]]] = []
    for e in elements:
        if isinstance(e, ImageElement):
            b = e.bbox
            out.append((e.paint_seqno, (b.x0, b.y0, b.x1, b.y1)))
        elif isinstance(e, GradientFillElement):
            b = e.bbox
            out.append((int(e.paint_seqno), (b.x0, b.y0, b.x1, b.y1)))
    return out


def _is_background_vector(
    vector: VectorElement,
    images: list[tuple[int, tuple[float, float, float, float]]],
) -> bool:
    if not _is_simple_rect_path(vector.path_data):
        return False
    if not images:
        return True
    vb = vector.bbox
    v_box = (vb.x0, vb.y0, vb.x1, vb.y1)
    for img_seqno, img_box in images:
        if img_seqno <= vector.paint_seqno:
            continue
        if _bbox_overlap(v_box, img_box):
            return True
    return False


def _bbox_overlap(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _is_simple_rect_path(path_data: str) -> bool:
    if not path_data:
        return False
    if "C " in path_data or " C " in path_data:
        return False
    commands = [c for c in path_data if c.isalpha()]
    return commands == ["M", "L", "L", "L", "Z"]
