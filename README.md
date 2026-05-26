# pdf2html

Python 版 PDF 转可编辑 HTML MVP。项目按 `pdf.md` 的分层结构搭建：PyMuPDF 负责主解析，输出自建中间模型，再由 renderer 生成绝对定位的 HTML / SVG / 图片资产 / QA 对比页。

## 当前能力

- 页面尺寸、cropbox、rotation 基础信息进入 PageModel。
- 文本按 block / line / span 输出为真实 HTML 文本节点，可选择、可编辑。
- 图片按 xref 提取为独立资产，并按 PDF bbox 绝对定位。
- 简单矢量绘制通过 `get_drawings()` 输出到页面 SVG 层。
- link annotation 输出为透明 `<a>` 热区。
- 嵌入字体做基础提取和 `@font-face` 输出，失败时走系统 fallback。
- 每页导出原始 PDF 渲染 PNG，生成 `index.html` / `original.html` / `compare.html`。
- 输出 debug model、diagnostics、contract report。

## 安装

```bash
python -m pip install -r requirements.txt
```

如果要启用 HTML 截图，还需要安装 Playwright 浏览器：

```bash
python -m playwright install chromium
```

## 使用

```bash
python pdf2html.py input.pdf
```

指定输出目录：

```bash
python pdf2html.py input.pdf -o output_input
```

启用可选 HTML 截图：

```bash
python pdf2html.py input.pdf --html-screenshots
```

启用 pdfplumber 表格候选提取：

```bash
python pdf2html.py input.pdf --tables
```

## 输出结构

```text
output_<filename>/
  index.html
  original.html
  compare.html
  page1.html
  page2.html
  assets/
    images/
    fonts/
    original_pages/
    html_pages/
    debug/
      page1.model.json
      page1.diagnostics.json
      document.model.json
      document.diagnostics.json
      contract.json
      report.json
```

## 设计边界

第一版不默认整页截图，也不把文字烘焙成图片。原始页面 PNG 只用于 QA 和人工对比。复杂透明度、高级 mask、完整表格语义、完整阅读顺序、OCR 暂时作为后续阶段扩展。
