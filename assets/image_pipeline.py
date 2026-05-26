from __future__ import annotations

import hashlib
from pathlib import Path

import fitz

from assets.cache import AssetCache
from assets.naming import image_asset_name
from model.assets import ImageAsset


def save_image_xref(
    doc: fitz.Document,
    xref: int | None,
    occurrence: int,
    output_dir: Path,
    cache: AssetCache,
    kind: str = "pdf-image",
    name_prefix: str | None = None,
) -> ImageAsset | None:
    if not xref:
        return None
    try:
        extracted = doc.extract_image(xref)
    except Exception:
        return None
    image_bytes = extracted.get("image")
    if not image_bytes:
        return None
    digest = hashlib.sha1(image_bytes).hexdigest()
    cached = cache.get_image(xref, digest)
    if cached:
        return cached
    ext = extracted.get("ext") or "png"
    filename = image_asset_name(xref, occurrence, ext, digest)
    if name_prefix:
        filename = f"{name_prefix}_{filename}"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / filename
    path.write_bytes(image_bytes)
    asset = ImageAsset(
        stable_id=f"asset-image-{xref}",
        path=f"assets/images/{filename}",
        source={"xref": xref, "kind": kind},
        xref=xref,
        ext=ext,
        digest=digest,
    )
    cache.remember_image(asset)
    return asset


def save_inline_image(
    image_bytes: bytes,
    ext: str | None,
    occurrence_index: int,
    output_dir: Path,
    cache: AssetCache,
) -> ImageAsset | None:
    """Save a raw inline image (xref=0) decoded from page.get_text('rawdict').

    Inline images have no xref, so PyMuPDF cannot extract them via the usual
    extract_image path. We treat their content hash as the identity key.
    """
    if not image_bytes:
        return None
    digest = hashlib.sha1(image_bytes).hexdigest()
    cached = cache.get_image(None, digest)
    if cached:
        return cached
    ext = (ext or "png").lower()
    filename = f"inline_{occurrence_index}_{digest[:12]}.{ext}"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / filename
    path.write_bytes(image_bytes)
    asset = ImageAsset(
        stable_id=f"asset-image-inline-{digest[:12]}",
        path=f"assets/images/{filename}",
        source={"kind": "pdf-inline-image", "digest": digest},
        xref=None,
        ext=ext,
        digest=digest,
    )
    cache.remember_image(asset)
    return asset


def save_derived_image(
    image_bytes: bytes,
    output_dir: Path,
    cache: AssetCache,
    source_xref: int | None,
    mask_xref: int | None,
    pipeline: list[str],
    ext: str = "png",
) -> ImageAsset | None:
    if not image_bytes:
        return None
    digest = hashlib.sha1(image_bytes).hexdigest()
    cached = cache.get_image(None, digest)
    if cached:
        return cached
    pipeline_tag = "-".join(pipeline) or "derived"
    filename = f"derived_{source_xref or 'inline'}_{pipeline_tag}_{digest[:12]}.{ext}"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / filename
    path.write_bytes(image_bytes)
    asset = ImageAsset(
        stable_id=f"asset-image-derived-{digest[:12]}",
        path=f"assets/images/{filename}",
        source={
            "xref": source_xref,
            "mask_xref": mask_xref,
            "kind": "derived",
            "pipeline": pipeline,
        },
        xref=source_xref,
        ext=ext,
        digest=digest,
    )
    cache.remember_image(asset)
    return asset


def _read_matte(doc: fitz.Document, smask_xref: int) -> tuple[float, float, float] | None:
    """Read the ``/Matte`` entry from a soft mask's PDF object, if present.

    Per PDF 1.7 §11.6.5.3, when an image's SMask carries ``/Matte``, the image
    samples are **premultiplied** with that matte color. To recover the
    actual painted color we must un-premultiply:

        true = matte + (stored - matte) / alpha

    Most PPT exports use ``/Matte [0 0 0]`` (black matte) — see Page 15 of
    the magazine PDF where the page-background image stores opaque gray
    samples that should appear white once un-premultiplied.
    """
    try:
        raw = doc.xref_get_key(smask_xref, "Matte")
    except Exception:
        return None
    if not raw or not isinstance(raw, (tuple, list)) or len(raw) < 2:
        return None
    kind, value = raw[0], raw[1]
    if kind not in ("array", "Matte"):
        # ``xref_get_key`` may also return ('array', '[ 0 0 0 ]').
        return None
    text = str(value).strip().strip("[]").strip()
    try:
        nums = [float(tok) for tok in text.split() if tok]
    except ValueError:
        return None
    if not nums:
        return None
    if len(nums) == 1:
        v = nums[0]
        return (v, v, v)
    if len(nums) == 3:
        return (nums[0], nums[1], nums[2])
    if len(nums) == 4:
        # DeviceCMYK matte — approximate to RGB.
        c, m, y, k = nums
        r = (1 - c) * (1 - k)
        g = (1 - m) * (1 - k)
        b = (1 - y) * (1 - k)
        return (r, g, b)
    return None


def compose_image_with_smask(
    doc: fitz.Document,
    image_xref: int,
    smask_xref: int,
    output_dir: Path,
    cache: AssetCache,
) -> ImageAsset | None:
    """Compose a PDF image and its soft mask into a single RGBA PNG.

    Handles three PDF nuances PyMuPDF doesn't:

    1. Mismatched mask / image dimensions — rescale the mask to the image.
    2. ``/Matte`` premultiplication — un-premultiply samples before saving so
       a "black-matte gray-bg" image renders as expected (transparent where
       the alpha is low, true color where it's high).
    3. Mask alpha values are written into the PNG alpha channel directly.
    """
    try:
        base_pix = fitz.Pixmap(doc, image_xref)
        smask_pix = fitz.Pixmap(doc, smask_xref)
    except Exception:
        return None

    matte = _read_matte(doc, smask_xref)

    try:
        if smask_pix.width != base_pix.width or smask_pix.height != base_pix.height:
            try:
                smask_pix = fitz.Pixmap(
                    smask_pix, base_pix.width / max(smask_pix.width, 1)
                )
            except Exception:
                pass

        if matte is not None:
            composed_bytes = _compose_with_matte(base_pix, smask_pix, matte)
            if composed_bytes:
                return save_derived_image(
                    composed_bytes,
                    output_dir,
                    cache,
                    source_xref=image_xref,
                    mask_xref=smask_xref,
                    pipeline=["smask-compose", "unpremultiply-matte"],
                )

        try:
            composed = fitz.Pixmap(base_pix, smask_pix)
        except Exception:
            return None
        image_bytes = composed.tobytes("png")
    finally:
        try:
            base_pix = None  # type: ignore
            smask_pix = None  # type: ignore
        except Exception:
            pass
    return save_derived_image(
        image_bytes,
        output_dir,
        cache,
        source_xref=image_xref,
        mask_xref=smask_xref,
        pipeline=["smask-compose"],
    )


def _compose_with_matte(
    base_pix, smask_pix, matte: tuple[float, float, float]
) -> bytes | None:
    """Un-premultiply ``base_pix`` against ``matte`` weighted by ``smask_pix``.

    Implements the PDF 1.7 §11.6.5.3 matte recovery:

        true_color = matte + (stored_color - matte) / mask_alpha

    Vectorised with numpy for speed; a 960×540 image takes ~10 ms instead of
    multiple seconds with a Python loop.

    Returns a PNG byte string with the un-premultiplied RGB and the mask
    moved into the alpha channel. Returns ``None`` when numpy/PIL are
    unavailable or pixmaps cannot be decoded.
    """
    try:
        import io

        import numpy as np
        from PIL import Image
    except Exception:
        return None
    try:
        base_bytes = base_pix.tobytes("png")
        smask_bytes = smask_pix.tobytes("png")
    except Exception:
        return None
    try:
        base_img = Image.open(io.BytesIO(base_bytes)).convert("RGB")
        mask_img = Image.open(io.BytesIO(smask_bytes)).convert("L")
    except Exception:
        return None
    if base_img.size != mask_img.size:
        return None

    matte_rgb = np.array(
        [int(round(max(0.0, min(1.0, c)) * 255)) for c in matte], dtype=np.float32
    )
    base_arr = np.asarray(base_img, dtype=np.float32)  # (h, w, 3)
    mask_arr = np.asarray(mask_img, dtype=np.float32)  # (h, w)

    # alpha in [0, 1]; clamp to avoid divide-by-zero. Where alpha is 0, the
    # true color is undefined — we keep the matte color and the resulting
    # alpha is 0 so the pixel is fully transparent.
    alpha = (mask_arr / 255.0).clip(min=1e-3)
    diff = base_arr - matte_rgb[None, None, :]
    true_rgb = matte_rgb[None, None, :] + diff / alpha[..., None]
    true_rgb = np.clip(true_rgb, 0, 255).astype(np.uint8)

    rgba = np.empty((base_arr.shape[0], base_arr.shape[1], 4), dtype=np.uint8)
    rgba[..., :3] = true_rgb
    rgba[..., 3] = mask_arr.astype(np.uint8)

    out = Image.fromarray(rgba, mode="RGBA")
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def smask_max_alpha(doc: fitz.Document, smask_xref: int | None) -> int | None:
    """Return the maximum alpha value (0-255) of a soft mask, or None on error.

    A soft mask with uniformly low max alpha is almost always a *subtle
    overlay* layer the PDF designer put on top of the main element to add a
    barely-visible tint or halo. PyMuPDF's pixmap renderer composes these
    into the final raster correctly (often invisibly), but our per-image
    HTML stacking shows them as a visible offset shape. We use this value
    to decide whether such an overlay should be suppressed at render time.
    """
    if not smask_xref:
        return None
    try:
        pix = fitz.Pixmap(doc, smask_xref)
    except Exception:
        return None
    try:
        img_bytes = pix.tobytes("png")
    except Exception:
        return None
    try:
        import io

        from PIL import Image

        img = Image.open(io.BytesIO(img_bytes)).convert("L")
    except Exception:
        return None
    # Sample 5x5 grid + center + center quarter
    w, h = img.size
    if w < 2 or h < 2:
        return None
    max_a = 0
    for y_frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        for x_frac in (0.0, 0.25, 0.5, 0.75, 1.0):
            x = max(0, min(w - 1, int(x_frac * w)))
            y = max(0, min(h - 1, int(y_frac * h)))
            try:
                v = img.getpixel((x, y))
            except Exception:
                continue
            if isinstance(v, tuple):
                v = v[0]
            if v > max_a:
                max_a = v
    return max_a


def detect_image_alpha(doc: fitz.Document, xref: int | None) -> tuple[bool, str | None]:
    """Return (has_alpha, smask_kind) for an image xref."""
    if not xref:
        return False, None
    try:
        pix = fitz.Pixmap(doc, xref)
    except Exception:
        return False, None
    has_alpha = bool(getattr(pix, "alpha", 0))
    kind = None
    try:
        n = pix.n
        if has_alpha and pix.colorspace is None:
            kind = "stencil-mask"
        elif has_alpha:
            kind = "alpha-channel"
        elif n == 1:
            kind = "gray"
    except Exception:
        kind = None
    return has_alpha, kind
