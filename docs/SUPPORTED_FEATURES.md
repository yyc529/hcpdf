# Supported Features

## MVP 已支持

- PDF 页面基本信息读取。
- 原始页面 PNG 导出。
- 文字 block / line / span 绝对定位。
- HTML 文本 span 可编辑。
- 图片 xref 提取和定位。
- 简单 PDF drawing 转 SVG path。
- link annotation 转透明 `<a>` 热区。
- 基础嵌入字体提取。
- `index.html` / `original.html` / `compare.html` 总览。
- PageModel / DocumentModel / diagnostics JSON。
- 基础 contract check 和 editability report。

## 部分支持

- 表格候选：使用 `--tables` 可运行 pdfplumber `extract_tables()`，目前作为 sidecar 模型信息，不替换视觉层。
- HTML 截图：使用 `--html-screenshots` 可尝试 Playwright 截图，浏览器依赖缺失时写入 diagnostics。
- 字体：只提取浏览器可加载的 ttf / otf / woff / woff2，其他字体走 fallback。

## Phase 2 / Phase 3 已落地

- 字体管线扩展：识别 Type0 / TrueType / Type1 / Type3 / CFF / CID，浏览器可加载（ttf/otf/woff/woff2）的字体写入 `assets/fonts/` 并生成 `@font-face`，其他类型记录 diagnostics + fallback 原因。
- 字体名 subset 前缀去除，保留 raw_name 映射；font asset 带 `font_type / subset_prefix / is_cjk / loadable / fallback_reason`。
- CJK 检测：CJK 字符或 CJK 字体名自动切换 `Noto Sans CJK / Microsoft YaHei` fallback 栈；ToUnicode 缺失会输出 `text-replacement-char` 诊断。
- 文字精度：使用 `get_text("rawdict")` 拿 ascender / descender / origin / char-level bbox；输出 `letter-spacing`、`writing-mode`、旋转 transform、bold/italic、`data-has-cjk` / `data-glyph-source`。
- 矢量管线：`get_drawings(extended=True)` 解析 `clip` / `group` / `f` / `s`，维护 clip stack 与 group stack；line / cubic / rect / quad / closePath 完整 SVG path；dash array + dash offset、line cap / join / miter limit。
- SVG `<defs>` 输出 `<clipPath>`，矢量 path 引用 `url(#...)`；group blend mode 映射到 CSS `mix-blend-mode`，group opacity 应用到 path 的 `opacity`。
- 颜色管线：Device Gray / RGB / CMYK 转 CSS RGB；CMYK 转换记录 diagnostic；超出 1..3..4 通道的 colorspace（Indexed / Separation / DeviceN）写 `color-unsupported-channels`。

## Phase 4 / Phase 5 已落地

- 图片 soft mask / stencil mask / alpha 检测；可用 SVG `<mask>` 表达，必要时合成 derived PNG，并在 `effect_pipeline` 中记录处理链。
- 图片支持 SVG `<clipPath>` 与 SVG `<filter>`（grayscale / brightness / contrast），需要时自动改用 `<svg><image></svg>` 输出。
- pdfplumber 表格升级为 sidecar：`assets/debug/pageN.tables.json` 记录每个 cell 的 bbox / 文本 / 置信度；高置信度（≥0.7）才额外输出可编辑 `<table>`。
- PDF 表单控件（text / checkbox / radio / listbox / combobox / signature）输出为语义 `<input>` / `<select>` 叠加层；非链接、非控件类 annotation 输出为 `.pdf-annotation` 叠加。
- 阅读顺序 resolver：检测多栏与 header / footer，输出 `assets/debug/pageN.text_flow.json`，每个 text block 都带 `visual_order` 与 `reading_order`。

## 后续阶段

- char-level 精细定位。
- Type3 / CID / CJK 字体增强。
- blend mode 与 transparency group。
- OCR 独立模式。
- pixel diff / SSIM 视觉回归。
