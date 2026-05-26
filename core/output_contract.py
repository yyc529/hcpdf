from __future__ import annotations

from model.document import DocumentModel
from model.elements import (
    ElementModel,
    ImageElement,
    TableCandidate,
    TextBlockElement,
)


REQUIRED_DATA_FIELDS = {
    "page",
    "source_type",
    "source_index",
    "bbox",
    "render_mode",
    "editable",
    "z",
}


def _element_data_fields(element: ElementModel) -> set[str]:
    fields = {
        "page",
        "source_type",
        "source_index",
        "bbox",
        "render_mode",
        "editable",
        "z",
    }
    if isinstance(element, TextBlockElement):
        fields.update({"font_name", "font_size", "writing_mode", "text_flow"})
    if isinstance(element, ImageElement):
        fields.update({"xref", "source_asset", "mask_xref", "effect_pipeline"})
    if isinstance(element, TableCandidate):
        fields.update({"confidence", "rows", "cols"})
    return fields


def check_document_contract(document: DocumentModel) -> list[dict]:
    violations: list[dict] = []
    for page in document.pages:
        if not page.stable_id:
            violations.append({"page": page.page_index, "reason": "missing page id"})
        for element in page.elements:
            if not element.stable_id:
                violations.append(
                    {"page": page.page_index, "reason": "missing element id", "element": element.type}
                )
            missing = REQUIRED_DATA_FIELDS - _element_data_fields(element)
            if missing:
                violations.append(
                    {
                        "page": page.page_index,
                        "element": element.stable_id,
                        "reason": "missing data fields",
                        "missing": sorted(missing),
                    }
                )
    return violations
