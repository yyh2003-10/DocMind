# 向量知识库检索根基升级（激进·三阶段）

## Context（背景）

向量知识库是 DocMind 的核心。用户反馈：语义检索常返回与关键词无关的内容，却显示"相关度 60+"。要求「用尽一切办法升级根基」，商用安全。

**根因（已定位）**：`_distance_to_score = 1/(1+d)` 把 cosine 距离映射过高——「正交内容（真实相似度 0）」显示 0.50，「真实余弦 0.33」显示 0.60。下游阈值（rag.py 硬编码 0.4、rag\_min\_score）都在虚高标尺上。另：中文 BM25 用 FTS5 `trigram`，2 字词检索不到；无 bge 查询指令；重排分未校准；无语义下限兜底；无量化评估。

本次升级分**三阶段**：A 基础核心（必做，修根因+中文召回+标定+评估）→ B 激进召回（模型升级与迁移、父子/邻块上下文、融合加权）→ C 高级可选（LLM 驱动的查询扩展、上下文检索前缀、晚分块）。全部保持轻量本地架构与**商用安全**（仅 MIT 组件）。

## 现状要点（已核实）

- `_distance_to_score` 定义于 [search.py](file:///e:/DocMindY/src/doc2mind/core/retriever/search.py#L352-L359)，仅第 165 行使用，无直接单测。`Retriever.search()`(L116-249) 第 147 行 `embed_query(query)`。

- FTS5 `bm25_index` 用 `trigram`（[sqlite\_vec.py](file:///e:/DocMindY/src/doc2mind/core/store/sqlite_vec.py#L247-L257)）；`bm25_search`(L1050-1130) 对 <3 字中文词走 LIKE fallback。`vec_chunks` 用 `vec0 cosine`。`documents` 表已有 `title/tags/summary`（[sqlite\_vec.py](file:///e:/DocMindY/src/doc2mind/core/store/sqlite_vec.py#L203-L218)）。

- **换模型重建索引工具已存在**：`cli.py reindex`(L618-644)、`mcp.py _tool_reindex`(L406-478)、`store.rebuild_chunk_embeddings(..., dim)`；`cli.py model use`(L715-737) 已处理维度变化提示。

- 配置 [config.py](file:///e:/DocMindY/src/doc2mind/core/config.py#L56-L121)：`rag_min_score=0.0`（test\_config.py:46、test\_integration.py:316 断言此默认，须保留）。LLM 抽象 `get_llm_client`/`LLMClient`（rag.py L35-43）可复用做查询扩展/摘要。

- rag.py L1414 硬编码 `>= 0.4`（虚高标尺）。展示标签层 `rag.py:_format_context`(L976-1018) 与 http.py/mcp.py DTO 不动，只纠正数值。

- `embed_query` 同时用于 curator 对称去重/聚类（curator.py:471/554）：查询指令须在 Retriever 非对称边界应用。

***

## 阶段 A — 基础核心（必做，每步可运行）

> **状态：已全部落地（A1–A9）**。全量 `pytest -q`：450 通过 / 1 跳过，无失败。
> `python tools/eval_retrieval.py` 实测：MRR=1.0、Recall\@1=1.0；受控负样本（真实余弦 0.33）
> 显示 `vector_score=0.33` 且 `==1-distance`（旧公式 1/(1+d) 显示 0.599，即“相关度 60+”根因）。
> 标定：负样本 7 条显示均分 0.047 → `semantic_floor` 建议 0.33（保守默认保持 0.0 向后兼容，
> 由本机数据标定后按需启用）；`pitfall_min_score=0.30` 已确认（远高于负样本均分）。

### A1 加入 jieba 依赖（MIT）

`pyproject.toml` + `requirements-core.txt` 增 `jieba==0.42.1`（商用安全）。

### A2 忠实距离→相似度映射（根因修复）

[search.py](file:///e:/DocMindY/src/doc2mind/core/retriever/search.py#L352-L359) `_distance_to_score` 改为 `min(1.0, max(0.0, 1.0 - distance))`。效果：`d=0→1.0, d=0.67→0.33, d=1(正交)→0.0, d=2→0, 负→1.0`。同步更新顶部注释(L15-18)。新增单测断言精确值。现有 `test_bm25_ranking/test_rerank`（扁平 embedder 距离 0）`test_rag`（伪造分）不受影响。

### A3 配置项（新增，默认值保留）

[config.py](file:///e:/DocMindY/src/doc2mind/core/config.py#L56-L121) 新增并加入 `_PERSIST_FIELDS`：

- `query_instruction: str = ""`（bge 非对称查询前缀，B 阶段生效）

- `semantic_floor: float = 0.0`（相关度下限，默认关；A8 用评估件定）

- `bm25_jieba_enabled: bool = True`（中文 BM25，老库自动重建）

- `pitfall_min_score: float = 0.30`（替代 0.4，A8 确认）

- `rrf_weights: str = "1,1"`（向量/BM25 融合权重，B 阶段生效）

- 保留 `rag_min_score=0.0`（不破坏断言）。

### A4 jieba 分词 + FTS5 中文化

- 新建 `src/doc2mind/core/store/tokenizer.py`：`segment(text, enabled)`（enabled 时空格拼接 `jieba.cut`；否则原样）；`segment_query(query, enabled)`（首用 `jieba.initialize()`，锁内）。

- [sqlite\_vec.py](file:///e:/DocMindY/src/doc2mind/core/store/sqlite_vec.py)：`VectorStore.__init__(bm25_jieba_enabled=False)`；`_FTS_SQL` jieba 开→`tokenize='unicode61'`，关→`trigram`；加 `app_meta` 存模式；`open()` 后 `_sync_bm25_tokenizer` 一次性重建（DELETE+按 `segment(content)` 重灌）；插入路径 `segment(chunk.content)` 进 FTS（`chunks_meta` 留原文）；`bm25_search` jieba 开→全走 FTS5（跳过 <3 字 LIKE fallback），关→原分支。

- 9 处 `VectorStore(` 透传 `settings.bm25_jieba_enabled`（cli.py:92、mcp.py:98/157/418、http.py:954、rag.py:1541、pipeline.py:113/223/531）。

### A5 BGE 查询指令（非对称）

[search.py](file:///e:/DocMindY/src/doc2mind/core/retriever/search.py)：`Retriever.__init__(query_instruction="")`；L147 对 `f"{instruction}{query}"` 调 `embed_query`（不动文档 `embed`，curator 纯净）。4 处 `Retriever(` 透传（http.py:1805、mcp.py:242、rag.py:1351、cli.py:169）。

### A6 语义下限 + 阈值重标

- [search.py](file:///e:/DocMindY/src/doc2mind/core/retriever/search.py)：`Retriever.__init__(semantic_floor=0.0)`；排序过滤后弱命中丢弃（重排启用用 `rerank_scores`，否则 `max(v,b)`）；`top_k` 是上限不硬填；`stats.message` 提示。

- [rag.py](file:///e:/DocMindY/src/doc2mind/core/rag.py#L1403-L1415)：L1414 `>=0.4`→`>=pitfall_min_score`；`rag_min_score` 门控语义不变（默认 0.0）；透传 `semantic_floor`。

### A7 检索评估件 + pytest

- 新建 `tools/eval_retrieval.py`（可独立 `python tools/eval_retrieval.py`）：内存 `VectorStore` + 确定性假 embedder（免下载）；`GOLDEN=[{query,collection,relevant,negatives}]`；算 MRR/Recall\@k(k=1,3,5) + 标定检查（负样本展示分均值<阈值、top-1 为负则 FAIL）；**注册用户"60+"案例**（负样本真实余弦\~0.33，断言新显示 `vector_score<0.5` 且 `==1-distance`）；输出建议 `semantic_floor`。

- 新建 `tests/test_retrieval_eval.py`：忠实映射、60+ 回归、MRR/Recall 基线、标定、`test_bm25_jieba_improves_short_cjk`（2 字中文词经 unicode61 召回，不依赖 LIKE）。

### A8 重定阈值

运行 `python tools/eval_retrieval.py` 据输出设 `semantic_floor`（暂定 0.15）、`pitfall_min_score`（确认 0.30）；若 floor>0，相关 pytest fixture 显式设 0.0 防误伤。

### A9 文档 + 全量验证

README/CHANGELOG/docs/HANDOVER 记录新参数、`vector_score` 现为忠实余弦、`bm25_jieba_enabled` 一次性 FTS 重建；`pytest -q`（testpaths=\["tests"]）。

***

## 阶段 B — 激进召回（模型升级 + 上下文 + 融合）

### B1 嵌入模型升级与安全迁移

- 复用既有工具（cli.py `reindex`/mcp.py `_tool_reindex`/`rebuild_chunk_embeddings`），**新增托管模型预设表**（窄快/均衡/多语言三档）：

  - 窄快：`bge-small-zh-v1.5`（512，现状，默认）— 保持对新安装零风险。

  - 均衡：`bge-base-zh-v1.5`(768)。

  - 多语言（推荐升级）：`BGE-M3`(1024，稀疏+稠密，可顺带启用后续稀疏召回路)。

- `config.embed_model` 改为可读预设名或完整模型名；`model use` 交互提示：切换更高维预设需 `doc2mind reindex --model <name>` 一次性重建（依赖已存在）。

- 所有模型为 MIT 许可（BAAI bge 系列），商用安全。

- 效果验证：用 A7 评估件对「当前模型 vs 目标模型」跑同一语料，出 recall/MRR 增量，数据说话再切换默认（避免盲目增大体积）。

### B2 小到大 / 邻块上下文（父子检索）

- 现有 chunker 已产出语义 chunk 并带 `chunk_index/source/page/heading`（`chunks_meta`）。**新增"命中返回邻块上下文"**：

  - `store.get_neighbor_chunks(chunk_id, window=1)`：按同 source 的 `chunk_index` 相邻取前后块。

  - RAG 组装（`rag.py:_format_context`/`curator`)命中时把邻块并入上下文（父级段落），提升回答完整性；检索排序仍以命中为准。

- 可选"小块索引、大块返回"：对超长语义块额外生成小子块索引，命中后返回其父段落。默认先做邻块上下文，小到大作为可开关。

> **状态：B2 已落地**。`store.get_neighbor_chunks(chunk_id, window)` 已实现；`_format_context` 新增
> 可选 `store=, neighbor_window=` 参数（默认 0 关，纯格式化调用/测试不受影响），RAG 主链路按
> `neighbor_context_window=1`（config）开启，邻块缺失绝不阻塞（异常兜底）。单测覆盖窗口/边界与上下文并入。
> 「小块索引、大块返回」保留为可选开关，未启用。

### B3 融合与重排调优

- 融合 [search.py](file:///e:/DocMindY/src/doc2mind/core/retriever/search.py#L310-L349) 由纯 RRF 升级为**加权 RRF + 可选分数加权**：`score = w_v/(k+rank_v) + w_b/(k+rank_b)`，`rrf_weights` 默认 "1,1" 保持中性；B1 用 BGE-M3 时可选使能其**稀疏向量**作为第三条召回路并入融合。

- 重排 [search.py](file:///e:/DocMindY/src/doc2mind/core/retriever/search.py#L194-L223)：`Reranker.rerank(query, docs, batch_size)` 用更大 `rerank_recall`（20→按 top\_k/candidate 动态），并加**校准温度/标定**（在 A7 评估集上重排分与真实相关性对齐）——展示的 `rerank_score` 不再被误读为校准概率。

- `vector_search` 召回候选按 `top_k*3`（无 collection 过滤时）保持，collection 多选场景评估候选放大倍数避免召回遗漏。

> **状态：B3 已落地**。`search.py` 融合升级为 `_fuse` 分派双模式：
>
> - `fusion_mode="rrf"`（默认）加权倒数排名融合；`fusion_mode="score"` 加权分数融合
>   `score=w_v*vec+w_b*bm25`（利用忠实余弦+归一化 BM25 的分数强度）。`rrf_weights`
>   由 `config.parse_rrf_weights("vec,bm25")` 从配置解析，已接入 cli/mcp/http/rag 四处构造点。
>
> - 重排候选数动态放大：`min(len(fused), max(rerank_recall, top_k*3))`，top\_k 大时不再漏召回。
>
> - 重排分校准温度 `rerank_calibration_temperature`（默认 1.0 = 原 plain sigmoid，向后兼容；
>   <1 更陡、>1 更保守），`_sigmoid(logit/T)` 才展示，避免「相关度 5.32」误读。
>
> - 单测 7 项：权重解析/加权 RRF 重排/分数融合数学/分派/sigmoid 温度/动态 recall/score 端到端。

### B4 大语料向量索引量化（可选）

- `vector_search` 第二优先路子优化染色体（FPGA/量化）仅当收益明确时启用。

- **评估完成（当前栈结论）**：探测 [sqlite\_vec.py](file:///e:/DocMindY/src/doc2mind/core/store/sqlite_vec.py#L1684) 实测 —— 安装的 `sqlite-vec 0.1.9` PyPI wheel 把 INT8 列插入的 blob 一律按 float32 解释（无类型头），抛 `expected int8, but float32 vector was provided`（带类型头也仅报 `invalid float32 length`），**int8 量化存储在该 wheel 不可写入**。

- **落地形态（零风险）**：

  - 新增配置 `vector_quantize`（"none" 默认 / "int8" 实验性），持久化。

  - 存储层 [open/reindex](file:///e:/DocMindY/src/doc2mind/core/store/sqlite_vec.py#L438) 用 `_probe_vec_int8_supported()` 自检（进程缓存）决定建 `INT8[dim]` 还是 `FLOAT[dim]`；请求 int8 但本机不支持时**自动回退 FLOAT 并告警，绝不破坏建库/检索**。

  - 序列化按存储类型分派：`_to_bytes()` → `_vector_to_bytes_int8()`（\[-1,1] 钳制 + round(x\*127)）或 `_vector_to_bytes()`（float32）。

  - A7 准确率评估恒在 FLOAT 存储跑，int8 仅当依赖升级解决写入后方可验证启用。

### 阶段 C：LLM 驱动的查询扩展 / 上下文检索前缀 / 晚分块（可选）

***

## 阶段 C — 高级可选（LLM 驱动，可开关，无 LLM 自动降级）

### C1 查询扩展（HyDE + 多查询）

- 复用 `get_llm_client`（rag.py L35-43）。`Retriever` 或调用侧新增可选 `query_expansion`：

  - **多查询**：LLM 生成 2-3 个查询变体，分别检索后并入候选（合并去重）。

  - **HyDE**：LLM 生成一个"假设文档"，以其嵌入辅助检索（可作额外召回路）。

- 开关 `query_expansion="off|multi|hyde|both"`，默认 off；LLM 不可用→降级单查询（沿用现有容错模式）。

- 注意：查询/假设文档须走与 A5 一致的指令前缀；成本由 LLM 计费，仅解锁时启用。

> **状态：C1 已落地**。`comfy/ui/stream` 中 `Rag` 拉取以 LLM 生成多查询变体（`_expand_multi_queries`）
> 与假设文档（`_expand_hyde`），`_retrieve_with_expansion` 并入基础召回，`_merge_hits` 按
> chunk.id 去重并保留相关度最优者（优先 `rerank_score`，否则取 `max(vector,bm25)`），
> 重排后统一降序。开关 `query_expansion`（config + `_PERSIST_FIELDS`）、LLM 不可用/空输出
> 自动降级单查询，绝不阻断检索。单测 8 项（去重/重排/变体解析去噪/HyDE/多路合并降级）。

### C2 上下文检索（文档摘要前缀）

- 入库 enrichment 钩子（`auto_curate_on_ingest`，config.py L146-155）可选：LLM 生成文档级摘要写入已有 `documents.summary`（[sqlite\_vec.py](file:///e:/DocMindY/src/doc2mind/core/store/sqlite_vec.py#L203-L218)），嵌入时在 chunk 前拼接 `[文档摘要]...`。改善长文档/跨章节召回，Anthropic Contextual Retrieval 思想（本地化、可开关）。

- 依赖 B1 的 `rebuild_chunk_embeddings` 重新嵌入已启用块的文档。

> **状态：C2 已落地**。配置 `contextual_retrieval`（config + `_PERSIST_FIELDS`，默认 False）。
> 存储层新增 `store.list_chunk_contexts(collection)`（JOIN 文档摘要）；`reindex_store` 开启时
> 用它对含摘要 chunk 拼接 `[文档摘要]<summary>` 后嵌入，无摘要文档退化为原文（绝不改
> chunks\_meta 正文）。关闭时仍走 `list_chunk_contents` 原样嵌入。返回带 `contextual` 标记。
> 需清单库启用后手动 reindex 生效（摘要由 enrich 在入库后生成，新入库首嵌不受前缀影响）。
> 单测 7 项（前缀拼接/无摘要退化/存储层摘要关联/list\_chunk\_contents 兼容/开关生效）。

### C3 晚分块（Late Chunking，调研后落地）

- 优先做 B2/B1/C1/C2；晚分块依赖对文档做 token 级嵌入再按块池化（fastembed 导出 token embedding 不直观），列为**调研任务**：评估是否可用 Sentence-Transformers/onnx 落本地后推出；若成本高，用 C2（上下文前缀）等价替代。保持轻量，不强行引入重框架。

> **C3 调研结论：技术上可行、工程可落地，但当前 ROI 不足 → 暂不引入**（决策已记录，见下）。🗂 证据脚本 `tools/late_chunking_probe.py`（可 `python tools/late_chunking_probe.py` 复现）。
>
> **可行性（已实测，零新重量级依赖）**：现役 `bge-small-zh-v1.5` 的原始 ONNX 本身只导出
> `last_hidden_state [batch, seq, hidden]`（token 级），fastembed 只是内部对 CLS 池化后返回 512d。
> 因此可用**已有的** `onnxruntime` + `tokenizers`（均在 core 依赖内，**无需 PyTorch/Sentence-Transformers**，
> 商用许可安全）直接加载同一模型文件驱动 token 级嵌入：实测 token→onnx→mean-pool 与 fastembed 输出
> 余弦 ≈0.91（残差仅池化/归一细节，与新建索引路径无关）。晚分块算法（整篇一次前向 → 按 token 字符
> 跨度归块 → 逐块 mean-pool）在同款模型上跑通，5 条语义查询 A（独立嵌入）/B（晚分块）均 top-1 命中
> 目标块。
>
> **ROI 判定（诚实数据）**：在 chunk 级内容（≤512 token、语义自包含）上，**独立嵌入已与晚分块
> 持平（5/5）**——bge 独立嵌入对常规 RAG 块召回已足够强，晚分块无增益。且 bge-small 序列窗口仅
> 512 token，长文档需滑窗，"全文语境"实际退化为 \~邻块上下文，已被 B2（邻块）与 C2（摘要前缀，已落地）
> 在检索层以更低代价覆盖。
>
> **工程成本（若硬上）**：需新增"以整篇为单位的嵌入模式"，与现有 chunker→逐块 embed→批量写库的
> 架构冲突，还要改 char→token 对齐、滑窗、维度不变的增量 reindex 语义，并对 curator 对称去重/聚类
> 保持兼容。成本显著高于 C2，收益在当前模型/语料下不成立。
>
> **决策（ADR 待归档）**：**不落地 C3 晚分块索引**，用 C2（文档摘要前缀，已落地）+ B2（邻块上下文，
> 已落地）等价替代。保留再评估触发条件：若未来选用超长上下文嵌入模型（如 `jina-embeddings-v2-base-zh`
> 8192 token，已在 `EMBED_MODEL_CATALOG`）且语料跨段交叉引用密集，重跑 `tools/late_chunking_probe.py`
> 观察晚分块对 A 的增益后再决定。

***

## 阶段 D — 评估固化 · 稀疏召回 · 参数自动标定

> 阶段 D 由用户在与三阶段（A/B/C）对话中显式追加（无既定文件），覆盖三项：D1 端到端评估固化、
> D2 稀疏向量多路召回、D3 阈值/参数自动标定。均以「可回归、A/B、数据驱动」为原则。

### D1 端到端评估固化

> **状态：D1 已落地**。`tools/eval_retrieval.py` 在既有受控基准（60+ 回归标定）之外，新增
> `evaluate_zh()` 中文端到端基准：真实中文语料（5 主题 × 2 篇、词汇重叠、relevant 可多根），
> 以确定性 `_TextHashEmbedder`（jieba 分词 → token 经 crc32 稳定哈希累加 → L2 归一）贯通
> 分词→嵌入→向量检索/BM25/RRF 融合→排序，**免联网、跨进程可回归**。
>
> - 回归门槛 `ZH_MIN_{MRR,R1,R3}=0.70/0.40/0.80`；CLI `--zh` 打印指标与门槛判定，过低以非零退出码卡 CI。
>
> - A/B：`--zh --model <模型名>` 在**同一语料**上切换真实模型，与确定性基线对比（`_run_with_model_zh`）。
>
> - `--json <path>` 落盘结构化结果供自动化消费。
>
> - 实测基线：MRR=0.886（≥0.70）、R\@1=0.643（≥0.40）、R\@3=0.857（≥0.80）→ **门槛 PASS**。非 1.0，对回归敏感。
>
> - 单测新增 4 项（门槛通过/语料与键/跨运行确定性/stub 模型 A/B 标记）。

### D2 稀疏向量 + 多路召回（调研后落地）

> **状态：D2 已落地**。新增第三条召回路——词法稀疏向量（倒排索引），与 向量+BM25
> 构成三路融合，增强召回冗余；默认保持两路（稀疏路权重 0）向后兼容。
>
> - **存储层** [tokenizer.py](file:///e:/DocMindY/src/doc2mind/core/store/tokenizer.py)：`token_counts()`（jieba 分词或缺省视为中文粗切）+ `sparse_token_weights`（log-TF / L2 归一，∈\[0,1] 与忠实余弦同量纲）。
>
> - **存储层** [sqlite\_vec.py](file:///e:/DocMindY/src/doc2mind/core/store/sqlite_vec.py)：`sparse_terms(term,chunk_id,weight,collection)` 倒排表 WITHOUT ROWID + `idx_sparse_chunk`；写入/替换/删除/迁集/回填 `_sync_sparse_index`（旧库 open() 一次性回填）随文档 CRUD 同步；`new`配置 `sparse_retrieval_enabled`（默认 False）。**修复**：`_delete_sparse_in_txn` 先探测表存在，稀疏关闭的库删除/替换不再抛 `no such table`（原来 pollute 全部 delete/replace 路径，导致 curator 去重/合并与文档替换回归，已修并有全量回归覆盖）。
>
> - **检索层** [search.py](file:///e:/DocMindY/src/doc2mind/core/retriever/search.py)：`_rrf_fuse` 升级为 3 路加权 RRF / `_unpack3`；`SearchHit.sparse_score`、`SearchStats.sparse_enabled/sparse_candidates`；`Retriever.rrf_weights` 第 3 位>0 且存储启用时才并入稀疏路。
>
> - **配置/构造点** [config.py](file:///e:/DocMindY/src/doc2mind/core/config.py)：`sparse_retrieval_enabled`、3 元 `rrf_weights`（`parse_rrf_weights("vec,bm25[,sparse]")`，缺第 3 位补 0）；cli/mcp/http/rag 透传。
>
> - **评估/测试** [eval\_retrieval.py](file:///e:/DocMindY/tools/eval_retrieval.py)：`compare_multipath_zh()` + CLI `--zh --multipath`（A/B：两路 vs 三路，判定三路 R\@3 不劣于两路）；`--zh --multipath` 实测 Δ=+0.0 **PASS**。单测：稀疏检索余弦∈\[0,1]、开关 flag、三路权重填 sparse\_score、权重为 0 时冗余兼容、A/B 门与确定性。
>
> —— D3 参数自动标定受此支撑：同一中文语料上三路权重由扫描自动给出。

### D3 阈值/参数自动标定（落地为工具）

> **状态：D3 已落地**。新增 `autocalibrate_zh()`（CLI `--zh --calibrate`）在**同一中文
> 语料**上数据驱动给出四类参数建议，可写回 config：
>
> - **rrf\_weights 扫描**：`RRF_WEIGHT_SCAN`（两基 + 偏向量 / 偏 BM25 / 偏稀疏 × 三路），
>   以「两路基线 (1,1,0) 的 R\@3」为退化门选最优（MRR→R\@3→R\@1），全部劣于基线则退回基线。
>
> - **semantic\_floor**：以负样代理分（max(vector,bm25)）为切点，逐个**走生产 search()**
>   实测召回损失，选「R\@3 损失≤5%」的最高 floor（保守下界）。
>
> - **pitfall\_min\_score**：正/负样代理分安全分隔——完全可分取中点；重叠取 min\_pos
>   （永不越过任何正样、绝不挡相关答案）。
>
> - **rerank\_calibration\_temperature**：`autocalibrate_rerank_temperature(pairs)` 对
>   (logit, 相关标注) 网格搜 T∈\[0.3,3.0] 最小化二分类对数损失；确定性基线无重排器 → 保持 1.0。
>
> 实测（确定性基线，同语料）：best=三路 `1,1,1`（不劣于基线，Δ=0）；floor≈0.16；
> pitfall=0.0（该语料正负代理分重叠，保守取 min\_pos）；rerank T=1.0。切真实 embedder /
> 真实语料后其分值会有区分力。单测 6 项（退化门/确定性/解析回写/floor 保召回/pitfall 不挡正样/T 优化器）。

***

## 融合打分统一（跨阶段）

- 展示语义：`vector_score`=忠实余弦相似度；`rerank_score`=校准后相关度；`bm25_score`=归一化关键词强度；RRF `score` 仅排名信号（小量纲），三处标注已存在、仅数值变真实。

- 语义下限（A6）、坑位阈值（A6）、内部 0.4（A6）都统一由 A7 评估件重标，杜绝虚高。

***

## 商用安全

- 仅新增 MIT 组件（jieba==0.42.1）；嵌入模型用 BAAI bge 系列（MIT）；融合/重排/分块沿用既有工程惯例与通用 IR 方法论（RRF、加权融合、jieba 分词、bge 查询指令、small-to-big、上下文检索、纯检索评估 Metric）。

- 不逐字复制外部仓库代码；如需参考仅取思想，按本项目架构与异常/锁/配置既有模式自研。项目本身 AGPL-3.0，引入均为许可宽松组件。

## 验证（端到端）

1. `pip install -r requirements-core.txt`；`pytest -q` 全绿（默认值保留）。
2. `python tools/eval_retrieval.py`：MRR/Recall\@k/标定 + 建议 semantic\_floor；"60+"案例 `vector_score<0.5`。
3. B 阶段：对比当前 vs 目标嵌入模型的同语料 recall/MRR（A7 复用）；BGE-M3 时稀疏召回路并入融合；邻块上下文回显正常。
4. C 阶段：开 `query_expansion`/摘要前缀后，检索/对话引用带正确上下文与来源标注；无 LLM 时降级、`pytest` 仍绿。
5. 手动 `POST /v1/search`/MCP `_tool_search`：2 字中文词出真实 BM25 命中；`vector_score` 反映真实余弦；语义下限诚实过滤而非虚高硬填 top-k。
6. 切换 `bm25_jieba_enabled` 与 `embed_model`：store 打开一次性 FTS/向量重建，检索仍返回正确 chunk id。

## 关键文件

- src/doc2mind/core/retriever/search.py

- src/doc2mind/core/store/sqlite\_vec.py · src/doc2mind/core/store/tokenizer.py(新)

- src/doc2mind/core/config.py · src/doc2mind/core/rag.py

- src/doc2mind/core/embedder/{factory,fastembed\_impl,api\_impl}.py（预设/透传）

- src/doc2mind/cli.py · src/doc2mind/server/mcp.py · src/doc2mind/server/http.py（透传/预设）

- tools/eval\_retrieval.py(新) · tools/reindex\_embeddings.py(新增预设入口，或复用 cli reindex)

- tests/test\_retrieval\_eval.py(新) · tests/test\_tokenizer.py(新或并入 test\_bm25\_ranking.py)

- pyproject.toml · requirements-core.txt · README.md · CHANGELOG.md · docs/ · HANDOVER.md

