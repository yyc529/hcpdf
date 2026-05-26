from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Diagnostic:
    severity: str
    code: str
    message: str
    source: dict
