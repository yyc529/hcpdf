from __future__ import annotations

from model.document import DocumentModel
from model.elements import TextBlockElement


def text_editability_report(document: DocumentModel) -> dict:
    text_blocks = 0
    editable_text_blocks = 0
    spans = 0
    for page in document.pages:
        for element in page.elements:
            if isinstance(element, TextBlockElement):
                text_blocks += 1
                if element.editable:
                    editable_text_blocks += 1
                spans += sum(len(line.spans) for line in element.lines)
    ratio = editable_text_blocks / text_blocks if text_blocks else 1.0
    return {
        "text_blocks": text_blocks,
        "editable_text_blocks": editable_text_blocks,
        "text_editable_ratio": ratio,
        "text_spans": spans,
    }
