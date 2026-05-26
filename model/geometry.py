from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(slots=True)
class BBox:
    x0: float
    y0: float
    x1: float
    y1: float

    @classmethod
    def from_seq(cls, values: Iterable[float]) -> "BBox":
        x0, y0, x1, y1 = values
        return cls(float(x0), float(y0), float(x1), float(y1))

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    def as_list(self) -> list[float]:
        return [self.x0, self.y0, self.x1, self.y1]

    def data_value(self) -> str:
        return ",".join(f"{value:.3f}" for value in self.as_list())


@dataclass(slots=True)
class PageBox:
    width: float
    height: float
    media_box: BBox
    crop_box: BBox
