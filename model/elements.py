from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from model.geometry import BBox
from model.styles import TextStyle, VectorStyle


@dataclass(slots=True)
class ElementModel:
    stable_id: str
    type: str
    page_index: int
    bbox: BBox
    z_index: int
    source_index: int
    render_mode: str
    editable: bool
    opacity: float = 1.0
    source: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[dict] = field(default_factory=list)


@dataclass(slots=True)
class TextSpan:
    stable_id: str
    text: str
    bbox: BBox
    style: TextStyle
    source_index: int
    origin: tuple[float, float] = (0.0, 0.0)
    char_count: int = 0


@dataclass(slots=True)
class TextLine:
    stable_id: str
    bbox: BBox
    spans: list[TextSpan]
    source_index: int


@dataclass(slots=True)
class TextBlockElement(ElementModel):
    lines: list[TextLine] = field(default_factory=list)
    text_flow: str = "visual"


@dataclass(slots=True)
class ImageElement(ElementModel):
    xref: int | None = None
    asset_path: str | None = None
    mask_xref: int | None = None
    mask_asset_path: str | None = None
    derived_asset_path: str | None = None
    effect_pipeline: list[str] = field(default_factory=list)
    clip_path_data: str | None = None
    transform: list[float] | None = None
    smask_kind: str | None = None
    has_alpha: bool = False
    filters: list[dict] = field(default_factory=list)
    blend_mode: str | None = None
    paint_seqno: int = 0


@dataclass(slots=True)
class VectorElement(ElementModel):
    path_data: str = ""
    style: VectorStyle = field(default_factory=VectorStyle)
    even_odd: bool = False
    clip_path_id: str | None = None
    blend_mode: str | None = None
    group_opacity: float = 1.0
    paint_seqno: int = 0


@dataclass(slots=True)
class GradientStop:
    offset: float
    rgb: tuple[float, float, float]


@dataclass(slots=True)
class GradientFillElement(ElementModel):
    """An SVG-renderable shading-pattern fill recovered by the deep parser.

    Carries a path (the geometry, in page coords with CTM applied) plus a
    linear-gradient definition (coords + colour stops in the same space).
    Replaces the rasterised inline image that PyMuPDF would otherwise hand
    us for Page-6-style decorative cards, so we get a crisp SVG with both
    the gradient *and* the border.
    """
    path_data: str = ""
    even_odd: bool = False
    fill_alpha: float = 1.0
    pattern_name: str = ""
    grad_x1: float = 0.0
    grad_y1: float = 0.0
    grad_x2: float = 0.0
    grad_y2: float = 0.0
    grad_extend_start: bool = False
    grad_extend_end: bool = False
    grad_stops: list[GradientStop] = field(default_factory=list)
    grad_matrix: tuple[float, float, float, float, float, float] | None = None
    paint_seqno: int = 0
    # Optional luminosity SMask defined by another shading pattern. When
    # present, the path is rendered with a SVG <mask> that uses this
    # gradient's luminance as alpha — recovers PDF's
    # ``gs /SMask /S Luminosity`` semantic.
    mask_kind: str | None = None  # "Luminosity" or "Alpha"
    mask_grad_x1: float = 0.0
    mask_grad_y1: float = 0.0
    mask_grad_x2: float = 0.0
    mask_grad_y2: float = 0.0
    mask_grad_stops: list[GradientStop] = field(default_factory=list)


@dataclass(slots=True)
class ClipPathDefinition:
    stable_id: str
    path_data: str
    bbox: BBox
    even_odd: bool = False
    level: int = 0


@dataclass(slots=True)
class LinkElement(ElementModel):
    href: str = ""
    target_page: int | None = None


@dataclass(slots=True)
class TableCandidate(ElementModel):
    cells: list[list[str]] = field(default_factory=list)
    cell_bboxes: list[list[list[float]]] = field(default_factory=list)
    confidence: float = 0.0
    rows: int = 0
    cols: int = 0


@dataclass(slots=True)
class FormElement(ElementModel):
    field_type: str = "text"
    field_name: str = ""
    value: str = ""
    checked: bool = False
    options: list[str] = field(default_factory=list)
    flags: int = 0


@dataclass(slots=True)
class AnnotationElement(ElementModel):
    annot_type: str = ""
    contents: str = ""
    title: str = ""
    color: str | None = None
    icon: str | None = None
