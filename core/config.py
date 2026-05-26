from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ConversionConfig:
    css_px_per_pt: float = 96.0 / 72.0
    original_dpi: int = 144
    image_dedupe: bool = True
    extract_fonts: bool = True
    extract_tables: bool = False
    generate_html_screenshots: bool = False
    continue_on_page_error: bool = True
    pretty_json: bool = True
    optimize_vector_clips: bool = True
    clip_bbox_tolerance_pt: float = 0.5
    # Phase 7: pikepdf-based content stream walker that recovers semantic
    # gradient fills (and, in subsequent phases, stroke borders / proper clip
    # stacks). When disabled, the pipeline behaves exactly as it did before
    # Phase 7 — useful for A/B comparing fidelity.
    use_deep_parser: bool = True
