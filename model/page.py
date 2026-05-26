from __future__ import annotations

from dataclasses import dataclass, field

from model.elements import ClipPathDefinition, ElementModel
from model.geometry import BBox


@dataclass(slots=True)
class PageModel:
    stable_id: str
    page_index: int
    width_pt: float
    height_pt: float
    width_px: float
    height_px: float
    rotation: int
    media_box: BBox
    crop_box: BBox
    scale_factor: float
    elements: list[ElementModel] = field(default_factory=list)
    clip_paths: list[ClipPathDefinition] = field(default_factory=list)
    diagnostics: list[dict] = field(default_factory=list)
