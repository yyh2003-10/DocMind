# DocMind 现代简约质感升级计划

> 目标：克制的高级感 + 精细动效 + 可感知的「悬停绽放」与按压反馈  
> 原则：动效服务信息层级，不做炫技；所有动画尊重 `SystemParameters.ClientAreaAnimation`

---

## 0. 全局评估（现状 → 缺口）

| 维度 | 已有 | 缺口（档次感瓶颈） |
|---|---|---|
| Token | 色/圆角/阴影/高度/动效时长 | 缺 Bloom / Lift / Press 规范 |
| 页面切换 | 200ms 淡入 | 无 Y 轴位移，切换偏「闪」 |
| 悬停 | 变背景/边框/阴影 | **无绽放光晕、无抬升、无图标微缩** |
| 按压 | 变深色 | **无 0.96–0.98 微缩放**（现代感关键） |
| 选中 | 左指示条淡入 | 列表卡片缺「沉入」层次 |
| 抽屉/Rail | 已有滑动/指示条 | 图标无 hover 绽放 |
| Toast | 上移+淡入 | 可加轻微 scale |
| 性能 | Performance 阴影 | 绽放只用 Opacity+Scale，避免模糊动画 |

**结论**：结构与 token 已立住，下一步补的是 **Interaction Motion 层**——同一套 120/180/240 语言下的绽放、微缩放、抬升。

---

## 1. 设计锚点（Style Anchor）

对标气质：**Linear / Arc / Raycast 控制台** × **Apple 系统控件的克制**  
- 背景近中性，品牌靛蓝只出现在「可操作 / 已选中 / 聚焦」  
- 悬停 = 微绽放（品牌色光晕从中心展开）+ 阴影加深  
- 按压 = 瞬时微缩放（像被轻轻按下去）  
- 列表选中 = 左指示条 + 轻背景沉降，不用大色块  

---

## 2. 动效语言（统一规范）

| 档位 | 时长 | 用途 |
|---|---|---|
| **Snappy** | 120ms | 按压缩放、图标微缩、光标反馈 |
| **Smooth** | 180ms | 悬停绽放、卡片抬升、指示条 |
| **Elegant** | 240ms | 抽屉、页面切换、Toast |

**Easing**  
- 默认：`CubicEase EaseOut`  
- 按压回弹：`CubicEase EaseOut`（去时快、回时稳）  
- 禁止 Bounce/弹跳（不符合「简约」）

**绽放 Bloom 定义**  
```
默认: Opacity=0, Scale=0.55
悬停: Opacity=1, Scale=1.0   // 180ms EaseOut
离开: Opacity=0, Scale=0.55  // 140ms EaseIn
```
绽放层：品牌 `PrimaryLight` 圆角底 / 或透明度 12% 品牌色圆，**不另开模糊**。

**按压 Press**  
```
IsPressed → Scale 0.97 (60ms)
释放    → Scale 1.00 (100ms)
```

**卡片抬升 Lift**  
```
Hover: TranslateY -1px + ShadowCard→ShadowMd + Border 主色
```

---

## 3. 分波实施

### Wave 1 — 交互基座（Theme，全局受益）
1. `BloomHalo` 结构写入 Primary / Secondary / Icon / Danger / Text 按钮模板  
2. 按压 `ScaleTransform 0.97`  
3. `InteractiveCardStyle` 升级：抬升 + 绽放边  
4. `PromptShowcaseCard` / 会话项 / 文档项 hover 绽放  
5. `TransitioningContentControl`：淡入 + Y 8→0  

### Wave 2 — 主壳微交互
1. Rail 图标 hover 微缩放 1.06 + 绽放底  
2. 品牌角标 hover 绽放  
3. 抽屉折叠图标旋转 180°（开↔关）  
4. 发送按钮：可发送时脉冲光晕（Opacity 0.3↔0.55，2s）  

### Wave 3 — 列表与内容流
1. 搜索结果卡 / 文档项：悬停绽放 + 抬升  
2. 会话列表项：指示条 + 绽放  
3. 空态 CTA 按钮统一 Bloom  
4. 进度条流光已有，补完成时 100% 闪一下成功色  

### Wave 4 — 深色主题与性能核对
1. Dark 下 Bloom 用 `#818CF8` 12% 叠加  
2. 关闭系统动画时全部 fallback 到静态态  
3. 列表虚拟化下不给每一项预挂 Effect  

---

## 4. 不做什么（避免掉档）

- 不做粒子、波纹、弹簧乱跳  
- 不给大面积区域挂 DropShadow 动画  
- 不把所有 hover 都做成 scale（列表只抬升 1px）  
- 不在对话流里做消息入场乱飞（仅首屏空态可轻浮入）  

---

## 5. 验收标准

- [ ] 主按钮悬停：可见品牌光晕绽开，180ms 内完成  
- [ ] 主按钮按压：有 0.97 微缩「按下去」手感  
- [ ] 搜索/文档卡悬停：阴影加深 + 轻微上浮  
- [ ] 切换页面：淡入 + 上移 8px，无白闪  
- [ ] 切主题：Bloom 色自动跟随，无需改页面  
- [ ] 系统关动画：界面立即稳定可见  

---

## 6. 建议执行顺序

**先做 Wave 1（本回合）** → 主壳 Wave 2 → 列表 Wave 3 → 深色/性能 Wave 4  
Wave 1 完成后，全局按钮与卡片的手感会立刻「现代一截」。
