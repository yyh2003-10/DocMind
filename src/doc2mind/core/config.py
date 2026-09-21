"""核心配置管理 — 全局默认参数与持久化。

配置来源优先级（高 → 低）：
1. CLI 参数（`doc2mind --config ...`）
2. 环境变量 `DOC2MIND_*`
3. 配置文件 `config.toml`（用户目录）
4. 内置默认值（本文件 `DEFAULTS`）
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# --- 平台相关目录 ---
def _user_config_dir() -> Path:
    """跨平台用户配置目录。

    Windows: %APPDATA%\\doc2mind
    macOS:   ~/Library/Application Support/doc2mind
    Linux:   ~/.config/doc2mind
    """
    if os.name == "nt":  # Windows
        base = os.environ.get("APPDATA", str(Path.home()))
        return Path(base) / "doc2mind"
    if os.name == "posix":
        xdg = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
        return Path(xdg) / "doc2mind"
    return Path.home() / ".doc2mind"


def _user_data_dir() -> Path:
    """跨平台用户数据目录（向量库、嵌入缓存）。

    Windows: %LOCALAPPDATA%\\doc2mind
    macOS:   ~/Library/Application Support/doc2mind
    Linux:   ~/.local/share/doc2mind
    """
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", str(Path.home()))
        return Path(base) / "doc2mind"
    if os.name == "posix":
        xdg = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
        return Path(xdg) / "doc2mind"
    return Path.home() / ".doc2mind"


@dataclass
class Settings:
    """运行时配置。

    所有字段都有默认值，构造后通常通过 `Settings.from_env()` 加载用户配置。
    """

    # --- 嵌入引擎 ---
    embed_model: str = "BAAI/bge-small-zh-v1.5"
    embed_dim: int = 512  # bge-small-zh-v1.5 输出维度
    # 嵌入批大小：ONNX 本地嵌入的主要吞吐旋钮（fastembed 官方调优结论：
    # 更大批次显著摊薄单批开销）。32 → 128 在多核 CPU 上常见 2-4× 提升。
    embed_batch_size: int = 128
    # ONNX 推理线程数（intra_op_num_threads）。0 = 交给 onnxruntime 默认；
    # 与物理核数对齐时吞吐最佳（与 ocr_workers 同时调大时注意互相抢核）。
    embed_threads: int = 0
    # 嵌入 token 截断上限。默认 512（bge-small-zh 原生窗口）；切换到长上下文
    # 模型（如 jina-embeddings-v2-base-zh 的 8192）时建议调到 2048，
    # 让 chunk_max_tokens=1500 的分块全文入模，不再被截断丢信息。
    # 改动需重建会话（工厂缓存键包含此字段），调大后嵌入耗时按 token 线性增长。
    embed_max_length: int = 512

    # 本地模型目录（可选）：指向一个含 ONNX 模型文件的目录，优先于 embed_model
    # 使用（fastembed specific_model_path）。留空则用 embed_model 从网络下载。
    embed_model_path: str | None = None

    # --- 分块 ---
    # 出厂对齐 embed_max_length=512：chunk 不得超过嵌入窗口，否则大块只嵌前半截
    # （「库里有却搜不到」根因之一）。默认 480 token（中文约 1200 字）。
    # 切换长窗口模型（jina 2048 等）时再放宽，并同步调大 embed_max_length。
    chunk_max_tokens: int = 480
    chunk_min_chars: int = 50
    chunk_overlap_chars: int = 120
    chunk_max_chars: int = 1200  # 480 token × ~2.5 字符/token

    # --- 检索 ---
    search_top_k: int = 10
    rrf_k: int = 60  # Reciprocal Rank Fusion 常数

    # --- 存储 ---
    db_path: Path = field(default_factory=lambda: _user_data_dir() / "doc2mind.db")
    collection_default: str = "default"

    # --- 字符 ↔ token 估算 ---
    # 中文 ~1 token ≈ 2-3 字符，英文 ~1 token ≈ 4 字符
    # 用 tiktoken 精确计数；fallback 用 chars_per_token 估算
    chars_per_token: float = 2.5

    # --- 服务 ---
    server_host: str = "127.0.0.1"
    server_port: int = 8765

    # --- LLM / RAG 对话 ---
    # 大模型提供商：none（不启用）| openai（OpenAI 兼容 API）| ollama（本地 Ollama）
    llm_provider: str = "none"
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str = ""
    llm_temperature: float = 0.7
    llm_max_tokens: int = 8192

    # RAG 检索上下文参数
    rag_top_k: int = 5
    # 对话检索命中的相关性下限：按每条命中的 max(vector_score, bm25_score) 分量纲
    # 原始分（0-1）过滤。注意与 POST /v1/search 的请求参数 min_score 不同——后者
    # 过滤的是 RRF 融合分（约 0.016~0.033 区间），两者量纲不同，不可混用。
    rag_min_score: float = 0.0
    # RAG 问答模式："strict"（严格知识库模式，未命中直接拒绝）或 "hybrid"（混合增强模式，未命中本地文档时使用大模型常识回答）
    rag_mode: str = "strict"

    # --- 对话 AI 意图路由与科研写作（M1~M6，默认全部 = 旧行为）---
    # 科研写作路由总开关：关闭时 research 意图不生效（回落 question/analysis），
    # 科研子链路不装配。灰度：M3 里程碑后先内部库验证，观察 eval/intent_v1.jsonl
    # 科研命中率 ≥ 0.80 与创作误判 ≤ 0.10 双达标再全量。
    intent_research_enabled: bool = False
    # 创作/科研/问答模糊仲裁模式："rules"（L0+L1 规则仲裁，零额外 LLM 成本）
    # | "llm"（追加 L2 LLM 二判）| "none"（关闭仲裁，沿用规划器初判）。
    # 仲裁器 L1 规则打分 Δ>0.5 高者胜、Δ≤0.5 进 L2 的阈值固化于此注释；
    # 跑分（tools/eval_intent_routing.py）后可微调，调整记录见 spec 4.A.3。
    intent_conflict_arbitration: str = "rules"
    # 通识/百科/外部概念类查询（general_qa 细类）本地未命中或弱命中时自动联网补搜；
    # 关闭时维持现状行为（用户开总开关 + 知识型查询才补搜）。
    web_auto_supplement_general_qa: bool = False
    # 知识型查询自动补搜的本地弱命中阈值（参数化现状硬编码 0.45，不改行为）。
    web_auto_supplement_min_score: float = 0.45
    # 规划器产出 general_qa 细类（通识/百科/外部概念）标签；关闭时规划维持旧 query_type 集合。
    general_qa_type_enabled: bool = False
    # 科研回答引用支撑验证：开启后 research 场景调用 verify_citation_support 并在
    # done 帧输出 research_citation_support。先记录基线不卡 FAIL（spec 6.2 双轨节奏）。
    research_citation_support_check: bool = False
    # 文献集合切片检索/引用上限（防超大集合打爆上下文）。
    research_max_literature: int = 20
    # 图谱 topic 层扩散深度（0 = 不注入 topic，仅文献切片）。
    research_graph_topic_depth: int = 2
    # 规划降级对用户可见（status+thinking 提示 + 审计日志）；关闭 = 回到现状静默回退。
    planning_degradation_visible: bool = True
    # 科研写作 draft 子任务是否追加 web_search 交叉验证前沿观点。
    research_web_crosscheck: bool = False

    # --- 使用档案（Usage Profile）---
    # 决定默认回答风格与参数预设：notes（个人沉淀）/ docs（项目文档，默认）
    # / agent（MCP 记忆层）/ library（批量库管理）。
    # 仅在用户显式「切换档案」时改写相关字段；存量配置不会被静默覆盖。
    usage_profile: str = "docs"

    # --- 检索后重排（Reranker / cross-encoder）---
    # 是否启用重排精排：召回（BM25+向量+RRF）后，用 cross-encoder 对候选逐对打分重排，
    # 显著提升相关性（客服/问答场景最有效）。模型不可用（未装 fastembed / 下载失败）
    # 时自动降级为原始 RRF 排序，绝不阻断检索。
    rerank_enabled: bool = True
    # 重排模型名。fastembed 0.8 的 TextCrossEncoder 仅支持列表内模型；
    # 默认 BAAI/bge-reranker-base（列表内、中英可用、体积 ~280MB）。
    # 注意：Xenova/bge-reranker-v2-m3 不在支持列表，配置它会在首次推理时
    # 静默降级为纯 RRF 排序（2026-09 实测确认）。
    rerank_model: str = "BAAI/bge-reranker-base"
    # 送入重排器的候选数上限（从 RRF 结果截取最靠前若干条），
    # 越大越准但越慢；20 对默认 top_k=5 已绰绰有余。
    rerank_recall: int = 20

    # --- 检索升级（召回质量核心）---
    # 非对称检索查询指令前缀：拼在用户查询前再 embed_query（不动文档嵌入）。
    # 对 bge 系列（本地/API）可显著提升检索；空 = 不启用。已知 bge-zh 检索指令：
    # "为这个句子生成表示以用于检索相关文章："
    query_instruction: str = ""
    # 检索相关度下限（语义下限）：命中的相关度代理（重排启用时用 rerank_score，
    # 否则用 max(vector_score, bm25_score)）低于该值则丢弃，top_k 是上限不硬填。
    # 默认 0.0 = 不启用（向后兼容），建议值由 tools/eval_retrieval.py 输出。
    semantic_floor: float = 0.0
    # 邻块上下文（父子检索）：RAG 组装上下文时，把命中 chunk 同源的相邻分块
    # 一并并入（前后各 N 块），提升回答完整性；0 = 关闭（仅用命中块）。
    # 检索排序仍以命中为准，邻块仅作补充上下文。
    neighbor_context_window: int = 1
    # 父子/小到大上下文模式：
    #   "neighbor"（默认）— 现有邻块 window 行为
    #   "heading"        — 命中后优先并入同 (source, heading) 兄弟块（章节级父上下文），
    #                      无 heading 时回退 neighbor；检索排序仍以命中为准
    #   "off"            — 不做额外上下文
    parent_context_mode: str = "neighbor"
    # 中文 BM25：开启后 FTS5 用 jieba 分词（unicode61 tokenizer），显著改善
    # 2 字中文词召回；切换会触发一次性 FTS5 索引重建。false 保持原 trigram 路径。
    bm25_jieba_enabled: bool = True
    # 专家把关/避坑检索的相关度阈值（替代旧的硬编码 0.4；为忠实余弦标尺）。
    pitfall_min_score: float = 0.30
    # 逐条引用门控线（2026-09-13 引用治理）：重排启用时每条命中按自己的
    # rerank_score（sigmoid 0-1）判定——>= 该值才拿引用编号 [n]；>= 60% 该值
    # 降为「背景勿引用」；其余丢弃。重排不可用时回退旧组级逻辑（jina 余弦
    # 标尺正负样本重叠，逐条余弦门不可靠，见 tools/calibrate_citation_threshold.py）。
    citation_min_score: float = 0.35
    # RRF 融合权重 "vec,bm25[,sparse]"（如 "1,1" 中性向量/BM25；"2,1,1" =
    # 向量 2、BM25 1、稀疏 1）。第三位为稀疏向量路（D2），须同时开启
    # sparse_retrieval_enabled 才生效。
    rrf_weights: str = "1,1"
    # 融合模式（B3）：
    #   "rrf"   —— 加权倒数排名融合（默认，对分数尺度不敏感，稳健）
    #   "score" —— 加权分数融合：score = w_v*vector + w_b*bm25（善用分数信息，
    #              要求两路分数已校准：忠实余弦 + 归一化 BM25）
    fusion_mode: str = "rrf"
    # 重排分校准温度（B3）：展示前对 cross-encoder logits 做温度缩放后再 sigmoid
    # 归一化，0<T<1 让重排分更陡（更自信的分辨），>1 更平缓（更保守）。
    # 默认 1.0 = 原 plain sigmoid，向后兼容。用于让 rerank_score 不再被误读为
    # 严格校准概率，交由评估集按需标定（见 tools/eval_retrieval.py）。
    rerank_calibration_temperature: float = 1.0
    # 大语料向量索引量化（B4，实验性）：
    #   "none" —— float32 存储（默认，最高精度，向后兼容）
    #   "int8" —— 8 位量化存储（降内存/加速检索），仅当语料规模受益且本机
    #             sqlite-vec 支持 int8 写入时才实际启用；不支持时自动回退
    #             float32 并告警，绝不破坏现有库。切到 int8 需重建索引生效。
    vector_quantize: str = "none"
    # 查询扩展（C1，LLM 可选）："off" 关闭（默认）| "multi" 多查询 | "hyde" 假设文档
    # | "both" 二者都要。开启后录入 RAG 检索前用 LLM 生成查询变体/假设文档，分别
    # 检索后合并去重，提升长尾/多义查询召回。LLM 不可用或调用失败时自动降级为
    # 单查询（沿用既有容错模式），绝不影响检索可用性。成本由 LLM 计费，仅解锁启用。
    query_expansion: str = "off"
    # 上下文检索（C2，LLM 可选）：True 时在嵌入 chunk 前拼上文档级摘要前缀
    # [文档摘要]...，改善长文档/跨章节召回（Anthropic Contextual Retrieval 本地化）。
    # 需文档已含 summary（enrich 生成）且对存量库执行 reindex 重新嵌入生效。
    contextual_retrieval: bool = False
    # 稀疏向量召回路（D2）：开启后建独立稀疏倒排索引（sparse_terms），检索时作为
    # 第三条词法稀疏召回路并入三路融合（RRF/score），提升词法召回冗余。默认关闭
    # （向后兼容不含 sparse_terms 的旧库）；开启需与 rrf_weights 第三位>0 搭配。
    # 首次开启会对存量索引一次性回填稀疏词，无需重建向量索引。
    sparse_retrieval_enabled: bool = False

    # 自定义 RAG 系统提示词（人设/回答风格）；None/空 = 用内置默认提示词。
    # 环境变量 DOC2MIND_RAG_SYSTEM_PROMPT 可覆盖。
    rag_system_prompt: str | None = None

    # 多轮对话历史 token 预算：从最新消息向前保留,直到累计 token 超过此值。
    # 0 = 不按 token 截断(仍受历史上限条数保护)。
    # 环境变量 DOC2MIND_RAG_MAX_HISTORY_TOKENS 可覆盖。
    rag_max_history_tokens: int = 4096

    # 多轮对话历史上限（条）：仅保留最近 N 条 user/assistant 消息,防止内存与上下文无限增长。
    # 0/负数 = 用内置默认 20；正数下限 2（至少保留一轮问答）。
    # 环境变量 DOC2MIND_RAG_MAX_HISTORY_MESSAGES 可覆盖（旧名
    # DOC2MIND_RAG_MAX_HISTORY_TURNS 兼容读取，语义同为消息条数）。
    rag_max_history_messages: int = 20

    # 对话附件允许目录（可选白名单）：非空时，对话请求携带的附件路径必须位于
    # 其中一个目录下（逗号分隔；环境变量 DOC2MIND_ATTACHMENT_ALLOWED_DIRS 可覆盖）。
    # 空列表 = 不限制目录（仍受扩展名白名单约束：仅 loader 支持的文档/代码/图片
    # 与纯文本类型可读）。
    attachment_allowed_dirs: list[str] = field(default_factory=list)

    # LLM 调用超时（秒），0 = 使用默认值 180s
    llm_timeout: float = 0.0

    # 联网搜索总预算（秒）：多引擎聚合 + 正文精读的硬上限。
    # 到点带着已完成结果返回；过短会在慢网/反爬下几乎拿不到正文。
    # 默认 36s：给免费引擎反爬/慢响应留出精读窗口（旧 16s 常只够候选聚合）。
    # 环境变量 DOC2MIND_WEB_SEARCH_TIMEOUT 可覆盖。
    web_search_timeout: float = 36.0
    # 自建/自选 SearXNG 实例（元搜索主通道，开源免 Key）。
    # 空 = 使用内置公有实例；可填单个 URL 或逗号分隔多个。
    # 例：http://127.0.0.1:8888 或 https://searx.example.com,https://searx.be
    # 环境变量 DOC2MIND_WEB_SEARCH_SEARXNG_URL 可覆盖。
    web_search_searxng_url: str = ""

    # --- 摄入并发 ---
    # 文件级并行 worker 数：两段式流水线（多线程并行 解析→分块→嵌入，
    # 主线程串行写库保住 SQLite 单写者语义）。1 = 串行（默认，与历史行为
    # 一致）；调大对扫描件（OCR 释放 GIL）/ 大批量导入收益最明显。
    ingest_workers: int = 1

    # --- 摄入护栏（防病态输入打爆内存）---
    # 单文件大小上限（MB），超过则在收集阶段整文件跳过并记为 skipped，
    # 避免几百 MB 的扫描 PDF / 超大 Excel 整读进内存。0 或负数 = 不限制。
    max_file_size_mb: int = 500
    # 单次目录导入最大文件数，超过的部分在收集阶段直接丢弃（进度按实际
    # 处理数上报）。0 或负数 = 不限制。
    max_files_per_import: int = 5000

    # --- OCR（扫描件）---
    # 扫描型 PDF 渲染 DPI（越高越准但越慢，200 是精度/速度平衡点）
    ocr_render_dpi: int = 200
    # CPU OCR 并行实例数（PaddleOCR 无原生 batch，官方推荐多实例并行；
    # 每实例独占一份模型内存 ~几百MB。GPU 模式固定串行，此值仅 CPU 生效）
    ocr_workers: int = 1
    # CPU OCR 启用 oneDNN 加速。默认关闭：Paddle 3.x PIR 执行器下 oneDNN
    # 算子会崩溃（见 image_loader._disable_paddle_pir 注释）；开启后运行时
    # 一旦崩溃会自动回退并永久禁用（_OCR_MKLDNN_BROKEN 锁存）。
    ocr_enable_mkldnn: bool = False

    # --- AI 自动整理（curate）---
    # 入库成功后自动打标签/生成摘要；ingest_text 未指定集合时还会自动归类。
    auto_curate_on_ingest: bool = True
    # 语义去重候选阈值（向量相似分 0-1，越高越严格，默认 0.85）
    curate_dedup_score_threshold: float = 0.85
    # 整理时送入 LLM 的文档内容截断上限（字符）
    curate_max_chars: int = 8000
    # 目录摄入超过该文件数时跳过入库自动整理（防一次触发海量 LLM 调用，
    # 此时改用 curate 工具/接口批量整理）
    curate_auto_max_files: int = 20

    # --- 嵌入模型下载 ---
    # HuggingFace 端点/镜像。国内网络直连 HF 常超时，设为
    # `https://hf-mirror.com` 可正常下载模型（fastembed 首次使用约 90MB）。
    # 环境变量 `DOC2MIND_HF_ENDPOINT` 可覆盖；留空时自动使用镜像
    # hf-mirror.com，无需手动配置。
    hf_endpoint: str | None = None

    # --- 嵌入模型缓存目录 ---
    # 默认与知识库同目录：%LOCALAPPDATA%\doc2mind\fastembed_cache
    embed_cache_dir: Path = field(
        default_factory=lambda: _user_data_dir() / "fastembed_cache"
    )

    # --- poppler 可执行目录（扫描 PDF OCR 渲染依赖）---
    # 指向包含 pdftoppm(.exe) 的 bin 目录；为空时 pdf_loader 按内置顺序自动探测
    # （PATH → 项目 tools/poppler → 常见安装目录）。客户端设置页可指定。
    poppler_path: str = ""

    # --- 文件系统监控（文件变更自动摄入）---
    watch_paths: list[str] = field(default_factory=list)
    watch_debounce_seconds: float = 5.0

    @classmethod
    def from_env(cls) -> Settings:
        """从配置文件 + 环境变量加载配置（覆盖默认值）。

        优先级（高 → 低）：
        1. 环境变量 `DOC2MIND_<UPPER_FIELD>`
        2. 配置文件 `config.toml`（用户目录，`doc2mind config --set-model` 写入）
        3. 内置默认值

        例如：
        - `DOC2MIND_EMBED_MODEL`
        - `DOC2MIND_DB_PATH`
        - `DOC2MIND_SERVER_PORT`
        """
        # 先读 config.toml（低优先级），再用环境变量覆盖（高优先级）
        kwargs: dict[str, object] = dict(load_config_file())
        for f in cls.__dataclass_fields__.values():
            env_key = f"DOC2MIND_{f.name.upper()}"
            if (raw := os.environ.get(env_key)) is None:
                continue
            try:
                if f.type is int or f.type == "int":
                    kwargs[f.name] = int(raw)
                elif f.type is float or f.type == "float":
                    kwargs[f.name] = float(raw)
                elif f.type is bool or f.type == "bool":
                    kwargs[f.name] = raw.lower() in ("1", "true", "yes", "on")
                elif f.type is Path or f.type == "Path":
                    kwargs[f.name] = Path(raw).expanduser().resolve()
                elif f.type is list or "list" in str(f.type):
                    kwargs[f.name] = [x.strip() for x in raw.split(",") if x.strip()]
                else:
                    kwargs[f.name] = raw
            except (ValueError, TypeError):
                continue
        # 兼容旧环境变量名：DOC2MIND_RAG_MAX_HISTORY_TURNS（语义一直是消息条数，
        # AUD-011 改名 DOC2MIND_RAG_MAX_HISTORY_MESSAGES）
        if (
            os.environ.get("DOC2MIND_RAG_MAX_HISTORY_MESSAGES") is None
            and (legacy := os.environ.get("DOC2MIND_RAG_MAX_HISTORY_TURNS")) is not None
        ):
            logger.warning(
                "环境变量 DOC2MIND_RAG_MAX_HISTORY_TURNS 已改名为 "
                "DOC2MIND_RAG_MAX_HISTORY_MESSAGES（语义同为消息条数），本次按新名生效"
            )
            try:
                kwargs["rag_max_history_messages"] = int(legacy)
            except (ValueError, TypeError):
                pass
        s = cls(**kwargs)  # type: ignore[arg-type]
        s._sanitize_rerank_model()

        # embed_dim 与 embed_model 对齐：catalog 已收录的模型直接查维度，
        # 避免用预设 512 建 vec_chunks 表后与模型实际输出维度不符（切换
        # 嵌入模型后 Dimension mismatch 的根因）。显式配置（config.toml /
        # 环境变量的 embed_dim）优先，此处不覆盖；catalog 未收录的自定义
        # 模型保持预设值，由 reindex 的 probe 重建兜底。
        if "embed_dim" not in kwargs:
            from doc2mind.core.embedder.catalog import get_model_info

            info = get_model_info(s.embed_model)
            if info is not None:
                s.embed_dim = info.dim
        return s

    def _sanitize_rerank_model(self) -> None:
        """把已知不可用的重排模型名替换为默认可用模型。

        根因（2026-09-13 实测）：WPF 旧默认 / 用户 appsettings 曾写入
        `Xenova/bge-reranker-v2-m3`，经 DOC2MIND_RERANK_MODEL 环境变量覆盖
        config.toml 后，fastembed TextCrossEncoder 直接报 not supported，
        每次对话都「检索降级：重排模型不可用」。这里在配置加载层静默纠偏，
        避免用户被半可用状态拖垮。
        """
        model = (self.rerank_model or "").strip()
        if not model:
            self.rerank_model = "BAAI/bge-reranker-base"
            return
        lowered = model.lower()
        # fastembed 0.8 TextCrossEncoder 支持列表外的常见误配
        if "xenova" in lowered or lowered.endswith("/bge-reranker-v2-m3"):
            logger.warning(
                "重排模型 %s 不在 fastembed TextCrossEncoder 支持列表，"
                "已自动切换为 BAAI/bge-reranker-base",
                model,
            )
            self.rerank_model = "BAAI/bge-reranker-base"

    def ensure_dirs(self) -> None:
        """确保数据目录存在。"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


# --- config.toml 持久化 ---
# 允许写入 config.toml 的字段（其余字段由环境变量 / 默认值决定）
_PERSIST_FIELDS: tuple[str, ...] = (
    "embed_model",
    "embed_dim",
    "embed_model_path",
    "embed_batch_size",
    "embed_threads",
    "embed_max_length",
    "ingest_workers",
    "max_file_size_mb",
    "max_files_per_import",
    "ocr_render_dpi",
    "ocr_workers",
    "ocr_enable_mkldnn",
    "chunk_max_tokens",
    "chunk_min_chars",
    "chunk_overlap_chars",
    "chunk_max_chars",
    "search_top_k",
    "rrf_k",
    "hf_endpoint",
    # LLM / RAG 对话（llm_api_key 除外 — 见 save_settings）
    "llm_provider",
    "llm_base_url",
    "llm_model",
    "llm_temperature",
    "llm_max_tokens",
    "rag_top_k",
    "rag_min_score",
    "rag_mode",
    "usage_profile",
    "rerank_enabled",
    "rerank_model",
    "rerank_recall",
    "rag_system_prompt",
    "rag_max_history_tokens",
    "rag_max_history_messages",
    "attachment_allowed_dirs",
    "llm_timeout",
    "web_search_timeout",
    "web_search_searxng_url",
    # AI 自动整理（curate）
    "auto_curate_on_ingest",
    "curate_dedup_score_threshold",
    "curate_max_chars",
    "curate_auto_max_files",
    # 检索升级（召回质量核心）
    "query_instruction",
    "semantic_floor",
    "parent_context_mode",
    "bm25_jieba_enabled",
    "pitfall_min_score",
    "citation_min_score",
    "rrf_weights",
    "fusion_mode",
    "rerank_calibration_temperature",
    "vector_quantize",
    "query_expansion",
    "contextual_retrieval",
    "sparse_retrieval_enabled",
    "neighbor_context_window",
    # 对话 AI 意图路由与科研写作（默认=旧行为；web_auto_supplement_min_score
    # 为参数型阈值仅环境变量/默认值生效，不入 config.toml）
    "intent_research_enabled",
    "intent_conflict_arbitration",
    "web_auto_supplement_general_qa",
    "general_qa_type_enabled",
    "research_citation_support_check",
    "research_max_literature",
    "research_graph_topic_depth",
    "planning_degradation_visible",
    "research_web_crosscheck",
    # 文件监控
    "watch_paths",
    "watch_debounce_seconds",
)

# 敏感字段：不写入 config.toml（API Key 明文落盘有泄漏风险）。
# 运行时 key 由 WPF 前端通过环境变量 / POST /v1/config 注入；
# 历史版本允许手写 config.toml 的 llm_api_key 生效；为消除 API Key 明文
# 落盘的泄漏面，1.1 起不再从 config.toml 读取任何敏感字段（llm_api_key 仅
# 由环境变量 DOC2MIND_LLM_API_KEY / POST /v1/config 运行时注入）。
_SENSITIVE_FIELDS: frozenset[str] = frozenset()


def config_file_path() -> Path:
    """用户配置文件路径：Windows %APPDATA%\\doc2mind\\config.toml。

    支持 DOC2MIND_CONFIG 环境变量重定向到任意位置（客户端「自动寻找
    可用配置」找到非默认位置的配置文件时注入），未设置时用默认目录。
    """
    override = os.environ.get("DOC2MIND_CONFIG")
    if override:
        return Path(override).expanduser()
    return _user_config_dir() / "config.toml"


def server_port_file_path() -> Path:
    """后端实际监听端口状态文件：Windows %LOCALAPPDATA%\\doc2mind\\server.port。

    `doc2mind serve` 在端口被占用自动 +1 探测后写入，供 WPF 客户端
    读取以跟随实际端口（默认 8765 被占时后端会在 8766/8767… 上服务）。
    """
    return _user_data_dir() / "server.port"


def load_config_file() -> dict[str, object]:
    """读取 config.toml（若存在），返回字段字典；缺失/损坏时返回空 dict。

    支持顶层 `[doc2mind]` 小节（推荐），也兼容平铺键值。

    损坏（语法错误/读失败）时记录告警并可通过 `get_config_load_error()`
    获取原因，供启动界面 / `/v1/config` 提示用户，而不是静默丢弃全部自定义配置。
    """
    global _config_load_error
    path = config_file_path()
    if not path.is_file():
        return {}
    try:
        try:
            import tomllib  # Python 3.11+
        except ImportError:  # pragma: no cover — Python 3.10 回退
            import tomli as tomllib  # type: ignore[no-redef]

        with open(path, "rb") as f:
            data = tomllib.load(f)
        root = data.get("doc2mind", data) if isinstance(data, dict) else {}
        # 读取集合 = 持久化字段（密钥不再从 config.toml 读取，见 _SENSITIVE_FIELDS）
        readable = set(_PERSIST_FIELDS) | _SENSITIVE_FIELDS
        result = {k: v for k, v in root.items() if k in readable}
        # 兼容旧配置文件：rag_max_history_turns → rag_max_history_messages（AUD-011 改名）
        if "rag_max_history_messages" not in result and "rag_max_history_turns" in root:
            logger.warning(
                "config.toml 中的 rag_max_history_turns 已改名为 "
                "rag_max_history_messages（语义同为消息条数），本次按新名生效"
            )
            result["rag_max_history_messages"] = root["rag_max_history_turns"]
        _config_load_error = None
        return result
    except Exception as e:  # noqa: BLE001 — 配置损坏时回退默认值
        _config_load_error = f"config.toml 解析失败（{path}）：{e}，已临时回退默认配置"
        logger.warning("%s；请修复或删除该文件后重启", _config_load_error)
        return {}


# 最近一次 load_config_file 的失败原因（None = 正常）。config 在进程启动时
# 加载一次，这里缓存错误供 /v1/config 等查询；save_settings 成功写入后清除。
_config_load_error: str | None = None


def get_config_load_error() -> str | None:
    """返回启动时 config.toml 的解析错误（无则 None）。"""
    return _config_load_error


def _toml_repr(value: object) -> str:
    """把 Python 值渲染为 TOML 字面量。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list | tuple):
        return json.dumps(list(value), ensure_ascii=False)
    return json.dumps(str(value), ensure_ascii=False)


def save_settings(settings: Settings) -> bool:
    """把当前配置持久化到 config.toml（下次启动自动生效）。

    Returns:
        True = 写入成功；False = 失败（目录不可创建/磁盘满/权限不足，
        已记录 error 日志，调用方应向用户提示"重启后配置可能回退"）。
    """
    global _config_load_error
    path = config_file_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.error("创建配置目录失败（%s）：%s，配置未持久化", path.parent, e)
        return False
    lines = [
        "# DocMind 配置文件（`doc2mind config` 命令写入）",
        "# 可用 `doc2mind models` 查看可选嵌入模型",
        "",
        "[doc2mind]",
    ]
    for name in _PERSIST_FIELDS:
        value = getattr(settings, name, None)
        if value is None:
            continue
        lines.append(f"{name} = {_toml_repr(value)}")
    try:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as e:
        logger.error("写入配置文件失败（%s）：%s，配置未持久化", path, e)
        return False
    # 成功写入后，此前启动时的解析错误已不复存在
    _config_load_error = None
    return True


# --- 融合权重解析 ---
def parse_rrf_weights(value: str) -> tuple[float, float, float]:
    """把 "vec,bm25[,sparse]" 字符串解析为权重元组，非法输入回退中性 1:1:0。

    例：parse_rrf_weights("2,1") → (2.0, 1.0, 0.0)；
        parse_rrf_weights("2,1,1") → (2.0, 1.0, 1.0)；
        parse_rrf_weights("") → (1.0, 1.0, 0.0)。
    ≤0 会被钳制为 0（完全忽略该路，等价融合模式只由其余路决定）。
    第三位（稀疏路）未提供默认为 0（不启用，向后兼容）。
    """
    try:
        parts = [p.strip() for p in str(value).split(",")]
        if len(parts) < 2:
            return (1.0, 1.0, 0.0)
        ws = [max(0.0, float(p)) for p in parts[:3]]
        while len(ws) < 3:
            ws.append(0.0)
        return (ws[0], ws[1], ws[2])
    except (ValueError, TypeError):
        return (1.0, 1.0, 0.0)


# --- 使用档案预设 ---
# 仅在用户显式切换档案时调用 apply_usage_profile；不静默覆盖用户已改字段。
USAGE_PROFILES: dict[str, dict[str, Any]] = {
    "docs": {
        "label": "项目文档",
        "description": "手册/需求/设计文档问答，强调出处与可核对",
        "rag_top_k": 5,
        "rag_min_score": 0.35,
        "rag_mode": "strict",
        "query_expansion": "off",
        "persona_hint": "docs",
    },
    "notes": {
        "label": "个人沉淀",
        "description": "笔记/踩坑/经验，短答、诚实引用、不硬凑洞察",
        "rag_top_k": 4,
        "rag_min_score": 0.35,
        "rag_mode": "strict",
        "query_expansion": "off",
        "persona_hint": "notes",
    },
    "agent": {
        "label": "Agent 记忆",
        "description": "MCP/工具检索：稳定、可解析、少闲聊",
        "rag_top_k": 5,
        "rag_min_score": 0.30,
        "rag_mode": "strict",
        "query_expansion": "off",
        "persona_hint": "agent",
    },
    "library": {
        "label": "库管理",
        "description": "批量入库与整理优先，对话为辅",
        "rag_top_k": 8,
        "rag_min_score": 0.30,
        "rag_mode": "strict",
        "query_expansion": "multi",
        "persona_hint": "office",
    },
}


def apply_usage_profile(s: Settings, profile: str) -> Settings:
    """按档案写入推荐默认（dataclass replace，不原地改全局单例）。

    未知档案名原样返回。已手调过的用户显式切换时会被覆盖——这是切换档案
    的预期语义，UI 应二次确认。
    """
    preset = USAGE_PROFILES.get((profile or "").strip().lower())
    if not preset:
        return s
    return dataclasses.replace(
        s,
        usage_profile=profile.strip().lower(),
        rag_top_k=int(preset["rag_top_k"]),
        rag_min_score=float(preset["rag_min_score"]),
        rag_mode=str(preset["rag_mode"]),
        query_expansion=str(preset["query_expansion"]),
    )


def profile_persona_hint(profile: str) -> str:
    """返回档案对应的回答风格提示（拼进 system prompt）。"""
    preset = USAGE_PROFILES.get((profile or "").strip().lower())
    if not preset:
        return ""
    hint = preset.get("persona_hint")
    if hint == "notes":
        return (
            "\n【使用档案：个人沉淀】像私人笔记助手：直接引用笔记原文要点；"
            "没有就明确说笔记里没找到；禁止硬凑「架构洞察」和 [ACTIONS] 行动列表；回答尽量短。"
        )
    if hint == "agent":
        return (
            "\n【使用档案：Agent 记忆】作为工具检索层：输出事实与出处编号即可；"
            "不要寒暄、不要行动建议列表；证据不足时返回「知识库中未找到」。"
        )
    if hint == "office":
        return "\n【使用档案：库管理】回答偏清单与可操作步骤，便于整理知识库。"
    return (
        "\n【使用档案：项目文档】优先给出可核对的参数/流程/结论，并标注 [n] 出处编号；"
        "篇幅以说清为准，禁止为显得专业而拉长；"
        "若资料中没有高价值延伸，不要输出「架构洞察」段落；"
        "仅当问题本身是「下一步做什么/怎么推进」时才输出 [ACTIONS: ...]，否则省略该行。"
    )


# --- 推荐检索质量配置（F0：把已验证的根基参数产品化）---
# 依据：vector-retrieval-foundation-upgrade 阶段 A/B/D + tools/eval_retrieval.py
# 标定结论。默认 Settings() 保持向后兼容；仅在用户显式「应用推荐」时写入。
BGE_ZH_QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："

RECOMMENDED_RETRIEVAL_PRESET: dict[str, Any] = {
    "label": "推荐检索配置",
    "description": (
        "开启 bge 中文检索指令 + 语义下限 + jieba BM25 + 邻块上下文；"
        "稀疏路/上下文前缀/查询扩展保持关闭（需额外索引或 LLM 成本，按需再开）"
    ),
    "query_instruction": BGE_ZH_QUERY_INSTRUCTION,
    "semantic_floor": 0.15,
    "bm25_jieba_enabled": True,
    "neighbor_context_window": 1,
    "fusion_mode": "rrf",
    "rrf_weights": "1,1",
    "pitfall_min_score": 0.30,
    "sparse_retrieval_enabled": False,
    "contextual_retrieval": False,
    "query_expansion": "off",
    "rerank_enabled": True,
}

# 应用推荐配置时会改动的字段（preview / apply 白名单，防止误写密钥类字段）
RECOMMENDED_RETRIEVAL_FIELDS: tuple[str, ...] = (
    "query_instruction",
    "semantic_floor",
    "bm25_jieba_enabled",
    "neighbor_context_window",
    "fusion_mode",
    "rrf_weights",
    "pitfall_min_score",
    "sparse_retrieval_enabled",
    "contextual_retrieval",
    "query_expansion",
    "rerank_enabled",
)


def recommended_retrieval_preview(s: Settings | None = None) -> dict[str, Any]:
    """对比当前配置与推荐检索预设，返回将要变更的字段（只读，不写回）。

    Returns:
        {
          "label", "description",
          "current": {field: value},
          "recommended": {field: value},
          "changes": [{field, from, to}],
          "aligned": bool,
        }
    """
    if s is None:
        s = get_settings()
    current: dict[str, Any] = {}
    recommended: dict[str, Any] = {}
    changes: list[dict[str, Any]] = []
    for name in RECOMMENDED_RETRIEVAL_FIELDS:
        cur = getattr(s, name, None)
        rec = RECOMMENDED_RETRIEVAL_PRESET.get(name)
        current[name] = cur
        recommended[name] = rec
        if cur != rec:
            changes.append({"field": name, "from": cur, "to": rec})
    return {
        "label": RECOMMENDED_RETRIEVAL_PRESET["label"],
        "description": RECOMMENDED_RETRIEVAL_PRESET["description"],
        "current": current,
        "recommended": recommended,
        "changes": changes,
        "aligned": not changes,
    }


def apply_recommended_retrieval(
    s: Settings | None = None,
    fields: Sequence[str] | None = None,
) -> tuple[Settings, list[dict[str, Any]]]:
    """应用推荐检索配置（dataclass replace，不原地改单例）。

    Args:
        s: 基准配置；None 时取 get_settings()
        fields: 仅应用这些字段；None = 全部 RECOMMENDED_RETRIEVAL_FIELDS

    Returns:
        (new_settings, changes) — changes 与 preview.changes 同构。
        已对齐时 changes 为空，new_settings 与 s 等价。
    """
    if s is None:
        s = get_settings()
    allow = (
        tuple(fields) if fields is not None else RECOMMENDED_RETRIEVAL_FIELDS
    )
    updates: dict[str, Any] = {}
    changes: list[dict[str, Any]] = []
    for name in allow:
        if name not in RECOMMENDED_RETRIEVAL_PRESET:
            continue
        rec = RECOMMENDED_RETRIEVAL_PRESET[name]
        cur = getattr(s, name, None)
        if cur != rec:
            updates[name] = rec
            changes.append({"field": name, "from": cur, "to": rec})
    if not updates:
        return s, []
    return dataclasses.replace(s, **updates), changes


# --- 全局单例（惰性）---
_settings: Settings | None = None


def get_settings() -> Settings:
    """获取全局配置单例。"""
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings


def set_settings(s: Settings) -> None:
    """注入配置（测试用）。"""
    global _settings
    _settings = s
