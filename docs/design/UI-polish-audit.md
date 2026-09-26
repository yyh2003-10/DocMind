# DocMind 桌面端 UI 质感审计与优化方案

> 方法：Product Design `$audit`（代码证据审计，无运行时截图）+ frontend-design 质感原则  
> 范围：WPF 客户端（`DocMind/Views`、`Styles`、`MainWindow`）  
> 日期：2026-02

---

## 1. 审计范围与目标

| 项 | 内容 |
|---|---|
| 产品 | DocMind 本地向量知识库桌面客户端 |
| 用户目标 | 高频检索 / 对话 / 文档管理时，界面「像专业桌面工具」而非「半成品后台」 |
| 感知档次 | 从「功能可用」→「细节克制、状态清晰、品牌统一」 |
| 证据边界 | 仅基于当前 XAML/代码静态审计，未截图跑通；对比度与焦点环需真机键盘验证 |

---

## 2. 现状优点（应保留）

- 已有较完整 Design System：语义色、间距、圆角、阴影、字阶（`Styles/Theme.xaml`）
- Flow 2.0 双轨导航（Rail 54px + Drawer）+ 左侧指示条，结构有辨识度
- 矢量图标系统统一线宽 1.5（`Styles/Icons.xaml`）
- 空态、骨架屏、Toast、页面淡入（`TransitioningContentControl`）已具备
- 深色主题独立字典，阴影/主色已做 dark 适配

---

## 3. 主要问题（按感知影响排序）

### A. 品牌色污染 — 最伤「档次」

| 问题 | 证据 | 影响 |
|---|---|---|
| 页面内硬编码 iOS 蓝 `#007AFF`，与主色 `#4F46E5` 冲突 | `ChatView.xaml:79,1002,1487` 阴影 Color | 悬停/卡片高光偏「系统蓝」，品牌不统一 |
| 深色遮罩/全屏层硬编码 `#66000000`、`#FF0B1120`、`#99000000` | ChatView / GraphView 多处 | 深浅主题切换时色阶失控 |
| 危险色硬编码 `#EF4444`，主题里是 `#FF3B30`/`#FF453A` | ChatView:2743,2806 | 同一语义两种红 |
| 对话卡片阴影用 `#007AFF` 而非 Primary | ChatView Prompt 卡 | 与 `ShadowButtonHover`（已用品牌染色）不一致 |

### B. Token 使用不一致 — 系统「形同虚设」

| 问题 | 证据 |
|---|---|
| 卡片圆角混用 12/14/16，未走 `CornerLg/Xl` | QualityView 多处 `CornerRadius="14"`；StatCardStyle 用 12；ImportView 工具卡 14 |
| 统计卡：两张用 `StatCardStyle`，两张手写 Border + 14 | QualityView:70 vs 84/117 |
| 库状态徽章底色 = 父级 Card 色，几乎看不见 | QualityView:145–153 `Background=CardBrush` 套在 CardBrush 上 |
| 页根 `Grid Margin="0"`，画布无统一 ContentShell 外边距 | Search/Import/Documents/Convert；Settings 反而有 `ScrollViewer Padding=24` |
| StaticResource / DynamicResource 混用，主题切换可能漏刷 | 全项目大量 Static 引用色刷 |

### C. 交互态与可达性 — 「专业感」硬伤

| 问题 | 证据 |
|---|---|
| 几乎无全局 `FocusVisualStyle`；列表项甚至 `FocusVisualStyle={x:Null}` | SearchView:346；全库 grep 仅 3 处相关 |
| 键盘焦点不可见（有 Ctrl+K 等快捷键但 Tab 无焦点环） | MainWindow 已有 KeyBinding，但控件无 focus ring |
| ComboBox/按钮无键盘高亮统一规则 | 仅 IsMouseOver，无 IsKeyboardFocused |
| Emoji 与矢量图标混用，字重/彩色不受控 | MainWindow `🔍` `＋` `◧`；QualityView `✨`；ChatView `▾▸` |

### D. 组件深度不足

| 问题 | 说明 |
|---|---|
| CheckBox 未自定义 Template | 仅设 Border/Background，仍是系统方框 |
| Expander 无动画/无 chevron 统一 | 仅 Header 文字样式 |
| 滚动条无「悬停加宽/出现动画」 | 常驻 8px，现代桌面应用多为 hover 再显 |
| 页面切换只有 200ms 淡入 | 可接受；缺 drawer 折叠、列表选中、hover 微动效规范 |
| ChatView 2865 行单文件 | 后续改版成本高，质感细节难统一维护 |

### E. 信息密度与层次

| 问题 | 说明 |
|---|---|
| 工具条按钮高度不统一（Primary 40 / Secondary 34 / 页面 36） | 搜索/文档/导入并排时不对齐 |
| 文本「页码: 第 P3 页」文案冗余 | SearchView 元数据 |
| 无统一 PageHeader（标题+副文案+主操作） | 各页各写一套工具条 |

---

## 4. 优化原则（执行约束）

1. **一切颜色走 token**：禁止在 View 里写死 `#RRGGBB`（阴影染色用 `PrimaryBrush` 对应 Color）
2. **圆角只认 4 档**：Badge 4 / Control 8 / Card 12 / Modal 16；禁止 14、13、17 等散值
3. **控件高度分三档**：Compact 28（芯片）/ Default 36（表单）/ Primary 40（主 CTA）；同排必须同高
4. **图标只用几何 Path**：UI 操作不出现 emoji 字符
5. **每个可点元素必须有**：Hover、Pressed、Disabled、KeyboardFocus 四态
6. **页面壳统一**：`ContentShell` 统一外边距（建议 16,12）与滚动策略

---

## 5. 分阶段优化方案

### Phase 0 — 设计系统加固（1–2 天，收益最大）

| # | 任务 | 文件 | 验收 |
|---|---|---|---|
| 0.1 | 新增 `OverlayBrush`、`ScrimBrush`、`FocusRingBrush` token（浅/深各一份） | Theme.xaml + Theme.Dark.xaml | 遮罩不再手写黑透明度 |
| 0.2 | 新增 `ShadowBrandHover`（用 PrimaryColor，替换 `#007AFF`） | Theme.xaml | ChatView 悬停阴影换 token |
| 0.3 | 补齐 `BadgeStyle`、`ChipStyle`、`PageHeaderStyle`、`ContentShell` 边距 token | Theme.xaml | 圆角/内距一次定稿 |
| 0.4 | 全局 `FocusVisualStyle`：2px Primary 描边 + 2px offset | Theme.xaml | Tab 可见焦点环 |
| 0.5 | CheckBox / Toggle 完整 ControlTemplate | Theme.xaml | 与按钮圆角/动效一致 |
| 0.6 | Static → Dynamic 色刷批量替换（主题切换安全） | 全部 View | 切主题无残留色 |

### Phase 1 — 主壳与导航（2–3 天，第一眼观感）

| # | 任务 | 说明 |
|---|---|---|
| 1.1 | 标题栏：品牌字标 + 版本芯片化；连接状态用 Badge 而非裸圆点 | MainWindow |
| 1.2 | Rail：选中指示条动画（高度/透明度 150ms）；icon 统一 20×20 | MainWindow + RailItemStyle |
| 1.3 | Drawer：折叠过渡（宽度动画）；会话项时间次要信息层级 | MainWindow |
| 1.4 | Emoji → Path：`🔍`→IconSearch，`＋`→IconPlus，`◧`→新 IconPanel | MainWindow |
| 1.5 | 状态栏：忙碌指示改小胶囊 + 文案，避免裸 ProgressBar 贴边 | MainWindow |
| 1.6 | 内容画布统一 `Margin="16,12,16,12"`（或 ContentShell 样式） | TransitioningContentControl 外层 |

### Phase 2 — 核心页质感（3–5 天，高频路径）

| 页 | 关键动作 |
|---|---|
| **搜索** | 工具条统一高度 36；结果卡 hover 用品牌淡染（非仅边框变色）；详情分栏左侧 1px 分割与 rail 对齐；空态 CTA 补「快捷键 Ctrl+K」提示 |
| **对话** | 去掉 `#007AFF` 阴影；用户气泡微层次（阴影或描边，避免全平）；思考步骤轨道对齐主色 token；输入舱聚焦环与主题一致 |
| **文档库** | 列表项选中左指示条（与 Rail 同语言）；格式徽章用 BadgeStyle；删除确认用统一 Danger 对话框模板 |
| **导入** | 拖拽态边框虚线 + 主色淡底；进度条百分比与阶段文案分行 |
| **质量看板** | 统计卡全部 `StatCardStyle`；修状态徽章对比度；图表容器圆角 12；空警告去 emoji |
| **设置** | 已较完整；补齐 Focus 环、API Key 输入显示/隐藏切换、危险区卡片语义色 |

### Phase 3 — 微交互与动效规范（2 天）

- 统一 easing：`CubicEase EaseOut`，时长 120 / 180 / 240ms 三档  
- Hover：背景色变化即可，禁止整卡 scale（WPF 易糊）  
- Drawer / Panel 滑动：优先 RenderTransform Translate，避免布局抖动  
- 尊重 `SystemParameters.ClientAreaAnimation`（已有淡入逻辑可复用）  
- Toast 入场：自底 8px 上移 + 淡入 180ms  

### Phase 4 — 工程化（可并行）

- ChatView 拆分：气泡模板 / 输入舱 / 幻灯片预览 / 会话工具条  
- `docs/design/tokens.md`：色板、圆角、高度、动效速查  
- 可选：XAML 静态检查脚本（禁止 View 中 `#` 颜色字面量）

---

## 6. 优先级矩阵（建议执行顺序）

```
高感知 × 低成本   → Phase 0.2/0.4/0.5 + 去 emoji + 徽章对比度 + 圆角统一
高感知 × 中成本   → Phase 1 主壳 + 搜索/看板卡片统一
中感知 × 中成本   → Phase 2 对话输入舱与气泡层次
持续              → Phase 3 动效规范 + Phase 4 拆分
```

**最小可感知提升包（约 1 天）**  
1. 替换所有 `#007AFF` / `#EF4444` 为 token  
2. QualityView 圆角与 StatCardStyle 统一 + 状态徽章底色  
3. 全局键盘焦点环  
4. MainWindow 三处 emoji 换 Path  
5. 页面内容统一左右边距 16  

---

## 7. 风险与边界

- 改 StaticResource 批量替换时注意资源键必须在两份 Theme 中同步  
- 阴影 `DropShadowEffect` 过多会掉帧：悬停阴影仅主按钮/关键卡片  
- 未做运行时截图验收；Phase 0 完成后建议用 Playwright/手工截图做浅深双主题对比  
- 无障碍：本方案只覆盖焦点可见与对比度 token，不宣称 WCAG 全合规  

---

## 8. 建议的下一步

1. 确认执行范围（只做「最小包」还是 Phase 0+1）  
2. 从 Phase 0 token 与全局焦点环开刀  
3. 主壳与看板作为第一批视觉验收页  
