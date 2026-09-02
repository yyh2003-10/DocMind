# DocMind 护城河升级实施蓝图：知识编译引擎 × 主动巩固（Phase 4）

- **状态：规划完成，未实施**（2026-08-29 制定；同日经代码级评审修订：M4.6 通知形态改为分层提醒、M4.1 调度改为 daemon 线程并补 LLM 成本护栏、M4.7 补消费侧止损指标、若干行号/事实勘误）。任何代码/schema 变更在产品负责人明确下达推进指令前不得执行；推进时按里程碑顺序开工。
- 前置依赖：`docs/产品计划-2026H2-科研渠道与巩固闭环.md`（阶段 0–4，本文称"H2 计划"）已规划的功能默认先落地，本蓝图在其之上加码/补位。
- 制定依据：`docs/research/市场调研-2026-08-AI知识库赛道全景.md` + 2026 行业趋势（LLM Wiki/知识编译范式、Agentic 知识管理、FSRS 学习调度）+ 代码库实测。
- 定位一句话：**把"功能完整的知识库工具"改造成"用户知识资产越用越值钱、换不走的复利引擎"。**
- 所有文件路径、行号、函数签名均为 2026-08-29 代码库实测结果。

---

## 一、战略背景：为什么做这次升级

### 1.1 护城河第一性原理

工具层功能（解析格式、混合检索、引用溯源）全部可被竞品复制（AnythingLLM/RAGFlow 开源、Cherry Studio 免费）。真正的护城河只有三类：

| 护城河类型 | 迁移成本来源 | 赛道证据 |
|---|---|---|
| **知识资产复利** | 图谱实体、蒸馏卡片、复习记录、整理痕迹——已结构化的心血换工具即清零 | Heptabase ARR $120 万靠笔记资产口碑；NotebookLM 3000 万用户"2% 悖论" |
| **工作流粘性** | 文件监控自动摄入 + 每日回顾，嵌入每天开机即用的节奏 | Obsidian Copilot 173 万下载；印象笔记"只存不用"衰落的教训 |
| **生态位** | agent 依赖 DocMind 作为记忆层，MCP 调用成为习惯 | MCP 已成"agent 世界的 HTTP"；HN"该领域尚无领导者" |

### 1.2 2026 范式冲击（为什么要主动升级）

1. **RAG 有隐形天花板**：每次查询从零"检索碎片再拼凑"，知识不积累——这是 r/PKMS 里"Ask your second brain 聊天框"被点名为最无用 AI 功能的根因。**DocMind 的对话页若不建立在"已积累的结构化知识"之上，同样是弱功能。**
2. **范式转移：RAG → Agentic RAG → LLM Wiki（知识编译）**：知识应被"编译"成结构化形态并由 LLM/系统持续维护，而非每次重新拼装。知芽、GBrain 等新范式产品已验证方向。
3. **学习巩固头部留白**：NotebookLM 闪卡无间隔重复调度且社区公开请愿；Anki 1000 万用户但 AI 靠零散 add-on、创始人淡出；StudyFetch $11.5M A 轮验证资本热度。"知识库 + 图谱 + 复习调度"三位一体的闭环产品**至今缺位**——DocMind 是唯一已有前两块拼图的选手。

### 1.3 DocMind 已握住的底牌（本次升级的支点）

知识编译引擎的三大积木**已全部存在**，缺的只是把它们串成**持续运行的过程**：

| 积木 | 实测现状 | 对应"编译"环节 |
|---|---|---|
| `extract`（实体抽取） | `curator.py:37` VALID_ACTIONS 已含 extract；`curate()` 已支持增量跳过（`get_extracted_doc_ids`，`curator.py:722-731`） | 知识结构化 |
| `consolidate`（蒸馏笔记） | `consolidate_notes()`（`curator.py:523`），蒸馏笔记以 `tags=["distilled"]` 标记写入 documents 表 | 知识蒸馏 |
| `distill`（实体知识卡片） | `POST /v1/graph/entities/distill`（`http.py:2328-2392`），输出 markdown_card + suggested_tags | 知识固化 |

**分水岭**：它们是**被动的、离散的功能**；本次升级要把它们变成**主动的、持续运行的编译引擎**。

---

## 二、现状速览（执行者必读，实测）

### 2.1 后端（src/doc2mind/）

| 模块 | 现状 | 关键位置 |
|---|---|---|
| `core/curator.py` | 820 行。`VALID_ACTIONS=("enrich","categorize","dedup","consolidate","extract")`；`curate()`（L650-661）逐文档跑 enrich/categorize/extract，逐集合跑 dedup/consolidate；`_doc_representative_text`（L239-246）优先 summary、无摘要才取前 3 个 chunk 截断（**中后段零覆盖**——已知缺陷，M2.2 动机不变） | L37、L239、L523、L650、L721 |
| `core/store/graph_store.py` | 546 行。表：`entities` / `entity_relations` / `chunk_entities`（L81-106）。**无 distill 方法、无 entity_aliases、无 entity_interactions**。方法：upsert_entity / upsert_relation / link_chunk / add_document_entities / get_extracted_doc_ids / get_graph / get_entity_relations / find_entities_by_keyword / get_stats / get_entity_detail | L81-106 |
| `server/http.py` | 2927 行。`POST /v1/graph/entities/distill`（L2328-2394）：EntityDistillRequest{entityId,entityName,entityType,provider_config,model,**collection,dialogueSummary,localSnippets,webReferences**} → EntityDistillResponse{entityId,entityName,markdownCard,suggestedTags,model}（多出的 `collection` 等字段对 M4.1 下沉 `_distill_entity` 是利好，可直接传 collection）。全局写锁为 `_AppState._write_lock`（threading.Lock，L927）。**无 /v1/review、/v1/compile、/v1/assets 端点** | L381、L927、L2328 |
| `server/mcp.py` | 工具注册从 L650 起：ingest / search / ingest_text / ingest_job / graph_get …（共 15 个）。**无 distill/review/compile 工具** | L650-809 |
| 后台任务基础设施 | **无任何 asyncio 常驻任务**：全部为请求触发 daemon 线程（ingest job L1772、reindex L2720、curate L2811 等）+ watchdog Observer（file_watcher）。空闲检测/常驻调度是 M4.1 需新建的部分，**非复用** | — |
| `core/config.py` | `_PERSIST_FIELDS` 从 L219 起（embed_*/chunk_*/search_*/llm_* 等） | L219 |
| 测试 | `tests/` 29 个文件、~301 个测试函数（2026-08-29 复核）。**test_graph_store.py 不存在**（graph_store 无专项测试）；`tests/test_mcp_tools.py` 不存在（mcp.py 无专项测试）。WPF 侧 DocMind.Tests 现有 190 个测试方法（HANDOVER 所记 47 已过期） | — |

### 2.2 WPF 客户端（DocMind/）

| 模块 | 现状 | 关键位置 |
|---|---|---|
| `ViewModels/MainViewModel.cs` | 511 行。9 个导航项分三组（工作台：对话/搜索/知识图谱；知识资产：文档库/导入/转换/质量看板；系统与支持：设置/调试日志）；`CurrentPage` setter（L233-257）按 ViewModelType 分发 | L178-257 |
| `ViewModels/GraphViewModel.cs` | 1274 行。ExtractGraphAsync（L302）、SendEntityChatAsync（L569）、ExecuteAction（L814）等；**已超 1200 行，继续膨胀** | L302、L569、L814 |
| `Services/BackendProcessService.cs` | 负责拉起本地后端并注入 `DOC2MIND_*` 环境变量 | — |
| 通知/Toast | **仅应用内自绘 Toast**（`Services/NotificationService.cs` + `Controls/ToastControl.cs`，挂载于 MainWindow，窗口可见才渲染；最小化托盘后无渲染面）。**无 OS 级通知**：未引用 WinRT 通知包，H.NotifyIcon 2.1.3 仅用于托盘菜单，无 BalloonTip 调用。无"开机拉取回顾队列 → 提醒"链路 | — |

### 2.3 H2 计划已承诺、本蓝图依赖的落点

| 里程碑 | 落点（H2 计划已定义） | 本蓝图依赖点 |
|---|---|---|
| M1.1 场景包框架 | `core/scenario/` + `/v1/scenarios` 系列 | M4.3 Zotero 包（随场景包分发） |
| M2.1 实体规范化 | graph_store 加 `entity_aliases`、合并 API | M4.1 编译引擎前必须先合并错误实体 |
| M2.2 抽取覆盖 | `_doc_representative_text` 改摘要+首/中/尾采样（config flag） | M4.1 编译质量前提 |
| M3.1 学习者状态表 | graph_store 加 `entity_interactions` | M4.4 FSRS 数据基础 |
| M3.2 回顾队列 | `/v1/review/queue` | M4.6 开机即回顾 |
| M3.3 出卡引擎 | 实体关系生成 3–5 题自测 | M4.4 FSRS 调度对象 |

---

## 三、里程碑总览

| 里程碑 | 名称 | 类型 | 前置依赖 | 工期参考 |
|---|---|---|---|---|
| M4.1 | 知识编译流水线（compile engine） | **加码（护城河核心）** | M2.1、M2.2 | 2–3 周 |
| M4.2 | 知识资产度量与仪表 | 补位 | M4.1（部分可并行） | 1 周 |
| M4.3 | Zotero 文献库打通 | 补位 | M1.1（场景包） | 0.5–1 周 |
| M4.4 | 学习巩固闭环 Phase 2（FSRS 调度） | **加码** | M3.1、M3.2、M3.3 + 回顾队列月使用≥4 次 | 1.5 周 |
| M4.5 | MCP 记忆层工具（compile/review/asset） | 补位 | M4.1、M3.2 | 3–5 天 |
| M4.6 | 开机即回顾（桌面主动通道） | 补位 | M3.2 | 3–5 天 |
| M4.7 | 护城河指标埋点与止损 | 贯穿 | 随各里程碑 | 贯穿 |

**依赖关系**：

```
M2.1/M2.2(实体卫生) ──→ M4.1(编译引擎) ──→ M4.5(MCP记忆层)
                                    └──→ M4.2(资产度量)
M3.1/M3.2/M3.3(回顾队列) ──→ M4.4(FSRS) ──→ M4.6(开机即回顾)
M1.1(场景包) ──→ M4.3(Zotero)
```

---

## 四、里程碑详细设计

### M4.1 知识编译流水线（护城河核心）

**目标**：把离散的 extract / consolidate / distill 串成**可调度、可观测、幂等**的编译过程——文档进 → 自动抽取 → 自动蒸馏 → 卡片入图谱 → 图谱驱动后续回顾。

#### 4.1.1 数据模型（graph_store.py 增量建表，均 `CREATE TABLE IF NOT EXISTS`）

```sql
-- 编译状态：每篇文档的编译进度（替代内存判断，支撑增量续跑）
CREATE TABLE IF NOT EXISTS compile_state (
    doc_id       TEXT PRIMARY KEY,
    collection   TEXT NOT NULL DEFAULT 'default',
    stage        TEXT NOT NULL,            -- none | extracted | distilled | consolidated
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT,
    updated_at   TEXT NOT NULL
);

-- 蒸馏卡片索引：蒸馏产物独立登记，供 RAG 溯源与资产度量
CREATE TABLE IF NOT EXISTS distilled_notes (
    doc_id       TEXT PRIMARY KEY,         -- 对应 documents.id（note:xxx）
    entity_ids   TEXT NOT NULL DEFAULT '[]', -- JSON 数组：驱动蒸馏的实体
    source_docs  TEXT NOT NULL DEFAULT '[]', -- JSON 数组：原始文档 id
    created_at   TEXT NOT NULL
);
```

#### 4.1.2 流水线编排（新模块 `core/compiler.py`）

- `compile_documents(store, embedder, llm, settings, collection=None, stages=("extract","distill","consolidate"), dry_run=False, progress=None, cancel_check=None) -> CompileReport`
- **编译阶段**：
  1. `extract`：复用 `curate()` 的 extract 路径（增量跳过已提取 doc_id，`curator.py:722-731`）；抽取成功写 `compile_state.stage=extracted`。
  2. `distill`：对**已抽取且蒸馏不足**的实体调用现有蒸馏模板（复用 `http.py:2328` 的 prompt 链路，下沉为可调用函数 `core/compiler.py::_distill_entity`），产物经 `ingest_text(..., force=False, auto_curate_on_ingest=False)` 落库为 `tags=["distilled"]` 文档，同时登记 `distilled_notes` 与 `compile_state.stage=distilled`。
  3. `consolidate`：复用 `consolidate_notes()`（`curator.py:523`），蒸馏产物满足 min_cluster 阈值时自动合并为高密度笔记。
- **调度方式**（主动而非被动）：
  - **常驻 daemon 线程**（非 asyncio 常驻任务——实测 server 现有零 asyncio 常驻任务，且编译内含阻塞 LLM 调用，进事件循环会阻塞全部 HTTP/SSE）：后端启动即起编译线程，循环检测**空闲时段**（自建"最近一次 HTTP 活动"时间戳埋点，可配 `compile_idle_minutes=5`）触发增量编译；
  - **默认关**：`compile_auto_enabled` 默认 `false`（对齐铁律 3），用户在设置页显式开启后空闲触发才生效；手动触发 `POST /v1/compile/run`（参数 collection / stages / dry_run）随时可用；
  - 与 `curate` 共用 `_AppState._write_lock`（http.py:927，threading.Lock）与 WAL，**同一时刻只跑一个写任务**。
- **幂等与安全**：stage 只前进不倒退；`attempts` 超限（默认 3）自动降级跳过并记 `last_error`；失败不中断批次；dry_run 只预览不落库。
- **LLM 成本护栏**：单次编译文档数上限（`compile_batch_max_docs`，可配，超出留待下轮幂等续跑）；编译进度经 `GET /v1/compile/status` 暴露到质量看板、支持取消（复用 job_cancel_events 模式）；云端 provider（API 计费）用户开启自动编译时，设置页给出成本提示。

#### 4.1.3 API（http.py 只挂路由，逻辑在 compiler.py）

| 端点 | 方法 | 说明 |
|---|---|---|
| `POST /v1/compile/run` | 触发一次编译 | 请求 `{collection?, stages?, dryRun?}` → `CompileReport` |
| `GET /v1/compile/status` | 查询当前/最近一次编译 | `{running, last_run_at, stages_done, docs_processed, docs_failed, next_scheduled}` |
| `GET /v1/compile/stats` | 按 collection 汇总编译覆盖率 | `{collection, total_docs, extracted, distilled, consolidated}` |

#### 4.1.4 测试

- 新 `tests/test_compiler.py`：三阶段全链路（fake LLM + 小型样例文档）、增量续跑（已 extracted 的跳过）、dry_run 零写、失败降级不中断、幂等（重复 run 无重复蒸馏）、`compile_state` 表迁移幂等。
- 补 `tests/test_graph_store.py`（现有缺口）：新增三表 CRUD 与 CASCADE 行为。

#### 4.1.5 验收

- 一个含 10+ 文档的 collection，**显式开启自动编译后**空闲 5 分钟自动完成 extract→distill→consolidate，`compile/stats` 覆盖率 100%；未开启时仅手动 `POST /v1/compile/run` 生效；
- 重复触发 run 不产生重复蒸馏卡片；
- 蒸馏卡片可通过 RAG 检索到，且 `distilled_notes` 登记完整。

---

### M4.2 知识资产度量与仪表

**目标**：让"知识资产复利"可度量——护城河必须可观测，否则无法判断是否在建。

#### 4.2.1 资产指标定义

| 指标 | 计算来源 | 护城河含义 |
|---|---|---|
| 图谱实体总数 / 月增速 | `graph_store.get_stats` + 历史快照 | 知识结构化速度 |
| 蒸馏卡片总数 / 月增速 | `distilled_notes` 表 | 知识固化速度 |
| 编译覆盖率 | `compile_state`（已提取/总文档） | 编译引擎运转率 |
| 复习完成量 / 正确率 | `entity_interactions`（H2 M3.1） | 巩固闭环活跃度 |
| RAG 回答引用蒸馏卡片占比 | RAG 溯源统计（目标 <20%，H2 已定义） | 防回声室 |

#### 4.2.2 实现

- 新 `core/assets.py`：`collect_metrics(store, graph_store, settings) -> AssetMetrics`，一次读取所有计数；
- 新表 `asset_snapshots(snapshot_date, metrics_json)`（幂等 `INSERT OR REPLACE`），每日/每次 `compile/run` 后落一份快照；
- API：`GET /v1/assets/metrics`（当日值 + 近 30 天趋势）；`GET /v1/assets/trend?days=30`（增速曲线）；
- WPF：质量看板页（`QualityViewModel`）加"知识资产"分区，展示实体/卡片/复习三卡与月增速；不新增导航项（复用现有页面，避免导航膨胀）。

#### 4.2.3 验收

- `/v1/assets/metrics` 返回各指标当前值与 30 天趋势数组；增速为负时 UI 有提醒文案。

---

### M4.3 Zotero 文献库打通

**目标**：科研楔子（H2 阶段 4）的"文献已经在 DocMind 里"前提。科研用户现实中使用 Zotero/EndNote 管理文献，缺此一跳则科研包永远停在"试用"。

#### 4.3.1 设计（务实 MVP，不做深集成）

- 新增配置项（config.py `_PERSIST_FIELDS` 追加）：`zotero_data_dir`（Zotero 数据目录，含 `zotero.sqlite` 与 `storage/`）；
- 监控方案：**复用现有 file_watcher 机制**，监听 `<zotero_data_dir>/storage/*/*.pdf`（Zotero 附件存放结构）；
- 元数据补齐（避免"8 位哈希文件名"）：扫描时读取 `<storage>/<hash>/` 下的 `.zotero-ft-cache` 不可靠，MVP 采用**文件目录内 `zotero.sqlite` 只读直查**（`sqlite3` 只读模式 + `busy_timeout`，不写 Zotero 库）；查不到则退回"文件名即标题"并允许用户在 DocMind 内改名；
- 分发：随科研场景包（H2 M1.1）提供 `presets/zotero.toml` 预设，引导用户一键填路径；
- 边界：不做双向同步、不做注释/高亮回写、不做 Better BibTeX 标签同步（列入手动 TODO，访谈验证后按需加）。

#### 4.3.2 落点

- `core/zotero.py`：`scan_storage_dir(data_dir) -> list[ZoteroItem{local_path, title, authors, year, collection}]`、`watch_zotero(watcher, data_dir, on_ingested)`（复用 file_watcher）；
- API：`POST /v1/zotero/scan`（手动触发扫描+导入预览，dry_run 默认）、`PUT /v1/config` 支持 `zotero_data_dir`；
- 测试：`tests/test_zotero.py`——构造 fake storage 目录（临时 PDF + 迷你 zotero.sqlite），验证扫描、去重（同一附件不重复导入）、坏库降级。

#### 4.3.3 验收

- 在 Zotero 中新拖入一篇 PDF → DocMind 自动摄入（≤2 分钟），标题/作者正确；重复拖入不产生重复文档。

---

### M4.4 学习巩固闭环 Phase 2：FSRS 调度

**目标**：把 H2 M3.3 的出卡引擎升级为"间隔重复调度"——补上 NotebookLM 请愿的缺口、Anki 现代化的窗口，坐实"知识库+图谱+SRS"空位。

**前置门槛（尊重 H2 止损）**：回顾队列月使用 ≥4 次/活跃用户（H2 度量表）达标后才启动本里程碑；不达标则本里程碑止步。

#### 4.4.1 算法与数据

- 算法：FSRS-6（公开算法，纯 Python 可实现，约 200 行），MVP 用官方默认参数（w 矩阵内置），复习记录攒够（≥500 条）后可离线拟合；
- 新表 `review_log`（graph_store.py 增量）：

```sql
CREATE TABLE IF NOT EXISTS review_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id    TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    card_type    TEXT NOT NULL,             -- fill_blank | relation | categorize
    rating       INTEGER NOT NULL,          -- FSRS: 1=again 2=hard 3=good 4=easy
    due_at       TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    stability    REAL,
    difficulty   REAL,
    created_at   TEXT NOT NULL
);
```

- 调度状态演进：`entity_interactions`（M3.1，记录 visit/chat/quiz 事件）→ `review_log`（新增，记录每次作答与 FSRS 状态）；`review/queue` 排序算法从"陈旧度×doc_count"升级为"FSRS due_date 优先 + 权重分（M3.2 排序保留作 tie-break）"。

#### 4.4.2 API 增量

- `POST /v1/review/answer`：请求 `{entityId, cardType, rating}` → 回写 review_log、更新 FSRS 状态、返回 `{next_due_at, next_interval}`；
- `GET /v1/review/queue` 扩展：响应增加 `card_type`、`due_in_days`（向后兼容：**只加字段不改旧字段**）；
- `GET /v1/review/stats`：复习计数、正确率、FSRS 稳定性分布。

#### 4.4.3 测试

- `tests/test_fsrs.py`：FSRS 调度函数确定性（同输入同输出）、rating 后 due 日期单调性、四档 rating 边界、review_log 幂等写入；
- 回顾队列排序测试（due_date 优先）。

#### 4.4.4 验收

- 对同一实体连续作答不同 rating，下次 due 时间按 FSRS 规则正确推进；30 天后重启队列仍按持久化的 FSRS 状态排序。

---

### M4.5 MCP 记忆层工具

**目标**：外部 agent 从"可用 DocMind"升级为"**依赖 DocMind 的结构化知识**"。obsidian-mcp 免费但无图谱、无蒸馏、无复习——本里程碑把这三件暴露给 agent。

#### 4.5.1 新增 MCP 工具（mcp.py 注册表追加，**必须同步补 tests/test_mcp_tools.py 注册表自检**）

| 工具 | 参数 | 返回 |
|---|---|---|
| `compile_run` | `collection?`, `stages?`, `dryRun?` | 编译报告（与 /v1/compile/run 同构） |
| `review_queue` | `limit?`, `collection?` | 待复习实体 + 关联切片 + 卡片类型 |
| `review_answer` | `entityId`, `cardType`, `rating` | 下次复习时间 |
| `assets_metrics` | — | 知识资产指标（实体/卡片/复习/编译覆盖率） |
| `entity_distill` | `entityId`, `entityName`, `entityType?`, `model?` | 复用 distill 链路，返回 markdown_card（供 agent 直接引用） |

#### 4.5.2 落点

- 各 `_tool_*` 函数调用 `core/compiler.py` / `core/assets.py` / `core/store/graph_store.py` 的现有函数，**不复制逻辑**；
- 新 `tests/test_mcp_tools.py`：工具注册表自检（防漏注册）+ 每个新工具的最小调用测试（fake 依赖）。

#### 4.5.3 验收

- 任意外部 MCP 客户端调用 `review_queue` 拿到结构化复习建议；调用 `entity_distill` 拿到高密度卡片；注册表自检测试通过。

---

### M4.6 开机即回顾（桌面主动通道）

**目标**：让"主动找你"在桌面端落地——用户不开应用就收不到回顾提醒，是 H2 M3.2"主动"字号的缺口。

#### 4.6.1 设计（分层提醒）

**边界先行**：后端由 WPF 拉起，**应用不运行 = 后端不运行 = 回顾队列无从计算**，"应用未运行也能提醒"不在本里程碑范围。OS 级定时推送（退出时快照到期项 + WinRT ScheduledToastNotification，需新增通知 NuGet 包 + AppUserModelID 注册与卸载清理）列为后续访谈验证后的增强项，暂不实施。

按应用当前形态选通道：
- **窗口可见**：后端 Online 后调 `GET /v1/review/queue?limit=1`，非空且非"静默期"（同日去重）→ 触发现有应用内 Toast（`NotificationService`）；
- **最小化到托盘**（本产品常态形态）：同上条件 → H.NotifyIcon 气泡通知（`TrayService` 已持有托盘图标，包已引用，增量小）；
- 点击提醒 → 恢复窗口并导航到图谱页"今日回顾"入口（M3.2 已规划的入口）；
- 同日去重用**独立提醒时间记录**（AppSettings 新增 `last_review_reminder_date`），**不依赖 M4.4 才建的 `review_log` 表**；
- 设置项 `review_reminder_enabled`（默认开）、`review_reminder_time`（默认 09:00）进 SettingsViewModel（AppSettings 为 POCO 自动持久化，加属性即生效）；
- 复用 `ReviewViewModel`（H2 工程落地已定：回顾功能独立 ViewModel，不膨胀 GraphViewModel）。

#### 4.6.2 落点

- `DocMind/Services/ReviewReminderService.cs`：常驻轻量计时器 + 同日去重 + 通道分流（窗口可见→NotificationService；托盘最小化→TrayService 气泡）；
- `DocMind/ViewModels/ReviewViewModel.cs`（H2 已规划，本里程碑补 Toast 触发入口）；
- 测试：`DocMind.Tests/ReviewReminderServiceTests.cs`（同日去重、非空队列才提醒、开关生效）。

#### 4.6.3 验收

- 有到期复习项时，重启应用 ≤30 秒内出现一次提醒（窗口可见→应用内 Toast；最小化托盘→气泡通知）；同日不再重复提醒；关闭开关后无提醒；应用未运行时不提醒（边界内，见 4.6.1）。

---

### M4.7 护城河指标埋点与止损（贯穿）

| 指标 | 采集点 | 目标 | 止损动作 |
|---|---|---|---|
| 蒸馏卡片月增速 | `assets/trend` | ≥+20%/月（活跃库） | 低于则回查蒸馏质量与实体卫生 |
| 蒸馏卡片消费率（被查看/编辑/复习） | 卡片查看/编辑埋点 + review 记录 | 环比非零且缓升 | 持续为零 → 蒸馏产物无用户价值，暂停 distill 扩量、回查卡片质量（**M4.1 真止损信号**） |
| 编译覆盖率（运维观测，不作止损） | `compile/stats` | ≥90%（导入后 7 天内） | 低于则降级为"入库即同步抽取"直连模式 |
| 复习完成率 | `review/stats` | ≥60% 到期项 7 天内完成 | 低于则收敛提醒频率，避免打扰 |
| RAG 引用蒸馏卡片占比 | RAG 溯源统计 | <20% | 超标则进一步降权（H2 已定义，沿用） |
| MCP review_queue/entity_distill 调用量 | MCP 埋点 | 环比增长 | 持平则暂缓 agent 侧功能投入（H2 已定义，沿用） |

---

## 五、工程铁律（完整性保障，沿用 H2 四条并追加）

1. **纯增量**：新端点/新表/新字段只增不改；现有 API response shape 不动；缺列走既有幂等迁移模式（`sqlite_vec._migrate_documents_meta` 的 PRAGMA 检查 + ALTER 模式）。
2. **破坏性操作零静默**：实体合并、图谱重建、consolidate 删除原笔记一律用户主动触发 + dry-run 预览 + JSON 快照 + 单事务执行。
3. **行为变化走开关**：编译空闲触发、FSRS 排序替换等行为类改动加 config flag，默认关、灰度开。
4. **LLM 失败降级沿用 skipped 模式**：编译任一段失败 → 记 last_error + attempts+1，不阻塞整批；无 LLM 时编译退化为"仅 extract 依赖已有 LLM 才执行"。
5. **后台写任务单飞**（追加）：编译任务与 curate/手动写操作共用 `_AppState._write_lock`（http.py:927，threading.Lock）+ WAL + busy_timeout，同一时刻仅一个写任务；启动时检测上次编译中断 → 从 `compile_state` 续跑而非重跑。
6. **防膨胀**（追加）：编译/资产/Zotero 逻辑独立模块（`core/compiler.py`、`core/assets.py`、`core/zotero.py`），http.py 只挂路由；WPF 回顾/提醒逻辑独立 `ReviewViewModel` + `ReviewReminderService`，不继续膨胀 GraphViewModel（现 1274 行）。

---

## 六、风险登记表

| # | 风险 | 影响/概率 | 缓解措施 |
|---|---|---|---|
| 1 | 后台编译与用户手动 curate 并发写冲突 | 中/低 | 全局写锁单飞 + WAL + busy_timeout（铁律 5） |
| 2 | 自动蒸馏产生低质卡片污染知识库 | 高/中 | 蒸馏仅针对"已抽取且有图谱关联"的实体；dry-run 预览；`distilled_notes` 可溯源、可删除；防回声室降权（<20%） |
| 3 | FSRS 训练数据不足导致调度不准 | 中/中 | MVP 用官方默认参数；≥500 条作答后再拟合；止损门槛（回顾月使用≥4 次）先行 |
| 4 | Zotero 库结构版本差异（sqlite 只读直查兼容性） | 中/低 | 只读 + busy_timeout + try/except 降级为"文件名即标题"；`scan` 提供 dry_run 预览 |
| 5 | compile_state/distilled_notes 与 documents 元数据漂移 | 中/低 | 均以 documents.id 为主键外键对齐；`compile/stats` 覆盖率监控暴露漂移 |
| 6 | mcp.py 工具继续膨胀漏注册 | 中/高 | `tests/test_mcp_tools.py` 注册表自检（防漏注册）；工具函数只做参数适配、逻辑下沉到 core 模块 |
| 7 | WPF 版本与后端版本偏斜 | 低/低 | 同包发布（BackendProcessService 拉起本地后端）；仅加字段不改旧字段 |
| 8 | 开机即回顾打扰用户 | 中/中 | 默认 09:00 一次、同日去重、设置可关（review_reminder_enabled） |
| 9 | 后台自动编译静默消耗 LLM（API 计费用户账单冲击） | 中/中 | 自动编译默认关 + 显式开启 + 设置页成本提示；单次文档数上限（compile_batch_max_docs）；进度可见、可取消（M4.1 成本护栏） |

---

## 七、实施顺序建议

```
阶段A（并行、无新依赖）：M2.1/M2.2 实体卫生 ──→ M4.3 Zotero（独立）
阶段B：M4.1 编译引擎（依赖 A）──→ M4.2 资产度量
阶段C：M3.1/M3.2/M3.3 回顾队列 ──→ M4.4 FSRS（门槛达标后）──→ M4.6 开机即回顾
阶段D：M4.5 MCP 记忆层（依赖 M4.1 + M3.2）
贯穿：M4.7 埋点随各里程碑落地
```

**首个开工任务建议：M4.3 Zotero**（独立、低风险、快速见效，直接服务科研楔子），随后 M4.1 编译引擎（护城河核心）。

---

## 八、验收总清单

- [ ] 编译流水线三段自动跑通、幂等、可观测（M4.1）
- [ ] 知识资产四指标可查询、有 30 天趋势（M4.2）
- [ ] Zotero 新增 PDF 自动摄入、元数据正确、去重（M4.3）
- [ ] FSRS 调度正确、queue 排序升级、回顾门槛达标（M4.4）
- [ ] 5 个 MCP 工具注册 + 注册表自检测试通过（M4.5）
- [ ] 分层提醒（应用内 Toast / 托盘气泡）、同日去重、可开关（M4.6）
- [ ] 止损表全部指标接入埋点（M4.7）
- [ ] 现有 29 个测试文件 / ~301 个测试函数全部保持绿色（WPF 侧 190 个测试方法保持绿色）；新增 tests/test_compiler.py、test_graph_store.py、test_zotero.py、test_fsrs.py、test_mcp_tools.py
