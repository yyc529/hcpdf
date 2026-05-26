from __future__ import annotations

from pathlib import Path


def list_regression_pdfs(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(root.glob("*.pdf"))
