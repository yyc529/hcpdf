from __future__ import annotations

from dataclasses import dataclass, field

from model.assets import ImageAsset


@dataclass(slots=True)
class AssetCache:
    images_by_xref: dict[int, ImageAsset] = field(default_factory=dict)
    images_by_digest: dict[str, ImageAsset] = field(default_factory=dict)

    def get_image(self, xref: int | None, digest: str | None) -> ImageAsset | None:
        if xref and xref in self.images_by_xref:
            return self.images_by_xref[xref]
        if digest and digest in self.images_by_digest:
            return self.images_by_digest[digest]
        return None

    def remember_image(self, asset: ImageAsset) -> None:
        if asset.xref:
            self.images_by_xref[asset.xref] = asset
        if asset.digest:
            self.images_by_digest[asset.digest] = asset
