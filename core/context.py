from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from core.config import ConversionConfig


@dataclass(slots=True)
class DocumentContext:
    source_path: Path
    output_dir: Path
    config: ConversionConfig
    diagnostics: list[dict] = field(default_factory=list)

    @property
    def assets_dir(self) -> Path:
        return self.output_dir / "assets"

    @property
    def image_dir(self) -> Path:
        return self.assets_dir / "images"

    @property
    def font_dir(self) -> Path:
        return self.assets_dir / "fonts"

    @property
    def original_pages_dir(self) -> Path:
        return self.assets_dir / "original_pages"

    @property
    def html_screenshots_dir(self) -> Path:
        return self.assets_dir / "html_pages"

    @property
    def debug_dir(self) -> Path:
        return self.assets_dir / "debug"


@dataclass(slots=True)
class PageContext:
    document: DocumentContext
    page_index: int
    diagnostics: list[dict] = field(default_factory=list)

    @property
    def page_number(self) -> int:
        return self.page_index + 1
