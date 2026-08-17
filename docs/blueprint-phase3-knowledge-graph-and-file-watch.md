# DocMind Phase 3 实施蓝图 — 知识图谱 + 文件系统监控

> 本文件是给后续实施 AI 的自包含任务书。执行者无需额外上下文即可按此蓝图落地。
> 蓝图中的所有文件路径、行号、函数签名均为 2026-08 代码库实测结果(非文档转述)。

---

## 0. 项目现状速览(执行者必读)

- **定位**:本地优先的向量知识库工具。CLI / MCP(stdio) / WPF 客户端三层入口共用一份 SQLite(`%LOCALAPPDATA%\doc2mind\doc2mind.db`)。
- **后端**:Python FastAPI(`src/doc2mind/server/http.py`,共 25 个端点)+ MCP(`src/doc2mind/server/mcp.py`,12 工具)。存储层 `core/store/sqlite_vec.py`(VectorStore,四表:documents / chunks_meta / vec_chunks / bm25_index)。
- **前端**:WPF .NET 8(`DocMind/`),MVVM(CommunityToolkit.Mvvm),HttpClient 直连 `http://127.0.0.1:8765`。
- **测试**:后端 `tests/` 266 个 pytest;前端 `DocMind.Tests/` 90 个 xUnit。**任何改动必须补测试,验收标准含测试全绿**。
- **编码约定**(严格遵守):
  - 后端:异常分类包装(异常信息含可操作指引)、SQLite 写操作包 `BEGIN/COMMIT` 事务、失败降级不阻断主流程、日志用 `logging.getLogger(__name__)`,敏感字段不落盘。
  - 前端:ViewModel 用 `[RelayCommand]`、属性变更走 `SetProperty`/`SetField`、API 调用统一走 `IDoc2kbApiService`、所有用户可见错误写 `StatusMessage` + `DebugLog`。

---

## 1. Phase 3A — 知识图谱

### 1.1 目标与验收标准

**目标**:入库文本时由 LLM 抽取实体与关系,存入 SQLite 独立表;WPF 新增"知识图谱"页面,用 WebView2 + vis-network 力导向图可视化。

**验收标准**:
1. `doc2mind curate --actions extract` 或 `/v1/curate` 传 `actions=["extract"]` 能从文档抽取实体+关系入库。
2. `GET /v1/graph/visualize?collection=xxx` 返回 `{nodes: [{id,name,type,group}], edges: [{from,to,label}]}`。
3. WPF 新增导航项"知识图谱",打开后显示力导向图,节点可点击查看关联实体。
4. 新增测试全绿(见 1.7)。
5. LLM 未配置时 `extract` 动作 skip 并出报告,不抛异常。

### 1.2 可复用资产(已实测,零成本)

| 资产 | 位置 | 说明 |
|------|------|------|
| LLM JSON 调用骨架 | `src/doc2mind/core/curator.py` L85-132 | `_extract_json()` 剥代码栅栏 + `_llm_json()` 失败重试一次,`temperature=0.2` |
| 文档全文获取 | `curator.py` L161-164 | `_doc_full_text(store, doc, max_chars)` 拼接文档分块 |
| Prompt 设计范式 | `curator.py` L192-198 `_ENRICH_SYSTEM` | 「角色 + 只输出 JSON + 字段说明 + 不要多余文字」三段式 |
| 同库独立连接模式 | `src/doc2mind/core/store/chat_store.py` L60-127 | 独立 `sqlite3.connect(db_path)` + `CREATE TABLE IF NOT EXISTS` 幂等建表 |
| 写事务模式 | `sqlite_vec.py` 各写方法 | `conn.execute("BEGIN IMMEDIATE")` + COMMIT/ROLLBACK |
| 动作注册机制 | `curator.py` L37 + L578-685 | `VALID_ACTIONS` 元组 + `curate()` 分发循环 |
| HTTP 端点模式 | `server/http.py` | `@app.get(...)` + Pydantic response_model + `state.ensure_open()` |
| MCP 工具注册 | `server/mcp.py` L577 + L811-824 | TOOLS_SCHEMA(名称/描述/输入 schema) + handlers 分发表 |

### 1.3 存储层 — 新建 `src/doc2mind/core/store/graph_store.py`

复制 `chat_store.py` 的连接模式(独立连接、幂等建表、`_now_iso()`)。三张表:

```sql
CREATE TABLE IF NOT EXISTS entities (
    id          TEXT PRIMARY KEY,          -- uuid4().hex
    name        TEXT NOT NULL,             -- 实体名(中文优先,专名保留原文)
    type        TEXT NOT NULL,             -- person | org | tech | concept | event | place | other
    collection  TEXT NOT NULL DEFAULT 'default',
    doc_count   INTEGER NOT NULL DEFAULT 0,-- 出现过的文档数(去重合并用)
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_uniq ON entities(collection, name, type);

CREATE TABLE IF NOT EXISTS entity_relations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id     TEXT NOT NULL REFERENCES entities(id),
    to_id       TEXT NOT NULL REFERENCES entities(id),
    relation    TEXT NOT NULL,             -- 语义关系,如 belongs_to / uses / depends_on
    created_at  TEXT NOT NULL,
    UNIQUE(from_id, to_id, relation)
);

CREATE TABLE IF NOT EXISTS chunk_entities (
    chunk_id    INTEGER NOT NULL,          -- 对齐 chunks_meta.id
    entity_id   TEXT NOT NULL REFERENCES entities(id),
    PRIMARY KEY (chunk_id, entity_id)
);
```

**类 `GraphStore` 必须提供的方法(签名按此实现)**:

```python
class GraphStore:
    def __init__(self, db_path: Path) -> None
    def close(self) -> None

    # 实体:存在则 doc_count+1(upsert),返回 entity_id
    def upsert_entity(self, name: str, etype: str, collection: str) -> str

    # 关系:幂等插入,已存在则跳过
    def upsert_relation(self, from_id: str, to_id: str, relation: str) -> None

    # 记录 chunk→实体 关联
    def link_chunk(self, chunk_id: int, entity_id: str) -> None

    # 按文档入库一批实体+关系(chunk_id 可为 None,此时不建 chunk 关联)
    def add_document_entities(
        self, doc_id: str, collection: str,
        entities: list[dict],          # [{"name":..., "type":...}]
        relations: list[dict],         # [{"from":"名称","to":"名称","type":...}]
        chunk_id: int | None = None,
    ) -> dict                          # {"entities": n, "relations": m}

    # 可视化数据:节点 + 边
    def get_graph(self, collection: str | None = None, limit: int = 200) -> dict
    #   -> {"nodes": [{"id","name","type","group","size"}],
    #       "edges": [{"from","to","label"}]}

    # 按实体查关联(节点点击展开用)
    def get_entity_relations(self, entity_id: str, limit: int = 50) -> list[dict]

    # 按集合统计(空集合清理 / 看板用)
    def get_stats(self, collection: str | None = None) -> dict
```

**实现注意**:
- 所有写操作包事务(`BEGIN IMMEDIATE` … `COMMIT`,失败 `ROLLBACK`),与 `sqlite_vec.py` 一致。
- 同名实体跨文档合并:依赖 `idx_entities_uniq`,用 `INSERT ... ON CONFLICT(collection,name,type) DO UPDATE SET doc_count = doc_count + 1, updated_at = ?` 返回 id。
- `get_graph` 的 `size` 可用 `doc_count` 映射(节点大小=出现文档数)。
- LLM 抽取的实体名可能重复/相似(如 "RAG" 与 "rag"),MVP 阶段不做语义融合,仅精确匹配合并。**在代码注释里标注这一点作为已知限制**。

### 1.4 实体抽取 — 新建 `src/doc2mind/core/extractor.py`

复用 `curator.py` 的 `_llm_json`(从 curator import 或复制;**优先从 curator import 避免重复实现**——若因循环依赖可把 `_llm_json`/`_extract_json` 提为 `core/llm_utils.py` 共用模块,由实施者判断)。

**核心函数签名**:

```python
_ENTITY_SYSTEM = (
    "你是知识图谱实体抽取助手。从文档中提取关键实体及其语义关系。\n"
    '只输出 JSON 对象：{"entities": [{"name": "...", "type": "..."}], '
    '"relations": [{"from": "...", "to": "...", "type": "..."}]}\n'
    "要求：\n"
    "1. type 从 [person, org, tech, concept, event, place, other] 中选择；\n"
    "2. relations 的 from/to 必须是 entities 中已出现的 name；\n"
    "3. relation type 从 [belongs_to, uses, depends_on, part_of, develops, related_to] 中选择；\n"
    "4. 只抽取文档中明确出现或强烈暗示的实体,不要臆造；\n"
    "5. 中文优先,专有名词保留原文；\n"
    "6. 实体数量控制在 3~15 个,关系 2~20 条。\n"
    "不要输出 JSON 以外的任何文字。"
)

def extract_entities(
    text: str,
    llm: LLMClient,          # 复用 rag.get_llm_client 或注入
    max_chars: int = 8000,   # 与 curator 的 curate_max_chars 对齐
) -> dict:
    """返回 {"entities": [...], "relations": [...]};LLM 失败返回空 dict(降级)。"""

def extract_and_store(
    text: str,
    collection: str,
    llm: LLMClient | None,   # None → 返回 skipped 报告,不执行
    chunk_id: int | None = None,
    db_path: Path | None = None,
) -> dict:
    """抽取并按 collection 入库;LLM 未配置/调用失败时返回 {"skipped": 原因},不抛异常。"""
```

**关键点**:
- `extract_and_store` 打开 `GraphStore`(独立连接),调用 `extract_entities` → `store.add_document_entities` → close。
- LLM 失败(`_llm_json` 返回 None 或抛异常)必须降级为 skipped,与 `curator.py` 各动作的失败处理一致。
- `relation.from/to` 可能是 entities 里没有的名字(LLM 幻觉)——`add_document_entities` 内部要容忍:from/to 指向未注册实体时自动 upsert(类型 other)。

### 1.5 curator 集成 — 改 `src/doc2mind/core/curator.py`

1. `VALID_ACTIONS = ("enrich", "categorize", "dedup", "consolidate", "extract")`(L37)。
2. 在 `curate()`(L578)分发循环中加 `"extract"` 分支:
   - 遍历选定文档,取 `_doc_full_text()` → 调 `extract_and_store(text, doc.collection, llm_chat_client, db_path=...)`。
   - `dry_run=True` 时**只统计不写库**(与 dedup/consolidate 一致,在报告里给出将新增实体数,可用 LLM 抽取但丢弃结果)。
   - 每个文档的抽取结果写入 `CurateReport` 对应字段(新增 `extracted_entities`/`extracted_relations` 计数字段,`CurateReport` dataclass 在 curator.py 内,实施者需看一眼现有字段再扩展)。
3. `DEFAULT_TOP_K = 200`(L40)对 extract 同样生效,防止 LLM 调用失控。
4. **注意**:curate 的 LLM 客户端获取方式——看 L578+ 当前如何拿 `llm`(应从 settings 经 `get_llm_client` 创建一次复用),extract 沿用同一客户端,避免每文档新建连接。

### 1.6 HTTP API — 改 `src/doc2mind/server/http.py`

在 `create_app()` 内新增 3 个端点(参考 `@app.get("/v1/quality")` L1383 的写法,response_model 用 Pydantic 模型,不加裸 dict):

```python
# --- 知识图谱 ---
class GraphNodeDTO(BaseModel):
    id: str; name: str; type: str; size: int = 1
class GraphEdgeDTO(BaseModel):
    from: str; to: str; label: str = ""
class GraphResponse(BaseModel):
    nodes: list[GraphNodeDTO] = []
    edges: list[GraphEdgeDTO] = []
    total_nodes: int = 0

@app.get("/v1/graph/visualize")          # ?collection=xxx&limit=200
@app.get("/v1/graph/entities")           # ?collection=xxx 实体列表(分页)
@app.get("/v1/graph/relations/{entity_id}")  # 单实体关联(展开用)
```

- 端点实现:打开 `GraphStore(settings.db_path)` → `get_graph(...)` / `get_stats` → 组装 DTO。**用完必须 `close()`**(可 try/finally)。
- `state` 是 `ServerState`(L539 附近定义,持 `_write_lock` 和 `store`),graph 走独立连接**不需要** `_write_lock`,但读操作与写操作混跑时 SQLite WAL 通常没问题;若遇到 `database is locked`,参考 `sqlite_vec.py` 的 `_retry_on_locked` 装饰器思路加重试。
- `BaseModel` 定义放 `create_app()` 函数外的模块级(与 L185 `SourceRefDTO` 同级),便于测试 import。

**MCP 工具(可选加分项,非验收必需)**:按 `server/mcp.py` 的注册流程(L577 schema + L811 handlers)加 `graph_get` 工具,调 `get_graph` 返回 JSON 字符串。若时间紧可跳过,在 mcp.py 顶部注释标注 TODO。

### 1.7 WPF 可视化 — 新建 `GraphView`/`GraphViewModel`

**依赖**:`DocMind/DocMind.csproj` 加 `<PackageReference Include="Microsoft.Web.WebView2" Version="1.0.2592.51" />`(或 nuget 最新稳定版)。

**后端对接(先做,小)**:
- `DocMind/Models/GraphData.cs` 新建:record `GraphNode`(Id/Name/Type/Size)、`GraphEdge`(From/To/Label)、`GraphResponse`(Nodes/Edges)。
- `IDoc2kbApiService.cs` 加 `Task<GraphResponse> GetGraphAsync(string? collection = null, CancellationToken ct = default);`
- `Doc2kbApiService.cs` 实现:GET `v1/graph/visualize`,query 带 collection。
- `FakeDoc2kbApiService.cs` 加 `OnGetGraph` 委托(模式同其他方法,必须同步否则测试编译失败)。

**ViewModel** — 新建 `DocMind/ViewModels/GraphViewModel.cs`:
- 属性:`Collection`(string?,集合过滤)、`IsBusy`、`StatusMessage`、`GraphJson`(string,JSON 注入 WebView2 用)、`HasGraph`(bool)。
- Command:`[RelayCommand] LoadGraphAsync()` — 调 `GetGraphAsync` → 序列化为 JSON 存 `GraphJson` → 通知 WebView2 刷新(见 View 代码后置)。
- `EnsureLoadedAsync()` 幂等(首次导航自动加载),仿 `QualityViewModel.cs` L31-39。
- 复用 `ViewModelBase`。

**View** — 新建 `DocMind/Views/GraphView.xaml` + `.xaml.cs`:
- XAML:`<wv2:WebView2 x:Name="GraphWeb" Source="about:blank"/>`(xmlns:wv2 指向 WebView2 命名空间),上方工具条放集合过滤 + 刷新按钮。
- 内嵌 HTML 模板:写一个 `Resources/GraphTemplate.html`(Copy to Output),含:
  - `<script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js">`(或下载到本地 Assets 内嵌,离线可用更佳)。
  - JS 从 `window.__graphData` JSON 读数据,`new vis.Network(container, {nodes, edges}, {physics: {solver: 'forceAtlas2Based'}})`,节点按 type 着色、size 按文档数。
- Code-behind:
  - `OnDataChanged`(订阅 ViewModel PropertyChanged 或 LoadGraphAsync 完成后调用):`GraphWeb.CoreWebView2.NavigateToString(html)` + `ExecuteScriptAsync($"window.__graphData = {json}; renderGraph();")`。
  - WebView2 初始化:`EnsureCoreWebView2Async()`(首次加载耗时,注意异常处理;WebView2 运行时缺失时降级显示"当前系统缺少 WebView2 运行时"提示)。

**导航注册** — 改 `DocMind/ViewModels/MainViewModel.cs`:
- 构造注入 `GraphViewModel`(DI 注册在 `App.xaml.cs` 的 ServiceCollection)。
- `NavigationItems.Add(new NavigationItem { Title = "知识图谱", Icon = "🕸️", ViewModelType = typeof(GraphViewModel) });`(放"文档库"后)。
- `CurrentPage` switch(约 L154)加 `typeof(GraphViewModel)` 分支。

### 1.8 后端测试规划

| 文件 | 测试 | 验证点 |
|------|------|--------|
| `tests/test_graph_store.py`(新建) | `test_upsert_entity_dedups` | 同名实体二次 upsert doc_count=2、id 不变 |
| | `test_relation_idempotent` | 相同关系重复插入不报错 |
| | `test_get_graph_shape` | nodes/edges 结构正确 |
| | `test_link_chunk` | chunk_entities 表写入 |
| `tests/test_extractor.py`(新建) | `test_extract_parses_json` | 用 MockLLMClient 返回固定 JSON,断言解析结果 |
| | `test_extract_failure_skips` | LLM 抛异常 → 返回空 dict 不抛 |
| | `test_extract_and_store_no_llm` | llm=None → {"skipped": ...} |
| `tests/test_curator.py` | `test_extract_action_requires_llm` | 无 LLM 时 extract 动作 skipped 不阻断 |
| `tests/test_integration.py` | `test_graph_visualize_endpoint` | 先 ingest_text 再 GET /v1/graph/visualize 返回非空 |

MockLLMClient 可复用 `tests/test_integration.py` 里的(文件内已有,实施者参考其构造)。

### 1.9 实施顺序(依赖关系)

1. `graph_store.py`(存储层,可独立测试)
2. `extractor.py`(依赖 graph_store)
3. `curator.py` 集成(依赖 extractor)
4. `http.py` 3 端点(依赖 graph_store)
5. 后端测试(1-4 每个步骤都可先补)
6. WPF:GraphData 模型 + API 方法 + Fake
7. WPF:GraphViewModel + GraphView + 导航
8. 前端测试 + 全量回归

**工期参考**:存储+抽取 1.5 天,API 0.5 天,可视化 1 天。

---

## 2. Phase 3B — 文件系统监控自动摄入

### 2.1 目标与验收标准

**目标**:监控用户配置的目录,文件新增/修改时去抖后自动入库;后端 `/v1/events` 从占位心跳升级为真广播;WPF 收到变更通知并提示。

**验收标准**:
1. 配置 `watch_paths` 后启动 `doc2mind serve`,目录内新建 `.md` 文件 → 自动入库(约 5s 去抖内)。
2. `GET /v1/events` SSE 在文件入库后推送 `{"type": "file_ingested", ...}` 事件。
3. WPF 启动时若配置了 watch_paths,订阅事件流,收到事件弹 Toast + 文档库缓存失效。
4. 新增测试全绿(见 2.7)。
5. watchdog 未安装(extras 未装)时 serve 正常启动,仅日志提示监控未启用。

### 2.2 可复用资产

| 资产 | 位置 | 说明 |
|------|------|------|
| 摄入管线 | `src/doc2mind/core/pipeline.py` L62-70 | `ingest_path(path, settings, collection, recursive, force, progress)` 完整流程;自带 MD5 去重 |
| 格式白名单 | `src/doc2mind/core/loader/detect.py` | `is_supported(path)` 判断扩展名,监控时用它过滤无关文件 |
| 配置持久化 | `src/doc2mind/core/config.py` L197+ | `_PERSIST_FIELDS` 元组 + `from_env()` 反射 |
| SSE 流式基线 | `src/doc2mind/server/http.py` L1691-1705 | `/v1/events` 现有占位(心跳) |
| WPF SSE 解析 | `DocMind/Services/Doc2kbApiService.cs` L91-243 | `ChatStreamAsync` 的 ResponseHeadersRead + StreamReader 逐行解析,可复制为事件流解析 |

### 2.3 依赖与配置

**依赖**:`pyproject.toml` 加 `watchdog`(纯 Python,~100KB)。**建议放 extras 组 `[project.optional-dependencies] serve = [...]`**(看现有 extras 结构再定),避免 core 安装强制拉包;运行时 import 失败仅降级。

**配置** — 改 `src/doc2mind/core/config.py`:
```python
# --- 文件系统监控（文件监控自动摄入）---
watch_paths: list[str] = field(default_factory=list)   # 可同时监控多个目录
watch_debounce_seconds: float = 5.0                    # 同文件去抖(防编辑器半写状态)
```
- 加进 `_PERSIST_FIELDS`。
- `from_env()` 的反射对 `list[str]` 不适用(现状 `f.type is int/float/bool/Path` 分支,`list[str]` 会走 else 变成字符串)。**实施者需在 from_env 加 list 类型处理**:`elif f.type is list or str(f.type) == "list[str]": kwargs[f.name] = [x.strip() for x in raw.split(",") if x.strip()]`(逗号分隔)。
- `/v1/config` GET/POST 的 ConfigResponse/ConfigUpdate DTO(http.py 内)加 `watch_paths: list[str]` 字段,前端设置页可写。

### 2.4 监控模块 — 新建 `src/doc2mind/core/file_watcher.py`

```python
class FileWatcher:
    """watchdog 目录监控:文件新增/修改 → 去抖 → 调 ingest_path。线程安全。"""

    def __init__(
        self,
        paths: list[str],
        settings: Settings,
        collection: str = "default",
        debounce_seconds: float = 5.0,
        on_ingested: Callable[[dict], None] | None = None,  # 回调,供 HTTP 层广播事件
    ) -> None

    def start(self) -> None      # 创建 Observer + 注册多个 watchdog.observers.Observer 的 schedule
    def stop(self) -> None       # observer.stop() + join();幂等
    @property
    def is_running(self) -> bool
```

**关键设计**:
- 事件处理器 `on_any_event(event: watchdog.events.FileSystemEvent)`:
  - 仅处理 `FileCreatedEvent` 和 `FileModifiedEvent`(`event.is_directory` 跳过)。
  - `detect.is_supported(path)` 不过滤则跳过(只看扩展名)。
  - 去抖:字典 `pending: dict[str, float]` 记录 `path → 上次触发时间`,5s 内同路径跳过;用 `threading.Timer` 或后台线程轮询批量处理,避免 watchdog 回调线程里跑重活。
  - 实际入库:`ingest_path(Path(path), settings, collection=collection)` 在**线程池/独立线程**执行(不阻塞 watchdog 回调线程);异常吞掉并 `logging.warning`(符合降级约定)。
- `on_ingested`:入库成功后回调,payload 含 `{"path": str, "collection": str, "result": "ingested"|"skipped"|"failed"}`。
- **幂等保障**:`ingest_path` 自带 MD5 去重(重复触发也只第一次真正入库)。

### 2.5 后端 SSE 广播升级 — 改 `src/doc2mind/server/http.py`

现状 L1691-1705 是占位心跳。升级方案:

```python
# 模块级或 create_app 内维护连接表
_SSE_CONNECTIONS: set[asyncio.Queue] = set()

def _broadcast_event(payload: dict) -> None:
    """推送事件给所有 SSE 订阅者(线程安全:sync 侧调用用 run_coroutine_threadsafe)。"""
    blob = json.dumps(payload, ensure_ascii=False)
    for q in list(_SSE_CONNECTIONS):
        q.put_nowait(blob)

@app.get("/v1/events")
async def events() -> Any:
    async def event_stream() -> Any:
        q: asyncio.Queue = asyncio.Queue()
        _SSE_CONNECTIONS.add(q)
        try:
            # 先发一个 ready 事件
            yield _sse_fmt({"type": "ready", "ts": _now_iso()})
            while True:
                try:
                    blob = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {blob}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"   # 防代理掐断(与 /v1/chat/stream 心跳一致)
        finally:
            _SSE_CONNECTIONS.discard(q)
    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

**FileWatcher 与 HTTP 层互连**:
- `create_app()` 启动时(参考现有 `@app.on_event("shutdown")` L1707 的位置)加 `@app.on_event("startup")`:`if settings.watch_paths: _file_watcher = FileWatcher(...); _file_watcher.on_ingested = lambda payload: _broadcast_event({"type": "file_ingested", **payload}); _file_watcher.start()`。
- shutdown 时 `_file_watcher.stop()`。
- `_broadcast_event` 里 `q.put_nowait` 在**事件循环线程外**被调用(watchdog 线程),若报 `RuntimeError: no running event loop`,用 `asyncio.run_coroutine_threadsafe` 包装(实施者可按实际情况选:put_nowait 在无锁环程下也可,但多线程下建议 queue 用非 asyncio 的 `queue.Queue` + 事件循环轮询,或直接 `asyncio.run_coroutine_threadsafe`。**给出折中:若已在事件循环线程内调用正常,保持 put_nowait;否则 run_coroutine_threadsafe**)。

### 2.6 WPF 订阅与设置页

**API 服务** — 改 `DocMind/Services/IDoc2kbApiService.cs` + `Doc2kbApiService.cs` + `FakeDoc2kbApiService.cs`:
```csharp
/// <summary>订阅后端事件流(SSE /v1/events)。返回 IDisposable 用于取消订阅。</summary>
IDisposable SubscribeEvents(Action<EventMessage> onEvent, CancellationToken ct = default);
```
- `Doc2kbApiService` 实现:复制 `ChatStreamAsync`(L91-243)的 ResponseHeadersRead + StreamReader 逐行模式,解析 `data: {...}` 帧 → 反序列化为 `EventMessage`(新 model:`{Type, Ts, Payload}`),客户端挂后台 Task 循环。
- 连接断开自动重连(指数退避,最多 N 次),避免后端重启后永久失联。

**设置页** — 改 `DocMind/AppSettings.cs` + `SettingsViewModel.cs` + `SettingsView.xaml`:
- `AppSettings` 加 `List<string> WatchPaths { get; set; } = new();` 和 `bool WatchEnabled`(或复用 WatchPaths 空=禁用)。
- `SettingsViewModel` 加 `ObservableCollection<string> WatchPaths` + `AddWatchPathCommand`(输入框+回车/按钮添加)+ `RemoveWatchPathCommand`(删选中项)+ 保存时回写 `_appSettings.WatchPaths` 并推送 `/v1/config`(`BackendConfigUpdate` 加 `List<string>? WatchPaths`)。
- `SettingsView.xaml` 在"启动选项"分区加"文件监控"小节:路径列表 + 添加输入框 + 删除按钮,提示文案"监控目录内的文档变更会自动摄入(需重启后端生效)"。
- `BackendProcessService` 环境变量注入(`DOC2MIND_WATCH_PATHS` 逗号分隔,参考 L296 的 RAG_TOP_K 注入模式;注意空列表不注入)。

**通知** — 改 `DocMind/ViewModels/MainViewModel.cs` 或 `App.xaml.cs`:
- 启动时(后端 Online 后,参考 `App.xaml.cs` L225-259 自动导入处)调 `SubscribeEvents`:
  - `file_ingested` → `NotificationService.Success("已自动摄入: {path}")` + `_documentsViewModel.InvalidateCache()`。
- 注意:订阅生命周期与 App 一致,无需取消(MainViewModel 长生命周期);`EventMessage` 事件回调在后台线程,Toast 需 `Application.Current.Dispatcher.Invoke` 包装。

### 2.7 后端测试规划

| 文件 | 测试 | 验证点 |
|------|------|--------|
| `tests/test_file_watcher.py`(新建) | `test_supported_file_filters` | 仅 .md/.pdf 等 supported 文件触发;.tmp/.DS_Store 不触发 |
| | `test_debounce_merges` | 同文件 2 秒内两次修改 → 只入库一次(可注入 fake ingest 计数) |
| | `test_ingest_failure_doesnt_kill_watcher` | ingest_path 抛异常 → watcher 继续监听 |
| | `test_no_watchdog_graceful` | import watchdog 失败 → FileWatcher.start() 返回 None + 日志提示(monkeypatch import) |
| `tests/test_config.py` | `test_watch_paths_from_env` | `DOC2MIND_WATCH_PATHS=a,b,c` → list 解析 |
| `tests/test_integration.py` | `test_events_endpoint_ready_frame` | GET /v1/events 第一帧是 ready 事件 |
| | `test_events_broadcast_file_ingested` | 打开 SSE 连接 → 触发 _broadcast_event → 收到 file_ingested(可用同步测试直接调 broadcast 再读队列) |

watchdog 是 extras,`tests/test_file_watcher.py` 顶部 `pytest.importorskip("watchdog")` 保证未装时跳过(与 tests 里 sqlite-vec/FTS5 的 skip 模式一致)。

### 2.8 实施顺序

1. `config.py` 加字段 + from_env list 解析 + 持久化(可独立测试)
2. `file_watcher.py`(watchdog 封装,依赖 pipeline.ingest_path + detect.is_supported)
3. `http.py` SSE 广播 + startup/shutdown 挂钩
4. WPF:EventMessage 模型 + SubscribeEvents(Fake 同步)
5. WPF:设置页 watch 路径 UI + BackendConfigUpdate
6. WPF:MainViewModel 订阅 + Toast + 缓存失效
7. 测试全量回归

**工期参考**:监控模块 1 天,SSE 广播 0.5 天,WPF 对接 0.5 天。

---

## 3. 通用注意事项(两阶段都适用)

1. **不要破坏现有 API 契约**:
   - 后端所有新增字段必须**可空/带默认值**,旧客户端忽略新字段(本项目已有此惯例,如 SourceRefDTO 加 chunk_id 时老客户端兼容)。
   - `response_model` 变更向后兼容:Pydantic 多字段不破坏旧解析。
2. **降级优先于报错**:LLM 未配置、watchdog 未装、WebView2 缺失,都必须优雅降级并给出明确提示,不得抛异常中断主流程。
3. **SQLite 写操作必须事务**(参照 `sqlite_vec.py` 的 `BEGIN IMMEDIATE`/`COMMIT`/`ROLLBACK`)。
4. **测试补全**:每个改动点对应一个测试;跑 `python -m pytest tests -q` 和 `dotnet test DocMind.Tests` 全绿才算完成。
5. **代码风格**:后端符合 ruff(项目有 ruff 检查,`ruff check src tests` 必须过);前端遵循现有注释风格(中文注释,说明约束而非过程)。
6. **WPF 新模型/服务方法要同步 Fake**:`DocMind.Tests/FakeDoc2kbApiService.cs` 是接口全量实现,新增接口方法必须在 Fake 补实现,否则测试项目编译失败。

## 4. 验收清单(完成标记)

- [ ] 1.3 GraphStore 三表 + 全部方法
- [ ] 1.4 extractor 抽取 + 降级
- [ ] 1.5 curator extract 动作
- [ ] 1.6 /v1/graph/visualize + entities + relations 端点
- [ ] 1.7 WPF 知识图谱页(WebView2 力导向图)
- [ ] 1.8 知识图谱测试全绿
- [ ] 2.3 config watch_paths + from_env list
- [ ] 2.4 file_watcher.py
- [ ] 2.5 /v1/events 广播升级
- [ ] 2.6 WPF 订阅 + 设置页 + Toast
- [ ] 2.7 文件监控测试全绿
- [ ] 全量:后端 pytest 全绿 + 前端 dotnet test 全绿 + `ruff check src tests` 通过