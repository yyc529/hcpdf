from __future__ import annotations

import fitz

from assets.cache import AssetCache
from assets.image_pipeline import save_derived_image
from core.context import DocumentContext
from model.elements import GradientFillElement, ImageElement, TextBlockElement, VectorElement
from model.assets import ImageAsset
from model.geometry import BBox
from model.page import PageModel
from model.styles import VectorStyle
from parser.annotation_parser import parse_annotations, parse_form_widgets, parse_links
from parser.deep_pdf_parser import parse_page_paint_events
from parser.image_parser import parse_images
from parser.table_parser import parse_table_candidates
from parser.text_parser import parse_text
from parser.vector_parser import parse_vectors
from resolver.deep_paint_resolver import build_gradient_elements, suppress_redundant_rasters
from resolver.image_clip_resolver import attach_image_clips
from resolver.layer_resolver import assign_layer_order
from resolver.paint_order_resolver import assign_paint_order
from resolver.text_flow_resolver import resolve_text_flow, text_flow_sidecar
from resolver.vector_optimizer import optimize_clip_paths


def parse_page(
    doc: fitz.Document,
    page: fitz.Page,
    page_index: int,
    context: DocumentContext,
    cache: AssetCache,
) -> tuple[PageModel, list, dict]:
    rect = page.rect
    scale = context.config.css_px_per_pt
    page_model = PageModel(
        stable_id=f"pdf-page-{page_index + 1}",
        page_index=page_index + 1,
        width_pt=float(rect.width),
        height_pt=float(rect.height),
        width_px=float(rect.width) * scale,
        height_px=float(rect.height) * scale,
        rotation=int(page.rotation or 0),
        media_box=BBox.from_seq(page.mediabox),
        crop_box=BBox.from_seq(page.cropbox),
        scale_factor=scale,
    )
    vectors, clip_definitions = parse_vectors(page, page_index, context.diagnostics)
    images, image_assets = parse_images(doc, page, page_index, context, cache)
    assign_paint_order(vectors, images)
    # Phase 7: walk the raw content stream with pikepdf to recover semantic
    # gradient fills that PyMuPDF would otherwise have flattened into
    # blurry inline rasters. The bridge resolver also marks any redundant
    # raster for suppression so we don't double-paint.
    gradients = []
    if context.config.use_deep_parser:
        paint_events = parse_page_paint_events(
            str(context.source_path), page_index, context.diagnostics
        )
        gradients = build_gradient_elements(
            paint_events, page_index, page_model.height_pt
        )
        suppressed = suppress_redundant_rasters(gradients, images)
        _assign_gradient_bboxlog_seqnos(page, gradients)
        if gradients:
            page_model.diagnostics.append(
                {
                    "severity": "info",
                    "code": "deep-parser-gradients",
                    "gradient_count": len(gradients),
                    "raster_suppressed_count": suppressed,
                }
            )
    _suppress_vectors_inside_baked_crops(vectors, images)
    attach_image_clips(
        images,
        clip_definitions,
        vectors=vectors,
        page_bbox=BBox(0.0, 0.0, page_model.width_pt, page_model.height_pt),
    )
    if context.config.optimize_vector_clips:
        clip_definitions, clip_stats = optimize_clip_paths(
            vectors,
            clip_definitions,
            BBox(0, 0, page_model.width_pt, page_model.height_pt),
            tolerance=context.config.clip_bbox_tolerance_pt,
        )
        if clip_stats["original_clip_paths"] != clip_stats["kept_clip_paths"]:
            page_model.diagnostics.append(
                {
                    "severity": "info",
                    "code": "vector-clip-optimization",
                    **clip_stats,
                }
            )
    page_model.clip_paths = clip_definitions
    text_blocks: list[TextBlockElement] = parse_text(page, page_index)
    _suppress_broad_blur_overlays(images, page_model.width_pt, page_model.height_pt)
    _add_glass_panel_fallbacks(
        vectors,
        images,
        gradients,
        text_blocks,
        page_index,
        BBox(0.0, 0.0, page_model.width_pt, page_model.height_pt),
        context,
        cache,
        image_assets,
    )
    text_flow = resolve_text_flow(text_blocks, page_model.width_pt, page_model.height_pt)
    links = parse_links(page, page_index)
    forms = parse_form_widgets(page, page_index)
    annotations = parse_annotations(page, page_index)
    tables = parse_table_candidates(context.source_path, page_index) if context.config.extract_tables else []
    page_model.elements = assign_layer_order(
        [*vectors, *images, *gradients, *text_blocks, *links, *forms, *annotations, *tables]
    )
    return page_model, image_assets, text_flow_sidecar(text_flow)


def _assign_gradient_bboxlog_seqnos(
    page: fitz.Page, gradients: list[GradientFillElement]
) -> None:
    if not gradients:
        return
    try:
        shade_seqnos = [
            seqno
            for seqno, entry in enumerate(page.get_bboxlog())
            if entry and entry[0] == "fill-shade"
        ]
    except Exception:
        return
    if not shade_seqnos:
        return
    cursor = 0
    for gradient in gradients:
        if cursor >= len(shade_seqnos):
            break
        consume = 2 if gradient.mask_kind else 1
        end = min(len(shade_seqnos), cursor + consume)
        gradient.paint_seqno = shade_seqnos[end - 1]
        gradient.source["paint_seqno"] = gradient.paint_seqno
        gradient.source["bboxlog_shade_seqno"] = gradient.paint_seqno
        cursor = end


def _add_glass_panel_fallbacks(
    vectors: list[VectorElement],
    images: list[ImageElement],
    gradients: list[GradientFillElement],
    text_blocks: list[TextBlockElement],
    page_index: int,
    page_box: BBox,
    context: DocumentContext,
    cache: AssetCache,
    image_assets: list[ImageAsset],
) -> None:
    fallbacks: list[VectorElement] = []
    image_fallbacks: list[ImageElement] = []
    for image in images:
        if not _is_retained_subtle_rect_shadow(image, page_box):
            continue
        base = _find_matching_panel_base(image, images, gradients)
        if base is not None:
            image.paint_seqno = base[1] - 0.5
            image.source["paint_seqno"] = image.paint_seqno
            image.diagnostics.append(
                {
                    "severity": "info",
                    "code": "subtle-shadow-reordered-behind-panel",
                    "message": (
                        "Low-alpha shadow overlaps a matching rounded panel; "
                        "painted behind the panel so it does not gray the fill."
                    ),
                }
            )
            if base[2] == "gradient":
                overlay_asset = _make_white_smask_overlay_asset(image, context, cache)
                if overlay_asset is not None:
                    image_assets.append(overlay_asset)
                    image_fallbacks.append(
                        ImageElement(
                            stable_id=f"{image.stable_id}-white-highlight",
                            type="image",
                            page_index=page_index + 1,
                            bbox=image.bbox,
                            z_index=0,
                            source_index=image.source_index,
                            render_mode="asset-with-vector-effects",
                            editable=False,
                            source={
                                "source": "inferred-white-highlight-from-smask",
                                "shadow_image": image.stable_id,
                            },
                            diagnostics=[
                                {
                                    "severity": "info",
                                    "code": "glass-highlight-derived-from-soft-mask",
                                    "message": (
                                        "A low-alpha dark overlay sits on a gradient panel; "
                                        "its soft mask is reused as a white translucent "
                                        "highlight so the panel is lightened instead of greyed."
                                    ),
                                }
                            ],
                            asset_path=overlay_asset.path,
                            effect_pipeline=["white-highlight-from-smask"],
                            paint_seqno=base[1] + 0.5,
                        )
                    )
            continue
        inner = _inset_bbox(image.bbox, min(image.bbox.width, image.bbox.height) * 0.07)
        if inner.width <= 8.0 or inner.height <= 8.0:
            continue
        if not _has_panel_content(inner, vectors, text_blocks):
            continue
        image.diagnostics.append(
            {
                "severity": "info",
                "code": "subtle-shadow-replaced-by-glass-panel",
                "message": (
                    "Low-alpha rectangular image has no matching panel fill; "
                    "replaced it with an inferred translucent panel to avoid "
                    "rendering the shadow as a dark blurred interior."
                ),
            }
        )
        image.render_mode = "diagnostic-placeholder"
        image.asset_path = None
        image.effect_pipeline.append("subtle-shadow-replaced-by-glass-panel")
        fallbacks.append(
            _make_glass_panel_fallback(
                inner,
                page_index,
                len(fallbacks),
                image,
                fill_opacity=0.20,
                paint_seqno=image.paint_seqno + 0.5,
                code="glass-panel-inferred-from-subtle-shadow",
                message=(
                    "A retained low-alpha rectangular shadow contains "
                    "foreground content but has no matching fill layer; "
                    "added a translucent rounded panel fallback."
                ),
            )
        )
    if fallbacks:
        vectors[:] = sorted([*vectors, *fallbacks], key=lambda vector: vector.paint_seqno)
    if image_fallbacks:
        images[:] = sorted([*images, *image_fallbacks], key=lambda item: item.paint_seqno)


def _suppress_broad_blur_overlays(
    images: list[ImageElement], page_width: float, page_height: float
) -> None:
    page_area = max(1.0, page_width * page_height)
    local_subtle = [
        image
        for image in images
        if image.asset_path
        and image.derived_asset_path
        and any(d.get("code") == "image-subtle-overlay-retained" for d in image.diagnostics)
        and (image.bbox.width * image.bbox.height) / page_area <= 0.12
    ]
    if len(local_subtle) < 2:
        return
    for image in images:
        if not image.asset_path or not image.derived_asset_path:
            continue
        if not any(float(subtle.paint_seqno) < float(image.paint_seqno) for subtle in local_subtle):
            continue
        area_ratio = (image.bbox.width * image.bbox.height) / page_area
        if area_ratio < 0.80:
            continue
        if not image.mask_asset_path:
            continue
        image.diagnostics.append(
            {
                "severity": "info",
                "code": "broad-blur-overlay-suppressed-preserve-transparency",
                "message": (
                    "A broad masked precomposed overlay sits above local "
                    "glass-panel controls. Suppressed so the controls remain "
                    "transparent instead of inheriting a blurred raster layer."
                ),
            }
        )
        image.render_mode = "diagnostic-placeholder"
        image.asset_path = None
        image.effect_pipeline.append("broad-blur-overlay-suppressed")


def _salvage_lowres_overlay_control_crops(images: list[ImageElement]) -> None:
    controls = [
        image
        for image in images
        if any(d.get("code") == "subtle-shadow-reordered-behind-panel" for d in image.diagnostics)
    ]
    if not controls:
        return
    salvaged: set[str] = set()
    highlight_ids_to_remove: set[str] = set()
    crops: list[ImageElement] = []
    broad_overlays = sorted(
        [
            image
            for image in images
            if "lowres-precomposed-overlay-suppressed" in image.effect_pipeline
            and (image.derived_asset_path or image.asset_path)
        ],
        key=lambda item: float(item.paint_seqno),
    )
    for overlay in broad_overlays:
        for control in controls:
            if control.stable_id in salvaged:
                continue
            if float(control.paint_seqno) >= float(overlay.paint_seqno):
                continue
            if not _bbox_substantially_overlaps(control.bbox, overlay.bbox, threshold=0.25):
                continue
            clip = _pad_bbox(control.bbox, 3.0, overlay.bbox)
            crops.append(
                ImageElement(
                    stable_id=f"{overlay.stable_id}-control-crop-{len(crops)}",
                    type="image",
                    page_index=overlay.page_index,
                    bbox=overlay.bbox,
                    z_index=0,
                    source_index=overlay.source_index,
                    render_mode="asset-with-vector-effects",
                    editable=False,
                    source={
                        "source": "lowres-precomposed-overlay-control-crop",
                        "overlay_image": overlay.stable_id,
                        "control_image": control.stable_id,
                        "clip_bbox": clip.as_list(),
                    },
                    diagnostics=[
                        {
                            "severity": "info",
                            "code": "lowres-overlay-control-crop-restored",
                            "message": (
                                "A broad low-resolution precomposed overlay also "
                                "contains a local glass control. Restored only the "
                                "control crop so the button keeps its highlight "
                                "without covering sharper page imagery."
                            ),
                        }
                    ],
                    asset_path=overlay.derived_asset_path or overlay.asset_path,
                    derived_asset_path=overlay.derived_asset_path,
                    effect_pipeline=["lowres-overlay-control-crop-restored"],
                    clip_path_data=_rect_clip_path(clip),
                    paint_seqno=overlay.paint_seqno,
                )
            )
            salvaged.add(control.stable_id)
            highlight_ids_to_remove.add(f"{control.stable_id}-white-highlight")
    if crops:
        images[:] = sorted(
            [
                *[
                    image
                    for image in images
                    if image.stable_id not in highlight_ids_to_remove
                ],
                *crops,
            ],
            key=lambda item: float(item.paint_seqno),
        )


def _make_glass_panel_fallback(
    bbox: BBox,
    page_index: int,
    index: int,
    shadow_image: ImageElement,
    *,
    fill_opacity: float,
    paint_seqno: float,
    code: str,
    message: str,
) -> VectorElement:
    rx = min(bbox.width, bbox.height) * 0.10
    return VectorElement(
        stable_id=f"pdf-page-{page_index + 1}-glass-panel-{index}",
        type="vector",
        page_index=page_index + 1,
        bbox=bbox,
        z_index=0,
        source_index=-1,
        render_mode="semantic-svg",
        editable=False,
        source={
            "source": "inferred-glass-panel",
            "shadow_image": shadow_image.stable_id,
        },
        diagnostics=[
            {
                "severity": "info",
                "code": code,
                "message": message,
            }
        ],
        path_data=_rounded_rect_path(bbox, rx),
        style=VectorStyle(
            stroke=None,
            fill="rgb(255, 255, 255)",
            stroke_width=0.0,
            fill_opacity=fill_opacity,
        ),
        even_odd=True,
        paint_seqno=paint_seqno,
    )


def _make_white_smask_overlay_asset(
    image: ImageElement, context: DocumentContext, cache: AssetCache
) -> ImageAsset | None:
    mask_path = image.mask_asset_path or image.derived_asset_path
    if not mask_path:
        return None
    try:
        from io import BytesIO

        from PIL import Image
    except Exception:
        return None
    source = context.output_dir / mask_path
    if not source.exists():
        return None
    try:
        with Image.open(source) as mask_image:
            rgba = mask_image.convert("RGBA")
            if image.mask_asset_path:
                alpha = rgba.convert("L")
            else:
                alpha = rgba.getchannel("A")
            alpha = alpha.point(lambda value: min(255, int(value * 2.25)))
            white = Image.new("RGBA", rgba.size, (255, 255, 255, 0))
            white.putalpha(alpha)
            buffer = BytesIO()
            white.save(buffer, format="PNG")
    except Exception:
        return None
    return save_derived_image(
        buffer.getvalue(),
        context.image_dir,
        cache,
        source_xref=image.xref,
        mask_xref=image.mask_xref,
        pipeline=["white-highlight-from-smask"],
    )


def _is_retained_subtle_rect_shadow(image: ImageElement, page_box: BBox) -> bool:
    if not image.asset_path or not image.derived_asset_path:
        return False
    if not any(d.get("code") == "image-subtle-overlay-retained" for d in image.diagnostics):
        return False
    area = max(0.0, image.bbox.width * image.bbox.height)
    page_area = max(1.0, page_box.width * page_box.height)
    if area / page_area > 0.12:
        return False
    aspect = image.bbox.width / max(1.0, image.bbox.height)
    return 0.45 <= aspect <= 4.0


def _find_matching_panel_base(
    shadow_image: ImageElement,
    images: list[ImageElement],
    gradients: list[GradientFillElement],
) -> tuple[BBox, float, str] | None:
    shadow_box = shadow_image.bbox
    for gradient in gradients:
        if _panel_base_matches_shadow(gradient.bbox, shadow_box):
            return gradient.bbox, float(gradient.paint_seqno), "gradient"
    for image in images:
        if image is shadow_image:
            continue
        if image.asset_path and _panel_base_matches_shadow(image.bbox, shadow_box):
            return image.bbox, float(image.paint_seqno), "image"
    return None


def _panel_base_matches_shadow(base_box: BBox, shadow_box: BBox) -> bool:
    if not _bbox_substantially_overlaps(base_box, shadow_box, threshold=0.85):
        return False
    shadow_area = max(1.0, shadow_box.width * shadow_box.height)
    base_area = max(1.0, base_box.width * base_box.height)
    ratio = shadow_area / base_area
    inverse_ratio = base_area / shadow_area
    return ratio <= 2.2 and inverse_ratio <= 2.5


def _has_panel_content(
    panel_box: BBox,
    vectors: list[VectorElement],
    text_blocks: list[TextBlockElement],
) -> bool:
    if any(_bbox_contained(vector.bbox, panel_box, slack=8.0) for vector in vectors):
        return True
    return any(_bbox_contained(text.bbox, panel_box, slack=8.0) for text in text_blocks)


def _inset_bbox(bbox: BBox, inset: float) -> BBox:
    return BBox(bbox.x0 + inset, bbox.y0 + inset, bbox.x1 - inset, bbox.y1 - inset)


def _pad_bbox(bbox: BBox, pad: float, bounds: BBox) -> BBox:
    return BBox(
        max(bounds.x0, bbox.x0 - pad),
        max(bounds.y0, bbox.y0 - pad),
        min(bounds.x1, bbox.x1 + pad),
        min(bounds.y1, bbox.y1 + pad),
    )


def _rounded_rect_path(bbox: BBox, radius: float) -> str:
    r = min(radius, bbox.width / 2.0, bbox.height / 2.0)
    x0, y0, x1, y1 = bbox.x0, bbox.y0, bbox.x1, bbox.y1
    k = 0.5522847498
    c = r * k
    return (
        f"M {x0 + r:.3f} {y0:.3f} "
        f"L {x1 - r:.3f} {y0:.3f} "
        f"C {x1 - r + c:.3f} {y0:.3f} {x1:.3f} {y0 + r - c:.3f} {x1:.3f} {y0 + r:.3f} "
        f"L {x1:.3f} {y1 - r:.3f} "
        f"C {x1:.3f} {y1 - r + c:.3f} {x1 - r + c:.3f} {y1:.3f} {x1 - r:.3f} {y1:.3f} "
        f"L {x0 + r:.3f} {y1:.3f} "
        f"C {x0 + r - c:.3f} {y1:.3f} {x0:.3f} {y1 - r + c:.3f} {x0:.3f} {y1 - r:.3f} "
        f"L {x0:.3f} {y0 + r:.3f} "
        f"C {x0:.3f} {y0 + r - c:.3f} {x0 + r - c:.3f} {y0:.3f} {x0 + r:.3f} {y0:.3f} Z"
    )


def _rect_clip_path(bbox: BBox) -> str:
    return (
        f"M {bbox.x0:.3f} {bbox.y0:.3f} "
        f"L {bbox.x1:.3f} {bbox.y0:.3f} "
        f"L {bbox.x1:.3f} {bbox.y1:.3f} "
        f"L {bbox.x0:.3f} {bbox.y1:.3f} Z"
    )


def _suppress_vectors_inside_baked_crops(vectors, images) -> None:
    baked_boxes = [
        image.bbox
        for image in images
        if "page-region-baked-fallback" in getattr(image, "effect_pipeline", [])
    ]
    if not baked_boxes:
        return
    vectors[:] = [
        vector
        for vector in vectors
        if not any(_bbox_contained(vector.bbox, baked_box) for baked_box in baked_boxes)
    ]


def _bbox_contained(inner: BBox, outer: BBox, slack: float = 0.5) -> bool:
    return (
        inner.x0 >= outer.x0 - slack
        and inner.y0 >= outer.y0 - slack
        and inner.x1 <= outer.x1 + slack
        and inner.y1 <= outer.y1 + slack
    )


def _bbox_substantially_overlaps(
    a: BBox, b: BBox, threshold: float = 0.85
) -> bool:
    overlap_x = max(0.0, min(a.x1, b.x1) - max(a.x0, b.x0))
    overlap_y = max(0.0, min(a.y1, b.y1) - max(a.y0, b.y0))
    overlap = overlap_x * overlap_y
    if overlap <= 0:
        return False
    area_a = max(1.0, a.width * a.height)
    area_b = max(1.0, b.width * b.height)
    return overlap / min(area_a, area_b) >= threshold
