from __future__ import annotations

from html import escape

from core.units import bbox_to_px
from model.elements import TableCandidate
from renderer.css_renderer import css_rule


def _common_attrs(table: TableCandidate) -> str:
    return f'id="{escape(table.stable_id)}"'


def render_table(table: TableCandidate, scale: float) -> str:
    if not table.editable or table.render_mode != "semantic-html":
        return (
            f'<div {_common_attrs(table)} class="pdf-table pdf-table-sidecar" '
            f'aria-hidden="true"></div>'
        )
    rows_html: list[str] = []
    for row in table.cells:
        cells_html = "".join(
            f'<td>{escape(value or "")}</td>'
            for value in row
        )
        rows_html.append(f"<tr>{cells_html}</tr>")
    return (
        f'<table {_common_attrs(table)} class="pdf-table">'
        f'{"".join(rows_html)}</table>'
    )


def render_table_css(table: TableCandidate, scale: float) -> str:
    box = bbox_to_px(table.bbox, scale)
    return css_rule(
        f"#{table.stable_id}",
        {
            "left": f"{box.x0:.3f}px",
            "top": f"{box.y0:.3f}px",
            "width": f"{max(0, box.width):.3f}px",
            "height": f"{max(0, box.height):.3f}px",
        },
    )
