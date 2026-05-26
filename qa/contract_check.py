from __future__ import annotations

from core.output_contract import check_document_contract
from model.document import DocumentModel


def run_contract_check(document: DocumentModel) -> list[dict]:
    return check_document_contract(document)
