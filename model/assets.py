from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Asset:
    stable_id: str
    path: str
    source: dict


@dataclass(slots=True)
class ImageAsset(Asset):
    xref: int | None = None
    ext: str | None = None
    digest: str | None = None


@dataclass(slots=True)
class FontAsset(Asset):
    font_name: str = ""
    css_family: str = ""
    ext: str | None = None
    digest: str | None = None
    font_type: str = ""
    subset_prefix: str = ""
    raw_name: str = ""
    is_cjk: bool = False
    loadable: bool = True
    fallback_reason: str | None = None
