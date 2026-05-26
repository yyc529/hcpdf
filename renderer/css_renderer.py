from __future__ import annotations

from model.assets import FontAsset


def css_rule(selector: str, properties: dict[str, str | float | int | None]) -> str:
    body = "\n".join(
        f"        {name}: {value};"
        for name, value in properties.items()
        if value is not None and value != ""
    )
    if not body:
        return ""
    return f"    {selector} {{\n{body}\n    }}"


def render_font_faces(fonts: list[FontAsset]) -> str:
    rules: list[str] = []
    for font in fonts:
        if not font.loadable or not font.path:
            continue
        rules.append(
            "@font-face{"
            f"font-family:\"{font.css_family}\";"
            f"src:url(\"{font.path}\");"
            "font-display:swap;"
            "}"
        )
    return "\n".join(rules)


def render_page_css(fonts: list[FontAsset]) -> str:
    return f"""
{render_font_faces(fonts)}
    body {{
      margin: 0;
      padding: 0;
      display: flex;
      overflow: hidden;
      align-items: center;
      min-height: 100vh;
      justify-content: center;
      background: #f3f4f6;
      font-family: Arial, "Microsoft YaHei", sans-serif;
    }}

    section {{
      position: relative;
      overflow: hidden;
      background: #ffffff;
    }}

    .pdf-layer {{
      position: absolute;
      inset: 0;
    }}

    .vector-layer-background {{
      z-index: 1;
      pointer-events: none;
    }}

    .content-layer {{
      z-index: 2;
      pointer-events: none;
    }}

    .vector-layer-foreground {{
      z-index: 4;
      pointer-events: none;
    }}

    .pdf-vector-svg {{
      position: absolute;
      overflow: visible;
      pointer-events: none;
    }}

    .pdf-vector-css {{
      pointer-events: none;
    }}

    .text-layer {{
      z-index: 5;
    }}

    .link-layer {{
      z-index: 6;
    }}

    .pdf-text-block,
    .pdf-text-line,
    .pdf-text-span {{
      position: absolute;
      box-sizing: border-box;
    }}

    .pdf-text-block,
    .pdf-text-line {{
      pointer-events: none;
    }}

    .pdf-text-span {{
      white-space: pre;
      line-height: 1;
      transform-origin: 0 0;
      pointer-events: none;
    }}

    .pdf-image {{
      position: absolute;
      object-fit: fill;
    }}

    .pdf-image-css-clip > .pdf-image-content {{
      width: 100%;
      height: 100%;
      display: block;
      object-fit: fill;
    }}

    .pdf-link {{
      position: absolute;
      display: block;
      background: transparent;
      text-decoration: none;
    }}

    .pdf-link:focus {{
      outline: 2px solid rgba(0, 122, 255, .7);
    }}

    .pdf-diagnostic-placeholder {{
      position: absolute;
      border: 0;
      color: transparent;
      background: transparent;
      pointer-events: none;
      overflow: hidden;
    }}

    .table-layer {{
      z-index: 7;
    }}

    .form-layer {{
      z-index: 8;
    }}

    .annotation-layer {{
      z-index: 9;
    }}

    .pdf-table {{
      position: absolute;
      border-collapse: collapse;
      table-layout: fixed;
      background: transparent;
      pointer-events: auto;
    }}

    .pdf-table td {{
      border: 1px solid rgba(0, 0, 0, .08);
      padding: 2px 4px;
      vertical-align: top;
      font-size: 11px;
      background: transparent;
      outline: 0;
    }}

    .pdf-table-sidecar {{
      pointer-events: none;
    }}

    .pdf-form {{
      position: absolute;
      box-sizing: border-box;
      background: rgba(255, 255, 0, .05);
      border: 1px solid rgba(0, 122, 255, .35);
      font: inherit;
    }}

    .pdf-annotation {{
      position: absolute;
      box-sizing: border-box;
      border: 1px dashed rgba(0, 122, 255, .35);
      background: rgba(255, 235, 59, .12);
      pointer-events: auto;
    }}

    .pdf-annotation[title]:hover::after {{
      content: attr(title);
      position: absolute;
      top: 100%;
      left: 0;
      padding: 4px 6px;
      background: #1f2937;
      color: #fff;
      font: 11px Arial;
      white-space: pre;
      z-index: 10;
    }}
""".strip()


def render_index_css() -> str:
    return """
        * {
            box-sizing: border-box;
        }

        body {
            margin: 0;
            padding: 24px;
            color: #1f2937;
            background: #f3f4f6;
            font-family: Arial, "Microsoft YaHei", sans-serif;
        }

        h1 {
            margin: 0;
            font-size: 24px;
            font-weight: 700;
        }

        .toolbar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
            margin-bottom: 20px;
        }

        .toolbar nav {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }

        .toolbar a {
            padding: 7px 12px;
            color: #1f2937;
            text-decoration: none;
            border: 1px solid #d1d5db;
            border-radius: 6px;
            background: #ffffff;
        }

        .toolbar a.active {
            color: #ffffff;
            border-color: #1f2937;
            background: #1f2937;
        }

        .slides {
            display: grid;
            gap: 24px;
            grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
        }

        .slide-card,
        .empty-card {
            overflow: hidden;
            background: #ffffff;
            border: 1px solid #d1d5db;
            border-radius: 8px;
            box-shadow: 0 8px 24px rgba(15, 23, 42, 0.08);
        }

        .slide-card header {
            padding: 10px 14px;
            font-size: 14px;
            font-weight: 700;
            border-bottom: 1px solid #e5e7eb;
            background: #ffffff;
        }

        .iframe-wrap,
        .screenshot-wrap {
            width: 100%;
            aspect-ratio: var(--page-ratio, 16 / 9);
            overflow: hidden;
            background: #ffffff;
        }

        .iframe-wrap iframe {
            display: block;
            width: var(--page-width, 1280px);
            height: var(--page-height, 720px);
            border: 0;
            transform: scale(var(--page-scale, 1));
            transform-origin: top left;
            background: #ffffff;
        }

        .screenshot-wrap img {
            display: block;
            width: 100%;
            height: 100%;
            object-fit: contain;
            background: #ffffff;
        }

        .compare-row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 14px;
            align-items: start;
        }

        .compare-pane {
            min-width: 0;
            overflow: hidden;
            background: #ffffff;
        }

        .empty-card {
            padding: 18px;
            grid-column: 1 / -1;
        }

        .empty-card p {
            margin: 0;
            color: #4b5563;
        }

        @media (min-width: 1400px) {
            .slides {
                grid-template-columns: repeat(auto-fit, minmax(560px, 1fr));
            }
        }
""".strip()
