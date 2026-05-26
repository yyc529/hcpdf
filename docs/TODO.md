# PDF → HTML 极致复刻 TODO 清单

本文档按"优先级 → 优化点 → 方案 → 收益"格式组织待办事项。已完成项目归档到底部。

## 项目目标

- 页面视觉尽量复刻 PDF 原图渲染。
- 输出 HTML 干净（展示无关属性不输出，元数据进 sidecar）。
- 文本默认只展示，不可点击编辑。
- PDF 内部复杂 compositing 优先走 Python-only 深层解析，不引入 Java/C++ 主链路。
- **设计意图优先于数据忠实**：识别 PowerPoint 设计模式（glass panel / 3D button / placeholder box / decorative band / focal-point with vignette / frosted text backdrop 等），按设计意图复刻而不是机械重画 PDF 数据。

## 工作流原则

- **任何改动前先有 fixture 保护**。没回归集就不能做大重构。
- **新加启发式必须先有 fixture + sidecar 审计字段**。改阈值前先跑 fixture 看视觉是否回归。
- **TODO 必须与代码同步**。每次完成 / PR 前 grep TODO 里引用的函数名、文件路径，过期项立即归档；过期会让后续 detection 报告再次浮现"TODO 与现状脱钩"。

---

## 待完成 TODO（按优先级排列）

### P0 — 紧迫，下一步立刻动（按顺序做）

#### P0-1：实装 visual regression + 锁定已修过的 case 为 fixture

**优化点**
当前每次改动只能凭肉眼对比两个 PDF 共 35 页 compare 视图。已经修过的 case 随时可能在新改动里悄悄回归。`qa/visual_compare.py` 还是占位实现（`visual_compare_placeholder`）。

**这是所有后续 P0/P1 项目的安全网**。没有它，P0-2/P0-3/P0-4 任何一项都可能引发新一轮"修了 A 坏了 B"。

**方案**
- 实装 `python -m qa.regression --pdf <path>`：每页对 `original_pages/pageN.png` 和 `html_pages/pageN.png` 做 pixel diff + SSIM。
- 输出 `assets/debug/visual_compare.json`：每页分数、最差 N 页、整文档平均分。
- 把已修过的 case 抠成 `tests/regression/fixtures/`：单页最小 PDF + 期望视觉 baseline PNG。
- **fixture 必须覆盖最近 3 个月所有修过的 case**（防回归的核心）：

  | PDF | Page | 防回归点 |
  |---|---|---|
  | 西财蓝1 | P1 | 演示文稿模板第二行 + 20XX 不重叠 + Logo 透明背景 |
  | 西财蓝1 | P2 | "01" 不可见黑色 stencil 修复 + 添加章节标题位置 |
  | 西财蓝1 | P4 | （最近指出的页面，需要单独抠出来）|
  | 西财蓝1 | P5 | "Contents" 渐变 hollow 修复 |
  | 西财蓝1 | P7 | （最近指出的页面）|
  | 西财蓝1 | P12 | 多元素布局 |
  | 北大杂志 | P1 | 装饰条 |
  | 北大杂志 | P2 | 狮子图 + 目录 box |
  | 北大杂志 | P3 | 樱花 + 粉色 mask |
  | 北大杂志 | P5 | 小标题Title 不重叠 + 渐变开窗 |
  | 北大杂志 | P6 | 卡片 gradient + border |
  | 北大杂志 | P7 | 圆形 3D button（多次翻车的核心 case）|
  | 北大杂志 | P10 | 时间线弧线 |
  | 北大杂志 | P11 | hexagons + 照片仅在中央 hex |
  | 北大杂志 | P14 | 蓝色矩形透明度（已知 PDF 数据缺失限制）|
  | 北大杂志 | P15 | glass panels + 双色卡片（最近突破点）|
  | 北大杂志 | P17 | 蓝色占位 + 真照片 |
  | 北大杂志 | P20 | 横图模块高光（最近突破点）|
- **SVG cluster 拆分逻辑** 没有回归保护，必须新增专项 fixture（你最新加的 `_cluster_vectors` / `_render_svg_cluster`）。
- CI 跑全套，diff 超 1% 报 fail，附加截图。

**收益**
- 任何阈值 / detector 改动都有客观回归保证。
- 后续 P0-2 / P0-3 / P0-4 改动可以安全推进。
- 任何用户提的新 bug 走"抠 fixture → 改代码 → 跑回归"工作流，彻底告别"修一个坏一个"。

---

#### P0-2：image clip 决策合并为 `ClipDecision` + sidecar 审计

**优化点**
Page 7 多次翻车的直接源头：图像 clip 走 3 套独立规则，结果互相覆盖且不可追溯。
1. `_best_clip_for_image`（bbox 邻近 + path 复杂度评分）
2. 跳过 `mask_asset_path` 已设的 image
3. `_build_placeholder_union_clip` fallback（60pt 阈值）

3 条规则都直接修改 `image.clip_path_data`，互相不知情。Page 7 image-264 之前被规则 3 在规则 1/2 沉默时偷偷触发裁掉 99% 内容；同类问题随时可能出现在新 case 上。

**这条比"消除散落启发式"更紧迫**，因为它对应已经在用户面前翻过车的具体问题，且修复范围小、改动可控、可审计性立刻能换来排查能力。

**方案**
- 改 [resolver/image_clip_resolver.py](resolver/image_clip_resolver.py) `resolve_image_clip(image, context) -> ClipDecision`，返回显式枚举：
  ```python
  ("attach", clip_id, path, score)
  ("skip-smask-handles-it")
  ("synth-union", path, [source_vector_ids])
  ("none", reason)
  ```
- 决策过程串行尝试每条规则，第一个成功的胜出，丢弃后续。
- 所有决策结果写入 `image.source["clip_decision"]`；同时写到 `assets/debug/pageN.clip_decisions.json` sidecar。
- 任何规则之间需要互相感知时，必须通过 ClipDecision 显式表达，不能再隐式互改 `clip_path_data`。

**收益**
- 任何图像 clip 异常都能从 sidecar 一眼看到"经过了哪条规则 / 为什么 / 得分多少"。
- 新增 clip 规则不再担心打破现有规则的隐式依赖。
- 单元测试可以直接断言 `assert resolve_image_clip(...) == ("synth-union", ...)`，不用跑端到端。
- 为 P0-4 detector 框架打基础（同样的 "Decision + Sidecar" 模式可复用到其他启发式）。

---

#### P0-3：Form XObject 递归 walker

**优化点**
当前 `parse_page_paint_events` 只走 page-level content stream，`Do /MetaXXX` 直接跳过。但 PPT 卡片、icon module、cherry blossom banner 都画在 Meta Form XObject 内部，page 看不到。Phase 7 半成品里这是最后一块大砖。
- Page 6 cherry blossom banner（在 Meta 内）
- Page 20 4 个 icon module 圆角背景（Meta644 / 639 / 647 内部）
- Page 7 圆形按钮 stroke + 高光（Meta XObject 内）

**方案**
- 把 `_walk_page` 重构成 `class StreamWalker`，状态封装到对象，可重入。
- `Do /XObject` 拦截 → 如果是 `/Subtype /Form` 就：
  - push GraphicsState（保存 CTM、alpha、clip stack、soft mask）
  - 把 Form 的 `/Matrix` 与当前 CTM 复合
  - push resource scope（Form 自己的 `/ExtGState` `/Pattern` `/XObject` `/Font`，回退查找时优先 Form 内 → page → document）
  - 递归调用 `walker.walk(form.contents)`
  - 退出时 pop
- 测试样本（依赖 P0-1 fixture 已落地）：Page 20 跑通后必须看到至少 4 个新 PaintEvent（icon module 背景）；Page 6 cherry blossom banner 应该出现；Page 7 圆形按钮 stroke 可见。

**收益**
- Page 20 模块圆角背景出现（不再只是 4 个孤立 icon）。
- Page 6 cherry blossom banner 真的出现（不再靠 raster 兜底）。
- Page 7 圆形按钮的 stroke / 高光从 deep parser 直接拿到，glass-panel detector 之外的另一条解决路径。
- 一次性解开三类问题，性价比最高的 Phase 7 投资。

---

#### P0-4：搭最小 detector/handler 骨架（不做大爆炸迁移）

**优化点**
P0-1 给出回归保护后才有资格做框架重构，但**也只做最小骨架 + 迁一个 case 试水**，验证 detector/handler 抽象方向可行，再分批迁其他启发式。范围不超过：
- 数据模型 `PPTPatternMatch`
- sidecar 审计 `assets/debug/pageN.patterns.json`
- 一个最小 detector：**glass-panel**（你最新加的 `_add_glass_panel_fallbacks` 内部逻辑搬过来，函数已写好，迁移代价最小）
- pipeline 入口 `detect_ppt_patterns(elements, page) -> list[PPTPatternMatch]` + `apply_ppt_pattern_handlers(matches, elements)`

**不**在这个阶段做的：
- ❌ 不迁 outer ring drop / vignette / grayscale multiply / placeholder-union 等其他启发式
- ❌ 不重写 image_parser.parse_images
- ❌ 不动 page_parser 主流程除了加 2 行 pipeline 调用

**方案**
- 新建 `parser/ppt_pattern_detectors.py`：
  ```python
  @dataclass
  class PPTPatternMatch:
      kind: str   # "glass-panel" | (未来) "vignette-overlay" | ...
      elements: list[str]  # 涉及的 stable_id
      metadata: dict       # 模式特定数据（panel_bbox / shadow_bbox / smask_path 等）
      diagnostic_code: str
      message: str

  def detect_glass_panel(images, gradients) -> list[PPTPatternMatch]:
      # 把 _is_retained_subtle_rect_shadow + _find_matching_panel_base 搬过来
      ...
  ```
- 新建 `resolver/ppt_pattern_handlers.py`：
  ```python
  def handle_glass_panel(match, elements, context, cache, image_assets):
      # 把 _add_glass_panel_fallbacks 内部对 image / image_fallback 的修改搬过来
      ...
  ```
- `page_parser.py` 调用：
  ```python
  patterns = detect_ppt_patterns(...)
  apply_ppt_pattern_handlers(patterns, ...)
  # 旧的 _add_glass_panel_fallbacks 删除
  ```
- patterns.json sidecar 输出每页命中的所有 PPTPatternMatch。

**验收**
- P0-1 fixture 全部跑通（Page 15 / 20 glass panel 不能回归）。
- patterns.json 在 Page 15 / 20 应该看到 `kind: "glass-panel"` 命中。
- 代码行数：detector + handler 总和不超过原 `_add_glass_panel_fallbacks` 的 1.5 倍。

**收益**
- 验证 detector / handler 抽象方向可行，为 P1 阶段迁其他启发式打基础。
- 风险可控：只动一个 detector，回归面有限。
- patterns.json 让"哪一页命中了什么模式"立刻可见。

---

### P1 — 重要，本季度做

#### P1-1：clean HTML 契约更新（紧急程度提升）

**优化点**
[docs/AI_OUTPUT_CONTRACT.md:30](docs/AI_OUTPUT_CONTRACT.md) 和 [core/output_contract.py:12](core/output_contract.py) 仍要求每个元素输出完整 `data-*`、`contenteditable` 等字段。实际 DOM 已经清理过了，**契约与现状完全脱钩**：CI 跑 contract check 现在就是全文件 violations 飙红但实际渲染正常。这种"契约存在但永远 fail"的状态比没契约更糟，会让真正的违反淹没在噪音里。

**方案**
- 更新 `docs/AI_OUTPUT_CONTRACT.md`，删除旧的必备 `data-*` 约束。
- 重写 `core/output_contract.py`：
  - 检查页面 HTML 不应出现冗余 `data-*`（白名单：`data-page-width`、`data-page-height` 在 index.html 是真实需要的 JS 依赖）
  - 检查每个展示元素必须有稳定 `id` 和对应 CSS 几何规则。
  - 检查空图层不输出。
  - 检查 `contenteditable` / `spellcheck` 默认不输出。
  - 检查 paint_events / patterns / clip_decisions sidecar 完整可追踪。
- 任何展示无关属性出现在 DOM 里 → contract violation。
- 修完后 `contract_violation_count` 在两个测试 PDF 上必须归零。

**收益**
- contract check 重新有意义，能捕捉真实违反。
- HTML 输出始终干净，不会因为某次调试加的 `data-*` 留在 production。
- AI 模型从 sidecar 拿调试信息，从 HTML 拿视觉信息，职责分离。

---

#### P1-2：统一启发式阈值表 `core/heuristic_thresholds.py`

**优化点**
散落的"魔法常数"已经积到一定规模，每个都是某次 debug 时塞进去的，互相不知情：

| 文件 | 常数 | 值 | 用途 |
|---|---|---|---|
| `resolver/image_clip_resolver._MATCH_TOLERANCE_PT` | 1.5 | bbox 邻近匹配 |
| `resolver/image_clip_resolver._CONTAINMENT_SLACK_PT` | 20.0 | clip 内嵌松弛 |
| `resolver/image_clip_resolver._UNION_CLIP_MIN_BLEED_PT` | 60.0 | placeholder-union 触发阈 |
| `resolver/image_clip_resolver._RASTER_DEDUP_TOLERANCE_PT` | 4.0 | 渐变-栅格去重 |
| `parser/image_parser._RING_SLACK_HUNDREDTHS` | 200 | outer ring 检测 |
| `parser/image_parser` smask_max_alpha 阈值 | 64 | subtle overlay 判定 |
| `parser/image_parser` glass-panel alpha 提亮系数 | 2.25 | 白高光合成 |
| `parser/page_parser._is_retained_subtle_rect_shadow` 面积比 | 0.12 | glass-panel 候选 |
| `parser/page_parser._panel_base_matches_shadow` 重叠/面积比 | 0.85 / 2.2 / 2.5 | shadow-panel 配对 |
| `parser/text_parser._estimate_letter_spacing` | 0.1 / 0.5 / 0.95 / 0.05 | letter-spacing 估算 |

**方案**
- 新建 `core/heuristic_thresholds.py`：每个常数作为 module-level dataclass 字段。
- 注释里必须写：why this value、命中它的 fixture 列表（依赖 P0-1）、关闭后退化行为。
- 新加常数必须先写 dataclass 字段，禁止源文件内联 magic number；在 P1-1 契约里加这条 lint 规则。
- 每个阈值在 `tests/regression/fixtures/` 已经配套最小复现 PDF（依赖 P0-1）。

**收益**
- 改阈值前先跑 fixture 看视觉是否回归，告别"改一个常数破一个页面"。
- 阈值调优可以变成"在 fixture 集合上扫一个参数空间找最优值"的科学过程。

---

#### P1-3：image_parser 启发式拆出去（继续 P0-4 的工作）

**优化点**
P0-4 只迁了 glass-panel 一个 detector。`parse_images` 主循环里还内联着 5+ 个启发式：outer-ring drop、inline-grayscale multiply、vignette multiply、subtle-overlay suppress / retained、inline softmask compose。运行顺序固定且互相依赖。

**方案**
- 按 P0-4 验证过的 detector / handler 模式逐个迁：
  1. `detect_outer_ring_card`（Page 6 卡片三联）
  2. `detect_vignette_overlay`（Page 2 / 15 角落 vignette）
  3. `detect_image_placeholder_box`（Page 17 蓝色占位）
  4. `detect_flatten_precomposed_overlay`（Page 7 image-264 / Page 20 大 overlay）
  5. `detect_inline_softmask_pair`（已是函数，封装成 detector 即可）
- 每迁一个 detector 必须：
  - 对应 P0-1 fixture 跑通
  - 新增 detector 单元测试
  - patterns.json 命中该 detector

**收益**
- `parse_images` 函数从 ~400 行降到 ~150 行（数据收集纯函数）。
- 启发式互不干扰，新加 / 删除某一条不影响其他。
- diagnostic 标签可直接对应 detector 名。

---

#### P1-4：detector / handler 配置开关 A/B

**优化点**
当前 `use_deep_parser` 一个总开关。每个 detector / heuristic 都应该单独可关，调试"是哪条规则把视觉搞坏了"时直接关一条试。

**方案**
- 新建 `PatternDetectorConfig` dataclass，每个 detector 一个 bool 字段。
- CLI 增加 `--disable-pattern <name>` 多次可用。
- diagnostic 里加 `category` 字段：`pdf-data-fault` / `pymupdf-extraction-fault` / `design-intent-recovery` / `heuristic-fallback`。
- 跑 P0-1 回归集时自动对每个 detector 做"关闭 → 跑回归 → 输出 diff 报告"，确认关掉每个 detector 影响的页面集合。

**收益**
- 排查回归时不用注释代码。
- 自动化测试可以扫所有 detector 关 / 开组合，验证非交互。
- 知道每个 detector 真正影响的页面范围。

---

#### P1-5：ExtGState SMask 应用到 image / form（不只 pattern）

**优化点**
当前 SMask 处理只覆盖：
- 直接 image 的 `/SMask`（compose with matte unpremultiply）✓
- pattern 渐变上的 `gs /SMask` 引用 pattern ✓（Page 3 / 20）

但 image XObject 上的 `gs /SMask` 引用 image / form 没处理。Page 5 "Contents" 大字底下的体育馆图就是这个模式。

**方案**
- 在 deep parser walker 状态机里跟踪 `gs` 设置的 SMask（不只 pattern fill 的，所有后续操作都受影响），SoftMaskGradient 已经在 [deep_pdf_parser.py:62](parser/deep_pdf_parser.py) 定义过基础结构，扩展支持 reference Form / image。
- 把 SMask group 转换成 SVG `<mask>`：里面绘制 group 内容（pattern / image），用作主元素的 mask。
- 优先实现 luminosity SMask，alpha SMask 次之。

**收益**
- Page 5 "Contents" 大字背后体育馆图通过开窗效果露出来。
- 后续遇到同类"全屏渐变 + 文字开窗"模式不用再单独处理。

---

### P2 — 应做，按节奏推进

#### P2-1：classify_vectors sidecar 审计 + 单元测试（已落地补尾）

**优化点**
之前 P2-6 写的"per-overlap 语义判定"已经在 [renderer/svg_renderer.py:323](renderer/svg_renderer.py) `_collect_image_metadata` + `_is_background_vector` 落地。**但**：
- 没有 sidecar 审计：哪个 vector 被哪些后绘 image 覆盖 → 因此归 background，无法从外部查证。
- 没有单元测试：fixture 没锁 → 任何 paint_seqno 浮点重排（glass-panel handler 的 `paint_seqno = base - 0.5`）可能让分类失准都无法察觉。

**方案**
- vector classify 决策写到 `assets/debug/pageN.vector_layers.json`：每个 vector 记录 `layer: "background" | "foreground"` + `because: ["covered-by image-X (seqno=N)"]`。
- 单元测试 fixture：
  - simple rect drawn before image at same bbox → background ✓
  - simple rect drawn after image at same bbox → foreground ✓
  - complex path → always foreground ✓
  - vector_seqno = base - 0.5 浮点 → 仍正确分类

**收益**
- 任何 vector 出现在错误图层都能从 sidecar 反查原因。
- glass-panel 类 paint_seqno 重排不会再次引发分类失准。

---

#### P2-2：Page 14 蓝色矩形透明度（**证据驱动**，非硬猜）

**优化点**
PDF stream 描述为 opaque blue gradient，无 `/ca` / SMask / Group transparency。PyMuPDF 原图渲染也跳过了它（PyMuPDF pattern 解码 bug）。原 PPT 设计意图是半透明，但 PDF 数据丢了。

**关键纠正**：之前我写"自动加 fill-opacity: 0.6"是硬编码 magic number，违反 P1-2 阈值表原则。**改成证据驱动推断**。

**方案**
- 触发条件（必须**全部**满足，缺一不可）：
  1. pikepdf 解析出了 pattern fill paint event
  2. PyMuPDF `get_pixmap` 在该区域的渲染与 pattern 实色差异显著（说明 PyMuPDF "认为不应该完全画出来"）
  3. 该 pattern 无 `/ca` / 无 ExtGState SMask / 无 Group transparency
  4. pattern bbox 与某张底图（image）高度重叠
- opacity 不写死，从证据反推：
  - 采样 PyMuPDF 原图在该 bbox 的色彩 vs pattern 实色与底图实色的色差距离
  - 用 `alpha = (rendered_color - underlying_color) / (pattern_color - underlying_color)` 反算
  - clamp 到 [0.2, 0.95] 防止极端值
- 写 `category: heuristic-fallback` diagnostic：标注"PDF stream 缺失透明度信息，从原图反推 alpha=X.X"。
- 必须有 fixture（Page 14 + 收集其他类似 case）。

**收益**
- Page 14 蓝色矩形按真实视觉证据复刻，不靠拍脑袋。
- 同类"PDF 数据丢了透明信息"的 case 有统一证据驱动兜底，未来新 PPT 自动适配。

---

#### P2-3：detect_image_with_smask_vignette 整合现有逻辑

**优化点**
当前"主图 + 暗角 SMask vignette"模式靠 `_compose_with_matte` 反预乘 + `image-subtle-overlay-retained` 标记两套独立机制走通。逻辑分散，难以演进。

**方案**
- 抽 `detect_image_with_smask_vignette` detector（按 P0-4 / P1-3 框架）：识别主图 + matte premultiplied SMask 的组合。
- 命中后统一返回 PPTPatternMatch，handler 负责反预乘 + retain（不再两个机制各跑一遍）。

**收益**
- Page 2 / 15 主图渲染逻辑集中。
- 后续修 matte 算法 / vignette 检测只在一处改。

---

#### P2-4：detect_frosted_text_backdrop 模式

**优化点**
Page 5 / 15 标题文字背后那层模糊背板就是这个模式。当前只解决了"渐变背板 + SMask 开窗"一个变体。还有：
- 标题文字直接绘制在模糊图上（无渐变背板）
- 标题用 outline 文字 + 内部填 gradient

**方案**
- 识别"文字与背后模糊图共生"模式：text bbox 包含在某个 image bbox 里，且 image 有明显高斯模糊（PIL Laplacian variance 估算）。
- 输出 PPTPatternMatch("frosted-text-backdrop", text_block, backdrop_image, blur_radius_hint)。
- Handler：可选地降低 backdrop 模糊程度（CSS `filter: blur()` 反向），让文字更易读。

**收益**
- Page 5 / 15 + 其他类似 PPT 标题页文字 + 模糊背景的组合统一处理。

---

#### P2-5：Phase 7-5 image transparency group blend mode

**优化点**
3 张光栅图通过 PDF transparency group 的 isolation + blend mode 合成的"3D 球体"效果（Page 7 圆形当前用 glass-panel 兜底，但更通用的解法是真正解析 group）。

**方案**
- 解析 image XObject 的 `/Group /S /Transparency` 字典 + `/I`（isolated）/`/K`（knockout）。
- 当 `Do /ImageXXX` 序列在同一隔离组内时，按 group blend mode 合成。
- 复杂时退到 PIL 离线合成成单张 derived PNG。

**收益**
- Page 7 圆形不靠 glass-panel 兜底，按 PDF 真实意图渲染。
- 其他用 group + blend mode 设计的复杂 UI 元素自动支持。

---

### P3 — 长期，按需

#### P3-1：高级 ShadingType + Form XObject 完整覆盖

**优化点**
当前只支持 `ShadingType 2` (axial linear gradient)。`ShadingType 3` (radial)、4-7 (mesh / coons / tensor) 全部 fall back PyMuPDF raster。

**方案**
- `ShadingType 3` → SVG `<radialGradient>`。
- `ShadingType 4-7` → 离线 PIL 栅格化成 PNG，作为 derived asset。

**收益**
- 高级渐变设计的 PPT 模板视觉精度提升。

---

#### P3-2：Phase 6 受控局部 fallback 白名单

**优化点**
现在没有正式 fallback 机制。`subtle-shadow-replaced-by-glass-panel` / `placeholder-union-fallback` 等已经事实上是 fallback，但没有统一白名单和审计。

**方案**
- 定义 fallback 白名单：`type3_font_unrecoverable` / `transparency_group_too_complex` / `unsupported_blend_mode` / `unsupported_shading` / `clip_stack_too_complex` / `image_mask_unrecoverable` / `glass_panel_inferred` 等。
- 每个 fallback 必须写 sidecar：source bbox、source objects、lost features、future parser suggestion。
- contract check 禁止非白名单 fallback。

**收益**
- 视觉准确度可衡量：每个 fallback 都知道损失了什么。
- 用户和 AI 修改时清楚"这部分是兜底，不是真实数据"。

---

#### P3-3：char-level 文字定位

**优化点**
当前 letter-spacing 估算够用了，但对旋转文字、竖排文字、异常字距、span bbox 不可信场景仍可能错位。

**方案**
- 检测条件命中时（span bbox 与 char 累加位置差异 > 阈值），改用 char-level 输出策略。
- 每个 char 独立 `<span>` 绝对定位，牺牲 DOM 干净度换精度。

**收益**
- 极端排版 case 不丢字 / 不重叠。

---

#### P3-4：表格 / 表单 / 阅读顺序回归

**优化点**
当前 `--tables` / 表单 widget / reading-order 都跑通了，但没回归样本。

**方案**
- 抠 5-10 个真实 PDF 作为 fixture，每个测一类语义结构。
- 视觉回归 + 语义结构断言（cell 数量 / 顺序 / 表单字段类型）。

**收益**
- 这几个 phase 的能力不会随主流程改动悄悄退化。

---

#### P3-5：CLI / 配置 / 性能

**优化点**
当前 CLI 选项少（`--html-screenshots` / `--tables` / `--no-fonts`）。性能上没并行、没缓存。

**方案**
- 支持 `--pages` / `--dpi` / `--font-policy` / `--qa` / `--ocr` / `--strict-contract` / `--disable-pattern <name>`。
- 支持 `pdf2html.toml` 配置文件。
- 页面级并行解析与渲染。
- 图片 / font / path 按 hash 全局去重。
- 大 PDF 流式写 debug JSON。

**收益**
- 100+ 页 PDF 转换从分钟级到秒级。
- 配置可以版本化。

---

#### P3-6：OCR 独立模式

**优化点**
扫描版 PDF 没有 text layer，当前直接报错。

**方案**
- 检测无文本页面 → 显式 OCR 模式（CLI flag `--ocr`）。
- OCR 文本输出 sidecar 或可选隐藏文本层。

**收益**
- 扫描版 PDF 也能转。

---

## 终态判断检查表

- [ ] 常见文本、图片、矢量、链接、表格、表单可语义化输出。
- [ ] 页面 DOM 干净：展示无关 `data-*`、空预留层、默认 `contenteditable` 不输出。
- [ ] 文本保持真实 HTML 节点，不默认整页/整块烘焙。
- [ ] 默认不整页截图，`baked_page_ratio == 0`。
- [ ] 所有 fallback 都有白名单原因、sidecar 和 diagnostics。
- [ ] 字体、颜色、透明度、clip/mask 有明确 resolver 和测试。
- [ ] 同一 PDF 多次转换输出稳定。
- [ ] QA 每次输出像素分数、契约违反数、baked ratio、missing font/image failure 统计。
- [ ] `index.html` / `original.html` / `compare.html` 能快速定位坏页。
- [ ] PowerPoint 设计模式（glass-panel / 3D button / placeholder box / vignette / frosted text / decorative band）都有 detector + handler + fixture。
- [ ] Form XObject / transparency group / blend / stroke 能通过 Python-only 深层解析逐步恢复。
- [ ] TODO 与代码同步：每次完成 / PR 前 grep TODO 里引用的函数名 / 路径，过期项立即归档。

---

## 已完成存档

### Phase 0 — 输出格式与干净 DOM

- 单页 HTML 已与 `ppt2html` 风格对齐：`<!DOCTYPE html>` / `lang="zh-CN"` / 居中画布 / 稳定 `section#pdf-page-N` / 元素几何样式集中到 `<style>` 块。
- `index.html` / `original.html` / `compare.html` 总览、原图、对比视图齐全。
- 默认输出目录 `output_<pdf-stem>` 自动派生。
- 命令行不传 PDF 时使用 `pdf2html.py` 内固定测试路径。
- 删除展示无关 `data-*` / `contenteditable` / `spellcheck`。
- 空 `link-layer` / `table-layer` / `form-layer` / `annotation-layer` 不输出。
- 矢量 clipPath 已安全优化：未使用 / 整页矩形 / 无实际裁剪效果的矩形会被删除。
- 图片 clipPath 已基础简化：能识别为简单 rect / rounded rect / ellipse 的长 path 会简化为 SVG 基础形状。

### Phase 1 — 文本 / 图片 / 链接 MVP

- 页面尺寸、cropbox、rotation 基础信息进入 PageModel。
- 文本 block / line / span 三层相对定位（修复了双重 absolute 引起的位置偏移）。
- 图片 xref + inline image 提取、asset 去重。
- soft mask / alpha 合成、derived asset 生成。
- inline image pair 合成（mask + color → 单张 RGBA PNG）。
- link annotation 透明 `<a>` 热区。

### Phase 2 — 字体与文字精度

- TTF/OTF/CFF/CID/Type0/Type3 识别，可加载字体写 `@font-face`，subset 前缀清理。
- CJK fallback 字体栈，replacement char 诊断。
- ToUnicode 缺失 / CID 异常诊断。
- ascender / descender baseline 辅助、letter-spacing 估算。
- 混合 CJK + Latin 文本字距估算修复（Page 5 "小标题Title" 不再重叠）。

### Phase 3 — 矢量 / 颜色 / 透明度

- `get_drawings(extended=True)` 完整 line / cubic / rect / quad / closePath。
- fill rule、stroke cap / join、miter、dash array + dash offset。
- DeviceGray / RGB / CMYK 转 CSS RGB，diagnostic 记录。
- clip stack → SVG `<clipPath>`，blend mode → `mix-blend-mode`。
- 矢量按 background / foreground 分两层渲染（避免 image 盖住 outlined text）。
- 简单矩形 vector 与复杂 path 分流到 CSS div / SVG path（DOM 更干净）。
- 矢量按空间聚类成独立局部 SVG（`_cluster_vectors` / `_render_svg_cluster`，避免无关 vector 共享 SVG viewBox）。
- falsy-0.0 opacity 强制转换 bug 修复（Page 2 黑色 "01" stencil 不再可见）。
- **per-overlap 分类**：simple-rect vector 只在某张 image 后绘且 bbox 重叠时归 background（语义判定已落地，sidecar 审计在 P2-1 补完）。

### Phase 4 — 图片 mask / clip / filter

- soft mask / stencil mask / alpha 检测，SVG `<mask>` 输出。
- 必要时 derived PNG（matte un-premultiply via numpy）。
- 图片支持 `<svg><image clip-path>` + grayscale / brightness / contrast SVG filter。
- 图片 clipPath 自动简化为 `<rect>` / `<ellipse>` / `<rect rx ry>`。
- smask xref 从 PDF 对象字典直读（PyMuPDF 不返回时的 fallback）。
- smask 已设的 image 跳过额外 clip 挂载（避免双重裁剪，Page 7 圆形对齐修复）。
- subtle overlay（smask max alpha ≤ 25%）抑制（避免 ghost circle）。
- placeholder-union clip 触发阈值提到 60pt（避免 Page 7 预合成图被误裁）。

### Phase 5 — 表格 / 表单 / 阅读顺序

- `--tables` pdfplumber 表格候选，`assets/debug/pageN.tables.json` sidecar。
- 高置信度时输出可编辑 `<table>`（已撤销 contenteditable，仅展示）。
- PDF widget 表单控件（text / checkbox / radio / listbox / combobox / signature）。
- 普通 annotation 输出 hover title。
- visual order / reading order 双轨，输出 `assets/debug/pageN.text_flow.json`。
- 多栏 / header / footer 初步检测。

### Phase 7 — Python-only 深层 PDF 解析

- `parser/deep_pdf_parser.py`：pikepdf 内容流 walker，跟踪 `q/Q/cm/gs` 栈，识别 pattern fill 序列。
- ShadingType 2（axial linear gradient）完整解析：`/Coords` + `/Function`（Type 2 exponential / Type 3 stitching）+ `/Bounds` + `/Encode` → SVG `<linearGradient>`。
- ExtGState `/ca` fill alpha、`/BM` blend mode 跟踪。
- ExtGState `/SMask /S /Luminosity` 引用另一个 shading pattern → SVG `<mask>`（Page 3 / 20 粉色 overlay 透明效果）。
- `SoftMaskGradient` 数据结构定义（支持后续 P1-5 扩展到 image / form references）。
- `resolver/deep_paint_resolver.py`：PaintEvent → GradientFillElement，y-up→y-down bbox 转换，同 bbox inline raster 抑制避免双绘制。
- `renderer/gradient_renderer.py`：每个 GradientFillElement 独立 SVG（page-size viewBox + y-flip 变换）。
- 统一 paint-order content-layer：image + gradient 按 paint_seqno 合并 DOM 排序，z-stack 自然成立。
- numpy 实现 PDF 1.7 §11.6.5.3 matte 反预乘公式（Page 15 灰底修复）。
- `core/config.use_deep_parser` 总开关，A/B 比较。
- `assets/debug/pageN.paint_events.json` sidecar。
- Page 7 image-264 预合成图识别 + placeholder-union 触发阈值校准。

### Phase 7-glass-panel — 设计意图复刻（核心突破）

- `_is_retained_subtle_rect_shadow`：识别 PowerPoint glass-panel 模式中的"低透明度矩形阴影"。
- `_find_matching_panel_base`：把阴影与同 bbox 的渐变/图像 panel 配对。
- 命中后把 shadow `paint_seqno = base - 0.5`，从"压住面板"变成"垫在面板下面" → panel 颜色不再被压灰，shadow 在 panel 边缘外延露出。
- `_make_white_smask_overlay_asset`：把 dark overlay 的 SMask 形状反推为白色高光（同一 alpha 形状，颜色换成白），`paint_seqno = base + 0.5` 叠在面板上 → 玻璃质感高光复刻。
- `_add_glass_panel_fallbacks`：低透明度阴影找不到配对 panel 时，合成 20% 白圆角矩形作为推断面板。
- `_suppress_broad_blur_overlays`：覆盖大半页的 mask 化预合成图直接抑制（避免盖住所有本地控件）。
- `_salvage_lowres_overlay_control_crops`：在被抑制的大 overlay 里裁出本地控件 crop 还原回去（保留控件高光）。
- `_suppress_vectors_inside_baked_crops`：salvaged crop 包含的 vector 不重复绘制。
- `_suppress_gradients_when_raster_preserves_pdf_effect`：当 inline raster 比 SVG gradient 更能保留 PDF transparency / stacking 时，drop 渐变保留栅格。
- **直接受益页面**：Page 15 双色 glass panels + 蓝/粉鲜艳渲染 / Page 20 横图模块高光复刻 / 其他类似 glass-card UI 的 PPT 通吃。

### 已修复 case 索引（防回归参考，依赖 P0-1 fixture 固化）

| PDF | Page | 问题 | 修复方案 |
|---|---|---|---|
| 西财蓝1 | P1 | 演示文稿模板第二行消失 | text block / line / span 三层相对定位修复 |
| 西财蓝1 | P1 | 20XX 字母重叠 | Latin proportional 字体 advance variance > 10% 时禁用 letter-spacing 推算 |
| 西财蓝1 | P2 | "01" 显示为不透明黑色 | falsy-0.0 opacity 强制转换 bug 修复 |
| 西财蓝1 | P5 | "Contents" 渐变 hollow | clip 匹配器优先 simple solid letter 而非高 item count stroke trace |
| 北大杂志 | P1 | 装饰条消失 | 复杂 path 永远 foreground（不靠 bbox 大小判定 background） |
| 北大杂志 | P2 | 狮子图过高 + 目录 box 丢 | paint_order_resolver synthesize image seqno + per-overlap classifier |
| 北大杂志 | P3 | 樱花被粉色覆盖 | ExtGState SMask luminosity → SVG mask |
| 北大杂志 | P5 | "小标题Title" 字符重叠 | 混合 CJK+Latin 文本禁用 letter-spacing 估算 |
| 北大杂志 | P6 | 卡片渐变方向反 / 边框消失 | deep parser shading pattern + 内层 pair 外层 ring 分离 |
| 北大杂志 | P7 | 右侧圆形多种错位 | placeholder-union 阈值 60pt + smask-image 跳过额外 clip |
| 北大杂志 | P10 | 时间线弧线消失 | mask vs color 选 strict grayscale 优先于 luminance spread |
| 北大杂志 | P11 | 照片不限于六边形 | placeholder-union clip 从白填充 vector 合成 |
| 北大杂志 | P15 | 双色 glass panels 灰底 | glass-panel detector + shadow reorder + 白高光从 SMask 反推 |
| 北大杂志 | P15 | 页面灰底（之前） | matte unpremultiply numpy 公式 |
| 北大杂志 | P17 | 蓝色占位盖住照片 | per-overlap classifier 把蓝矩形放回 background |
| 北大杂志 | P20 | 模块圆角背景灰 | broad blur overlay suppress + control crop salvage |
