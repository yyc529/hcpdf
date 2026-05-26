from __future__ import annotations

import fitz


def parse_document_metadata(doc: fitz.Document) -> dict:
    metadata = dict(doc.metadata or {})
    metadata["page_count"] = doc.page_count
    metadata["is_pdf"] = doc.is_pdf
    return metadata
