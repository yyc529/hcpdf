from __future__ import annotations

import re
from pathlib import Path


def safe_stem(path: Path) -> str:
    stem = path.stem.strip() or "document"
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", stem)
    return stem.rstrip(" .") or "document"


def default_output_dir(pdf_path: Path) -> Path:
    return pdf_path.parent / f"output_{safe_stem(pdf_path)}"


def image_asset_name(xref: int | None, occurrence: int, ext: str, digest: str) -> str:
    prefix = f"img{xref}" if xref else f"inline{occurrence}"
    return f"{prefix}_{digest[:12]}.{ext.lower()}"


def font_asset_name(font_name: str, ext: str, digest: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", font_name).strip("_") or "font"
    return f"{safe}_{digest[:12]}.{ext.lower()}"
