from __future__ import annotations

import math

import fitz

from model.elements import TextBlockElement, TextLine, TextSpan
from model.geometry import BBox
from model.styles import TextStyle
from resolver.color_resolver import int_color_to_css
from resolver.font_resolver import (
    clean_font_name,
    font_is_bold,
    font_is_italic,
    font_stack,
    has_cjk_text,
    is_cjk_font_name,
)


REPLACEMENT_CHAR = "�"


def parse_text(page: fitz.Page, page_index: int) -> list[TextBlockElement]:
    result: list[TextBlockElement] = []
    raw = page.get_text("rawdict")
    for block_index, block in enumerate(raw.get("blocks", [])):
        if block.get("type") != 0:
            continue
        block_id = f"pdf-page-{page_index + 1}-text-{block_index}"
        block_diagnostics: list[dict] = []
        lines: list[TextLine] = []
        for line_index, line in enumerate(block.get("lines", [])):
            wmode = int(line.get("wmode", 0) or 0)
            direction = line.get("dir") or (1.0, 0.0)
            try:
                rotation = math.degrees(math.atan2(float(direction[1]), float(direction[0])))
            except Exception:
                rotation = 0.0
            writing_mode = "vertical-rl" if wmode == 1 else "horizontal-tb"

            spans: list[TextSpan] = []
            for span_index, span in enumerate(line.get("spans", [])):
                text, char_count, span_bbox, origin = _collect_span_text(span)
                if not text:
                    continue
                font_name_raw = span.get("font") or ""
                font_name = clean_font_name(font_name_raw)
                flags = int(span.get("flags", 0) or 0)
                alpha_raw = span.get("alpha", 1.0)
                # Same falsy-0.0 trap as vector_parser: PDF can legitimately
                # set alpha to 0 (invisible text used as a knockout marker).
                # Only fall back to 1.0 when the value is missing.
                alpha = float(alpha_raw) if alpha_raw is not None else 1.0
                if alpha > 1:
                    alpha = alpha / 255.0
                ascender = float(span.get("ascender", 0.8) or 0.8)
                descender = float(span.get("descender", -0.2) or -0.2)
                cjk_text = has_cjk_text(text)
                cjk_font = is_cjk_font_name(font_name)
                bold = font_is_bold(font_name, flags)
                italic = font_is_italic(font_name, flags)
                letter_spacing = _estimate_letter_spacing(span, char_count, font_name, text)

                style = TextStyle(
                    font_name=font_name,
                    font_family=font_stack(font_name, prefer_cjk=cjk_text or cjk_font),
                    font_size=float(span.get("size", 12.0)),
                    color=int_color_to_css(span.get("color")),
                    opacity=alpha,
                    writing_mode=writing_mode,
                    ascender=ascender,
                    descender=descender,
                    rotation=rotation,
                    letter_spacing=letter_spacing,
                    bold=bold,
                    italic=italic,
                    has_cjk=cjk_text,
                    glyph_source="embedded" if font_name_raw else "fallback",
                )

                _record_text_diagnostics(
                    block_diagnostics, text, font_name_raw, font_name, span_index,
                    line_index, block_index, cjk_text, cjk_font,
                )

                span_id = f"{block_id}-line-{line_index}-span-{span_index}"
                spans.append(
                    TextSpan(
                        stable_id=span_id,
                        text=text,
                        bbox=span_bbox,
                        style=style,
                        source_index=span_index,
                        origin=origin,
                        char_count=char_count,
                    )
                )
            if not spans:
                continue
            line_id = f"{block_id}-line-{line_index}"
            lines.append(
                TextLine(
                    stable_id=line_id,
                    bbox=BBox.from_seq(line.get("bbox", block.get("bbox"))),
                    spans=spans,
                    source_index=line_index,
                )
            )
        if not lines:
            continue
        result.append(
            TextBlockElement(
                stable_id=block_id,
                type="text",
                page_index=page_index + 1,
                bbox=BBox.from_seq(block.get("bbox")),
                z_index=0,
                source_index=block_index,
                render_mode="semantic-html",
                editable=True,
                source={"block_type": "text", "source": "page.get_text(rawdict)"},
                diagnostics=block_diagnostics,
                lines=lines,
            )
        )
    return result


def _collect_span_text(span: dict) -> tuple[str, int, BBox, tuple[float, float]]:
    """Concatenate char-level text and compute span bbox / origin from chars when available."""
    chars = span.get("chars") or []
    if not chars:
        text = span.get("text", "") or ""
        bbox = BBox.from_seq(span.get("bbox"))
        origin = tuple(span.get("origin", (bbox.x0, bbox.y1))[:2])
        return text, len(text), bbox, origin

    text_parts: list[str] = []
    x0 = math.inf
    y0 = math.inf
    x1 = -math.inf
    y1 = -math.inf
    origin = tuple(span.get("origin", (0.0, 0.0))[:2])
    char_count = 0
    for ch in chars:
        c = ch.get("c") or ""
        text_parts.append(c)
        char_count += len(c)
        bbox = ch.get("bbox")
        if bbox:
            try:
                x0 = min(x0, float(bbox[0]))
                y0 = min(y0, float(bbox[1]))
                x1 = max(x1, float(bbox[2]))
                y1 = max(y1, float(bbox[3]))
            except Exception:
                pass
    if x0 == math.inf:
        bbox = BBox.from_seq(span.get("bbox"))
    else:
        bbox = BBox(x0, y0, x1, y1)
    return "".join(text_parts), char_count, bbox, origin


def _estimate_letter_spacing(
    span: dict, char_count: int, font_name: str, text: str
) -> float:
    """Estimate extra ``letter-spacing`` introduced by the PDF beyond glyph advances.

    PDF can add extra horizontal spacing between glyphs via the ``Tc`` operator
    or by inserting numeric offsets inside ``TJ`` arrays. We do **not** want to
    convert *natural* glyph advances into a letter-spacing value — that's a
    Latin-font trap: ``X`` is naturally wider than ``2``, so the X→X advance
    is bigger than 2→0 even with zero PDF ``Tc``.

    Two subtleties this code handles:

    1. The "expected" glyph advance depends on **the text**, not the font
       name. A CJK font (MicrosoftYaHei-Bold) used to render Latin digits
       ("01", "02", "03") still produces narrow Latin advances, so we must
       compare against the Latin expectation, not 0.95×font_size.
    2. For proportional Latin text (X, 2, 0, etc.) per-char advances vary
       naturally; we treat that as zero extra spacing rather than guessing.
    """
    if char_count < 2:
        return 0.0
    chars = span.get("chars") or []
    if len(chars) < 2:
        return 0.0
    advances: list[float] = []
    for i in range(1, len(chars)):
        try:
            prev_origin = chars[i - 1].get("origin")
            cur_origin = chars[i].get("origin")
            if prev_origin and cur_origin:
                advances.append(float(cur_origin[0]) - float(prev_origin[0]))
        except Exception:
            continue
    if not advances:
        return 0.0
    avg_advance = sum(advances) / len(advances)
    size = float(span.get("size", 12.0) or 12.0)

    # Decide expected per-glyph advance from the actual text, not from the
    # font name. A CJK font carrying Latin digits is a common PPT export
    # pattern and trips up the font-name check.
    if text:
        has_cjk = has_cjk_text(text)
        has_latin = any(ch.isascii() and ch.isalnum() for ch in text)
        if has_cjk and has_latin:
            # Mixed CJK + Latin ("小标题Title"): per-char advances span two
            # very different glyph widths, so the mean advance is meaningless
            # as a letter-spacing signal. Bail out — render at natural width.
            return 0.0
        cjk = has_cjk
    else:
        cjk = is_cjk_font_name(font_name)

    if not cjk:
        # Proportional Latin font: only honor letter-spacing when every advance
        # is within 10% of the mean (i.e. the text is uniformly spaced).
        if avg_advance <= 0:
            return 0.0
        max_dev = max(abs(a - avg_advance) for a in advances)
        if max_dev > 0.1 * avg_advance:
            return 0.0

    expected = size * (0.95 if cjk else 0.5)
    extra = avg_advance - expected
    if abs(extra) < 0.05 * size:
        return 0.0
    return round(extra, 3)


def _record_text_diagnostics(
    bag: list[dict],
    text: str,
    raw_font: str,
    clean_font: str,
    span_index: int,
    line_index: int,
    block_index: int,
    cjk_text: bool,
    cjk_font: bool,
) -> None:
    if REPLACEMENT_CHAR in text:
        bag.append(
            {
                "severity": "warning",
                "code": "text-replacement-char",
                "block": block_index,
                "line": line_index,
                "span": span_index,
                "font": clean_font,
                "raw_font": raw_font,
                "message": "Span contains U+FFFD; ToUnicode mapping may be missing.",
            }
        )
    if cjk_text and not cjk_font:
        bag.append(
            {
                "severity": "info",
                "code": "text-cjk-non-cjk-font",
                "block": block_index,
                "line": line_index,
                "span": span_index,
                "font": clean_font,
                "message": "CJK text painted with a non-CJK-looking font; CJK fallback applied.",
            }
        )
