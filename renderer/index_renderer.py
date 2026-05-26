from __future__ import annotations

from html import escape
from pathlib import Path

from model.document import DocumentModel
from renderer.css_renderer import render_index_css


def render_index(document: DocumentModel) -> str:
    cards: list[str] = []
    for page in document.pages:
        cards.append(
            f"""\
        <article class="slide-card">
            <header>Page {page.page_index}</header>
            <div class="iframe-wrap" data-width="{page.width_px:.3f}" data-height="{page.height_px:.3f}">
                <iframe src="page{page.page_index}.html" title="Page {page.page_index}" loading="lazy"></iframe>
            </div>
        </article>"""
        )
    return _shell(document, "HTML iframe", "index", "\n".join(cards))


def render_original(document: DocumentModel) -> str:
    cards: list[str] = []
    for page in document.pages:
        src = f"assets/original_pages/page{page.page_index}.png"
        cards.append(
            f"""\
        <article class="slide-card">
            <header>Page {page.page_index}</header>
            <div class="screenshot-wrap" data-width="{page.width_px:.3f}" data-height="{page.height_px:.3f}">
                <img src="{escape(src)}" alt="Original Page {page.page_index}">
            </div>
        </article>"""
        )
    return _shell(document, "Original screenshots", "original", "\n".join(cards))


def render_compare(document: DocumentModel) -> str:
    cards: list[str] = []
    for page in document.pages:
        src = f"assets/original_pages/page{page.page_index}.png"
        cards.append(
            f"""\
        <article class="slide-card">
            <header>Page {page.page_index}</header>
            <div class="compare-row">
                <div class="compare-pane">
                    <div class="screenshot-wrap" data-width="{page.width_px:.3f}" data-height="{page.height_px:.3f}">
                        <img src="{escape(src)}" alt="Original Page {page.page_index}">
                    </div>
                </div>
                <div class="compare-pane">
                    <div class="iframe-wrap" data-width="{page.width_px:.3f}" data-height="{page.height_px:.3f}">
                        <iframe src="page{page.page_index}.html" title="Page {page.page_index}" loading="lazy"></iframe>
                    </div>
                </div>
            </div>
        </article>"""
        )
    return _shell(document, "Compare", "compare", "\n".join(cards))


def _shell(document: DocumentModel, label: str, active: str, body: str) -> str:
    source_name = escape(Path(document.source_path).name)
    title = f"{source_name} - {label}"
    total_pages = len(document.pages)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
{render_index_css()}
    </style>
    <script>
        function resizePageFrames() {{
            document.querySelectorAll('.iframe-wrap, .screenshot-wrap').forEach(function (wrap) {{
                const width = Number(wrap.dataset.width || 1280);
                const height = Number(wrap.dataset.height || 720);
                wrap.style.setProperty('--page-width', width + 'px');
                wrap.style.setProperty('--page-height', height + 'px');
                wrap.style.setProperty('--page-ratio', width + ' / ' + height);
                wrap.style.setProperty('--page-scale', wrap.clientWidth / width);
            }});
        }}

        window.addEventListener('load', resizePageFrames);
        window.addEventListener('resize', resizePageFrames);

        if ('ResizeObserver' in window) {{
            const observer = new ResizeObserver(resizePageFrames);
            window.addEventListener('DOMContentLoaded', function () {{
                document.querySelectorAll('.iframe-wrap, .screenshot-wrap').forEach(function (wrap) {{
                    observer.observe(wrap);
                }});
            }});
        }}
    </script>
</head>
<body>
    <header class="toolbar">
        <h1>{source_name} - {total_pages} pages</h1>
        <nav>
            <a class="{"active" if active == "index" else ""}" href="index.html">HTML iframe</a>
            <a class="{"active" if active == "original" else ""}" href="original.html">Original screenshots</a>
            <a class="{"active" if active == "compare" else ""}" href="compare.html">Compare</a>
        </nav>
    </header>
    <main class="slides">
{body}
    </main>
</body>
</html>
"""
