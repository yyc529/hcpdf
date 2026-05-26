from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from model.assets import FontAsset, ImageAsset
from model.page import PageModel


@dataclass(slots=True)
class DocumentModel:
    source_path: Path
    output_dir: Path
    page_count: int
    metadata: dict
    pages: list[PageModel] = field(default_factory=list)
    images: list[ImageAsset] = field(default_factory=list)
    fonts: list[FontAsset] = field(default_factory=list)
    diagnostics: list[dict] = field(default_factory=list)
