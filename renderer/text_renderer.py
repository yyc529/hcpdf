from __future__ import annotations

from html import escape

from core.units import bbox_to_px
from model.elements import TextBlockElement, TextLine, TextSpan
from model.geometry import BBox
from renderer.css_renderer import css_rule


def _common_attrs(element: TextBlockElement) -> str:
    return f'id="{escape(element.stable_id)}"'


def render_text_block(block: TextBlockElement, scale: float) -> str:
    lines_html: list[str] = []
    for line in block.lines:
        span_html: list[str] = []
        for span in line.spans:
            span_html.append(_render_span(span, block))
        lines_html.append(
            f'<div id="{escape(line.stable_id)}" class="pdf-text-line" '
            f'>{"".join(span_html)}</div>'
        )
    return (
        f'<div {_common_attrs(block)} class="pdf-text pdf-text-block" '
        f'>{"".join(lines_html)}</div>'
    )


def _render_span(span: TextSpan, block: TextBlockElement) -> str:
    style = span.style
    return (
        f'<span id="{escape(span.stable_id)}" class="pdf-text-span" '
        f'>{escape(span.text)}</span>'
    )


def render_text_block_css(block: TextBlockElement, scale: float) -> str:
    """Emit CSS for one text block.

    All three of ``.pdf-text-block`` / ``.pdf-text-line`` / ``.pdf-text-span``
    are absolutely positioned. The naive approach of giving each the absolute
    PDF coordinate produces compound offsets — the inner element ends up at
    ``parent_top + child_top``, drifting off-page. We therefore position each
    nested level **relative to its parent's bbox**:

    - block: absolute PDF coords (relative to the page layer)
    - line: ``line.bbox - block.bbox`` (relative to block)
    - span: ``span.bbox - line.bbox`` (relative to line)
    """
    rules = [
        css_rule(f"#{block.stable_id}", _rect_properties(block.bbox, scale)),
    ]
    for line in block.lines:
        line_rel = _relative_bbox(line.bbox, block.bbox)
        rules.append(css_rule(f"#{line.stable_id}", _rect_properties(line_rel, scale)))
        for span in line.spans:
            span_rel = _relative_bbox(span.bbox, line.bbox)
            props = _rect_properties(span_rel, scale)
            style = span.style
            font_size_px = style.font_size * scale
            props.update(
                {
                    "font-family": style.font_family,
                    "font-size": f"{font_size_px:.3f}px",
                    "color": style.color,
                    "opacity": f"{style.opacity:.3f}",
                    "writing-mode": _writing_mode_css(style.writing_mode),
                    "font-weight": "700" if style.bold else None,
                    "font-style": "italic" if style.italic else None,
                }
            )
            if abs(style.letter_spacing) > 0.0:
                props["letter-spacing"] = f"{style.letter_spacing * scale:.3f}px"
            if abs(style.rotation) > 0.1:
                props["transform"] = f"rotate({style.rotation:.3f}deg)"
                props["transform-origin"] = "0 100%"
            # Align glyph baseline using ascender ratio when known.
            ascender_offset = (style.ascender - 1.0) * font_size_px
            if abs(ascender_offset) > 0.5:
                props["padding-top"] = f"{max(0.0, ascender_offset):.3f}px"
            rules.append(css_rule(f"#{span.stable_id}", props))
    return "\n".join(rule for rule in rules if rule)


def _relative_bbox(child: BBox, parent: BBox) -> BBox:
    return BBox(
        child.x0 - parent.x0,
        child.y0 - parent.y0,
        child.x1 - parent.x0,
        child.y1 - parent.y0,
    )


def _writing_mode_css(value: str) -> str | None:
    if value == "vertical-rl":
        return "vertical-rl"
    if value == "horizontal-tb":
        return None
    return value


def _rect_properties(bbox, scale: float) -> dict[str, str]:
    box = bbox_to_px(bbox, scale)
    return {
        "left": f"{box.x0:.3f}px",
        "top": f"{box.y0:.3f}px",
        "width": f"{max(0, box.width):.3f}px",
        "height": f"{max(0, box.height):.3f}px",
    }
