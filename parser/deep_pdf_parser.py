"""Deep PDF content-stream parser built on pikepdf.

PyMuPDF's high-level APIs (``get_drawings``, ``get_image_info``, the
``"rawdict"`` text mode) flatten Form XObject and Pattern fills into inline
raster images — losing the structural distinction between a *gradient fill*
on a rounded rectangle and a *stroke* on its outline. PyMuPDF was designed
for fast page rendering, not for round-tripping the source structure into a
different representation.

This module uses **pikepdf** to walk the PDF content stream operator by
operator. We extract semantic ``PaintEvent`` records that downstream code
can turn into crisp SVG (linear gradients, stroked paths, proper clip
stacks). The parser is intentionally narrow at this stage — we only extract
what's needed to fix the Page 6 / Page 11 / Page 15 issues. Coverage will
grow as we hit more cases.

What we currently extract per page:

- ``PatternFillEvent`` — ``cs /Pattern + scn /Pxxx + path + f|f*`` sequences,
  carrying both the path geometry and the resolved gradient (coords + colour
  stops) so the renderer can emit ``<linearGradient>`` + ``<path>``.

Out of scope for now (tracked in Phase 7 roadmap):

- Stroke operators (``S``, ``s``, ``b``, ``B``) — Page 6 borders need this.
- Form XObject recursion with CTM composition — Page 1 pill-curtain pieces.
- Soft-mask with ``/Matte`` re-validation against the rasterised path.

The module degrades gracefully: if pikepdf is missing or any page raises,
the rest of the pipeline runs unchanged. Failed pages get a diagnostic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence


@dataclass(slots=True)
class GradientStop:
    """One colour stop in a shading function, after normalisation to SVG units."""

    offset: float  # 0.0 .. 1.0 in shading domain
    rgb: tuple[float, float, float]  # 0..1 per channel


@dataclass(slots=True)
class LinearGradient:
    """An axial PDF shading (PatternType 2 / ShadingType 2) ready for SVG."""

    pattern_name: str
    x1: float
    y1: float
    x2: float
    y2: float
    extend_start: bool
    extend_end: bool
    stops: list[GradientStop] = field(default_factory=list)
    matrix: tuple[float, float, float, float, float, float] | None = None


@dataclass(slots=True)
class SoftMaskGradient:
    """A luminosity soft mask defined by a PDF shading pattern.

    PDF's ``/ExtGState /SMask`` can reference a Form XObject whose contents
    fill the mask region with another shading pattern. When ``S = Luminosity``
    the mask's RGB luminance becomes alpha for whatever the ExtGState is
    applied to. We extract that mask gradient so it can be emitted as an
    SVG ``<mask>`` containing the same linear gradient.
    """

    smask_kind: str  # "Luminosity" or "Alpha"
    bbox: tuple[float, float, float, float] | None
    gradient: LinearGradient | None
    background: tuple[float, float, float] | None  # /BC matte fallback


@dataclass(slots=True)
class PaintEvent:
    """One paint operation parsed out of the content stream."""

    kind: str  # "pattern-fill" for now
    seqno: int  # position in stream, used to merge with PyMuPDF events
    bbox: tuple[float, float, float, float]
    path_data: str  # SVG path d="" string in page coords
    even_odd: bool = False
    closed: bool = False
    gradient: LinearGradient | None = None
    ctm: tuple[float, float, float, float, float, float] = (1, 0, 0, 1, 0, 0)
    fill_alpha: float = 1.0
    blend_mode: str | None = None
    clip_path: str | None = None
    pdf_op: str = ""  # f, f*, B, b, ...
    source: dict[str, Any] = field(default_factory=dict)
    soft_mask: SoftMaskGradient | None = None


def parse_page_paint_events(
    pdf_path: str,
    page_index: int,
    diagnostics: list[dict] | None = None,
) -> list[PaintEvent]:
    """Return the list of ``PaintEvent`` records for one page.

    Currently only emits ``"pattern-fill"`` events. Other event kinds will
    follow in subsequent Phase 7 steps.
    """
    diagnostics = diagnostics if diagnostics is not None else []
    try:
        import pikepdf
    except ImportError:
        diagnostics.append(
            {
                "severity": "info",
                "code": "deep-parser-unavailable",
                "message": "pikepdf not installed; Phase 7 deep parser disabled.",
            }
        )
        return []
    try:
        pdf = pikepdf.open(pdf_path)
    except Exception as exc:
        diagnostics.append(
            {
                "severity": "warning",
                "code": "deep-parser-open-failed",
                "page": page_index + 1,
                "message": str(exc),
            }
        )
        return []
    try:
        try:
            page = pdf.pages[page_index]
        except IndexError:
            return []
        try:
            return _walk_page(pikepdf, pdf, page, page_index, diagnostics)
        except Exception as exc:
            diagnostics.append(
                {
                    "severity": "warning",
                    "code": "deep-parser-walk-failed",
                    "page": page_index + 1,
                    "message": str(exc),
                }
            )
            return []
    finally:
        pdf.close()


def _walk_page(
    pikepdf,
    pdf,
    page,
    page_index: int,
    diagnostics: list[dict],
) -> list[PaintEvent]:
    events: list[PaintEvent] = []
    try:
        stream = list(pikepdf.parse_content_stream(page))
    except Exception as exc:
        diagnostics.append(
            {
                "severity": "warning",
                "code": "deep-parser-stream-failed",
                "page": page_index + 1,
                "message": str(exc),
            }
        )
        return events

    state = _GraphicsState()
    state_stack: list[_GraphicsState] = []
    patterns = page.Resources.get("/Pattern", {}) if "/Resources" in page else {}
    extg_states = page.Resources.get("/ExtGState", {}) if "/Resources" in page else {}

    path_builder = _PathBuilder()
    pending_fill_pattern: str | None = None
    pending_fill_alpha: float = 1.0

    for seqno, (operands, op_raw) in enumerate(stream):
        op = _op_name(op_raw)

        # --- graphics state stack ---
        if op == "q":
            state_stack.append(state.clone())
            continue
        if op == "Q":
            if state_stack:
                state = state_stack.pop()
            continue
        if op == "cm" and len(operands) == 6:
            state.ctm = _mul_ctm(_to_floats(operands), state.ctm)
            continue
        if op == "gs" and operands:
            gs_name = _name_str(operands[0])
            ext = extg_states.get(gs_name)
            if ext is not None:
                ca = ext.get("/ca")
                if ca is not None:
                    state.fill_alpha = float(ca)
                bm = ext.get("/BM")
                if bm is not None:
                    state.blend_mode = _name_str(bm).lstrip("/") or None
                # Soft mask sub-stream — when /SMask references a Form
                # XObject whose content is another shading pattern, capture
                # that so we can emit an SVG <mask> later.
                smask = ext.get("/SMask")
                if smask is not None and not _is_none_obj(smask):
                    state.soft_mask = _resolve_soft_mask(pikepdf, smask)
                elif "/SMask" in ext:
                    # /SMask explicitly set to /None resets the state.
                    state.soft_mask = None
            continue

        # --- fill colour selection ---
        if op == "cs" and operands:
            cs_name = _name_str(operands[0])
            state.fill_pattern_mode = cs_name == "/Pattern"
            continue
        if op == "scn" and state.fill_pattern_mode and operands:
            pending_fill_pattern = _name_str(operands[-1])
            continue
        if op in {"rg", "g", "k", "sc"}:
            # Switched back to a non-pattern colour; clear any pending pattern.
            state.fill_pattern_mode = False
            pending_fill_pattern = None
            continue

        # --- path construction ---
        if op == "m" and len(operands) == 2:
            x, y = _to_floats(operands)
            path_builder.move_to(x, y)
            continue
        if op == "l" and len(operands) == 2:
            x, y = _to_floats(operands)
            path_builder.line_to(x, y)
            continue
        if op == "c" and len(operands) == 6:
            x1, y1, x2, y2, x3, y3 = _to_floats(operands)
            path_builder.cubic_to(x1, y1, x2, y2, x3, y3)
            continue
        if op == "v" and len(operands) == 4:
            x2, y2, x3, y3 = _to_floats(operands)
            path_builder.cubic_to_v(x2, y2, x3, y3)
            continue
        if op == "y" and len(operands) == 4:
            x1, y1, x3, y3 = _to_floats(operands)
            path_builder.cubic_to_y(x1, y1, x3, y3)
            continue
        if op == "re" and len(operands) == 4:
            x, y, w, h = _to_floats(operands)
            path_builder.rect(x, y, w, h)
            continue
        if op == "h":
            path_builder.close_subpath()
            continue

        # --- fill / stroke ---
        if op in {"f", "F", "f*", "b", "b*", "B", "B*"}:
            even_odd = op in {"f*", "b*", "B*"}
            closed = op != "B"  # B does not auto-close; others do
            if pending_fill_pattern and pending_fill_pattern in patterns:
                gradient = _resolve_linear_gradient(
                    pikepdf, patterns[pending_fill_pattern], pending_fill_pattern
                )
                if gradient is not None:
                    path_data, bbox = path_builder.to_svg(state.ctm)
                    if path_data and bbox is not None:
                        events.append(
                            PaintEvent(
                                kind="pattern-fill",
                                seqno=seqno,
                                bbox=bbox,
                                path_data=path_data,
                                even_odd=even_odd,
                                closed=closed,
                                gradient=gradient,
                                ctm=state.ctm,
                                fill_alpha=state.fill_alpha,
                                blend_mode=state.blend_mode,
                                pdf_op=op,
                                source={
                                    "pattern_name": pending_fill_pattern,
                                    "shading_type": 2,
                                    "has_soft_mask": state.soft_mask is not None,
                                },
                                soft_mask=state.soft_mask,
                            )
                        )
            path_builder.reset()
            pending_fill_pattern = None
            continue
        if op in {"S", "s"}:
            # Stroke — placeholder; full stroke extraction is Phase 7-D.
            path_builder.reset()
            pending_fill_pattern = None
            continue
        if op == "n":
            # No-op terminator (typically after a clip set up by W/W*).
            path_builder.reset()
            continue

    return events


def _resolve_linear_gradient(
    pikepdf, pattern, name: str
) -> LinearGradient | None:
    try:
        if int(pattern.get("/PatternType", 0)) != 2:
            return None
        shading = pattern.get("/Shading")
        if shading is None:
            return None
        if int(shading.get("/ShadingType", 0)) != 2:
            return None  # Only axial (linear) shadings for now.
        coords = shading.get("/Coords")
        if not coords or len(coords) < 4:
            return None
        x1, y1, x2, y2 = [float(coords[i]) for i in range(4)]
        extend = shading.get("/Extend")
        extend_start, extend_end = (False, False)
        if extend and len(extend) == 2:
            extend_start, extend_end = bool(extend[0]), bool(extend[1])
        stops = _extract_function_stops(shading.get("/Function"))
        if not stops:
            return None
        matrix_raw = pattern.get("/Matrix")
        matrix = None
        if matrix_raw and len(matrix_raw) == 6:
            matrix = tuple(float(matrix_raw[i]) for i in range(6))
        return LinearGradient(
            pattern_name=name,
            x1=x1, y1=y1, x2=x2, y2=y2,
            extend_start=extend_start, extend_end=extend_end,
            stops=stops,
            matrix=matrix,
        )
    except Exception:
        return None


def _extract_function_stops(fn) -> list[GradientStop]:
    if fn is None:
        return []
    try:
        ftype = int(fn.get("/FunctionType", -1))
    except Exception:
        return []
    if ftype == 2:
        # Exponential interpolation: domain → (C0..C1). Emit two stops.
        c0 = _to_rgb_tuple(fn.get("/C0"))
        c1 = _to_rgb_tuple(fn.get("/C1"))
        if c0 is None or c1 is None:
            return []
        return [GradientStop(0.0, c0), GradientStop(1.0, c1)]
    if ftype == 3:
        # Stitching: a list of Type 2 functions over Bounds intervals in
        # the function's Domain. We sample each sub-function at its
        # endpoints and emit stops mapped onto the parent Domain.
        try:
            domain = [float(v) for v in fn.get("/Domain", [0, 1])]
            d_lo, d_hi = domain[0], domain[1]
            d_range = max(1e-9, d_hi - d_lo)
            bounds_raw = fn.get("/Bounds") or []
            bounds = [float(v) for v in bounds_raw]
            sub_funcs = list(fn.get("/Functions") or [])
            encode_raw = fn.get("/Encode")
            encode = [float(v) for v in encode_raw] if encode_raw else []
        except Exception:
            return []
        if not sub_funcs:
            return []
        # Build the segment boundaries in the parent domain.
        segment_starts = [d_lo, *bounds]
        segment_ends = [*bounds, d_hi]
        stops: list[GradientStop] = []
        for i, sub in enumerate(sub_funcs):
            s_start = segment_starts[i] if i < len(segment_starts) else d_lo
            s_end = segment_ends[i] if i < len(segment_ends) else d_hi
            try:
                if int(sub.get("/FunctionType", -1)) != 2:
                    continue
                c0 = _to_rgb_tuple(sub.get("/C0"))
                c1 = _to_rgb_tuple(sub.get("/C1"))
            except Exception:
                continue
            if c0 is None or c1 is None:
                continue
            # Encode flips the sub-function direction if encode is [1, 0].
            if encode and i * 2 + 1 < len(encode):
                if encode[i * 2] == 1 and encode[i * 2 + 1] == 0:
                    c0, c1 = c1, c0
            off_start = (s_start - d_lo) / d_range
            off_end = (s_end - d_lo) / d_range
            # Only append the start of the first segment; subsequent
            # segments' starts duplicate the previous end.
            if not stops or stops[-1].offset < off_start - 1e-6:
                stops.append(GradientStop(off_start, c0))
            stops.append(GradientStop(off_end, c1))
        return stops
    return []


def _to_rgb_tuple(arr) -> tuple[float, float, float] | None:
    if arr is None:
        return None
    try:
        values = [float(v) for v in arr]
    except Exception:
        return None
    if len(values) == 1:
        v = values[0]
        return (v, v, v)
    if len(values) >= 3:
        return (values[0], values[1], values[2])
    if len(values) == 4:
        # CMYK approximation.
        c, m, y, k = values[0], values[1], values[2], values[3]
        return ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    return None


@dataclass
class _GraphicsState:
    ctm: tuple[float, float, float, float, float, float] = (1, 0, 0, 1, 0, 0)
    fill_alpha: float = 1.0
    blend_mode: str | None = None
    fill_pattern_mode: bool = False
    soft_mask: SoftMaskGradient | None = None

    def clone(self) -> "_GraphicsState":
        return _GraphicsState(
            ctm=self.ctm,
            fill_alpha=self.fill_alpha,
            blend_mode=self.blend_mode,
            fill_pattern_mode=self.fill_pattern_mode,
            soft_mask=self.soft_mask,
        )


def _is_none_obj(obj) -> bool:
    """pikepdf returns PDF /None either as Python None or a Name('/None')."""
    if obj is None:
        return True
    try:
        return str(obj) == "/None"
    except Exception:
        return False


def _resolve_soft_mask(pikepdf, smask) -> SoftMaskGradient | None:
    """Resolve an ExtGState ``/SMask`` dict into a ``SoftMaskGradient``.

    Only handles the common PPT-export pattern where the SMask is a
    transparency-group Form XObject whose entire content stream is a single
    ``cs /Pattern + scn /Pxxx + path + f|f*`` sequence painting a shading
    gradient. The mask's ``/S`` selects how that content becomes alpha:

    - ``Luminosity`` (most common here): RGB luminance → alpha.
    - ``Alpha``: the rendered alpha channel → alpha.

    Returns ``None`` for shapes we don't yet handle (image-based SMask,
    Type 3+ shadings, etc.); the caller falls back to opaque fill.
    """
    try:
        kind_obj = smask.get("/S")
        kind = "Luminosity"
        if kind_obj is not None:
            kind = _name_str(kind_obj).lstrip("/") or "Luminosity"
        g = smask.get("/G")
        if g is None:
            return None
        bbox_raw = g.get("/BBox")
        bbox = (
            (float(bbox_raw[0]), float(bbox_raw[1]),
             float(bbox_raw[2]), float(bbox_raw[3]))
            if bbox_raw and len(bbox_raw) == 4
            else None
        )
        bc_raw = smask.get("/BC")
        background = None
        if bc_raw:
            try:
                bc = [float(v) for v in bc_raw]
            except Exception:
                bc = []
            if len(bc) == 1:
                background = (bc[0], bc[0], bc[0])
            elif len(bc) >= 3:
                background = (bc[0], bc[1], bc[2])
        # Parse the SMask form's content stream to find a single pattern fill.
        try:
            sub_stream = list(pikepdf.parse_content_stream(g))
        except Exception:
            return SoftMaskGradient(kind, bbox, None, background)
        sub_patterns = (g.get("/Resources") or {}).get("/Pattern", {})
        gradient = _scan_substream_for_gradient(pikepdf, sub_stream, sub_patterns)
        return SoftMaskGradient(kind, bbox, gradient, background)
    except Exception:
        return None


def _scan_substream_for_gradient(pikepdf, stream, patterns) -> LinearGradient | None:
    """Scan a tiny SMask Form's content stream for its single pattern fill."""
    pending_pattern: str | None = None
    pattern_mode = False
    for operands, op_raw in stream:
        op = _op_name(op_raw)
        if op == "cs" and operands:
            pattern_mode = _name_str(operands[0]) == "/Pattern"
            continue
        if op == "scn" and pattern_mode and operands:
            pending_pattern = _name_str(operands[-1])
            continue
        if op in {"f", "f*", "F", "b", "b*", "B", "B*"} and pending_pattern:
            if pending_pattern in patterns:
                return _resolve_linear_gradient(
                    pikepdf, patterns[pending_pattern], pending_pattern
                )
            pending_pattern = None
    return None


def _mul_ctm(a: Sequence[float], b: tuple[float, ...]) -> tuple[float, ...]:
    """Multiply two PDF CTM matrices: ``new = a @ b``.

    PDF stores CTM as ``[a b c d e f]`` representing the 3×3 affine
    ``[[a b 0][c d 0][e f 1]]``. The ``cm`` operator post-multiplies the
    current CTM: ``CTM_new = a @ CTM_old``.
    """
    a0, a1, a2, a3, a4, a5 = a
    b0, b1, b2, b3, b4, b5 = b
    return (
        a0 * b0 + a1 * b2,
        a0 * b1 + a1 * b3,
        a2 * b0 + a3 * b2,
        a2 * b1 + a3 * b3,
        a4 * b0 + a5 * b2 + b4,
        a4 * b1 + a5 * b3 + b5,
    )


class _PathBuilder:
    """Accumulates path operators, applies CTM at SVG-emit time."""

    def __init__(self) -> None:
        self._segments: list[tuple] = []
        self._subpath_starts: list[tuple[float, float]] = []
        self._current: tuple[float, float] | None = None

    def reset(self) -> None:
        self._segments.clear()
        self._subpath_starts.clear()
        self._current = None

    def move_to(self, x: float, y: float) -> None:
        self._segments.append(("M", x, y))
        self._subpath_starts.append((x, y))
        self._current = (x, y)

    def line_to(self, x: float, y: float) -> None:
        self._segments.append(("L", x, y))
        self._current = (x, y)

    def cubic_to(self, x1, y1, x2, y2, x3, y3) -> None:
        self._segments.append(("C", x1, y1, x2, y2, x3, y3))
        self._current = (x3, y3)

    def cubic_to_v(self, x2, y2, x3, y3) -> None:
        x1, y1 = self._current if self._current else (x2, y2)
        self.cubic_to(x1, y1, x2, y2, x3, y3)

    def cubic_to_y(self, x1, y1, x3, y3) -> None:
        self.cubic_to(x1, y1, x3, y3, x3, y3)

    def rect(self, x, y, w, h) -> None:
        self.move_to(x, y)
        self.line_to(x + w, y)
        self.line_to(x + w, y + h)
        self.line_to(x, y + h)
        self.close_subpath()

    def close_subpath(self) -> None:
        self._segments.append(("Z",))
        if self._subpath_starts:
            self._current = self._subpath_starts[-1]

    def to_svg(
        self, ctm: tuple[float, ...]
    ) -> tuple[str, tuple[float, float, float, float] | None]:
        if not self._segments:
            return "", None
        parts: list[str] = []
        xs: list[float] = []
        ys: list[float] = []
        for seg in self._segments:
            cmd = seg[0]
            if cmd == "Z":
                parts.append("Z")
                continue
            coords = list(seg[1:])
            transformed: list[float] = []
            for i in range(0, len(coords), 2):
                x_user, y_user = coords[i], coords[i + 1]
                x_page, y_page = _apply_ctm(ctm, x_user, y_user)
                transformed.extend([x_page, y_page])
                xs.append(x_page)
                ys.append(y_page)
            nums = " ".join(f"{v:.3f}" for v in transformed)
            parts.append(f"{cmd} {nums}")
        if not xs:
            return "", None
        bbox = (min(xs), min(ys), max(xs), max(ys))
        return " ".join(parts), bbox


def _apply_ctm(
    ctm: tuple[float, ...], x: float, y: float
) -> tuple[float, float]:
    a, b, c, d, e, f = ctm
    return a * x + c * y + e, b * x + d * y + f


def _op_name(op) -> str:
    if isinstance(op, bytes):
        return op.decode("latin-1", errors="replace")
    return str(op)


def _name_str(value) -> str:
    if hasattr(value, "__bytes__"):
        try:
            return bytes(value).decode("latin-1", errors="replace")
        except Exception:
            pass
    s = str(value)
    return s


def _to_floats(operands) -> list[float]:
    return [float(v) for v in operands]
