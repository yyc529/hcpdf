from __future__ import annotations

from pathlib import Path

from model.elements import TableCandidate
from model.geometry import BBox


def parse_table_candidates(pdf_path: Path, page_index: int) -> list[TableCandidate]:
    try:
        import pdfplumber
    except Exception:
        return []
    candidates: list[TableCandidate] = []
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            page = pdf.pages[page_index]
            try:
                found_tables = page.find_tables() or []
            except Exception:
                found_tables = []
            for table_index, table in enumerate(found_tables):
                try:
                    cells_text = table.extract() or []
                except Exception:
                    cells_text = []
                if not cells_text:
                    continue
                bbox_seq = table.bbox or (0, 0, page.width, page.height)
                bbox = BBox(
                    float(bbox_seq[0]),
                    float(bbox_seq[1]),
                    float(bbox_seq[2]),
                    float(bbox_seq[3]),
                )
                rows = len(cells_text)
                cols = max((len(row) for row in cells_text), default=0)
                cell_bboxes = _collect_cell_bboxes(table)
                confidence = _estimate_confidence(cells_text, cell_bboxes, rows, cols)
                render_mode = "semantic-html" if confidence >= 0.7 else "diagnostic-placeholder"
                editable = confidence >= 0.7
                diagnostics = []
                if confidence < 0.7:
                    diagnostics.append(
                        {
                            "severity": "info",
                            "code": "table-low-confidence",
                            "message": (
                                f"Table {table_index} confidence {confidence:.2f}; "
                                "kept as sidecar without semantic <table>."
                            ),
                        }
                    )
                candidates.append(
                    TableCandidate(
                        stable_id=f"pdf-page-{page_index + 1}-table-{table_index}",
                        type="table",
                        page_index=page_index + 1,
                        bbox=bbox,
                        z_index=0,
                        source_index=table_index,
                        render_mode=render_mode,
                        editable=editable,
                        source={
                            "source": "pdfplumber.find_tables",
                            "settings": _table_settings_signature(table),
                        },
                        diagnostics=diagnostics,
                        cells=cells_text,
                        cell_bboxes=cell_bboxes,
                        confidence=confidence,
                        rows=rows,
                        cols=cols,
                    )
                )
    except Exception:
        return []
    return candidates


def _collect_cell_bboxes(table) -> list[list[list[float]]]:
    grid: list[list[list[float]]] = []
    rows = getattr(table, "rows", None) or []
    for row in rows:
        cells = getattr(row, "cells", None) or []
        grid_row: list[list[float]] = []
        for cell in cells:
            if cell is None:
                grid_row.append([])
                continue
            try:
                x0, y0, x1, y1 = cell
                grid_row.append([float(x0), float(y0), float(x1), float(y1)])
            except Exception:
                grid_row.append([])
        grid.append(grid_row)
    return grid


def _table_settings_signature(table) -> dict:
    settings = getattr(table, "settings", None)
    if not settings:
        return {}
    try:
        return {
            key: value
            for key, value in settings.items()
            if isinstance(value, (str, int, float, bool, list, tuple))
        }
    except Exception:
        return {}


def _estimate_confidence(
    cells: list[list], cell_bboxes: list[list[list[float]]], rows: int, cols: int
) -> float:
    if rows < 2 or cols < 2:
        return 0.3
    total = 0
    filled = 0
    for row in cells:
        for value in row:
            total += 1
            if value not in (None, "", " "):
                filled += 1
    fill_ratio = filled / total if total else 0.0
    bbox_total = 0
    bbox_ok = 0
    for row in cell_bboxes:
        for cell in row:
            bbox_total += 1
            if cell:
                bbox_ok += 1
    bbox_ratio = bbox_ok / bbox_total if bbox_total else 0.0
    score = 0.5 * fill_ratio + 0.5 * bbox_ratio
    if rows >= 3 and cols >= 2:
        score += 0.1
    return max(0.0, min(1.0, score))
