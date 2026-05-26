from __future__ import annotations

import re

from model.elements import ClipPathDefinition, VectorElement
from model.geometry import BBox


def optimize_clip_paths(
    vectors: list[VectorElement],
    clips: list[ClipPathDefinition],
    page_bbox: BBox,
    tolerance: float = 0.5,
) -> tuple[list[ClipPathDefinition], dict]:
    """Remove clipPath definitions that do not affect visual output.

    PyMuPDF's extended drawing mode exposes the PDF clip stack very faithfully.
    That is useful for diagnostics, but exported PPT/PDF files often contain
    many full-page or object-bbox rectangle clips. Emitting all of them makes
    the SVG noisy without improving fidelity.
    """
    if not clips:
        return [], _stats(0, 0, 0, 0, 0)

    by_id = {clip.stable_id: clip for clip in clips}
    used_ids = {vector.clip_path_id for vector in vectors if vector.clip_path_id}
    unused_count = len([clip for clip in clips if clip.stable_id not in used_ids])

    alias: dict[str, str | None] = {}
    full_page_count = 0
    no_effect_count = 0

    for vector in vectors:
        clip_id = vector.clip_path_id
        if not clip_id:
            continue
        clip = by_id.get(clip_id)
        if clip is None:
            vector.clip_path_id = None
            continue
        if _bbox_close(clip.bbox, page_bbox, tolerance) and _is_rect_path(clip.path_data):
            vector.clip_path_id = None
            alias[clip_id] = None
            full_page_count += 1
            continue
        if _is_rect_path(clip.path_data) and _bbox_contains(clip.bbox, vector.bbox, tolerance):
            vector.clip_path_id = None
            alias[clip_id] = None
            no_effect_count += 1

    dedup_count = 0
    seen: dict[tuple, str] = {}
    kept: list[ClipPathDefinition] = []
    kept_ids: set[str] = set()
    for clip in clips:
        if clip.stable_id not in used_ids:
            continue
        if alias.get(clip.stable_id, clip.stable_id) is None:
            continue
        key = _clip_key(clip, tolerance)
        existing_id = seen.get(key)
        if existing_id:
            alias[clip.stable_id] = existing_id
            dedup_count += 1
            continue
        seen[key] = clip.stable_id
        kept.append(clip)
        kept_ids.add(clip.stable_id)

    for vector in vectors:
        if vector.clip_path_id in alias:
            vector.clip_path_id = alias[vector.clip_path_id]
        if vector.clip_path_id and vector.clip_path_id not in kept_ids:
            vector.clip_path_id = None

    return kept, _stats(
        original=len(clips),
        kept=len(kept),
        unused=unused_count,
        full_page=full_page_count,
        no_effect=no_effect_count,
        dedup=dedup_count,
    )


_COMMAND_RE = re.compile(r"[A-Za-z]")


def _stats(
    original: int,
    kept: int,
    unused: int,
    full_page: int,
    no_effect: int,
    dedup: int = 0,
) -> dict:
    return {
        "original_clip_paths": original,
        "kept_clip_paths": kept,
        "removed_unused_clip_paths": unused,
        "removed_full_page_clip_paths": full_page,
        "removed_no_effect_rect_clip_paths": no_effect,
        "deduplicated_clip_paths": dedup,
    }


def _is_rect_path(path_data: str) -> bool:
    if not path_data:
        return False
    commands = _COMMAND_RE.findall(path_data.upper())
    return commands in (["M", "L", "L", "L"], ["M", "L", "L", "L", "Z"])


def _bbox_close(a: BBox, b: BBox, tolerance: float) -> bool:
    return (
        abs(a.x0 - b.x0) <= tolerance
        and abs(a.y0 - b.y0) <= tolerance
        and abs(a.x1 - b.x1) <= tolerance
        and abs(a.y1 - b.y1) <= tolerance
    )


def _bbox_contains(outer: BBox, inner: BBox, tolerance: float) -> bool:
    return (
        outer.x0 <= inner.x0 + tolerance
        and outer.y0 <= inner.y0 + tolerance
        and outer.x1 >= inner.x1 - tolerance
        and outer.y1 >= inner.y1 - tolerance
    )


def _clip_key(clip: ClipPathDefinition, tolerance: float) -> tuple:
    return (
        _rounded(clip.bbox.x0, tolerance),
        _rounded(clip.bbox.y0, tolerance),
        _rounded(clip.bbox.x1, tolerance),
        _rounded(clip.bbox.y1, tolerance),
        clip.even_odd,
        " ".join(clip.path_data.split()),
    )


def _rounded(value: float, tolerance: float) -> int:
    tolerance = max(tolerance, 0.001)
    return round(value / tolerance)
