from __future__ import annotations

from pathlib import Path

import fitz

from assets.cache import AssetCache
from assets.font_pipeline import extract_document_fonts
from assets.naming import default_output_dir
from core.config import ConversionConfig
from core.context import DocumentContext
from core.diagnostics import write_json
from model.document import DocumentModel
from model.elements import GradientFillElement, TableCandidate
from parser.document_parser import parse_document_metadata
from parser.page_parser import parse_page
from qa.contract_check import run_contract_check
from qa.report import build_report
from qa.screenshot import capture_html_pages, export_original_page
from renderer.html_renderer import render_page_html
from renderer.index_renderer import render_compare, render_index, render_original


def convert(
    pdf_path: str | Path,
    output_dir: str | Path | None = None,
    config: ConversionConfig | None = None,
) -> DocumentModel:
    source_path = Path(pdf_path).resolve()
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    config = config or ConversionConfig()
    resolved_output = Path(output_dir).resolve() if output_dir else default_output_dir(source_path).resolve()
    context = DocumentContext(source_path=source_path, output_dir=resolved_output, config=config)
    _ensure_output_dirs(context)

    image_cache = AssetCache()
    with fitz.open(source_path) as doc:
        document = DocumentModel(
            source_path=source_path,
            output_dir=resolved_output,
            page_count=doc.page_count,
            metadata=parse_document_metadata(doc),
        )
        if config.extract_fonts:
            document.fonts = extract_document_fonts(
                doc, context.font_dir, context.diagnostics
            )

        image_assets_by_id = {}
        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            export_original_page(page, page_index + 1, context.original_pages_dir, config.original_dpi)
            try:
                page_model, page_image_assets, text_flow_data = parse_page(
                    doc, page, page_index, context, image_cache
                )
            except Exception as exc:
                if not config.continue_on_page_error:
                    raise
                context.diagnostics.append(
                    {
                        "severity": "error",
                        "code": "page-parse-failed",
                        "page": page_index + 1,
                        "message": str(exc),
                    }
                )
                continue
            document.pages.append(page_model)
            for asset in page_image_assets:
                image_assets_by_id[asset.stable_id] = asset
            (resolved_output / f"page{page_model.page_index}.html").write_text(
                render_page_html(page_model, document.fonts),
                encoding="utf-8",
            )
            write_json(
                context.debug_dir / f"page{page_model.page_index}.model.json",
                page_model,
                pretty=config.pretty_json,
            )
            write_json(
                context.debug_dir / f"page{page_model.page_index}.diagnostics.json",
                page_model.diagnostics,
                pretty=config.pretty_json,
            )
            write_json(
                context.debug_dir / f"page{page_model.page_index}.text_flow.json",
                text_flow_data,
                pretty=config.pretty_json,
            )
            table_sidecar = _build_table_sidecar(page_model)
            if table_sidecar["tables"]:
                write_json(
                    context.debug_dir / f"page{page_model.page_index}.tables.json",
                    table_sidecar,
                    pretty=config.pretty_json,
                )
            paint_sidecar = _build_paint_sidecar(page_model)
            if paint_sidecar["paint_events"]:
                write_json(
                    context.debug_dir / f"page{page_model.page_index}.paint_events.json",
                    paint_sidecar,
                    pretty=config.pretty_json,
                )

    document.images = list(image_assets_by_id.values())
    document.diagnostics.extend(context.diagnostics)
    contract_violations = run_contract_check(document)
    if config.generate_html_screenshots:
        document.diagnostics.extend(capture_html_pages(resolved_output, document.page_count))

    (resolved_output / "index.html").write_text(render_index(document), encoding="utf-8")
    (resolved_output / "original.html").write_text(render_original(document), encoding="utf-8")
    (resolved_output / "compare.html").write_text(render_compare(document), encoding="utf-8")

    write_json(context.debug_dir / "document.model.json", document, pretty=config.pretty_json)
    write_json(context.debug_dir / "document.diagnostics.json", document.diagnostics, pretty=config.pretty_json)
    write_json(context.debug_dir / "contract.json", contract_violations, pretty=config.pretty_json)
    write_json(context.debug_dir / "report.json", build_report(document, contract_violations), pretty=config.pretty_json)
    return document


def _build_table_sidecar(page_model) -> dict:
    tables: list[dict] = []
    for element in page_model.elements:
        if isinstance(element, TableCandidate):
            tables.append(
                {
                    "stable_id": element.stable_id,
                    "page": element.page_index,
                    "bbox": element.bbox.as_list(),
                    "rows": element.rows,
                    "cols": element.cols,
                    "confidence": element.confidence,
                    "cells": element.cells,
                    "cell_bboxes": element.cell_bboxes,
                    "render_mode": element.render_mode,
                    "editable": element.editable,
                }
            )
    return {"page": page_model.page_index, "tables": tables}


def _build_paint_sidecar(page_model) -> dict:
    """Dump Phase 7 deep-parser gradient fills for inspection.

    Only emits when the page actually carries deep-parser output. Sidecar
    fields mirror the SVG we generate (gradient coords + stops + path
    bbox) so a regression can be diffed against either the original PDF
    or the on-disk model.
    """
    events: list[dict] = []
    for element in page_model.elements:
        if isinstance(element, GradientFillElement):
            events.append(
                {
                    "stable_id": element.stable_id,
                    "kind": "pattern-fill",
                    "page": element.page_index,
                    "bbox": element.bbox.as_list(),
                    "pattern_name": element.pattern_name,
                    "fill_alpha": element.fill_alpha,
                    "even_odd": element.even_odd,
                    "stream_seqno": element.source.get("stream_seqno"),
                    "gradient": {
                        "x1": element.grad_x1,
                        "y1": element.grad_y1,
                        "x2": element.grad_x2,
                        "y2": element.grad_y2,
                        "extend_start": element.grad_extend_start,
                        "extend_end": element.grad_extend_end,
                        "stops": [
                            {"offset": s.offset, "rgb": list(s.rgb)}
                            for s in element.grad_stops
                        ],
                        "matrix": list(element.grad_matrix) if element.grad_matrix else None,
                    },
                }
            )
    return {"page": page_model.page_index, "paint_events": events}


def _ensure_output_dirs(context: DocumentContext) -> None:
    for directory in [
        context.output_dir,
        context.image_dir,
        context.font_dir,
        context.original_pages_dir,
        context.html_screenshots_dir,
        context.debug_dir,
    ]:
        directory.mkdir(parents=True, exist_ok=True)
