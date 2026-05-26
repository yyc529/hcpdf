from __future__ import annotations

import fitz

from model.elements import AnnotationElement, FormElement, LinkElement
from model.geometry import BBox


def parse_links(page: fitz.Page, page_index: int) -> list[LinkElement]:
    elements: list[LinkElement] = []
    try:
        links = page.get_links()
    except Exception:
        return elements
    for link_index, link in enumerate(links):
        rect = link.get("from")
        if not rect:
            continue
        href = link.get("uri") or ""
        target_page = link.get("page")
        if not href and target_page is not None:
            href = f"page{int(target_page) + 1}.html"
        elements.append(
            LinkElement(
                stable_id=f"pdf-page-{page_index + 1}-link-{link_index}",
                type="link",
                page_index=page_index + 1,
                bbox=BBox(float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)),
                z_index=0,
                source_index=link_index,
                render_mode="semantic-html",
                editable=False,
                source={"source": "page.get_links", "kind": link.get("kind")},
                href=href,
                target_page=(int(target_page) + 1) if target_page is not None else None,
            )
        )
    return elements


_WIDGET_TYPE_MAP = {
    getattr(fitz, "PDF_WIDGET_TYPE_TEXT", 2): "text",
    getattr(fitz, "PDF_WIDGET_TYPE_CHECKBOX", 3): "checkbox",
    getattr(fitz, "PDF_WIDGET_TYPE_RADIOBUTTON", 4): "radio",
    getattr(fitz, "PDF_WIDGET_TYPE_LISTBOX", 5): "listbox",
    getattr(fitz, "PDF_WIDGET_TYPE_COMBOBOX", 6): "combobox",
    getattr(fitz, "PDF_WIDGET_TYPE_SIGNATURE", 7): "signature",
    getattr(fitz, "PDF_WIDGET_TYPE_BUTTON", 1): "button",
}


def parse_form_widgets(page: fitz.Page, page_index: int) -> list[FormElement]:
    elements: list[FormElement] = []
    widgets_iter = None
    try:
        widgets_iter = list(page.widgets() or [])
    except Exception:
        widgets_iter = []
    for widget_index, widget in enumerate(widgets_iter):
        rect = getattr(widget, "rect", None)
        if rect is None:
            continue
        field_type = _WIDGET_TYPE_MAP.get(
            getattr(widget, "field_type", None), "text"
        )
        diagnostics = []
        if field_type == "signature":
            diagnostics.append(
                {
                    "severity": "info",
                    "code": "signature-widget",
                    "message": "Signature widget kept as semantic placeholder.",
                }
            )
        elements.append(
            FormElement(
                stable_id=f"pdf-page-{page_index + 1}-form-{widget_index}",
                type="form",
                page_index=page_index + 1,
                bbox=BBox(float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)),
                z_index=0,
                source_index=widget_index,
                render_mode="semantic-html",
                editable=field_type != "signature",
                source={
                    "source": "page.widgets",
                    "field_type_raw": getattr(widget, "field_type", None),
                },
                diagnostics=diagnostics,
                field_type=field_type,
                field_name=getattr(widget, "field_name", "") or "",
                value=str(getattr(widget, "field_value", "") or ""),
                checked=bool(getattr(widget, "field_value", "")) if field_type in {"checkbox", "radio"} else False,
                options=list(getattr(widget, "choice_values", []) or []),
                flags=int(getattr(widget, "field_flags", 0) or 0),
            )
        )
    return elements


_ANNOT_TYPE_NAMES = {
    0: "text",
    1: "link",
    2: "freetext",
    3: "line",
    4: "square",
    5: "circle",
    6: "polygon",
    7: "polyline",
    8: "highlight",
    9: "underline",
    10: "squiggly",
    11: "strikeout",
    13: "stamp",
    14: "caret",
    15: "ink",
    16: "popup",
    17: "fileattachment",
    18: "sound",
    19: "movie",
    20: "widget",
    21: "screen",
    22: "printermark",
    23: "trapnet",
    24: "watermark",
    25: "3d",
}


def parse_annotations(page: fitz.Page, page_index: int) -> list[AnnotationElement]:
    elements: list[AnnotationElement] = []
    annot = None
    try:
        annot = page.first_annot
    except Exception:
        annot = None
    index = 0
    while annot is not None:
        try:
            atype = annot.type
            type_index = int(atype[0]) if atype else -1
            type_name = _ANNOT_TYPE_NAMES.get(type_index, atype[1] if atype else "unknown")
            if type_name in {"link", "widget"}:
                annot = annot.next
                continue
            rect = annot.rect
            info = annot.info or {}
            color_value = None
            try:
                colors = annot.colors or {}
                stroke = colors.get("stroke") or colors.get("fill")
                if stroke and len(stroke) >= 3:
                    r, g, b = stroke[0], stroke[1], stroke[2]
                    color_value = f"rgb({int(r * 255)},{int(g * 255)},{int(b * 255)})"
            except Exception:
                color_value = None
            elements.append(
                AnnotationElement(
                    stable_id=f"pdf-page-{page_index + 1}-annot-{index}",
                    type="annotation",
                    page_index=page_index + 1,
                    bbox=BBox(float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)),
                    z_index=0,
                    source_index=index,
                    render_mode="semantic-html",
                    editable=False,
                    source={
                        "source": "page.annotations",
                        "type_index": type_index,
                        "type_raw": atype,
                    },
                    annot_type=type_name,
                    contents=info.get("content", "") or "",
                    title=info.get("title", "") or "",
                    color=color_value,
                    icon=info.get("name", "") or None,
                )
            )
            index += 1
            annot = annot.next
        except Exception:
            break
    return elements
