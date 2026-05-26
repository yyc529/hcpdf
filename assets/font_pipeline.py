from __future__ import annotations

import hashlib
from pathlib import Path

import fitz

from assets.naming import font_asset_name
from model.assets import FontAsset
from resolver.font_resolver import (
    SUBSET_PREFIX_RE,
    clean_font_name,
    css_family_name,
    is_cjk_font_name,
)


# Extensions browsers can load directly.
LOADABLE_EXTENSIONS = {"ttf", "otf", "woff", "woff2"}


def extract_document_fonts(
    doc: fitz.Document, output_dir: Path, diagnostics: list[dict] | None = None
) -> list[FontAsset]:
    """Extract embedded fonts; record diagnostics for unsupported types.

    Returns one FontAsset per xref. Unloadable fonts (Type3, CFF without otf
    wrapper, etc.) also get a FontAsset entry — with ``loadable=False`` and a
    ``fallback_reason`` — so the renderer can still pick a real-name fallback.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    diagnostics = diagnostics if diagnostics is not None else []
    assets: dict[int, FontAsset] = {}
    for page_index in range(doc.page_count):
        try:
            fonts = doc.get_page_fonts(page_index, full=True)
        except Exception as exc:
            diagnostics.append(
                {
                    "severity": "warning",
                    "code": "font-list-failed",
                    "page": page_index + 1,
                    "message": str(exc),
                }
            )
            continue
        for font in fonts:
            xref = int(font[0] or 0)
            if xref <= 0 or xref in assets:
                continue
            ext_hint = (font[1] or "").lower()
            font_type = font[2] or ""
            raw_name = str(font[3] or font[4] or f"font-{xref}")
            assets[xref] = _extract_one_font(
                doc, xref, ext_hint, font_type, raw_name, output_dir, diagnostics
            )
    return list(assets.values())


def _extract_one_font(
    doc: fitz.Document,
    xref: int,
    ext_hint: str,
    font_type: str,
    raw_name: str,
    output_dir: Path,
    diagnostics: list[dict],
) -> FontAsset:
    subset_match = SUBSET_PREFIX_RE.match(raw_name)
    subset_prefix = subset_match.group(0)[:-1] if subset_match else ""
    clean_name = clean_font_name(raw_name)
    css_family = css_family_name(raw_name)
    cjk = is_cjk_font_name(clean_name)

    try:
        extracted = doc.extract_font(xref)
    except Exception as exc:
        diagnostics.append(
            {
                "severity": "warning",
                "code": "font-extract-failed",
                "font": raw_name,
                "xref": xref,
                "font_type": font_type,
                "message": str(exc),
            }
        )
        return _make_placeholder(xref, raw_name, clean_name, css_family, font_type,
                                 subset_prefix, cjk, "extract-failed")

    if not extracted or len(extracted) < 4:
        diagnostics.append(
            {
                "severity": "info",
                "code": "font-no-content",
                "font": raw_name,
                "xref": xref,
                "font_type": font_type,
                "message": "PyMuPDF returned no font payload.",
            }
        )
        return _make_placeholder(xref, raw_name, clean_name, css_family, font_type,
                                 subset_prefix, cjk, "no-payload")

    name, ext, declared_type, content = extracted[:4]
    declared_type = declared_type or font_type or ""
    ext = (ext or ext_hint or "").lower()
    if not content:
        diagnostics.append(
            {
                "severity": "info",
                "code": "font-empty",
                "font": raw_name,
                "xref": xref,
                "font_type": declared_type,
                "message": "Font payload was empty.",
            }
        )
        return _make_placeholder(xref, raw_name, clean_name, css_family, declared_type,
                                 subset_prefix, cjk, "empty-payload")

    if ext not in LOADABLE_EXTENSIONS:
        diagnostics.append(
            {
                "severity": "info",
                "code": "font-unsupported-format",
                "font": raw_name,
                "xref": xref,
                "font_type": declared_type,
                "ext": ext,
                "message": (
                    f"Font extension '{ext}' (type {declared_type}) is not directly "
                    "loadable by browsers; falling back to system stack."
                ),
            }
        )
        return _make_placeholder(
            xref, raw_name, clean_name, css_family, declared_type,
            subset_prefix, cjk, f"unloadable-format:{ext}"
        )

    digest = hashlib.sha1(content).hexdigest()
    filename = font_asset_name(clean_name, ext, digest)
    path = output_dir / filename
    if not path.exists():
        try:
            path.write_bytes(content)
        except Exception as exc:
            diagnostics.append(
                {
                    "severity": "warning",
                    "code": "font-write-failed",
                    "font": raw_name,
                    "xref": xref,
                    "message": str(exc),
                }
            )
            return _make_placeholder(
                xref, raw_name, clean_name, css_family, declared_type,
                subset_prefix, cjk, "write-failed"
            )

    return FontAsset(
        stable_id=f"asset-font-{xref}",
        path=f"assets/fonts/{filename}",
        source={
            "xref": xref,
            "kind": "embedded-font",
            "font_type": declared_type,
            "raw_name": raw_name,
            "subset_prefix": subset_prefix,
        },
        font_name=clean_name,
        css_family=css_family,
        ext=ext,
        digest=digest,
        font_type=declared_type,
        subset_prefix=subset_prefix,
        raw_name=raw_name,
        is_cjk=cjk,
        loadable=True,
        fallback_reason=None,
    )


def _make_placeholder(
    xref: int,
    raw_name: str,
    clean_name: str,
    css_family: str,
    font_type: str,
    subset_prefix: str,
    is_cjk: bool,
    reason: str,
) -> FontAsset:
    return FontAsset(
        stable_id=f"asset-font-{xref}",
        path="",
        source={
            "xref": xref,
            "kind": "embedded-font-fallback",
            "font_type": font_type,
            "raw_name": raw_name,
            "subset_prefix": subset_prefix,
            "fallback_reason": reason,
        },
        font_name=clean_name,
        css_family=css_family,
        ext=None,
        digest=None,
        font_type=font_type,
        subset_prefix=subset_prefix,
        raw_name=raw_name,
        is_cjk=is_cjk,
        loadable=False,
        fallback_reason=reason,
    )
