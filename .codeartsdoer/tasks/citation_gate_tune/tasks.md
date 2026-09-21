# T3 任务清单：弱相关引用门控调参（citation_gate_tune）

> 依据：`.codeartsdoer/specs/citation_gate_tune/spec.md`（FR-01 ~ FR-15）
> 关联：`docs/agents/dispatch/issues/ISSUE-03-citation-gate-tune.md` + 「中后端均匀提速」专项
> 产出物：调参前后对比表（问题 → 引用数/来源）+ 推荐默认阈值与理由
> 回归基线：
> - 单组：`python -m pytest tests/test_confidence_citation.py tests/test_context_and_citation_gates.py tests/test_rag.py -q --tb=line`
> - 全量：`scripts/run_full_verification.py`（排除 retrieval_eval 环境债）

## 现状盘点（已有实现，任务以此为增量）

- `partition_citation_hits()`（`src/doc2mind/core/rag.py:1929`）已实现 cite/bg/discard 三档分流，`gate` 含 `citation_min_score/bg_floor/bg_ratio/reranked_usable/hit_count/cite_count/bg_count/discarded_count/topic_demoted_count/top_k`。
- `_hit_supports_query()`（`rag.py:1896`）已实现词法主题重叠。
- 调用点 `rag_answer_stream`（`rag.py:2860`）已消费分区并产出「✔/⚠ 检索知识库」状态文案。
- `audit_answer_citations()`（`rag.py:500`）已实现 `disclaimer_only / evidence_support` 排除（FR-12），`tests/test_confidence_citation.py` 已覆盖。
- 阶段耗时已部分收集于 done 帧 `timing`（`retrieval_ms / web_ms / llm_first_token_ms / generation_ms / total_ms`），但未用 `stage_` 前缀暴露、缺 `stage_tool_ms`、缺开关。
- 慢首 token 一次性提示已存在（`rag.py:1546-1572`，`llm_first_token_slow_ms` 默认 30000），非 FR-14 要求的周期性递增提示。

## 依赖与顺序总览

```text
任务 1（门控计数+异常回退）─┬─> 任务 2（诚实降级与时序）
任务 3（阶段耗时+背景上限）──┴─> 任务 4（慢模型等待感知）
任务 5（真实库调参）依赖 1/2/3/4 完成后执行
任务 6（文档+全量验证）依赖 5 的回归数据
```

---

## 1. 引用门控计数口径与异常回退增强（FR-03 / FR-05 / FR-06 / FR-15）

**目标**：让「分数不足」与「主题不符」成为两个可独立统计、可在状态流/日志中区分的计数；引入 GateStats 对账；门控自身抛异常时绝不吞真相关命中。

### 1.1 拆分 dropped_by_score / dropped_by_topic 计数并对账
- 文件：`src/doc2mind/core/rag.py`（`partition_citation_hits`，1929-2020）
- 改动：在逐条分流中累计 `score_fail`（`rel < citation_min_score`）与 `topic_fail`（`rel >= citation_min_score` 但 `_hit_supports_query` 为 False）两个独立计数；`gate` 字典新增 `dropped_by_score` / `dropped_by_topic`（数值口径与 spec 6.3 一致：score 项含降为背景部分并注明口径）；在 `gate` 中输出 `cross_check`（`cite_count + bg_count + dropped_by_score + dropped_by_topic == hit_count`，不符时置 False 并日志告警）。
- 验收：`python -m pytest tests/test_context_and_citation_gates.py -q --tb=line` → 新增用例通过；`gate["cross_check"] is True` 覆盖构造混合数据。

### 1.2 门控判定异常回退「按可引用处理」（FR-06）
- 文件：`src/doc2mind/core/rag.py`（`rag_answer_stream` 调用点，约 2882-2894）
- 改动：用 `try/except Exception` 包裹 `partition_citation_hits(...)` 调用与 `_hit_supports_query` 路径；异常时整批命中按可引用处理（`cite_hits = hits`），`logger` 用 `logger.warning` 记录「门控异常已回退按可引用处理」+ 异常信息（堆栈级别 debug），绝不静默丢弃。
- 验收：新增单测 mock 门控抛异常 → 命中仍进入 `sources`、`cite_hits` 非空，捕获日志中「门控异常」字样。

### 1.3 状态流过滤说明按两类原因分开展示（FR-05 / FR-15）
- 文件：`src/doc2mind/core/rag.py`（2930-2974 状态文案区）
- 改动：将现有 `demote_reason` 文案扩展为「低分丢弃 N / 主题不符降级 M / 降为背景 K」三类分开表述（复用 1.1 的独立计数），保证两种失败原因可在状态流 message 中区分；全部丢弃时保留「⚠ 检索知识库：命中 X 个分块…不作为引用依据」文案。
- 验收：构造「低分 N 条 + 高分偏题 M 条」混合失败 → 状态流/日志出现 ≥2 个可区分过滤计数（FR-05 验收 a）。

### 1.4 单测补充（混合失败场景 + 对账断言）
- 文件：`tests/test_context_and_citation_gates.py`
- 改动：新增用例 `test_mixed_score_and_topic_demotions`（低分 2 条 + 高分偏题 1 条 → `dropped_by_score==2`、`dropped_by_topic==1`、`cross_check is True`、互不重叠）；新增 `test_gate_exception_falls_back_to_citable`（mock partition 抛异常 → 不丢命中）。
- 验收：`python -m pytest tests/test_context_and_citation_gates.py -q --tb=line` → 新旧用例全部 passed。

## 2. 诚实降级与空引用时序（FR-04 / FR-07 / FR-11 / FR-12）

**目标**：空命中/全门控时文案诚实且不硬编编号；过滤说明必须早于 LLM 首 token 到达客户端。

### 2.1 空命中诚实表达与免责声明审计回归
- 文件：`tests/test_confidence_citation.py`、`src/doc2mind/core/rag.py`（`audit_answer_citations` 复用，`_SUBJECT_ANCHOR` 复用）
- 改动：无代码改动预期（FR-11/12 已实现），执行回归并确认「[[1]] 并未提及」类免责声明不被计入 `evidence_support`；若回归暴露口径缺陷则修复 `audit_answer_citations` 并保持 `disclaimer_only` 语义。
- 验收：`python -m pytest tests/test_confidence_citation.py -q --tb=line` → 全部通过（含 `test_disclaimer_citations_not_counted_as_support` 等）。

### 2.2 空引用提示必须先于首 token（FR-07）
- 文件：`tests/test_rag.py`（新增流式用例）
- 改动：新增用例解析 `rag_answer_stream` 输出帧序列：构造「全部本地命中被门控且无联网」场景，断言 `⚠ 检索知识库...无可用库内依据` 状态帧出现于首个 `token` 帧与 `正在生成回答...` 之前；同时断言输出不含任何伪造 `[n]` 编号（对最终文本断言 `"知识库未找到"` 或明确无依据表述）。
- 验收：`python -m pytest tests/test_rag.py -k "gate or citation or empty" -q --tb=line` → 用例 passed。

## 3. 阶段耗时可观测与背景注入上限（FR-10 / FR-13）

**目标**：done 帧/状态流暴露 ≥5 个 `stage_` 前缀阶段耗时（检索、联网、工具、LLM 首 token、生成、总计）；背景注入有界；全部可开关、向后兼容。

### 3.1 新增配置并登记白名单
- 文件：`src/doc2mind/core/config.py`（Settings 字段区 ~210、字段白名单 ~490、`control/preset` 同步区 660-790）
- 改动：新增 `background_hit_limit: int = 5`（背景条数上限，0 = 关闭背景注入）；新增 `stage_elapsed_enabled: bool = True`（关闭时 FR-13 埋点仅落日志不落状态流/done 帧）；两字段登记进保存/下发白名单（含 `docs/api.md` 稍后同步）；保持 `citation_min_score` 默认 0.45 不变。
- 验收：`python -m pytest tests/test_config.py -q --tb=line` → Settings 结构体解析正常；`python scripts/` 下引用 config 的服务端可正常启动。

### 3.2 背景注入上限截断（FR-10）
- 文件：`src/doc2mind/core/rag.py`（2912-2924 背景注入区）
- 改动：注入 `bg_hits` 前按 `background_hit_limit` 截断（保留相关度最高者）；被截断部分计入丢弃计数并纳入状态流「背景与引用占比」说明（如「背景注入 5/12，超限 7 条已丢弃」），避免长背景拖慢生成。
- 验收：构造 12 条 bg_hits + limit=5 → 注入上下文总量 ≤5 条、（状态流可见超限丢弃数）；`python -m pytest tests/test_rag.py -k bg -q --tb=line` 通过。

### 3.3 `stage_*` 阶段耗时暴露（FR-13）
- 文件：`src/doc2mind/core/rag.py`（done 帧组装区 1821-1853 + 各阶段计时点）
- 改动：在 `timing` 基础上新增映射 `stage_retrieval_ms / stage_web_ms / stage_tool_ms / stage_first_token_ms / stage_generation_ms / stage_total_ms` 写入 done 帧顶层与 `timing` 内（非流式路径 `stage_first_token_ms` 记 `-1` 并附 `"stage_first_token_mode": "n/a"` 说明；RAG 非 Agent 模式 `stage_tool_ms` 记 0）；受 `stage_elapsed_enabled` 开关控制；结构化日志（`logger.info`）输出阶段耗时便于脚本解析。
- 验收：构造 mock 全链路对话（含联网）→ done 帧含 ≥5 个 `stage_` 字段，数值非负且各段均值自洽（检索+联网+首token+生成 ≤ 总耗时，允许少量统计重叠用 `≤` 断言）；`python -m pytest tests/test_rag.py -k stage -q --tb=line` 通过。

### 3.4 测试：阶段字段 + 背景有界断言
- 文件：`tests/test_rag.py`
- 改动：新增 `test_stage_elapsed_fields_present`（断言 done 帧 ≥5 阶段字段非负自洽、开关关闭时字段缺省）与 `test_bg_injection_bounded`（大量 bg 时上下文总数有界）。
- 验收：`python -m pytest tests/test_rag.py -q --tb=line` → 新用例 passed。

## 4. 慢模型等待感知（FR-14）

**目标**：慢模型首 token 超阈值后，状态流周期性出现「已等待 Ns」且 N 单调递增，不做静默转圈。

### 4.1 周期性等待提示
- 文件：`src/doc2mind/core/rag.py`（1543-1572 首 token 计时区）
- 改动：将现有一次性慢提示改为周期上报：首 token 未到达且已等待 ≥ `llm_first_token_slow_ms`（默认 30000，可配置）后，每间隔 5s 追加一条 `{"type":"status","message":"正在生成回答…（已等待 Ns），可关闭联网/换模型/继续等待"}`，N 取当前已等待秒数并单调递增；次数封顶（如 12 次）防刷屏；首次提示保留可操作建议文案。
- 验收：mock 慢 LLM（首 token > 5s）→ 状态流出现 ≥2 条「已等待」提示且 N 递增（FR-14 验收 a）。

### 4.2 测试：等待提示递增
- 文件：`tests/test_rag.py`（或 `tests/test_weak_model_resilience.py`）
- 改动：新增用例 mock LLM 首 token 延迟（如 6s）→ 断言出现「正在生成回答…（已等待 Ns）」且 N 随时间递增；同时断言输出正确性不受影响（tokens 完整到达）。
- 验收：`python -m pytest tests/test_rag.py -k slow -q --tb=line` → 用例 passed。

## 5. 真实库调参回归与阈值定稿（FR-02 / FR-08 / FR-09；ISSUE-03 验收 2/3）

**目标**：用真实知识库产出「问题 → cite 数 / bg 数 / 来源」对比表，据数据定稿推荐默认阈值。

### 5.1 阈值校准脚本 `tools/calibrate_citation_threshold.py`（新建）
- 文件：`tools/calibrate_citation_threshold.py`（新建，agent-p0 当前无 tools/ 目录，需新建并注册为可执行脚本）
- 改动：脚本载入真实知识库（`doc2mind` collection，默认路径 `%LOCALAPPDATA%\doc2mind\doc2mind.db` 可覆盖），对固定问题集（≥10 问：≥4 库内概念、≥3 库外概念、≥3 偏题/边缘）依次以 `citation_min_score ∈ {0.30, 0.45, 0.55}` 跑 `partition_citation_hits`/RAG 链路，输出 CSV/表格：问题 → 阈值 → cite 数 / bg 数 / dropped 数 → 来源清单（前 4）+ 门控说明；附带 `--dry-run` 只读不写库。
- 验收：`python tools/calibrate_citation_threshold.py --collection doc2mind` → 输出对比表文件（如 `docs/verification/citation_gate_calibration_<date>.csv`），行数与问题集一致且三档计数可对账（spec 6.3 cross-check）。

### 5.2 真实库回归：库外无伪造引用、库内引用相关
- 文件：`tests/test_real_kb_citation_gate.py`（新建，标记 `@pytest.mark.integration`）+ 复用 `scripts/run_full_verification.py` 框架
- 改动：对真实库执行两场景验收：①问库外概念（如「什么是豆包」）→ 断言无伪造 [n] 或出现「知识库未找到/无依据」；②问库内概念（如「什么是 DocMind 的引用门控」）→ 断言引用切片正文含问题关键词且属 cite_hits。场景库外失败时允许人工标注回归，但禁止静默通过。
- 验收：`python -m pytest tests/test_real_kb_citation_gate.py -q --tb=line` → 两场景通过；记录门控误杀实例（同义改写无词汇重叠被降为背景等）到报告。

### 5.3 调参对比表与推荐默认值归档
- 文件：`docs/verification/citation_gate_calibration_<date>.md`（新建产出物）
- 改动：基于 5.1 数据整理「调参前后对比表」：每问题列出 0.30/0.45/0.55 下的怂 cite 数、bg 数、来源来源清单；给出推荐默认阈值（建议基于数据在 0.45 或微调，不得拍脑袋）；记录门控误杀风险（同义改写无词汇重叠、短 query 无 token 时门停用等）与建议默认值理由。
- 验收：文档可被 review：包含对比表、推荐值、误杀风险与数据来源（链接 5.1 CSV）。

### 5.4 非法阈值 sanitize 加固（FR-08）
- 文件：`src/doc2mind/core/config.py`（sanitize 链）+ `tests/test_config.py` 或 `tests/test_context_and_citation_gates.py`
- 改动：`citation_min_score` 为负数或非数值字符串 → 取 0（= 关闭逐条门控）并 `logger.warning` 告警，不阻塞对话；保持 0 时与旧组级逻辑一致（`partition_citation_hits` 走回退路径）。
- 验收：新增用例设置 `citation_min_score=-1` 与 `"abc"` → 行为等于 0 值模式（不启用逐条门控），日志含告警；相关 pytest 通过。

## 6. 配置、文档与全量验证（FR-08 / FR-09；README 状态更新）

**目标**：阈值与新增配置可追溯、API 契约文档同步、全量回归通过、派工表状态更新。

### 6.1 更新 `docs/api.md` 状态流/done 帧契约
- 文件：`docs/api.md`（RAG 流式 SSE 章节 ~289-341）
- 改动：补充 done 帧新增 `stage_*` 字段说明（含 `stage_first_token_mode: "n/a"` 特例）、`timing` 内新增 `dropped_by_score/dropped_by_topic`、状态帧「已等待 Ns」周期提示语义；说明兼容性（旧客户端忽略未知字段）。
- 验收：人工核对 `docs/api.md` 字段与实际 done 帧 JSON 一致（可用 `tmp_debug_agent_stream.py` 之类流调试脚本抽查一帧）。

### 6.2 配置注释与派工表状态更新
- 文件：`src/doc2mind/core/config.py`（citation_min_score 注释补充推荐默认值与误杀风险引用）、`docs/agents/dispatch/README.md`（T3 状态列改 `done`）、ISSUE-03 验收勾选
- 改动：把 5.3 定稿的推荐默认阈值与校准数据路径写入 config 注释；README T3 行状态更新为 `done` 并附验证命令结果；ISSUE-03「验收标准」逐项勾选并附证据。
- 验收：文档 diff 可见、README 状态更新后与派工约定一致。

### 6.3 全量验证与回归门
- 文件：`scripts/run_full_verification.py`（无需改造，作回归门）
- 改动：依次执行三组回归：①本模块 pytest 三件套；②全量 `scripts/run_full_verification.py`（排除 retrieval_eval 环境债）；③`tests/test_commercial_gates.py` 等商用门禁。登记通过/失败明细到 ISSUE-03 或验证报告。
- 验收：`python scripts/run_full_verification.py` 全绿（或仅记录已排除的环境债项）；单组命令三文件全部 passed。