# AI Output Contract

HTML 输出从第一版开始保留稳定定位信息，方便后续 AI 或人工工具精确修改。

## 稳定 id

- page: `pdf-page-{pageIndex}`
- text block: `pdf-page-{pageIndex}-text-{blockIndex}`
- line: `pdf-page-{pageIndex}-text-{blockIndex}-line-{lineIndex}`
- span: `pdf-page-{pageIndex}-text-{blockIndex}-line-{lineIndex}-span-{spanIndex}`
- image: `pdf-page-{pageIndex}-image-{xref}-{occurrenceIndex}`
- vector: `pdf-page-{pageIndex}-path-{drawIndex}`
- link: `pdf-page-{pageIndex}-link-{linkIndex}`

`pageIndex` 在 HTML 契约里使用 1-based 编号，和输出文件 `page1.html` 对齐。

## 统一 class

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

## 必备 data 字段

每个可定位元素至少包含：

- `data-page`
- `data-source-type`
- `data-source-index`
- `data-bbox`
- `data-render-mode`
- `data-editable`
- `data-z`

文本元素额外包含：

- `data-font-name`
- `data-font-size`
- `data-writing-mode`
- `data-text-flow`

图片元素额外包含：

- `data-xref`
- `data-source-asset`
- `data-mask-xref`
- `data-effect-pipeline`

## render mode

- `semantic-html`: 文本、链接、表格等真实 HTML。
- `semantic-svg`: 矢量 path、线条、形状。
- `asset-with-vector-effects`: 原图资产按 bbox 定位，后续可接 clip/mask/filter。
- `diagnostic-placeholder`: 数据缺失但保留可诊断占位。
- `editable-baked`: 局部截图 fallback，MVP 不主动使用。
- `unsupported-baked`: 极端复杂对象兜底，MVP 不主动使用。
