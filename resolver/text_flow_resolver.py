from __future__ import annotations

from dataclasses import dataclass

from model.elements import TextBlockElement


@dataclass(slots=True)
class TextFlow:
    columns: list[list[int]]
    reading_order: list[int]
    header_block_ids: list[str]
    footer_block_ids: list[str]
    page_width: float
    page_height: float


def apply_visual_text_flow(blocks: list[TextBlockElement]) -> list[TextBlockElement]:
    """Assign visual order by top-left coordinates (legacy behaviour)."""
    for order, block in enumerate(sorted(blocks, key=lambda item: (item.bbox.y0, item.bbox.x0))):
        block.source["visual_order"] = order
        block.text_flow = "visual"
    return blocks


def resolve_text_flow(
    blocks: list[TextBlockElement],
    page_width: float,
    page_height: float,
) -> TextFlow:
    """Detect columns, header/footer and compute reading order.

    Visual order (top-left coord sort) is preserved in `block.source["visual_order"]`,
    reading order is recorded in `block.source["reading_order"]` and
    `block.text_flow` is updated to `"reading"` for blocks that are part of the
    primary flow (excludes header / footer).
    """
    if not blocks:
        return TextFlow([], [], [], [], page_width, page_height)

    apply_visual_text_flow(blocks)

    header_ids, footer_ids = _detect_header_footer(blocks, page_height)
    body_blocks = [
        block for block in blocks
        if block.stable_id not in header_ids and block.stable_id not in footer_ids
    ]

    columns_indices = _detect_columns(body_blocks, page_width)

    reading_indices: list[int] = []
    id_to_block_index = {block.stable_id: idx for idx, block in enumerate(blocks)}

    for column in columns_indices:
        ordered = sorted(column, key=lambda local: body_blocks[local].bbox.y0)
        for local in ordered:
            block = body_blocks[local]
            reading_indices.append(id_to_block_index[block.stable_id])

    for order, block_index in enumerate(reading_indices):
        block = blocks[block_index]
        block.source["reading_order"] = order
        block.text_flow = "reading"

    for header_id in header_ids:
        block = blocks[id_to_block_index[header_id]]
        block.source["text_role"] = "header"
        block.text_flow = "header"
    for footer_id in footer_ids:
        block = blocks[id_to_block_index[footer_id]]
        block.source["text_role"] = "footer"
        block.text_flow = "footer"

    column_block_ids = [
        [body_blocks[local].stable_id for local in column]
        for column in columns_indices
    ]
    return TextFlow(
        columns=column_block_ids,
        reading_order=[blocks[i].stable_id for i in reading_indices],
        header_block_ids=list(header_ids),
        footer_block_ids=list(footer_ids),
        page_width=page_width,
        page_height=page_height,
    )


def _detect_header_footer(
    blocks: list[TextBlockElement], page_height: float
) -> tuple[list[str], list[str]]:
    if page_height <= 0 or not blocks:
        return [], []
    header_zone = page_height * 0.08
    footer_zone = page_height * 0.92
    header_ids: list[str] = []
    footer_ids: list[str] = []
    for block in blocks:
        if block.bbox.y1 <= header_zone and block.bbox.height < page_height * 0.1:
            header_ids.append(block.stable_id)
        elif block.bbox.y0 >= footer_zone and block.bbox.height < page_height * 0.1:
            footer_ids.append(block.stable_id)
    return header_ids, footer_ids


def _detect_columns(
    blocks: list[TextBlockElement], page_width: float
) -> list[list[int]]:
    """Cluster block indices (local to provided list) by horizontal position."""
    if not blocks:
        return []
    if page_width <= 0:
        return [list(range(len(blocks)))]

    sorted_local = sorted(range(len(blocks)), key=lambda i: blocks[i].bbox.x0)
    columns: list[list[int]] = []
    column_ranges: list[tuple[float, float]] = []
    gap_threshold = page_width * 0.04

    for local in sorted_local:
        block = blocks[local]
        x0, x1 = block.bbox.x0, block.bbox.x1
        matched = False
        for col_index, (col_x0, col_x1) in enumerate(column_ranges):
            if _intervals_overlap(x0, x1, col_x0, col_x1, gap_threshold):
                columns[col_index].append(local)
                column_ranges[col_index] = (min(col_x0, x0), max(col_x1, x1))
                matched = True
                break
        if not matched:
            columns.append([local])
            column_ranges.append((x0, x1))

    paired = sorted(zip(column_ranges, columns), key=lambda item: item[0][0])
    return [column for _, column in paired]


def _intervals_overlap(
    a0: float, a1: float, b0: float, b1: float, slack: float
) -> bool:
    return not (a1 + slack < b0 or b1 + slack < a0)


def text_flow_sidecar(flow: TextFlow) -> dict:
    return {
        "columns": flow.columns,
        "reading_order": flow.reading_order,
        "header_block_ids": flow.header_block_ids,
        "footer_block_ids": flow.footer_block_ids,
        "page_width": flow.page_width,
        "page_height": flow.page_height,
    }
