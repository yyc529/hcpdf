from __future__ import annotations

from typing import Sequence


def int_color_to_css(value: int | None) -> str:
    if value is None:
        return "rgb(0, 0, 0)"
    red = (int(value) >> 16) & 255
    green = (int(value) >> 8) & 255
    blue = int(value) & 255
    return f"rgb({red}, {green}, {blue})"


def tuple_color_to_css(
    value: Sequence[float] | None,
    diagnostics: list[dict] | None = None,
    context: str = "",
) -> str | None:
    """Convert a PDF color tuple to a CSS color.

    Accepts gray (1 channel), RGB (3 channels) or CMYK (4 channels). Values are
    normalized into 0..1. Returns ``None`` for unsupported shapes — callers can
    treat that as "no fill"/"no stroke". When ``diagnostics`` is provided,
    records a note when conversion is lossy or skipped.
    """
    if value is None:
        return None
    try:
        channels = [float(v) for v in value]
    except Exception:
        if diagnostics is not None:
            diagnostics.append(
                {
                    "severity": "info",
                    "code": "color-non-numeric",
                    "context": context,
                    "value": str(value),
                }
            )
        return None
    if not channels:
        return None

    normalized = [_normalize_channel(c) for c in channels]
    if len(normalized) == 1:
        gray = int(round(normalized[0] * 255))
        return f"rgb({gray}, {gray}, {gray})"
    if len(normalized) == 3:
        r, g, b = (int(round(c * 255)) for c in normalized)
        return f"rgb({r}, {g}, {b})"
    if len(normalized) == 4:
        # DeviceCMYK -> RGB via the simple K-multiplied formula. The PDF spec
        # allows an ICC-based override; we record a diagnostic so the AI knows
        # the conversion was approximate.
        c, m, y, k = normalized
        r = round((1 - c) * (1 - k) * 255)
        g = round((1 - m) * (1 - k) * 255)
        b = round((1 - y) * (1 - k) * 255)
        if diagnostics is not None:
            diagnostics.append(
                {
                    "severity": "info",
                    "code": "color-cmyk-converted",
                    "context": context,
                    "cmyk": [c, m, y, k],
                }
            )
        return f"rgb({int(r)}, {int(g)}, {int(b)})"

    # Unsupported colorspace (Indexed / Separation / DeviceN / Pattern).
    if diagnostics is not None:
        diagnostics.append(
            {
                "severity": "warning",
                "code": "color-unsupported-channels",
                "context": context,
                "channels": len(normalized),
                "values": normalized,
            }
        )
    return None


def _normalize_channel(value: float) -> float:
    if value > 1.0:
        # Some PDFs report 0..255; rescale.
        value = value / 255.0
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value
