# Python 版 PDF 转 HTML 方案

本文档是沟通方案，不包含实现代码。目标是参考现有 `ppt2html` 项目的分层思路，设计一个 Python 版 `pdf2html`：用 PyMuPDF / pdfplumber 提取文字、图片、矢量路径、坐标和字体信息，再自己生成可检查、可编辑、可回归的静态 HTML。

---

## 1. 项目目标

### 核心目标

1. **视觉复刻**：HTML 在浏览器里尽可能接近原 PDF 页面。
2. **AI / 人类可编辑**：文字尽量保持真实文本节点，图片保留原始资源，矢量图尽量输出 SVG，而不是整页截图。
3. **稳定可追踪**：每个页面、文本块、图片、路径都有稳定 id 和来源信息，方便后续 AI 工具定位修改。
4. **可回归验证**：支持导出原始 PDF 页面截图、HTML 截图、diff 报告，避免修一个模板坏一片。

### 非目标

- 不追求恢复 PDF 原始编辑语义。PDF 本质是最终版排版格式，通常没有 PPT 那样的 shape / placeholder / master 语义。
- 不承诺自动理解所有表格、目录、段落层级。可以做启发式识别，但底层仍以坐标级复刻为主。
- 不默认整页 raster bake。整页截图只能作为调试基准或极端 fallback。

---

## 2. 和 PPT 转 HTML 的核心差异

PPT 是面向编辑的源格式，PDF 是面向呈现的终格式。这个差异决定了 PDF 转 HTML 的架构要更偏“版面重建”。

| 维度 | PPT → HTML | PDF → HTML |
|---|---|---|
| 源语义 | shape、text frame、theme、master、layout 较丰富 | 多数只是绘制指令、文字 glyph、图片、路径 |
| 坐标体系 | EMU，需要按 slide 尺寸映射到 1280×720 | PDF point，页面尺寸可变，需要按 page box 映射 |
| 字体 | 可读 run 字体，可能有嵌入字体 | 常见 subset font，字体名可能被改写，ToUnicode 可能缺失 |
| 文字结构 | paragraph / run 相对明确 | 只有 char/span/line/block，段落需重建 |
| 图形 | OOXML shape 可反推几何语义 | 多为 path、rect、curve、clip、image mask |
| 难点 | 继承链、主题色、几何、占位符 | 字体、文字顺序、透明度/混合、clip/mask、复杂矢量 |
| QA 标准 | 原 PPT 截图 vs HTML 截图 | 原 PDF 渲染图 vs HTML 截图 |

结论：PDF 项目也要走“解析层 / 中间模型 / 渲染层 / QA 层”，但中间模型应更贴近 PDF 绘制对象，而不是 PPT shape 语义。

---

## 3. 推荐技术栈

### 主解析引擎：PyMuPDF

推荐作为主引擎，原因：

- 速度快，适合批量 PDF。
- 可以提取页面尺寸、文字块、字符坐标、图片、字体、矢量绘制路径。
- 可以直接渲染原始页面截图，作为 QA 标准答案。
- 对透明度、图片 mask、页面 rotation/cropbox 的处理比很多纯 Python PDF 库稳定。

主要使用能力：

- 页面尺寸、rotation、cropbox / mediabox
- `get_text("rawdict")` / `get_text("dict")`：文字 block / line / span / char
- `get_drawings()`：线条、矩形、曲线、填充、描边、透明度
- 图片提取与页面图片 bbox
- 字体提取与字体元数据
- 页面 pixmap 渲染，用于原图截图和 fallback

### 辅助解析引擎：pdfplumber

pdfplumber 不作为主渲染来源，而作为辅助语义提取：

- 表格识别
- 文本阅读顺序辅助
- words / chars / rects / curves 的交叉验证
- 对部分 PyMuPDF 提取异常的页面做 fallback

### 其他依赖建议

| 依赖 | 用途 |
|---|---|
| Pillow | 图片格式转换、透明通道处理、截图 diff 预处理 |
| fontTools | 字体名称修正、subset font 检查、字体格式识别 |
| playwright | HTML 页面截图，做视觉回归 |
| scikit-image / opencv-python | SSIM / pixel diff，可选 |
| beautifulsoup4 / lxml | HTML contract 检查，可选 |

---

## 4. 总体架构

参考 `ppt2html` 的分层编译器思路，建议新项目目录如下：

```text
pdf2html/
  pdf2html.py
  README.md
  pdf.md
  requirements.txt

  core/
    converter.py              # convert(pdf) 主流程
    config.py                 # 输出尺寸、字体策略、fallback 策略
    context.py                # DocumentContext / PageContext
    units.py                  # point -> css px，页面 box 归一化
    output_contract.py        # id / class / data-* 契约
    diagnostics.py            # 元素级诊断
    errors.py

  parser/
    document_parser.py        # PDF 文档级元数据、页列表
    page_parser.py            # 单页解析入口
    text_parser.py            # block / line / span / char
    image_parser.py           # image xref、bbox、mask、alpha
    vector_parser.py          # path / rect / curve / stroke / fill
    annotation_parser.py      # link / form / annotation
    table_parser.py           # 基于 pdfplumber 的可选表格识别

  model/
    document.py
    page.py
    elements.py               # TextBlock / ImageElement / VectorElement 等
    styles.py
    geometry.py
    assets.py
    diagnostics.py

  resolver/
    font_resolver.py          # 嵌入字体、subset 名称、fallback
    color_resolver.py         # PDF 色值、透明度、colorspace
    transform_resolver.py     # PDF matrix / rotation / cropbox
    text_flow_resolver.py     # 阅读顺序、行合并、段落重建
    layer_resolver.py         # z-index、绘制顺序、遮罩关系

  renderer/
    html_renderer.py          # 单页 HTML
    css_renderer.py
    svg_renderer.py           # 矢量路径 SVG
    text_renderer.py
    image_renderer.py
    index_renderer.py         # iframe 总览 / 原图总览 / 对比页
    bake_fallback_renderer.py

  assets/
    image_pipeline.py         # 原图提取、mask 合成、格式转换
    font_pipeline.py          # 字体提取、@font-face
    cache.py                  # 按 xref/hash 去重
    naming.py                 # 稳定文件命名

  qa/
    screenshot.py             # PDF 原图 + HTML 截图
    visual_compare.py
    contract_check.py
    editability_check.py
    report.py
    regression.py

  docs/
    AI_OUTPUT_CONTRACT.md
    SUPPORTED_FEATURES.md
    DEBUGGING_GUIDE.md
```

---

## 5. 数据流

```text
PDF 文件
  -> core.converter.convert(pdf)
       打开文档
       建立 DocumentContext
       导出原始 PDF 页面截图
       for each page:
         建立 PageContext
         parser.page_parser.parse_page(page)
           text_parser       -> TextBlock / TextLine / TextSpan / Char
           image_parser      -> ImageElement
           vector_parser     -> VectorElement / SvgPath
           annotation_parser -> LinkElement / FormElement
           table_parser      -> TableCandidate（可选）
         resolver 层归一化：
           坐标 / 字体 / 颜色 / 透明度 / 阅读顺序 / z-index
         renderer 输出：
           pageN.html
           assets/images/*
           assets/fonts/*
           debug model / diagnostics
       输出：
         index.html
         original.html
         compare.html
```

---

## 6. 中间模型设计

### DocumentModel

记录文档级信息：

- source path
- page count
- metadata
- page models
- assets
- diagnostics

### PageModel

记录单页信息：

- page index
- original page size
- media box / crop box
- rotation
- scale factor
- elements
- background
- diagnostics

### ElementModel

所有元素共享字段：

- stable id
- type
- page index
- bbox
- transform
- z-index / drawing order
- opacity
- source reference
- render mode
- editable
- diagnostics

元素类型建议：

| 类型 | 输出策略 |
|---|---|
| TextElement | HTML 文本，绝对定位 |
| ImageElement | `<img>` 或 SVG `<image>`，保留原图资源 |
| VectorElement | SVG path / rect / line |
| TableElement | 可选语义 `<table>`，同时保留坐标文本 |
| LinkElement | HTML `<a>` 热区 |
| FormElement | input / checkbox 或 fallback |
| BakedElement | 局部截图 fallback |

---

## 7. HTML 输出策略

### 页面容器

每页输出一个固定尺寸容器，尺寸来自 PDF 页面尺寸：

- PDF point 转 CSS px：建议默认 `1pt = 96 / 72 px`
- 保留每页独立宽高，不强行统一成 1280×720
- `index.html` 使用 iframe 等比缩放，沿用当前 `ppt2html` 的总览思路

### 层结构

建议每页分 3 层：

1. `vector-layer`：SVG，承载背景色块、线条、路径、复杂矢量。
2. `image-layer`：图片资源，按 bbox 绝对定位。
3. `text-layer`：真实文本节点，按行/span 绝对定位。

这样做的好处：

- 文字可选择、可编辑。
- 矢量路径不散成大量 div。
- 图片和文字不会互相污染。
- z-index 可以按 PDF 绘制顺序进一步细化。

### render mode

沿用 `ppt2html` 的思路，但换成 PDF 语境：

| render mode | 含义 |
|---|---|
| `semantic-html` | 文本、链接、表格等真实 HTML |
| `semantic-svg` | 矢量 path、线条、形状 |
| `asset-with-vector-effects` | 原图 + clip/mask/filter |
| `editable-baked` | 局部截图 + sidecar 数据 |
| `diagnostic-placeholder` | 数据缺失但可诊断 |
| `unsupported-baked` | 极端复杂对象兜底 |

---

## 8. 文字重建方案

PDF 文字是最大风险点之一。方案要分层做。

### 第一阶段：视觉优先的文字定位

使用 PyMuPDF 的 span / char 信息：

- 读取每个 span 的文本、字体名、字号、颜色、bbox。
- 按 span 输出绝对定位 HTML。
- 对同一行内连续 span 做有限合并，但保留来源信息。
- 对旋转文字、竖排文字、字符间距异常的内容，必要时退到 char-level 输出。

### 第二阶段：阅读顺序和段落重建

使用启发式规则：

- 按列检测、y 坐标、x 坐标、字号、行距合并为段落。
- 对多栏 PDF，不简单全页按 y 排序。
- 引入 `text_flow_resolver`，把“视觉顺序”和“阅读顺序”分开保存。
- HTML 初始视觉复刻以坐标为准，AI 编辑时可参考阅读顺序。

### 字体策略

优先级：

1. 提取 PDF 嵌入字体，生成 `@font-face`。
2. subset font 名称去前缀，建立真实字体名映射。
3. 如果无法提取或浏览器无法加载，使用系统 fallback。
4. 对字体缺失导致的换行差异，用 diagnostics 标记，不做整段烘焙。

需要特别处理：

- ToUnicode 缺失导致文字乱码。
- 字体 subset 名称类似 `ABCDEE+SomeFont`。
- CID 字体、CJK 字体、Type3 字体。
- ligature，例如 `fi` / `fl`。
- 字距、字符缩放、水平/垂直 writing mode。

---

## 9. 图片处理方案

### 图片提取

以 PyMuPDF 为主：

- 按 xref 提取原图。
- 按页面出现位置记录 bbox。
- 同一 xref 多处使用时只保存一份资源。
- 稳定命名：`page{n}_img{xref}` 或基于 xref/hash。

### 透明度和 mask

必须重点处理：

- soft mask
- stencil mask
- alpha channel
- image opacity
- clip path

建议策略：

- 能保留原图 alpha 就保留。
- 图片本身 + mask 可以生成 derived PNG，但必须记录 source image 和 mask 来源。
- 不把整页背景和图片合并成大图。

### 输出

普通图片用 `<img>` 绝对定位。

有 clip/mask/filter 的图片可以放进 SVG：

- `<image>`
- `<clipPath>`
- `<mask>`
- `<filter>`

---

## 10. 矢量图形处理方案

PDF 中的线条、形状、曲线通常没有“这是六边形/箭头”的语义，最可靠的表达是 SVG path。

### 解析

使用 PyMuPDF `get_drawings()` 提取：

- path commands
- stroke color
- fill color
- stroke width
- dash
- line cap / join
- opacity
- even-odd fill rule
- bbox

### 输出

统一输出到每页的 SVG 层：

- rect / line / curve / path 尽量保持 SVG 原生元素。
- 复杂路径用 `<path d="...">`。
- 每个 path 带稳定 id 和 data-source。
- 多个同样样式、连续绘制且无文字夹杂的 path 可以合并为 group。

### 已知难点

- clipping path 栈
- blend mode
- soft mask
- transparency group
- shading / gradient
- pattern fill
- overprint

这些功能第一版可以先诊断标记，复杂时使用局部 fallback。

---

## 11. 表格和结构化内容

PDF 表格没有天然语义。建议分两层：

### 视觉层

先按普通文本 + 线条 + 背景块输出，保证视觉准确。

### 语义层

使用 pdfplumber 识别 table candidate：

- 如果置信度高，额外输出可编辑 `<table>`。
- 如果置信度低，只在 diagnostics 中记录，不强行替换视觉层。
- 表格识别结果可作为 sidecar 数据，供 AI 编辑参考。

原则：不要为了语义表格破坏视觉复刻。

---

## 12. 注释、链接、表单

第一版建议支持：

- link annotation：输出透明 `<a>` 热区。
- text annotation：可作为注释图标或 diagnostics。
- form field：简单 input / checkbox 可以转 HTML 表单控件。

复杂注释和签名：

- 保留视觉外观。
- 输出 diagnostics。
- 必要时局部 baked。

---

## 13. 资源和命名策略

输出结构建议：

```text
output_<filename>/
  index.html
  original.html
  compare.html
  page1.html
  page2.html
  ...
  assets/
    images/
    fonts/
    original_pages/
    debug/
      page1.model.json
      page1.diagnostics.json
```

命名要求：

- 同一个 PDF 多次转换，文件名稳定。
- 图片按 xref/hash 去重。
- 字体按真实字体名或字体 hash 去重。
- derived asset 必须记录来源和处理链。

---

## 14. AI 可编辑输出契约

建议从一开始就实现，不要等后期补。

### 稳定 id

示例规则：

- page：`pdf-page-{pageIndex}`
- text block：`pdf-page-{pageIndex}-text-{blockIndex}`
- line：`pdf-page-{pageIndex}-text-{blockIndex}-line-{lineIndex}`
- span：`pdf-page-{pageIndex}-text-{blockIndex}-line-{lineIndex}-span-{spanIndex}`
- image：`pdf-page-{pageIndex}-image-{xref}-{occurrenceIndex}`
- vector：`pdf-page-{pageIndex}-path-{drawIndex}`

### class

建议统一：

- `.pdf-page`
- `.pdf-text`
- `.pdf-text-block`
- `.pdf-text-line`
- `.pdf-text-span`
- `.pdf-image`
- `.pdf-vector`
- `.pdf-link`
- `.pdf-table`
- `.pdf-baked`

### data 字段

每个元素至少包含：

- `data-page`
- `data-source-type`
- `data-source-index`
- `data-bbox`
- `data-render-mode`
- `data-editable`
- `data-z`

文本额外：

- `data-font-name`
- `data-font-size`
- `data-writing-mode`
- `data-text-flow`

图片额外：

- `data-xref`
- `data-source-asset`
- `data-mask-xref`
- `data-effect-pipeline`

---

## 15. QA 和回归方案

第一天就应该建立 QA，因为 PDF 转 HTML 的视觉误差会非常细碎。

### 原图基准

用 PyMuPDF 把每页渲染为 PNG：

- 默认 144 DPI 或 192 DPI。
- 保持页面 cropbox / rotation。
- 输出到 `assets/original_pages/`。

### HTML 截图

用 Playwright 打开每个 `pageN.html` 截图：

- viewport 等于页面 CSS 尺寸。
- 禁止浏览器缩放。
- 等字体加载完成后截图。

### 指标

| 指标 | 目标 |
|---|---|
| pixel diff / SSIM | 趋势性改善 |
| text_editable_ratio | 尽量接近 1 |
| baked_page_ratio | 0 |
| baked_area_ratio | 初期可放宽，后期收紧 |
| missing_font_count | 趋近 0 |
| image_extract_failure_count | 0 |
| contract violation count | 0 |

### 总览页面

沿用当前项目经验，生成三个页面：

- `index.html`：所有 HTML page iframe 总览。
- `original.html`：原始 PDF 页面截图总览。
- `compare.html`：左原图，右 HTML iframe，方便人工快速扫问题。

---

## 16. Fallback 策略

不能把 fallback 当常规路径。建议分级：

### 允许

- 单个复杂图片带 mask 合成为 derived PNG。
- 单个复杂 transparency group 局部截图。
- 无法解析的 Type3 字体局部 baked，但需要 sidecar 原始文本。
- 表格语义识别失败时退回视觉层。

### 禁止

- 默认整页截图。
- 文字直接转图片。
- 图片和文字合并成背景图。
- 无 diagnostics 的黑盒 baked。

### fallback 必须记录

- reason
- source bbox
- source objects
- lost features
- whether editable
- suggested future parser capability

---

## 17. 开发阶段规划

### Phase 0：基线和契约

目标：先能衡量，再开始修。

任务：

- 建立输出契约。
- 建立 `index.html` / `original.html` / `compare.html`。
- 建立 PDF 原图截图和 HTML 截图。
- 建立 diagnostics JSON。
- 用 5-10 个真实 PDF 做回归集。

验收：

- 任意 PDF 转换后都有原图、HTML、对比页。
- contract check 能发现缺失 id / data 字段。

### Phase 1：页面、文字、图片 MVP

目标：大多数文本型 PDF 可看、可选、可编辑。

任务：

- PyMuPDF 读取页面尺寸、文字块、图片。
- 生成绝对定位 HTML。
- 提取图片资源。
- 基础字体 fallback。
- 支持链接 annotation。

验收：

- 普通论文 / 报告类 PDF 基本复刻。
- 文字不是图片。

### Phase 2：字体和文字精度

目标：解决字体、换行、字符间距、乱码。

任务：

- 嵌入字体提取。
- subset 字体名处理。
- CJK 字体 fallback。
- char-level 精细定位。
- 多栏阅读顺序 sidecar。

验收：

- 字体缺失导致的换行问题明显减少。
- CJK PDF 不乱码、不大面积错位。

### Phase 3：矢量和透明度

目标：复杂图表、色块、路径不丢。

任务：

- `get_drawings()` 输出 SVG。
- 支持 stroke/fill/dash/opacity。
- 支持 clip path 的基础场景。
- 对 pattern/shading/blend mode 做 diagnostics 或 fallback。

验收：

- 含大量图形的 PDF 不再只有文字和图片。
- 背景色块、线条、简单图标可复刻。

### Phase 4：图片 mask / filter / 高级效果

目标：透明图片、遮罩、软阴影更接近原图。

任务：

- soft mask 合成或 SVG mask。
- image opacity。
- clip path image。
- derived asset 可追踪。

验收：

- 透明图片不发黑、不丢背景。
- 局部 baked 都有完整 diagnostics。

### Phase 5：表格、表单、语义增强

目标：在视觉准确基础上增加可编辑语义。

任务：

- pdfplumber 表格识别。
- table sidecar / `<table>` 输出。
- 表单控件输出。
- 目录 / 标题层级启发式识别。

验收：

- 表格类 PDF 有可编辑数据结构。
- 不影响视觉层准确度。

---

## 18. 第一版 MVP 范围

建议第一版只做这些，避免一上来陷入 PDF 全规范：

1. 页面尺寸、rotation、cropbox 正确。
2. 文本 block / line / span 绝对定位。
3. 图片提取和定位。
4. 简单矢量 path 输出 SVG。
5. link annotation。
6. 字体 fallback + 基础嵌入字体提取。
7. `index.html` / `original.html` / `compare.html`。
8. diagnostics JSON。
9. 允许局部 fallback，但禁止整页默认截图。

MVP 不强求：

- 完整表格语义。
- 完整阅读顺序。
- 所有 blend mode / transparency group。
- 所有 Type3 字体完美还原。
- PDF reflow 成响应式网页。

---

## 19. 风险清单

| 风险 | 影响 | 应对 |
|---|---|---|
| PDF 没有 ToUnicode | 文本乱码 | 尝试 OCR / glyph name / diagnostics |
| subset 字体无法浏览器加载 | 字体错、换行错 | fontTools 检查 + fallback map |
| 复杂透明度组 | 视觉差异大 | 局部 baked + diagnostics |
| clip path 栈复杂 | 图片/矢量越界 | SVG clipPath，失败则局部 fallback |
| 多栏阅读顺序错 | AI 修改困难 | 视觉顺序和阅读顺序分开 |
| Type3 字体 | 文本像图形 | 可先局部 baked，保留 extracted text |
| 扫描版 PDF | 无文本 | OCR 模式作为独立功能，不混入主流程 |
| 页面尺寸不统一 | index 缩放复杂 | 每页 iframe 独立 scale |

---

## 20. 关键设计原则

1. **不要默认整页截图**。整页截图只能用于 QA 或用户显式选择的“纯预览模式”。
2. **文字优先真实 HTML**。哪怕视觉略差，也要保留可编辑文本，再通过字体和定位逐步提升。
3. **图片保留原始资产**。任何派生图都要可追踪。
4. **矢量优先 SVG**。PDF path 天然适合 SVG。
5. **视觉层和语义层分开**。表格、段落、阅读顺序可以作为增强层，不要破坏坐标复刻。
6. **先 QA 后优化**。没有原图/HTML/diff 三件套，就很难判断改动是否真的更好。
7. **所有 fallback 都要可解释**。`bake_reason` 和 diagnostics 是工程可持续的底线。

---

## 21. 推荐结论

Python 版 PDF 转 HTML 最合适的路线是：

> **PyMuPDF 主解析 + pdfplumber 辅助语义识别 + 自建中间模型 + 绝对定位 HTML/SVG 渲染 + QA 回归。**

这和当前 `ppt2html` 的方向一致：不是调用一个黑盒 `pdf2html` 工具直接吐 HTML，而是把 PDF 解析成可控模型，再由自己的 renderer 输出稳定、可编辑、可诊断的页面。

第一版应该优先把“页面、文本、图片、简单矢量、总览对比、QA”跑通。后续再逐步补字体、透明度、表格、表单、高级图形等能力。
