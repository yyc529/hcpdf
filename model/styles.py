from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class TextStyle:
    font_name: str
    font_family: str
    font_size: float
    color: str
    opacity: float = 1.0
    writing_mode: str = "horizontal-tb"
    ascender: float = 0.8
    descender: float = -0.2
    rotation: float = 0.0
    letter_spacing: float = 0.0
    bold: bool = False
    italic: bool = False
    has_cjk: bool = False
    glyph_source: str = "embedded"


@dataclass(slots=True)
class VectorStyle:
    stroke: str | None = None
    fill: str | None = None
    stroke_width: float = 1.0
    stroke_opacity: float = 1.0
    fill_opacity: float = 1.0
    dash: str | None = None
    dash_offset: float = 0.0
    line_cap: str | None = None
    line_join: str | None = None
    miter_limit: float | None = None
