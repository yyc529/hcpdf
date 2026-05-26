from __future__ import annotations

from html import escape

from model.assets import FontAsset
from model.elements import (
    AnnotationElement,
    FormElement,
    GradientFillElement,
    ImageElement,
    LinkElement,
    TableCandidate,
    TextBlockElement,
    VectorElement,
)
from model.page import PageModel
from renderer.css_renderer import css_rule, render_page_css
from renderer.form_renderer import (
    render_annotation,
    render_annotation_css,
    render_form,
    render_form_css,
)
from renderer.gradient_renderer import render_gradient_css, render_gradient_svg
from renderer.image_renderer import render_image, render_image_css
from renderer.svg_renderer import classify_vectors, render_vector_layer
from renderer.table_renderer import render_table, render_table_css
from renderer.text_renderer import render_text_block, render_text_block_css
from core.units import bbox_to_px


def render_link(link: LinkElement, scale: float) -> str:
    href = link.href or "#"
    return (
        f'<a id="{escape(link.stable_id)}" class="pdf-link" href="{escape(href)}" '
        f'></a>'
    )


def render_link_css(link: LinkElement, scale: float) -> str:
    box = bbox_to_px(link.bbox, scale)
    return css_rule(
        f"#{link.stable_id}",
        {
            "left": f"{box.x0:.3f}px",
            "top": f"{box.y0:.3f}px",
            "width": f"{max(0, box.width):.3f}px",
            "height": f"{max(0, box.height):.3f}px",
        },
    )


def render_page_html(page: PageModel, fonts: list[FontAsset]) -> str:
    vectors = [item for item in page.elements if isinstance(item, VectorElement)]
    _vec_bg, _vec_fg = classify_vectors(vectors, page)
    images = [item for item in page.elements if isinstance(item, ImageElement)]
    gradients = [item for item in page.elements if isinstance(item, GradientFillElement)]
    # Phase 7 needs images and pattern-fill gradients to interleave by actual
    # PDF paint order — a Pattern fill drawn *before* an image must sit below
    # it, after-image patterns must sit above. We merge both lists and sort
    # by ``paint_seqno`` (synthesised so vectors/images/gradients share one
    # monotonic timeline). DOM order inside the content layer then gives the
    # correct z-stack without per-element ``z-index``.
    content_stack: list = sorted(
        images + gradients, key=lambda e: getattr(e, "paint_seqno", 0)
    )
    texts = [item for item in page.elements if isinstance(item, TextBlockElement)]
    links = [item for item in page.elements if isinstance(item, LinkElement)]
    forms = [item for item in page.elements if isinstance(item, FormElement)]
    annotations = [item for item in page.elements if isinstance(item, AnnotationElement)]
    tables = [item for item in page.elements if isinstance(item, TableCandidate)]
    scale = page.scale_factor
    element_styles = "\n".join(
        rule
        for rule in [
            css_rule(
                f"#{page.stable_id}",
                {
                    "width": f"{page.width_px:.3f}px",
                    "height": f"{page.height_px:.3f}px",
                },
            ),
            *(render_image_css(image, scale) for image in images),
            *(render_gradient_css(grad, scale) for grad in gradients),
            *(render_text_block_css(text, scale) for text in texts),
            *(render_link_css(link, scale) for link in links),
            *(render_form_css(form, scale) for form in forms),
            *(render_annotation_css(annot, scale) for annot in annotations),
            *(render_table_css(table, scale) for table in tables),
        ]
        if rule
    )
    table_html = "".join(render_table(table, scale) for table in tables)
    form_html = "".join(render_form(form, scale) for form in forms)
    annotation_html = "".join(render_annotation(annot, scale) for annot in annotations)
    content_html_parts: list[str] = []
    for el in content_stack:
        if isinstance(el, GradientFillElement):
            content_html_parts.append(render_gradient_svg(el, page))
        elif isinstance(el, ImageElement):
            content_html_parts.append(render_image(el, scale))
    content_layer = (
        f'<div class="pdf-layer content-layer">{"".join(content_html_parts)}</div>'
        if content_html_parts
        else ""
    )
    text_layer = (
        f'<div class="pdf-layer text-layer">{"".join(render_text_block(text, scale) for text in texts)}</div>'
        if texts
        else ""
    )
    link_layer = (
        f'<div class="pdf-layer link-layer">{"".join(render_link(link, scale) for link in links)}</div>'
        if links
        else ""
    )
    table_layer = f'<div class="pdf-layer table-layer">{table_html}</div>' if table_html else ""
    form_layer = f'<div class="pdf-layer form-layer">{form_html}</div>' if form_html else ""
    annotation_layer = (
        f'<div class="pdf-layer annotation-layer">{annotation_html}</div>'
        if annotation_html
        else ""
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PDF page {page.page_index}</title>
    <style type="text/css">
{render_page_css(fonts)}

{element_styles}
    </style>
</head>
<body>
    <section id="{escape(page.stable_id)}" class="pdf-page">
        {render_vector_layer(page, _vec_bg, role="background")}
        {content_layer}
        {render_vector_layer(page, _vec_fg, role="foreground")}
        {text_layer}
        {link_layer}
        {table_layer}
        {form_layer}
        {annotation_layer}
    </section>
</body>
</html>
"""
