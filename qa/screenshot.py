from __future__ import annotations

from pathlib import Path

import fitz


def export_original_page(page: fitz.Page, page_number: int, output_dir: Path, dpi: int) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    pixmap = page.get_pixmap(dpi=dpi, alpha=False)
    path = output_dir / f"page{page_number}.png"
    pixmap.save(path)
    return path


def capture_html_pages(output_dir: Path, page_count: int, viewport_size: tuple[int, int] | None = None) -> list[dict]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return [{"severity": "warning", "code": "playwright-unavailable", "message": str(exc)}]

    diagnostics: list[dict] = []
    screenshot_dir = output_dir / "assets" / "html_pages"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={"width": 1280, "height": 720})
            page = context.new_page()
            for page_number in range(1, page_count + 1):
                page.goto((output_dir / f"page{page_number}.html").resolve().as_uri())
                page.evaluate("document.fonts && document.fonts.ready")
                # Resize viewport to match the actual rendered .pdf-page so the
                # screenshot captures one PDF page at 1:1 with no body chrome.
                dims = page.evaluate(
                    "() => { const s = document.querySelector('.pdf-page');"
                    " if (!s) return null;"
                    " const r = s.getBoundingClientRect();"
                    " return { w: Math.round(r.width), h: Math.round(r.height) }; }"
                )
                if dims and dims.get("w") and dims.get("h"):
                    page.set_viewport_size({"width": dims["w"], "height": dims["h"]})
                    page.evaluate(
                        "() => { document.body.style.margin='0';"
                        " document.body.style.padding='0';"
                        " document.body.style.minHeight='auto';"
                        " document.body.style.background='transparent';"
                        " document.body.style.display='block'; }"
                    )
                page.screenshot(
                    path=str(screenshot_dir / f"page{page_number}.png"),
                    full_page=False,
                    omit_background=False,
                )
            browser.close()
    except Exception as exc:
        diagnostics.append({"severity": "warning", "code": "html-screenshot-failed", "message": str(exc)})
    return diagnostics
