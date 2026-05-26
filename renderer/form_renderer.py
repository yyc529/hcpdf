from __future__ import annotations

from html import escape

from core.units import bbox_to_px
from model.elements import AnnotationElement, FormElement
from renderer.css_renderer import css_rule


def _common_attrs(element, source_type: str, extra: str = "") -> str:
    return f'id="{escape(element.stable_id)}"' + (f" {extra}" if extra else "")


def render_form(form: FormElement, scale: float) -> str:
    common = _common_attrs(
        form,
        "form",
        "",
    )
    if form.field_type == "checkbox":
        checked = " checked" if form.checked else ""
        return (
            f'<input {common} class="pdf-form pdf-form-checkbox" '
            f'type="checkbox"{checked} name="{escape(form.field_name)}" />'
        )
    if form.field_type == "radio":
        checked = " checked" if form.checked else ""
        return (
            f'<input {common} class="pdf-form pdf-form-radio" '
            f'type="radio"{checked} name="{escape(form.field_name)}" '
            f'value="{escape(form.value)}" />'
        )
    if form.field_type in {"listbox", "combobox"}:
        options = "".join(
            f'<option value="{escape(opt)}"'
            f'{" selected" if opt == form.value else ""}>{escape(opt)}</option>'
            for opt in form.options
        )
        return (
            f'<select {common} class="pdf-form pdf-form-select" '
            f'name="{escape(form.field_name)}">{options}</select>'
        )
    if form.field_type == "signature":
        return (
            f'<div {common} class="pdf-form pdf-form-signature" '
            f'title="signature placeholder"></div>'
        )
    return (
        f'<input {common} class="pdf-form pdf-form-text" type="text" '
        f'name="{escape(form.field_name)}" value="{escape(form.value)}" />'
    )


def render_form_css(form: FormElement, scale: float) -> str:
    box = bbox_to_px(form.bbox, scale)
    return css_rule(
        f"#{form.stable_id}",
        {
            "left": f"{box.x0:.3f}px",
            "top": f"{box.y0:.3f}px",
            "width": f"{max(0, box.width):.3f}px",
            "height": f"{max(0, box.height):.3f}px",
        },
    )


def render_annotation(annot: AnnotationElement, scale: float) -> str:
    title = annot.contents or annot.title or annot.annot_type
    extra = (
        f'title="{escape(title)}"'
    )
    common = _common_attrs(annot, "annotation", extra)
    return (
        f'<div {common} class="pdf-annotation pdf-annotation-{escape(annot.annot_type)}">'
        f'</div>'
    )


def render_annotation_css(annot: AnnotationElement, scale: float) -> str:
    box = bbox_to_px(annot.bbox, scale)
    props = {
        "left": f"{box.x0:.3f}px",
        "top": f"{box.y0:.3f}px",
        "width": f"{max(0, box.width):.3f}px",
        "height": f"{max(0, box.height):.3f}px",
    }
    if annot.color:
        props["border-color"] = annot.color
    return css_rule(f"#{annot.stable_id}", props)
