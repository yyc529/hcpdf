from __future__ import annotations

from model.elements import ClipPathDefinition, ImageElement, VectorElement
from model.geometry import BBox


# When the candidate clip's bbox is *strictly* contained inside the image
# bbox, the image is the bounding rect of a gradient fill, and the clip is the
# letterform / stencil cut-out inside it. The clip's bbox can therefore be up
# to this many points smaller on each side and still be the right clip.
_CONTAINMENT_SLACK_PT = 20.0

# When the candidate clip's bbox is *aligned* with the image bbox (same draw
# rect), we want a tight match. PDF tooling rarely diverges more than a couple
# of hundredths of a point on the alignment, so 1.5pt is a generous bound.
_ALIGN_TOLERANCE_PT = 1.5


def attach_image_clips(
    images: list[ImageElement],
    clip_definitions: list[ClipPathDefinition],
    vectors: list[VectorElement] | None = None,
    page_bbox: BBox | None = None,
) -> None:
    """Spatially associate non-trivial clip paths with images on the same page.

    PyMuPDF's ``get_image_info`` doesn't report which clip was active when an
    image was painted, but PPT-exported PDFs use a very stable pattern:

    1. Push a clip whose path is the **letterform / stencil cutout**
       (e.g. the "Contents" word shaped as a clip on page 5, the "01" digits
       on page 2, the rounded pill shape on page 2 button).
    2. Draw an inline image (often a gradient) whose bbox is the bounding
       rect of the cutout — sometimes padded by a few points.

    To map step 2 back to step 1 we look for clips whose bbox is either
    aligned with (≤ ``_ALIGN_TOLERANCE_PT``) or *contained inside* (within
    ``_CONTAINMENT_SLACK_PT``) the image bbox. When several clips match the
    same image we prefer the **simplest** path — solid letter cutouts have
    far fewer items than the stroked-outline traces some authoring tools
    additionally emit. Tie-breaker is deeper clip level (most recently
    pushed clip is the immediate parent of the draw).
    """
    if not images:
        return

    non_trivial_clips = [
        clip for clip in (clip_definitions or [])
        if _clip_is_non_trivial(clip)
    ]

    for image in images:
        # When an image carries its own ``/SMask``, the soft mask already
        # defines the visible (circular / shaped) area — attaching an extra
        # ``<clipPath>`` on top duplicates the masking and often picks up a
        # *different* nearby clip from the page's clip stack, producing a
        # misaligned crop on Page 7's button circle and similar elements.
        # The smask alone is the single source of truth for what's visible.
        if image.mask_asset_path or image.derived_asset_path:
            continue
        clip = _best_clip_for_image(image, non_trivial_clips) if non_trivial_clips else None
        if clip is None:
            continue
        image.clip_path_data = clip.path_data
        image.source["clip"] = {
            "clip_id": clip.stable_id,
            "even_odd": clip.even_odd,
            "level": clip.level,
            "item_count": _path_command_count(clip.path_data),
        }
        image.effect_pipeline.append("image-clip")
        if image.blend_mode == "multiply" and "inline-grayscale-multiply" in image.effect_pipeline:
            image.blend_mode = None
            image.effect_pipeline = [
                tag for tag in image.effect_pipeline
                if tag != "inline-grayscale-multiply"
            ]
            image.effect_pipeline.append("clip-overrides-grayscale-multiply")

    if page_bbox is not None:
        _slice_precomposed_overlays_to_smask_clusters(images, page_bbox)

    # Second pass: images that bleed past the page rectangle without an
    # explicit clip are almost always supposed to fill a set of placeholder
    # shapes (e.g. Page 11 hexagon honeycomb where one large photo is
    # masked into 5 hexagons). PyMuPDF doesn't surface that compound clip,
    # so we synthesise one from the non-trivial vectors that look like
    # photo placeholders — white-filled or no-fill shapes contained in the
    # image bbox.
    if vectors and page_bbox is not None:
        for image in images:
            if image.clip_path_data:
                continue
            # Only attach the synthesised placeholder-union clip when the
            # image bleeds *substantially* past the page (Page 11 hexagon
            # photo bleeds ~200pt). Slight bleed (Page 7's pre-composited
            # right-side image bleeds only ~20pt) is normal for raster
            # exports and clipping it would just remove visible content.
            if not _bbox_bleeds_substantially(image.bbox, page_bbox):
                continue
            union_path = _build_placeholder_union_clip(image.bbox, vectors)
            if union_path:
                image.clip_path_data = union_path
                image.source["clip"] = {
                    "kind": "synthesized-placeholder-union",
                    "source": "white-filled or no-fill vectors inside image bbox",
                }
                image.effect_pipeline.append("image-clip-union-fallback")


def _clip_is_non_trivial(clip: ClipPathDefinition) -> bool:
    """A clip is interesting if its path is not a plain rectangle (which adds
    nothing beyond restricting drawing to the rect itself)."""
    data = clip.path_data
    if not data:
        return False
    if "C " in data or " C " in data:
        return True
    l_segments = data.count(" L ") + (1 if data.startswith("L ") else 0)
    return l_segments != 3


def _best_clip_for_image(
    image: ImageElement, clips: list[ClipPathDefinition]
) -> ClipPathDefinition | None:
    image_bbox = image.bbox
    best: tuple[tuple, ClipPathDefinition] | None = None
    for clip in clips:
        score = _score_clip(image_bbox, clip)
        if score is None:
            continue
        if best is None or score < best[0]:
            best = (score, clip)
    return best[1] if best else None


def _score_clip(
    image_bbox: BBox, clip: ClipPathDefinition
) -> tuple | None:
    """Lower-is-better tuple used to rank clip candidates for one image.

    Returns ``None`` if the clip cannot be sensibly associated with the image.
    """
    align_delta = _l_infinity_delta(image_bbox, clip.bbox)
    contained_delta = _containment_slack(image_bbox, clip.bbox)

    if align_delta is None and contained_delta is None:
        return None
    if (align_delta is None or align_delta > _ALIGN_TOLERANCE_PT) and (
        contained_delta is None or contained_delta > _CONTAINMENT_SLACK_PT
    ):
        return None

    # Prefer (in order):
    #   1. Simpler path (fewer commands == cleaner solid letterform).
    #   2. Aligned bboxes over contained ones (when both candidates exist for
    #      the same image, aligned tends to be the canonical fill clip).
    #   3. Deeper level (more recently pushed clip is the immediate parent).
    item_count = _path_command_count(clip.path_data)
    alignment_score = align_delta if align_delta is not None else float("inf")
    containment_score = contained_delta if contained_delta is not None else float("inf")
    return (
        item_count,
        min(alignment_score, containment_score),
        -clip.level,
    )


def _l_infinity_delta(a: BBox, b: BBox) -> float | None:
    try:
        return max(
            abs(a.x0 - b.x0),
            abs(a.y0 - b.y0),
            abs(a.x1 - b.x1),
            abs(a.y1 - b.y1),
        )
    except Exception:
        return None


def _containment_slack(image: BBox, clip: BBox) -> float | None:
    """Return how much the clip bbox is shrunk *inward* from the image bbox.

    Positive on every side ⇒ the clip is fully contained. We return the
    maximum inset; the caller compares it against
    :data:`_CONTAINMENT_SLACK_PT`. If the clip extends *beyond* the image on
    any side (negative inset), this is not a containment match and we return
    ``None``.
    """
    insets = [
        clip.x0 - image.x0,
        clip.y0 - image.y0,
        image.x1 - clip.x1,
        image.y1 - clip.y1,
    ]
    if any(inset < -0.5 for inset in insets):
        return None
    return max(insets)


def _path_command_count(path_data: str) -> int:
    if not path_data:
        return 0
    return sum(1 for c in path_data if c.isalpha())


def _bbox_extends_past(image_bbox: BBox, page_bbox: BBox, slack: float = 1.0) -> bool:
    return (
        image_bbox.x0 < page_bbox.x0 - slack
        or image_bbox.y0 < page_bbox.y0 - slack
        or image_bbox.x1 > page_bbox.x1 + slack
        or image_bbox.y1 > page_bbox.y1 + slack
    )


# Threshold for the placeholder-union fallback: only apply the synthesised
# clip when the image bleeds *substantially* past the page on at least one
# side. Page 11's hexagon photo bleeds ~200pt past the page; Page 7's
# pre-composited right-side image bleeds only ~20pt and is NOT meant to be
# clipped — it's already the final flattened rendering and any extra
# clipping just removes 99% of the visible pixels.
_UNION_CLIP_MIN_BLEED_PT = 60.0

# Some PPT/PDF exports paint both the editable component images and a later
# low-resolution flattened raster of the same region. Keeping that flattened
# raster full-size fixes a complex effect (Page 7's pink circle), but it also
# covers crisp earlier photos/text with the blurry raster. Limit such overlays
# to the small soft-mask image cluster they are meant to correct.
_PRECOMPOSED_OVERLAY_MIN_PAGE_AREA = 0.60
_PRECOMPOSED_OVERLAY_MAX_CLUSTER_AREA = 0.16
_PRECOMPOSED_OVERLAY_CLUSTER_PAD_PT = 2.0


def _slice_precomposed_overlays_to_smask_clusters(
    images: list[ImageElement], page_bbox: BBox
) -> None:
    smask_clusters = [
        image
        for image in images
        if image.mask_asset_path or image.derived_asset_path
    ]
    if not smask_clusters:
        return

    for image in images:
        if image.clip_path_data or image.mask_asset_path or image.derived_asset_path:
            continue
        if not image.asset_path:
            continue
        if not _is_large_page_overlay(image.bbox, page_bbox):
            continue

        earlier_clusters = [
            candidate
            for candidate in smask_clusters
            if candidate.paint_seqno < image.paint_seqno
            and _bbox_intersects(candidate.bbox, image.bbox)
        ]
        if len(earlier_clusters) < 2:
            continue

        union = _union_bbox([candidate.bbox for candidate in earlier_clusters])
        if union is None or not _is_small_overlay_cluster(union, page_bbox):
            continue

        clipped = _pad_bbox(union, _PRECOMPOSED_OVERLAY_CLUSTER_PAD_PT, page_bbox)
        image.clip_path_data = _rect_path(clipped)
        image.source["clip"] = {
            "kind": "precomposed-overlay-smask-cluster",
            "source": "large late raster sliced to prior soft-mask image cluster",
            "cluster_bbox": clipped.as_list(),
        }
        image.effect_pipeline.append("precomposed-overlay-smask-slice")


def _is_large_page_overlay(image_bbox: BBox, page_bbox: BBox) -> bool:
    page_area = max(1.0, page_bbox.width * page_bbox.height)
    image_area = max(0.0, image_bbox.width * image_bbox.height)
    if image_area / page_area < _PRECOMPOSED_OVERLAY_MIN_PAGE_AREA:
        return False
    page_center_x = (page_bbox.x0 + page_bbox.x1) / 2
    page_center_y = (page_bbox.y0 + page_bbox.y1) / 2
    return image_bbox.x0 < page_center_x and image_bbox.y0 < page_center_y


def _is_small_overlay_cluster(cluster_bbox: BBox, page_bbox: BBox) -> bool:
    page_area = max(1.0, page_bbox.width * page_bbox.height)
    cluster_area = max(0.0, cluster_bbox.width * cluster_bbox.height)
    if cluster_area / page_area > _PRECOMPOSED_OVERLAY_MAX_CLUSTER_AREA:
        return False
    return cluster_bbox.width > 20.0 and cluster_bbox.height > 20.0


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


def _pad_bbox(bbox: BBox, pad: float, page_bbox: BBox) -> BBox:
    return BBox(
        max(page_bbox.x0, bbox.x0 - pad),
        max(page_bbox.y0, bbox.y0 - pad),
        min(page_bbox.x1, bbox.x1 + pad),
        min(page_bbox.y1, bbox.y1 + pad),
    )


def _rect_path(bbox: BBox) -> str:
    return (
        f"M {bbox.x0:.3f} {bbox.y0:.3f} "
        f"L {bbox.x1:.3f} {bbox.y0:.3f} "
        f"L {bbox.x1:.3f} {bbox.y1:.3f} "
        f"L {bbox.x0:.3f} {bbox.y1:.3f} Z"
    )


def _bbox_bleeds_substantially(image_bbox: BBox, page_bbox: BBox) -> bool:
    bleed = max(
        page_bbox.x0 - image_bbox.x0,
        page_bbox.y0 - image_bbox.y0,
        image_bbox.x1 - page_bbox.x1,
        image_bbox.y1 - page_bbox.y1,
    )
    return bleed >= _UNION_CLIP_MIN_BLEED_PT


def _build_placeholder_union_clip(
    image_bbox: BBox, vectors: list[VectorElement]
) -> str | None:
    """Construct a union ``<clipPath>`` ``d`` value from photo-placeholder vectors.

    A vector qualifies as a placeholder when its bbox is fully inside the
    image bbox AND its fill is either white (the PDF's "show photo here"
    placeholder color) or omitted (stroke-only outline). We concatenate the
    matching vectors' path data — SVG's nonzero winding gives the union we
    want as long as each subpath is independently closed (which PyMuPDF
    output always is via the ``Z`` we emit per subpath).
    """
    subpaths: list[str] = []
    for vector in vectors:
        if not _vector_is_photo_placeholder(vector, image_bbox):
            continue
        subpaths.append(vector.path_data)
    if not subpaths:
        return None
    return " ".join(subpaths)


def _vector_is_photo_placeholder(
    vector: VectorElement, image_bbox: BBox
) -> bool:
    """Identify a vector that probably marks where a photo should be clipped.

    A genuine photo-placeholder shape is:

    - **Closed** (its path ends with ``Z``). Open polylines / divider lines
      have no closing ``Z`` and would otherwise pass through the no-fill
      branch and corrupt the union clip into a set of thin segments.
    - **Non-trivial** (not a plain rectangle — a plain rect inside the image
      is more likely to be a background tint than a photo cutout).
    - **Substantial in area** (≥ 100 sq pt). Filters out tiny accent dots.
    - **Contained** inside the image bbox so we don't pull unrelated nearby
      shapes into the clip union.
    - **Filled white or unfilled** — the PDF placeholder convention.
    """
    if not vector.path_data:
        return False
    if not _bbox_contained(vector.bbox, image_bbox):
        return False
    bbox_area = max(0.0, vector.bbox.width) * max(0.0, vector.bbox.height)
    # Filters out thin divider lines (zero-area bbox) and tiny accent dots.
    if bbox_area < 100.0:
        return False
    if _path_is_plain_rect(vector.path_data):
        return False
    # Need at least 3 segments after the initial Move — anything less is
    # too simple to be a deliberate photo-cutout placeholder.
    cmd_count = sum(1 for c in vector.path_data if c.isalpha())
    if cmd_count < 4:
        return False
    style = vector.style
    fill = (style.fill or "").lower()
    if fill in {"", "none"}:
        return True
    if fill in {"rgb(255, 255, 255)", "#ffffff", "white"}:
        return True
    return False


def _path_is_plain_rect(path_data: str) -> bool:
    commands = [c for c in path_data if c.isalpha()]
    return commands == ["M", "L", "L", "L", "Z"]


def _bbox_contained(inner: BBox, outer: BBox, slack: float = 2.0) -> bool:
    return (
        inner.x0 >= outer.x0 - slack
        and inner.y0 >= outer.y0 - slack
        and inner.x1 <= outer.x1 + slack
        and inner.y1 <= outer.y1 + slack
    )
