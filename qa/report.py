from __future__ import annotations

from model.document import DocumentModel
from qa.editability_check import text_editability_report
from qa.visual_compare import visual_compare_placeholder


def build_report(document: DocumentModel, contract_violations: list[dict]) -> dict:
    return {
        "source": str(document.source_path),
        "page_count": document.page_count,
        "contract_violation_count": len(contract_violations),
        "contract_violations": contract_violations,
        "editability": text_editability_report(document),
        "visual_compare": visual_compare_placeholder(),
        "diagnostics": document.diagnostics,
    }
