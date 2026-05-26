from __future__ import annotations

import argparse
from pathlib import Path

from core.config import ConversionConfig
from core.converter import convert


# 不传命令行参数时使用这个固定测试 PDF，方便像 ppt2html 项目一样直接 F5 / python pdf2html.py 跑。
# 传入命令行参数时仍然优先使用命令行里的 PDF 路径。
#TEST_PDF_PATH = Path(r"D:/pk/【西财蓝1】演示文稿模板.pdf")
TEST_PDF_PATH = Path(r"D:/pk/北大PPT杂志风格(1).pdf")
#TEST_PDF_PATH = Path(r"D:/pk/ultrappt样本/ultrappt样本/B1U4 R&T 公开课.pdf")
#TEST_PDF_PATH = Path(r"D:/pk/蓝绿橙-精美逻辑图PPT模板.pdf")

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf2html",
        description="Convert PDF pages into editable, diagnosable HTML.",
    )
    parser.add_argument(
        "pdf",
        type=Path,
        nargs="?",
        help=f"Input PDF file. Defaults to {TEST_PDF_PATH} when omitted.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output directory. Defaults to output_<pdf-stem>.",
    )
    parser.add_argument(
        "--html-screenshots",
        action="store_true",
        help="Capture HTML screenshots with Playwright when browser dependencies are available.",
    )
    parser.add_argument(
        "--no-fonts",
        action="store_true",
        help="Skip embedded font extraction.",
    )
    parser.add_argument(
        "--tables",
        action="store_true",
        help="Run optional pdfplumber table candidate extraction.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    pdf_path = args.pdf or TEST_PDF_PATH
    config = ConversionConfig(
        generate_html_screenshots=args.html_screenshots,
        extract_fonts=not args.no_fonts,
        extract_tables=args.tables,
    )
    result = convert(pdf_path, args.output, config)
    print(f"Converted {result.source_path} -> {result.output_dir}")
    print(f"Pages: {result.page_count}")
    print(f"Diagnostics: {result.output_dir / 'assets' / 'debug' / 'document.diagnostics.json'}")


if __name__ == "__main__":
    main()
