from __future__ import annotations

from model.geometry import BBox


def pt_to_px(value: float, scale: float) -> float:
    return value * scale


def bbox_to_px(bbox: BBox, scale: float) -> BBox:
    return BBox(
        x0=pt_to_px(bbox.x0, scale),
        y0=pt_to_px(bbox.y0, scale),
        x1=pt_to_px(bbox.x1, scale),
        y1=pt_to_px(bbox.y1, scale),
    )


def css_px(value: float) -> str:
    return f"{value:.3f}px"


def css_rect(bbox: BBox) -> str:
    return (
        f"left:{css_px(bbox.x0)};top:{css_px(bbox.y0)};"
        f"width:{css_px(max(0, bbox.width))};height:{css_px(max(0, bbox.height))};"
    )

