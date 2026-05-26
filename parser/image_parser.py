from __future__ import annotations

import math

import fitz

from assets.cache import AssetCache
from assets.image_pipeline import (
    compose_image_with_smask,
    detect_image_alpha,
    save_image_xref,
    save_derived_image,
    save_inline_image,
    smask_max_alpha,
)
from core.context import DocumentContext
from model.elements import ImageElement
from model.geometry import BBox


def _resolve_smask_xref(
    doc: fitz.Document, info: dict, image_xref: int | None
) -> int | None:
    """Find the soft-mask xref for an image.

    PyMuPDF's ``page.get_image_info()`` returns ``has-mask: True`` for images
    with an ``/SMask`` entry but does **not** include the smask xref. We have
    to walk the PDF object dictionary ourselves to recover it.
    """
    # Explicit smask key if present (rare, but cheap to check).
    smask = info.get("smask")
    try:
        smask = int(smask) if smask else None
    except (TypeError, ValueError):
        smask = None
    if smask:
        return smask
    if not image_xref or not info.get("has-mask"):
        return None
    try:
        raw = doc.xref_get_key(image_xref, "SMask")
    except Exception:
        return None
    if not raw:
        return None
    # ``xref_get_key`` returns a (kind, value) tuple — kind 'xref' for refs.
    if isinstance(raw, (tuple, list)) and len(raw) >= 2:
        kind, value = raw[0], raw[1]
        if kind != "xref":
            return None
        text = str(value).strip()
    else:
        text = str(raw).strip()
    # ``value`` looks like "38 0 R"; pull the first integer.
    for token in text.split():
        if token.isdigit():
            return int(token)
    return None


def _matrix_to_list(matrix) -> list[float] | None:
    if matrix is None:
        return None
    try:
        return [float(v) for v in (matrix.a, matrix.b, matrix.c, matrix.d, matrix.e, matrix.f)]
    except Exception:
        try:
            return [float(v) for v in matrix]
        except Exception:
            return None


def _bbox_key(bbox) -> tuple[int, int, int, int]:
    """Round bbox to 0.01 pt resolution for use as a dedup key."""
    try:
        x0, y0, x1, y1 = bbox
    except Exception:
        return (0, 0, 0, 0)
    return (
        int(round(float(x0) * 100)),
        int(round(float(y0) * 100)),
        int(round(float(x1) * 100)),
        int(round(float(y1) * 100)),
    )


def _build_inline_image_pool(page: fitz.Page) -> dict:
    """Collect image bytes from get_text('rawdict') image blocks indexed by bbox.

    Each bbox key maps to a list of items. After collection we try to detect
    soft-mask pairs (one grayscale + one solid color at the same bbox) and
    compose them into a single RGBA image; the second slot is marked with
    ``None`` so the consumer skips it instead of painting the raw mask on top.
    """
    pool: dict[tuple[int, int, int, int], list] = {}
    try:
        raw = page.get_text("rawdict")
    except Exception:
        return pool
    for block in raw.get("blocks", []):
        if block.get("type") != 1:
            continue
        image_bytes = block.get("image")
        if not image_bytes:
            continue
        key = _bbox_key(block.get("bbox"))
        pool.setdefault(key, []).append((image_bytes, (block.get("ext") or "png").lower()))

    composed_keys: list[tuple[int, int, int, int]] = []
    for key, items in list(pool.items()):
        if len(items) != 2:
            continue
        composed = _try_compose_softmask_pair(items[0], items[1])
        if composed:
            pool[key] = [composed, None]
            composed_keys.append(key)

    # PPT exports very often emit a *triplet* per decorative card:
    #
    #   inner pair = (luminance mask + color image) at bbox A
    #   outer single = a stroke/shadow / border image at bbox A ± 1pt
    #
    # The outer single is a fully-opaque colored rectangle painted *after*
    # the pair. If we keep it, it covers the composed inner gradient (Page 6
    # cards lose their pink-top fade). If we render it with mix-blend-mode
    # multiply, it darkens the entire card area and washes the gradient
    # flat. There's no way to recover the original "thin border only" intent
    # without the Meta-XObject internal blend mode info that PyMuPDF
    # doesn't surface, so we drop the outer entirely: the inner gradient
    # wins, the subtle border becomes a known-limitation footnote.
    for outer_key in list(pool.keys()):
        if len(pool[outer_key]) != 1:
            continue
        slot = pool[outer_key][0]
        if slot is None:
            continue
        if any(_is_outer_ring(outer_key, inner_key) for inner_key in composed_keys):
            pool[outer_key] = [None]
    return pool


# Maximum bbox delta (per side, in 0.01pt) for the outer-ring detector.
_RING_SLACK_HUNDREDTHS = int(round(2.0 * 100))


def _is_outer_ring(
    outer_key: tuple[int, int, int, int],
    inner_key: tuple[int, int, int, int],
) -> bool:
    ox0, oy0, ox1, oy1 = outer_key
    ix0, iy0, ix1, iy1 = inner_key
    # Outer must enclose inner with ≤ 2pt slack on every side, and strictly
    # extend beyond on at least one side (otherwise it's the same bbox and
    # we'd have paired it already).
    insets = (ix0 - ox0, iy0 - oy0, ox1 - ix1, oy1 - iy1)
    if any(inset < 0 for inset in insets):
        return False
    if max(insets) > _RING_SLACK_HUNDREDTHS:
        return False
    return any(inset > 0 for inset in insets)


def _try_compose_softmask_pair(item_a, item_b):
    """Compose two inline images at the same bbox into one RGBA PNG.

    PDF very often emits a luminosity soft mask + a color/stencil fill as two
    consecutive inline image XObjects with identical bbox/transform. Painting
    them as independent opaque rasters either hides the mask (when the fill
    is solid and drawn on top) or hides the fill (when the mask is opaque).
    The intended result is **fill colored by mask luminosity** — i.e. mask
    luminance becomes alpha for the fill.

    We pick the "mask" by the spread of its luminance: the one with the
    larger min-to-max grayscale range is the soft mask; the other is the
    fill. If neither has any spread, give up. The colored channels of the
    fill image are preserved and its alpha is replaced by the mask's
    luminance.
    """
    try:
        from PIL import Image
        import io
    except Exception:
        return None
    try:
        img_a = Image.open(io.BytesIO(item_a[0])).convert("RGBA")
        img_b = Image.open(io.BytesIO(item_b[0])).convert("RGBA")
    except Exception:
        return None
    if img_a.size != img_b.size:
        return None

    # Pick mask vs color in priority order:
    #
    # 1. If exactly one image is *strictly* grayscale (R == G == B at every
    #    sampled point), that image is the mask. PDF soft masks are always
    #    grayscale by spec; the matching color image has *some* chromatic
    #    pixels (e.g. a pink→purple timeline curve).
    # 2. Otherwise fall back to luminance spread — the gradient with greater
    #    spread is more likely to be the mask. This rescues older patterns
    #    where both images are colored but only one varies meaningfully.
    gray_a = _is_grayscale_only(img_a)
    gray_b = _is_grayscale_only(img_b)
    mask_img: object | None = None
    color_img: object | None = None
    if gray_a != gray_b:
        mask_img, color_img = (img_a, img_b) if gray_a else (img_b, img_a)
    else:
        spread_a = _luminance_spread(img_a)
        spread_b = _luminance_spread(img_b)
        if spread_a == 0 and spread_b == 0:
            return None
        if spread_a >= spread_b:
            mask_img, color_img = img_a, img_b
        else:
            mask_img, color_img = img_b, img_a

    alpha = mask_img.convert("L")
    color_img.putalpha(alpha)
    buffer = io.BytesIO()
    color_img.save(buffer, format="PNG", optimize=False)
    return (buffer.getvalue(), "png")


def _luminance_spread(img) -> int:
    """Return ``max_luminance - min_luminance`` across a few sample points.

    Cheap O(1) approximation — enough to tell a gradient mask apart from a
    flat fill without scanning every pixel."""
    w, h = img.size
    if w < 2 or h < 2:
        return 0
    samples = [
        (0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1),
        (w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2),
        (w // 2, h // 2), (w // 4, h // 4), (3 * w // 4, 3 * h // 4),
    ]
    lo = 256
    hi = -1
    for x, y in samples:
        try:
            pixel = img.getpixel((x, y))
        except Exception:
            continue
        # Approximate luminance from RGB (ignore alpha for spread check).
        if len(pixel) >= 3:
            lum = (pixel[0] * 299 + pixel[1] * 587 + pixel[2] * 114) // 1000
        else:
            lum = pixel[0]
        lo = min(lo, lum)
        hi = max(hi, lum)
    if hi < 0:
        return 0
    return max(0, hi - lo)


def _is_grayscale_only(img) -> bool:
    """Quick test: every pixel has R==G==B (probe corners + center)."""
    w, h = img.size
    sample_points = [
        (0, 0),
        (w - 1, 0),
        (0, h - 1),
        (w - 1, h - 1),
        (w // 2, h // 2),
        (w // 4, h // 2),
        (3 * w // 4, h // 2),
    ]
    for x, y in sample_points:
        try:
            pixel = img.getpixel((x, y))
        except Exception:
            return False
        if len(pixel) < 3:
            return True
        if not (pixel[0] == pixel[1] == pixel[2]):
            return False
    return True


def parse_images(
    doc: fitz.Document,
    page: fitz.Page,
    page_index: int,
    context: DocumentContext,
    cache: AssetCache,
) -> tuple[list[ImageElement], list]:
    elements: list[ImageElement] = []
    assets = []
    occurrences: dict[int, int] = {}
    try:
        infos = page.get_image_info(xrefs=True)
    except Exception as exc:
        context.diagnostics.append(
            {"severity": "warning", "code": "image-info-failed", "page": page_index + 1, "message": str(exc)}
        )
        infos = []

    inline_pool = _build_inline_image_pool(page)
    inline_counter = 0
    bboxlog_image_seqnos = _image_seqnos_from_bboxlog(page)

    page_rect = page.rect
    page_bbox = (
        float(page_rect.x0),
        float(page_rect.y0),
        float(page_rect.x1),
        float(page_rect.y1),
    )

    for source_index, info in enumerate(infos):
        xref = info.get("xref")
        xref = int(xref) if xref else None
        bbox = BBox.from_seq(info.get("bbox"))
        image_number = _match_bboxlog_image_seqno(bbox, bboxlog_image_seqnos)
        if image_number is None:
            image_number = info.get("number")
        try:
            image_number = int(image_number) if image_number is not None else source_index
        except (TypeError, ValueError):
            image_number = source_index
        occurrence = occurrences.get(xref or 0, 0)
        occurrences[xref or 0] = occurrence + 1

        smask_xref = _resolve_smask_xref(doc, info, xref)

        base_asset = None
        kind_hint = "embedded"
        if xref:
            base_asset = save_image_xref(doc, xref, occurrence, context.image_dir, cache)
        merged_softmask = False
        skip_emit = False
        pool_blend_hint: str | None = None
        if base_asset is None:
            # Inline image (xref==0) or extract failure — pull bytes from rawdict pool.
            bbox_key = _bbox_key(info.get("bbox"))
            bucket = inline_pool.get(bbox_key)
            if bucket:
                slot = bucket.pop(0)
                if slot is None:
                    # The previous occurrence at this bbox already absorbed
                    # this entry into a composed soft-mask asset; drop this
                    # one entirely so we do not paint the raw mask on top.
                    skip_emit = True
                else:
                    # Pool entries are (bytes, ext) for normal items or
                    # (bytes, ext, blend_hint) for outer-ring siblings the
                    # pool builder wants painted with a blend mode.
                    if len(slot) == 3:
                        image_bytes, ext, pool_blend_hint = slot
                    else:
                        image_bytes, ext = slot
                    base_asset = save_inline_image(
                        image_bytes, ext, inline_counter, context.image_dir, cache
                    )
                    inline_counter += 1
                    if base_asset:
                        kind_hint = "inline"
                        if bucket and bucket[0] is None:
                            merged_softmask = True
        if skip_emit:
            continue
        if base_asset:
            assets.append(base_asset)

        mask_asset = None
        derived_asset = None
        effect_pipeline: list[str] = []
        has_alpha, smask_kind = detect_image_alpha(doc, xref)
        if smask_xref:
            mask_asset = save_image_xref(
                doc,
                smask_xref,
                0,
                context.image_dir,
                cache,
                kind="pdf-image-smask",
                name_prefix="smask",
            )
            if mask_asset:
                assets.append(mask_asset)
                effect_pipeline.append("svg-mask")
            if xref and mask_asset:
                derived_asset = compose_image_with_smask(
                    doc, xref, smask_xref, context.image_dir, cache
                )
                if derived_asset:
                    assets.append(derived_asset)
                    effect_pipeline.append("smask-compose")
        elif has_alpha:
            effect_pipeline.append("native-alpha")
        if kind_hint == "inline":
            effect_pipeline.append("inline-image")
        if merged_softmask:
            effect_pipeline.append("inline-softmask-merged")

        # A leftover, un-merged inline grayscale image is almost always a
        # luminosity soft-mask in the PDF stream. Painting it opaque hides the
        # content below; multiply blend approximates the original effect.
        inline_grayscale_blend = (
            kind_hint == "inline"
            and not merged_softmask
            and base_asset
            and _asset_is_grayscale(base_asset, context.output_dir)
        )
        if inline_grayscale_blend:
            effect_pipeline.append("inline-grayscale-multiply")

        transform = _matrix_to_list(info.get("transform"))
        stable = f"pdf-page-{page_index + 1}-image-{xref or 'inline'}-{occurrence}"
        diagnostics = []
        if base_asset is None:
            diagnostics.append(
                {
                    "severity": "warning",
                    "code": "image-extract-failed",
                    "message": "Image occurrence could not be extracted as a standalone asset.",
                    "bbox": [bbox.x0, bbox.y0, bbox.x1, bbox.y1],
                }
            )
        if smask_xref and mask_asset is None:
            diagnostics.append(
                {
                    "severity": "warning",
                    "code": "image-mask-extract-failed",
                    "message": f"smask xref {smask_xref} could not be extracted.",
                }
            )
        blend_mode = None
        if inline_grayscale_blend:
            blend_mode = "multiply"
        # An outer-ring sibling drawn over a composed inner pair gets a
        # multiply blend so its color tints the inner without covering it
        # — recovers the subtle border on Page 6 cards.
        if pool_blend_hint:
            blend_mode = pool_blend_hint
            effect_pipeline.append(f"inline-outer-ring-{pool_blend_hint}")
        if _bbox_outside_page(bbox, page_bbox):
            diagnostics.append(
                {
                    "severity": "info",
                    "code": "image-bbox-outside-page",
                    "source_bbox": [bbox.x0, bbox.y0, bbox.x1, bbox.y1],
                    "page_bbox": list(page_bbox),
                    "message": "Image bbox extends beyond page; relying on section overflow:hidden to clip.",
                }
            )
            if base_asset and _looks_like_vignette_overlay(
                base_asset, bbox, context.output_dir
            ):
                blend_mode = "multiply"
                effect_pipeline.append("vignette-multiply-fallback")
                diagnostics.append(
                    {
                        "severity": "info",
                        "code": "image-vignette-fallback",
                        "message": (
                            "Low-resolution image bleeds past page; PyMuPDF did not surface "
                            "a blend mode. Applying mix-blend-mode:multiply so the underlying "
                            "content remains visible."
                        ),
                    }
                )

        render_mode = "asset-with-vector-effects" if base_asset else "diagnostic-placeholder"
        if (
            xref
            and smask_xref
            and base_asset
            and smask_max_alpha(doc, smask_xref) is not None
            and smask_max_alpha(doc, smask_xref) <= 64
        ):
            diagnostics.append(
                {
                    "severity": "info",
                    "code": "image-subtle-overlay-retained",
                    "smask_max_alpha": smask_max_alpha(doc, smask_xref),
                    "message": (
                        "Image smask has uniformly low alpha; retained because "
                        "these layers often carry soft UI shadows, highlights, "
                        "and glass overlays."
                    ),
                }
            )
        elements.append(
            ImageElement(
                stable_id=stable,
                type="image",
                page_index=page_index + 1,
                bbox=bbox,
                z_index=0,
                source_index=source_index,
                render_mode=render_mode,
                editable=False,
                source={
                    "source": "page.get_image_info",
                    "xref": xref,
                    "smask": smask_xref,
                    "transform": transform,
                    "cs_name": info.get("cs-name"),
                    "bpc": info.get("bpc"),
                    "kind": kind_hint,
                    "source_bbox": [bbox.x0, bbox.y0, bbox.x1, bbox.y1],
                },
                diagnostics=diagnostics,
                xref=xref,
                asset_path=base_asset.path if base_asset else None,
                mask_xref=smask_xref,
                mask_asset_path=mask_asset.path if mask_asset else None,
                derived_asset_path=derived_asset.path if derived_asset else None,
                effect_pipeline=effect_pipeline,
                transform=transform,
                smask_kind=smask_kind,
                has_alpha=has_alpha,
                blend_mode=blend_mode,
                paint_seqno=image_number,
            )
        )
    _replace_precomposed_overlays_with_baked_crops(
        page, page_index, context, cache, elements, assets, page_bbox
    )
    _suppress_lowres_masked_precomposed_overlays(elements, context, page_bbox)
    return elements, assets


def _image_seqnos_from_bboxlog(page: fitz.Page) -> list[tuple[int, BBox]]:
    try:
        entries = page.get_bboxlog()
    except Exception:
        return []
    seqnos: list[tuple[int, BBox]] = []
    for seqno, entry in enumerate(entries):
        if not entry or entry[0] != "fill-image":
            continue
        try:
            seqnos.append((seqno, BBox.from_seq(entry[1])))
        except Exception:
            continue
    return seqnos


def _match_bboxlog_image_seqno(
    bbox: BBox, seqnos: list[tuple[int, BBox]]
) -> int | None:
    best_index = None
    best_delta = None
    for index, (_seqno, log_bbox) in enumerate(seqnos):
        delta = max(
            abs(bbox.x0 - log_bbox.x0),
            abs(bbox.y0 - log_bbox.y0),
            abs(bbox.x1 - log_bbox.x1),
            abs(bbox.y1 - log_bbox.y1),
        )
        if delta > 1.0:
            continue
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best_index = index
    if best_index is None:
        return None
    seqno, _bbox = seqnos.pop(best_index)
    return seqno


def _replace_precomposed_overlays_with_baked_crops(
    page: fitz.Page,
    page_index: int,
    context: DocumentContext,
    cache: AssetCache,
    elements: list[ImageElement],
    assets: list,
    page_bbox: tuple[float, float, float, float],
) -> None:
    smask_images = [
        image
        for image in elements
        if image.mask_asset_path or image.derived_asset_path
    ]
    if not smask_images:
        return

    page_box = BBox.from_seq(page_bbox)
    for image in list(elements):
        if not _is_precomposed_page_overlay(image, page_box):
            continue
        cluster = [
            candidate
            for candidate in smask_images
            if candidate.paint_seqno < image.paint_seqno
            and _bbox_intersects(candidate.bbox, image.bbox)
        ]
        if len(cluster) < 2:
            continue
        cluster_box = _union_bbox([candidate.bbox for candidate in cluster])
        if cluster_box is None or not _is_small_bake_cluster(cluster_box, page_box):
            continue

        crop_box = _pad_bbox(cluster_box, 2.0, page_box)
        baked_asset = _render_page_crop_asset(page, image.xref, crop_box, context, cache)
        if baked_asset is None:
            continue
        assets.append(baked_asset)

        image.diagnostics.append(
            {
                "severity": "info",
                "code": "precomposed-overlay-replaced-by-baked-crop",
                "crop_bbox": crop_box.as_list(),
                "message": (
                    "Large low-resolution precomposed overlay suppressed; "
                    "a local PyMuPDF crop preserves the complex transparency "
                    "region without covering crisp page imagery."
                ),
            }
        )
        image.render_mode = "diagnostic-placeholder"
        image.asset_path = None
        image.effect_pipeline.append("precomposed-overlay-suppressed")

        elements.append(
            ImageElement(
                stable_id=f"pdf-page-{page_index + 1}-baked-precomposed-{image.xref or 'inline'}",
                type="image",
                page_index=page_index + 1,
                bbox=crop_box,
                z_index=0,
                source_index=image.source_index,
                render_mode="unsupported-baked",
                editable=False,
                source={
                    "source": "page.get_pixmap clip fallback",
                    "kind": "local-baked-crop",
                    "replaces_xref": image.xref,
                    "crop_bbox": crop_box.as_list(),
                },
                diagnostics=[
                    {
                        "severity": "info",
                        "code": "local-baked-crop",
                        "reason": "complex transparency group / precomposed overlay",
                    }
                ],
                asset_path=baked_asset.path,
                effect_pipeline=["page-region-baked-fallback"],
                paint_seqno=image.paint_seqno,
            )
        )


def _render_page_crop_asset(
    page: fitz.Page,
    source_xref: int | None,
    crop_box: BBox,
    context: DocumentContext,
    cache: AssetCache,
):
    try:
        pix = page.get_pixmap(
            matrix=fitz.Matrix(2.0, 2.0),
            clip=fitz.Rect(crop_box.x0, crop_box.y0, crop_box.x1, crop_box.y1),
            alpha=False,
        )
        image_bytes = pix.tobytes("png")
    except Exception:
        return None
    return save_derived_image(
        image_bytes,
        context.image_dir,
        cache,
        source_xref=None,
        mask_xref=None,
        pipeline=["page-crop-baked"],
    )


def _is_precomposed_page_overlay(image: ImageElement, page_box: BBox) -> bool:
    if not image.asset_path:
        return False
    if image.mask_asset_path or image.derived_asset_path or image.clip_path_data:
        return False
    page_area = max(1.0, page_box.width * page_box.height)
    image_area = max(0.0, image.bbox.width * image.bbox.height)
    if image_area / page_area < 0.60:
        return False
    page_center_x = (page_box.x0 + page_box.x1) / 2
    page_center_y = (page_box.y0 + page_box.y1) / 2
    return image.bbox.x0 < page_center_x and image.bbox.y0 < page_center_y


def _is_small_bake_cluster(cluster_box: BBox, page_box: BBox) -> bool:
    page_area = max(1.0, page_box.width * page_box.height)
    cluster_area = max(0.0, cluster_box.width * cluster_box.height)
    return (
        cluster_area / page_area <= 0.16
        and cluster_box.width > 20.0
        and cluster_box.height > 20.0
    )


def _union_bbox(boxes: list[BBox]) -> BBox | None:
    if not boxes:
        return None
    return BBox(
        min(box.x0 for box in boxes),
        min(box.y0 for box in boxes),
        max(box.x1 for box in boxes),
        max(box.y1 for box in boxes),
    )


def _bbox_intersects(a: BBox, b: BBox) -> bool:
    return not (
        a.x1 <= b.x0
        or b.x1 <= a.x0
        or a.y1 <= b.y0
        or b.y1 <= a.y0
    )


def _pad_bbox(bbox: BBox, pad: float, page_box: BBox) -> BBox:
    return BBox(
        max(page_box.x0, bbox.x0 - pad),
        max(page_box.y0, bbox.y0 - pad),
        min(page_box.x1, bbox.x1 + pad),
        min(page_box.y1, bbox.y1 + pad),
    )


def _suppress_lowres_masked_precomposed_overlays(
    elements: list[ImageElement],
    context: DocumentContext,
    page_bbox: tuple[float, float, float, float],
) -> None:
    page_box = BBox.from_seq(page_bbox)
    for image in elements:
        if not _is_large_masked_overlay_candidate(image, page_box):
            continue
        image_density = _image_pixel_density(image, context)
        if image_density is None:
            continue
        sharper_prior = [
            prior
            for prior in elements
            if prior is not image
            and prior.paint_seqno < image.paint_seqno
            and prior.asset_path
            and _bbox_area_ratio(prior.bbox, page_box) >= 0.55
            and _bbox_substantially_overlaps(prior.bbox, image.bbox)
            and (_image_pixel_density(prior, context) or 0.0) >= image_density * 1.5
        ]
        if not sharper_prior:
            continue
        image.diagnostics.append(
            {
                "severity": "info",
                "code": "lowres-masked-precomposed-overlay-suppressed",
                "pixel_density": image_density,
                "message": (
                    "A later masked precomposed raster is much lower resolution "
                    "than an earlier overlapping image. Suppressed to avoid "
                    "covering the sharper source with a blurred fallback layer."
                ),
            }
        )
        image.render_mode = "diagnostic-placeholder"
        image.asset_path = None
        image.effect_pipeline.append("lowres-precomposed-overlay-suppressed")


def _is_large_masked_overlay_candidate(image: ImageElement, page_box: BBox) -> bool:
    if not image.asset_path:
        return False
    if not image.mask_asset_path and not image.derived_asset_path:
        return False
    area = max(0.0, image.bbox.width * image.bbox.height)
    page_area = max(1.0, page_box.width * page_box.height)
    if area / page_area < 0.25:
        return False
    # Must be a broad slide-level overlay, not a small decorative control.
    return image.bbox.width >= page_box.width * 0.55 or image.bbox.height >= page_box.height * 0.55


def _image_pixel_density(image: ImageElement, context: DocumentContext) -> float | None:
    path = image.derived_asset_path or image.asset_path
    if not path:
        return None
    try:
        from PIL import Image
    except Exception:
        return None
    asset_path = context.output_dir / path
    if not asset_path.exists():
        return None
    try:
        with Image.open(asset_path) as img:
            pixel_w, pixel_h = img.size
    except Exception:
        return None
    target_w = max(1.0, image.bbox.width)
    target_h = max(1.0, image.bbox.height)
    return min(pixel_w / target_w, pixel_h / target_h)


def _bbox_substantially_overlaps(a: BBox, b: BBox) -> bool:
    overlap_x = max(0.0, min(a.x1, b.x1) - max(a.x0, b.x0))
    overlap_y = max(0.0, min(a.y1, b.y1) - max(a.y0, b.y0))
    overlap = overlap_x * overlap_y
    if overlap <= 0:
        return False
    area_a = max(1.0, a.width * a.height)
    area_b = max(1.0, b.width * b.height)
    return overlap / min(area_a, area_b) >= 0.35


def _bbox_area_ratio(bbox: BBox, page_box: BBox) -> float:
    page_area = max(1.0, page_box.width * page_box.height)
    return max(0.0, bbox.width * bbox.height) / page_area


def _asset_is_grayscale(asset, output_dir) -> bool:
    try:
        from pathlib import Path

        from PIL import Image
    except Exception:
        return False
    if not asset or not asset.path:
        return False
    asset_path = Path(output_dir) / asset.path
    if not asset_path.exists():
        return False
    try:
        img = Image.open(asset_path).convert("RGBA")
    except Exception:
        return False
    return _is_grayscale_only(img)


def _looks_like_vignette_overlay(asset, bbox: BBox, output_dir) -> bool:
    """Heuristic: a low-resolution near-grayscale image painted larger than the
    page is almost always a darkening overlay drawn with a multiply / darken
    blend mode that PyMuPDF's image API does not surface."""
    try:
        from pathlib import Path

        from PIL import Image
    except Exception:
        return False
    asset_path = Path(output_dir) / asset.path
    if not asset_path.exists():
        return False
    try:
        img = Image.open(asset_path).convert("RGBA")
    except Exception:
        return False
    w, h = img.size
    target_w = max(1.0, bbox.x1 - bbox.x0)
    target_h = max(1.0, bbox.y1 - bbox.y0)
    upscale = max(target_w / max(w, 1), target_h / max(h, 1))
    if upscale < 8.0:
        return False
    return _is_grayscale_only(img)


def _bbox_outside_page(
    bbox: BBox, page_bbox: tuple[float, float, float, float]
) -> bool:
    """Return True when the bbox bleeds beyond the page rectangle.

    We do NOT shrink the bbox — that would squash the image into a smaller box
    and distort designs that rely on overflow (decorative halos, bleeds). The
    page section already has ``overflow:hidden`` and clips painted pixels.
    """
    px0, py0, px1, py1 = page_bbox
    eps = 0.05
    return (
        bbox.x0 < px0 - eps
        or bbox.y0 < py0 - eps
        or bbox.x1 > px1 + eps
        or bbox.y1 > py1 + eps
    )
