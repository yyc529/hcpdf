from __future__ import annotations

import fitz

from model.geometry import BBox


def fitz_rect_to_bbox(rect: fitz.Rect | tuple | list) -> BBox:
    if isinstance(rect, fitz.Rect):
        return BBox(float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))
    return BBox.from_seq(rect)
