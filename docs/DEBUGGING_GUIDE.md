# Debugging Guide

## 快速检查

1. 打开 `index.html` 看 HTML 总览。
2. 打开 `original.html` 看 PyMuPDF 原始渲染图。
3. 打开 `compare.html` 左右对照页面。
4. 查看 `assets/debug/report.json`，确认 `contract_violation_count` 和 `text_editable_ratio`。

## 常见问题

### 文本位置略有偏移

优先检查字体是否提取成功。若字体没有进入 `assets/fonts/`，浏览器会使用 fallback，可能造成宽度和换行差异。

### 图片没有显示

查看对应图片元素的 `data-xref` 和 `data-source-asset`。如果页面上出现 `image unavailable`，说明 PyMuPDF 发现了图片位置，但无法把它提取成独立资产。

### 整页变成纯白 / 缺失内容

很多 PPT 导出的 PDF 把背景照片、装饰光晕等做成 **inline image (xref=0) + soft mask** 的组合，PyMuPDF 的 `get_image_info` 不会返回这些 inline 的字节，需要从 `get_text("rawdict")` 的 image block 拿。我们已在 `parser/image_parser.py` 用 bbox 作为键合并两路信息，并尝试把"一张灰度 + 一张实色"自动识别为 soft-mask 对，合成成单张带 alpha 的 PNG。

少数情况下 PyMuPDF 完全没有暴露图像的 blend mode（典型场景：白色光晕被画在整页之上做 darken / multiply 蒙版）。我们用启发式：

- 当一个 inline 图像是灰度且尚未配对 → 渲染时加 `mix-blend-mode: multiply`，让下方内容透出来。
- 当一个图像 bbox 跑出页面边界、且自然分辨率远小于目标尺寸、且接近灰度 → 同样按 multiply 处理，并写入 `image-vignette-fallback` diagnostic。

如果一页内容仍然有大块装饰丢失，先查 `pageN.model.json` 中所有图像的 `effect_pipeline` 和 diagnostics，再结合原图判断是否需要补救。

### 矢量图形丢失

查看 `pageN.model.json` 中的 `vector` 元素数量。复杂 pattern、shading、clip stack 在 MVP 里可能只能作为后续 diagnostics 扩展。

### HTML 截图失败

确认已执行：

```bash
python -m playwright install chromium
```

即使 HTML 截图失败，核心转换仍然会输出 HTML、原图和 compare 页。
