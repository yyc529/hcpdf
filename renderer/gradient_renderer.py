"""Render ``GradientFillElement`` instances as SVG ``<linearGradient>`` + ``<path>``.

The output uses page-coordinate viewBox (PDF point space) so the path data
emitted by the deep parser maps directly without per-element coordinate
rebasing. One ``<svg>`` per element keeps the element self-contained and
lets CSS layout (left/top/width/height) work the same way as any other
positioned absolute child of the page section.
"""

from __future__ import annotations

from html import escape

from core.units import bbox_to_px
from model.elements import GradientFillElement
from model.page import PageModel


def render_gradient_element(element: GradientFillElement, scale: float) -> str:
    grad_def = _render_linear_gradient(element)
    defs_parts = [grad_def]

    fill_rule = "evenodd" if element.even_odd else "nonzero"
    opacity = "" if element.fill_alpha >= 0.999 else f' fill-opacity="{element.fill_alpha:.3f}"'

    mask_attr = ""
    if element.mask_kind and element.mask_grad_stops:
        mask_id = f"{element.stable_id}-mask"
        mask_grad_id = f"{element.stable_id}-mask-grad"
        # Build the SVG <mask> contents: the same path geometry filled with
        # the mask's own gradient. SVG /Luminosity matches PDF's luminosity
        # SMask one-to-one — RGB luminance of the mask paint becomes alpha
        # for the masked element.
        mask_grad_attrs = " ".join(
            [
                f'id="{escape(mask_grad_id)}"',
                f'x1="{element.mask_grad_x1:.3f}"',
                f'y1="{element.mask_grad_y1:.3f}"',
                f'x2="{element.mask_grad_x2:.3f}"',
                f'y2="{element.mask_grad_y2:.3f}"',
                'gradientUnits="userSpaceOnUse"',
                'spreadMethod="pad"',
            ]
        )
        mask_stops_html = "".join(
            f'<stop offset="{s.offset * 100:.3f}%" '
            f'stop-color="rgb({_clamp_byte(s.rgb[0])},{_clamp_byte(s.rgb[1])},{_clamp_byte(s.rgb[2])})" />'
            for s in element.mask_grad_stops
        )
        mask_kind_attr = ' mask-type="luminance"' if element.mask_kind == "Luminosity" else ''
        defs_parts.append(
            f'<linearGradient {mask_grad_attrs}>{mask_stops_html}</linearGradient>'
        )
        defs_parts.append(
            f'<mask id="{escape(mask_id)}" maskUnits="userSpaceOnUse"{mask_kind_attr}>'
            f'<path d="{escape(element.path_data)}" '
            f'fill="url(#{escape(mask_grad_id)})" fill-rule="{fill_rule}" />'
            f'</mask>'
        )
        mask_attr = f' mask="url(#{escape(mask_id)})"'

    return (
        f'<defs>{"".join(defs_parts)}</defs>'
        f'<path d="{escape(element.path_data)}" '
        f'fill="url(#{escape(_gradient_id(element))})" '
        f'fill-rule="{fill_rule}"{opacity}{mask_attr} />'
    )


def render_gradient_svg(element: GradientFillElement, page: PageModel) -> str:
    """Wrap one gradient element in its own absolutely-positioned ``<svg>``.

    pikepdf preserves PDF native coordinates (origin at the bottom-left,
    y-axis pointing up). SVG uses the opposite convention (origin top-left,
    y-axis down). We flip with an ``<g transform>`` wrapper so both the
    path geometry and the gradient endpoints — they share the same
    user-space — flip together. The matrix ``(1 0 0 -1 0 height)`` is the
    canonical PDF→SVG y-flip.
    """
    if not element.path_data:
        return ""
    h = page.height_pt
    return (
        f'<svg id="{escape(element.stable_id)}" '
        f'class="pdf-gradient pdf-vector-deep" '
        f'width="{page.width_px:.3f}" height="{page.height_px:.3f}" '
        f'viewBox="0 0 {page.width_pt:.3f} {page.height_pt:.3f}" '
        f'xmlns="http://www.w3.org/2000/svg">'
        f'<g transform="matrix(1 0 0 -1 0 {h:.3f})">'
        f'{render_gradient_element(element, page.scale_factor)}'
        f'</g>'
        f'</svg>'
    )


def render_gradient_css(element: GradientFillElement, scale: float) -> str:
    """Position the gradient SVG layer to span the page.

    The path inside is in page coords inside a page-size viewBox, so the
    SVG element itself just needs to overlay the whole page section.
    """
    return (
        f"    #{element.stable_id} {{\n"
        f"        position: absolute;\n"
        f"        left: 0;\n"
        f"        top: 0;\n"
        f"        pointer-events: none;\n"
        f"    }}"
    )


def _gradient_id(element: GradientFillElement) -> str:
    return f"{element.stable_id}-grad"


def _render_linear_gradient(element: GradientFillElement) -> str:
    stops_html = "".join(
        f'<stop offset="{stop.offset * 100:.3f}%" '
        f'stop-color="rgb({_clamp_byte(stop.rgb[0])},{_clamp_byte(stop.rgb[1])},{_clamp_byte(stop.rgb[2])})" />'
        for stop in element.grad_stops
    )
    spread = _spread_method(element)
    attrs = [
        f'id="{escape(_gradient_id(element))}"',
        f'x1="{element.grad_x1:.3f}"',
        f'y1="{element.grad_y1:.3f}"',
        f'x2="{element.grad_x2:.3f}"',
        f'y2="{element.grad_y2:.3f}"',
        'gradientUnits="userSpaceOnUse"',
        f'spreadMethod="{spread}"',
    ]
    if element.grad_matrix:
        a, b, c, d, e, f = element.grad_matrix
        attrs.append(
            f'gradientTransform="matrix({a:.6f},{b:.6f},{c:.6f},{d:.6f},{e:.6f},{f:.6f})"'
        )
    return f'<linearGradient {" ".join(attrs)}>{stops_html}</linearGradient>'


def _spread_method(element: GradientFillElement) -> str:
    """Map PDF ``/Extend`` flags to SVG ``spreadMethod``.

    PDF /Extend says "fill past the gradient endpoints with the end colour".
    SVG only offers ``pad``/``reflect``/``repeat``. ``pad`` matches PDF's
    extend behaviour 1-for-1 when both endpoints are extended, and is the
    safest fallback when only one is.
    """
    if element.grad_extend_start or element.grad_extend_end:
        return "pad"
    # Without extend, areas outside the gradient line stay unfilled in PDF.
    # SVG has no exact analogue; ``pad`` is the closest visible behaviour and
    # matches how PyMuPDF renders shading patterns.
    return "pad"


def _clamp_byte(v: float) -> int:
    n = int(round(max(0.0, min(1.0, v)) * 255))
    return n
