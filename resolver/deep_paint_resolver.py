"""Bridge between the pikepdf deep parser and the page model.

Converts ``PaintEvent`` records (semantic gradient/path operations) into
``GradientFillElement`` instances the renderer can paint as SVG. Also
identifies which existing rasterised images they replace so the duplicate
raster can be suppressed (no double-paint).
"""

from __future__ import annotations

from model.elements import (
    GradientFillElement,
    GradientStop,
    ImageElement,
)
from model.geometry import BBox
from model.page import PageModel
from parser.deep_pdf_parser import PaintEvent


# Maximum bbox L∞ delta between a deep-parser event and a PyMuPDF raster
# image that we treat as the same draw — i.e. "this gradient fill is what
# PyMuPDF rasterised into the inline image at the same bbox". Generous
# enough to cover sub-point quantisation but tight enough to never match
# unrelated images.
_RASTER_DEDUP_TOLERANCE_PT = 4.0


def build_gradient_elements(
    events: list[PaintEvent],
    page_index: int,
    page_height_pt: float,
) -> list[GradientFillElement]:
    """Translate ``PaintEvent`` instances into ``GradientFillElement``s.

    The resulting elements use ``paint_seqno`` derived from the event's
    stream position so they can be interleaved with existing PyMuPDF
    vectors/images by the paint-order-aware classifier.

    Element ``bbox`` is converted from PDF native (y-up from bottom-left)
    into the top-down coordinate space the rest of the model uses, so
    raster-suppression bbox matching against PyMuPDF images is correct.
    Path data and gradient endpoints are left in PDF native — the SVG
    renderer applies a ``y-flip`` transform at output time so both flip
    together inside the SVG's user space.
    """
    elements: list[GradientFillElement] = []
    for idx, event in enumerate(events):
        if event.kind != "pattern-fill" or event.gradient is None:
            continue
        x0, y0, x1, y1 = event.bbox
        # Convert PDF y-up (origin bottom-left) → top-down (origin top-left).
        # The path/gradient coords stay PDF-native; only the bbox we store
        # for downstream resolvers gets converted.
        bbox = BBox(
            float(x0),
            float(page_height_pt - y1),
            float(x1),
            float(page_height_pt - y0),
        )
        mask_kind = None
        mask_x1 = mask_y1 = mask_x2 = mask_y2 = 0.0
        mask_stops: list[GradientStop] = []
        if event.soft_mask is not None and event.soft_mask.gradient is not None:
            mg = event.soft_mask.gradient
            mask_kind = event.soft_mask.smask_kind or "Luminosity"
            mask_x1, mask_y1, mask_x2, mask_y2 = mg.x1, mg.y1, mg.x2, mg.y2
            mask_stops = [
                GradientStop(offset=s.offset, rgb=s.rgb) for s in mg.stops
            ]
        elements.append(
            GradientFillElement(
                stable_id=f"pdf-page-{page_index + 1}-gradient-{idx}",
                type="gradient-fill",
                page_index=page_index + 1,
                bbox=bbox,
                z_index=0,
                source_index=idx,
                render_mode="semantic-svg",
                editable=False,
                source={
                    "deep_parser": True,
                    "pattern_name": event.gradient.pattern_name,
                    "pdf_op": event.pdf_op,
                    "stream_seqno": event.seqno,
                    "soft_mask_kind": mask_kind,
                },
                path_data=event.path_data,
                even_odd=event.even_odd,
                fill_alpha=event.fill_alpha,
                pattern_name=event.gradient.pattern_name,
                grad_x1=event.gradient.x1,
                grad_y1=event.gradient.y1,
                grad_x2=event.gradient.x2,
                grad_y2=event.gradient.y2,
                grad_extend_start=event.gradient.extend_start,
                grad_extend_end=event.gradient.extend_end,
                grad_stops=[
                    GradientStop(offset=s.offset, rgb=s.rgb)
                    for s in event.gradient.stops
                ],
                grad_matrix=event.gradient.matrix,
                paint_seqno=event.seqno,
                mask_kind=mask_kind,
                mask_grad_x1=mask_x1,
                mask_grad_y1=mask_y1,
                mask_grad_x2=mask_x2,
                mask_grad_y2=mask_y2,
                mask_grad_stops=mask_stops,
            )
        )
    return elements


def suppress_redundant_rasters(
    gradients: list[GradientFillElement],
    images: list[ImageElement],
) -> int:
    """Mark inline images whose bbox matches a deep-parser gradient.

    PyMuPDF rasterises Pattern fills as inline images. Now that we paint the
    same content as crisp SVG gradients, those rasters become **redundant**
    and would just double up (and quietly cover the SVG with their slightly
    blurred raster version). Returns the count of rasters suppressed.
    """
    if not gradients or not images:
        return 0
    suppressed = 0
    for image in images:
        if image.source.get("kind") != "inline":
            continue  # Only suppress inline rasters; embedded xref images are real photos.
        matching_gradients = [g for g in gradients if _bbox_close(image.bbox, g.bbox)]
        if matching_gradients:
            for gradient in matching_gradients:
                gradient.paint_seqno = image.paint_seqno
            image.render_mode = "diagnostic-placeholder"
            image.editable = False
            image.asset_path = None  # The renderer will emit a hidden placeholder div.
            image.diagnostics.append(
                {
                    "severity": "info",
                    "code": "image-suppressed-by-deep-parser",
                    "message": (
                        "PyMuPDF rasterised a PDF Pattern fill into this "
                        "inline image; the deep parser emitted the same "
                        "content as an SVG gradient, so the raster is "
                        "suppressed to avoid double-paint."
                    ),
                }
            )
            suppressed += 1
    _suppress_gradients_when_raster_preserves_pdf_effect(gradients, images)
    return suppressed


def _bbox_close(image_bbox: BBox, grad_bbox: BBox) -> bool:
    return (
        abs(image_bbox.x0 - grad_bbox.x0) <= _RASTER_DEDUP_TOLERANCE_PT
        and abs(image_bbox.y0 - grad_bbox.y0) <= _RASTER_DEDUP_TOLERANCE_PT
        and abs(image_bbox.x1 - grad_bbox.x1) <= _RASTER_DEDUP_TOLERANCE_PT
        and abs(image_bbox.y1 - grad_bbox.y1) <= _RASTER_DEDUP_TOLERANCE_PT
    )


def _suppress_gradients_when_raster_preserves_pdf_effect(
    gradients: list[GradientFillElement],
    images: list[ImageElement],
) -> None:
    """Drop SVG gradients when the original inline raster is the safer source.

    Most inline pattern rasters can be replaced by crisp SVG gradients. A few
    PPT exports, though, use a rasterised pattern whose bbox is clipped or
    composited differently from the semantic pattern bbox. In that case the
    SVG gradient loses PDF transparency / stacking behavior and double-paints
    as an opaque panel. If a visible inline raster substantially overlaps the
    gradient but was not close enough to be suppressed above, keep the raster
    and drop the semantic approximation.
    """
    if not gradients:
        return
    visible_inline = [
        image
        for image in images
        if image.source.get("kind") == "inline" and image.asset_path
    ]
    if not visible_inline:
        return
    kept: list[GradientFillElement] = []
    for gradient in gradients:
        match = next(
            (
                image
                for image in visible_inline
                if _bbox_substantially_overlaps(image.bbox, gradient.bbox)
                and not _bbox_close(image.bbox, gradient.bbox)
            ),
            None,
        )
        if match is None:
            kept.append(gradient)
            continue
        match.diagnostics.append(
            {
                "severity": "info",
                "code": "semantic-gradient-suppressed-preserve-raster",
                "message": (
                    "Deep parser recovered a gradient, but the original inline "
                    "raster has a clipped/composited bbox that better preserves "
                    "the PDF transparency effect."
                ),
            }
        )
    gradients[:] = kept


def _bbox_substantially_overlaps(a: BBox, b: BBox) -> bool:
    overlap_x = max(0.0, min(a.x1, b.x1) - max(a.x0, b.x0))
    overlap_y = max(0.0, min(a.y1, b.y1) - max(a.y0, b.y0))
    overlap = overlap_x * overlap_y
    if overlap <= 0:
        return False
    area_a = max(1.0, a.width * a.height)
    area_b = max(1.0, b.width * b.height)
    return overlap / min(area_a, area_b) >= 0.85
