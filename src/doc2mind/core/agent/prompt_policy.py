"""对话提示词多轨策略。

RAG 轨：保持现有「精准回答 + 禁 tool JSON + 篇幅原则」。
Deep QA 轨：定义/原理类问题且具备可核对资料时，允许结构化展开（仍禁止注水）。
Delivery 轨：创作/科研/交付/续写任务，覆盖篇幅压制，允许充分展开。

只借鉴开源 agent 的能力分层概念，提示词与命名均为 DocMind 自研。
"""

from __future__ import annotations

import re
from typing import Any

PROMPT_TRACK_RAG = "rag"
PROMPT_TRACK_DEEP_QA = "deep_qa"
PROMPT_TRACK_DELIVERY = "delivery"

# 渲染轨（与内容轨正交）：markdown | html。html = 对话气泡整页 HTML 体验。
ANSWER_FORMAT_MARKDOWN = "markdown"
ANSWER_FORMAT_HTML = "html"

# 交付型意图：默认走 delivery 轨
DELIVERY_QUERY_TYPES = frozenset({"creative", "research"})

# 定义/原理类问法：启用 deep_qa 的必要条件之一（还需具备可核对资料）
_DEFINITION_QUERY_RE = re.compile(
    r"是什么|什么是|啥是|定义|含义|概念|原理|区别|作用|用途|指什么|指的是"
)

DEFAULT_CONTINUE_INSTRUCTION = (
    "请接着会话中上一条可能被截断的回答继续写。"
    "只输出续写部分，不要重复已经写过的内容；保持同一结构、口径与引用规则，"
    "直到本任务内容完整收束。"
)

CONTINUE_TRIGGER_WORDS = frozenset({"", "继续", "继续写", "接着写", "续写", "continue"})

# 交付模式覆盖段：追加在既有 system prompt 之后，避免大改共享提示词
DELIVERY_OVERRIDE = """【覆盖·交付/长文模式】
本回合按交付任务处理，篇幅以任务要求为准：
1. 不适用「简单事实题一段话 / 中等题 300-500 字」的压缩规则；
2. 报告/方案/课件/综述等应按结构完整展开，可用标题、分节、表格；
3. 禁止为凑字数空洞重复；资料不足时明确写出缺口与推测边界；
4. 引用编号仍只能来自本轮给出的资料列表，禁止编造；
   文末来源说明只能写本轮实际资料条数（如「基于本轮 N 条资料」），
   禁止「本地知识库（11-15）」式编号区间或虚构页码；
5. 【P0 约束】正文中仍禁止输出 JSON 工具调用或 function_call；
   若检索不足，请用文字说明局限，不要罗列搜索词或伪工具 JSON。
"""

# 定义/深度问答覆盖段：结构化但禁止注水；单源必须披露证据强度
DEEP_QA_OVERRIDE = """【覆盖·定义/深度问答模式】
本回合是概念/定义/原理类问题，且已具备可核对资料（深度搜索、多来源或库内命中）。
1. 不适用「简单事实题只写一段话」的压缩规则，但禁止为显得专业而注水；
2. 按以下结构作答（有依据才写对应项；资料未覆盖的项直接写「资料未覆盖」，禁止编造）：
   - 一句定义
   - 意义/为何重要（工程或业务含义）
   - 关键量与单位（如有）
   - 核心公式、要点或典型参数（资料有则写，并标注 [n]）
   - 相关概念边界或规范/限值提示（资料有则写）
   - 一两句总结
3. 引用纪律：本轮可引用资料 ≥2 条时，正文至少引用 2 条不同 [n]；
   仅 1 条可引用时，必须在文中写明「证据强度：弱（单一来源）」；
4. 引用编号只能来自给出的资料列表，禁止编造；文末来源说明只写实际条数，
   禁止「（11-15）」式编号区间；
5. 禁止输出 JSON 工具调用或 function_call；
6. 若本地知识库无可引用原文、仅依赖网页，可在结尾用一句话建议导入教材/规范等权威资料后重问。
"""

# 交付轨默认再抬升一档的输出预算下限（tokens）
DELIVERY_MIN_OUTPUT_TOKENS = 16384
DELIVERY_TOKEN_MULTIPLIER = 2
DEEP_QA_MIN_OUTPUT_TOKENS = 4096
DEEP_QA_TOKEN_MULTIPLIER = 1.5

_TRACK_OVERRIDES = {
    PROMPT_TRACK_DELIVERY: DELIVERY_OVERRIDE,
    PROMPT_TRACK_DEEP_QA: DEEP_QA_OVERRIDE,
}


def is_definition_query(query: str | None) -> bool:
    """是否像定义/原理/概念类问法（「什么是挠度」「X 的定义」等）。"""
    q = (query or "").strip()
    return bool(q and _DEFINITION_QUERY_RE.search(q))


def resolve_prompt_track(
    *,
    query_type: str | None = None,
    creative_mode: str | None = None,
    explicit: str | None = None,
    continue_writing: bool = False,
    query: str | None = None,
    deep_web: bool = False,
    citable_count: int = 0,
    local_cite_count: int = 0,
) -> str:
    """解析本轮提示词轨。

    优先级：continue_writing > 显式 response_mode > 交付意图 > deep_qa > rag。
    deep_qa 仅当：定义类问法 且（深度联网 或 可引用 ≥2 或 库内可引用 ≥1）。
    检索前可先解析；检索后可携带 citable/local_cite/deep_web 再解析一次升级。
    """
    if continue_writing:
        return PROMPT_TRACK_DELIVERY
    exp = (explicit or "").strip().lower()
    if exp in (PROMPT_TRACK_RAG, PROMPT_TRACK_DELIVERY, PROMPT_TRACK_DEEP_QA):
        return exp
    qt = (query_type or "").strip().lower()
    if qt in DELIVERY_QUERY_TYPES:
        return PROMPT_TRACK_DELIVERY
    if creative_mode and str(creative_mode).strip():
        return PROMPT_TRACK_DELIVERY
    has_evidence = bool(deep_web) or int(citable_count or 0) >= 2 or int(local_cite_count or 0) >= 1
    if is_definition_query(query) and has_evidence:
        return PROMPT_TRACK_DEEP_QA
    return PROMPT_TRACK_RAG


HTML_ANSWER_OVERRIDE = """【覆盖·HTML 体验模式】
本轮回答的可见内容必须是一个完整、自包含的 HTML 文档，用 ```html 围栏包裹。
1. 所有标题、正文、公式、图表、动画都写进这一份 HTML（含 style/script 或 CDN 库）；
2. 布局与表现形式由你根据知识形态自由决定（分栏、卡片、Canvas 示波器、SVG、时间轴等）；
3. 视觉基线（必须遵守，禁止裸默认样式）：
   - 在 <style> 中显式设置页面背景色与前景色，并跟随系统主题：
     :root { color-scheme: light dark; } 配合 @media (prefers-color-scheme: dark)
     定义两套变量（背景/表面/文字/强调色），页面在亮暗环境下都协调；
   - 使用系统字体栈并带中文回退，如 font-family: -apple-system, "Segoe UI",
     "Microsoft YaHei", "PingFang SC", sans-serif；正文行高 ≥ 1.6；
   - 内容卡片化组织：圆角容器、充分留白、清晰层级；禁止默认浏览器样式的
     裸 <h1>/<p> 直排；整页只用 1–2 个强调色，克制不花哨；
   - 排版品质对齐现代设计系统（如 shadcn / Notion 风格），而非 Word 文档。
4. 组件按知识形态选型（不要一律散文）：数值对比 → 大数字卡片或矩阵表格；
   流程/演变 → 步骤条或时间轴；结构关系 → SVG 图示或层级树；数据趋势 → 图表。
5. CDN 该用则用：涉及图表用 Chart.js、公式用 KaTeX、流程图用 Mermaid、
   动画可用 GSAP；简单内容纯内联实现即可，不为用而用；
6. 禁止跟踪像素、禁止引用用户本地文件；引用资料用 <a class="cite" data-n="1">[1]</a>；
7. 不要输出围栏之外的大段解说；说明写在页面里；
8. 仍禁止 JSON 工具调用与编造引用编号；来源说明只写实际条数，禁止（n-m）区间。
"""


def apply_prompt_track(system_prompt: str, track: str) -> str:
    """把轨策略应用到 system prompt。RAG 原样返回；其余轨追加对应覆盖段。"""
    override = _TRACK_OVERRIDES.get(track or "")
    if not override:
        return system_prompt or ""
    base = system_prompt or ""
    marker = override.split("\n", 1)[0]
    if marker in base:
        return base
    return (base.rstrip() + "\n\n" + override).strip() + "\n"


def apply_answer_format(system_prompt: str, answer_format: str | None) -> str:
    """把回答格式轨（markdown/html）叠加到 system prompt。"""
    fmt = (answer_format or "").strip().lower()
    if fmt != ANSWER_FORMAT_HTML:
        return system_prompt or ""
    return _append_override(system_prompt or "", HTML_ANSWER_OVERRIDE)


def _append_override(base: str, override: str) -> str:
    marker = override.split("\n", 1)[0]
    if marker in base:
        return base
    return (base.rstrip() + "\n\n" + override).strip() + "\n"


def boost_max_tokens(
    effective: int | None,
    track: str,
    *,
    ceiling: int | None = None,
) -> int | None:
    """按轨抬升输出预算；RAG 轨原样返回。

    effective 为 None（交由服务端默认）时不强行改写。
    """
    if track == PROMPT_TRACK_DELIVERY:
        if not effective or effective < 1:
            return effective
        boosted = max(int(effective) * DELIVERY_TOKEN_MULTIPLIER, DELIVERY_MIN_OUTPUT_TOKENS)
        cap = ceiling if isinstance(ceiling, int) and ceiling >= 1 else 65536
        return min(boosted, cap)
    if track == PROMPT_TRACK_DEEP_QA:
        if not effective or effective < 1:
            return effective
        boosted = int(effective * DEEP_QA_TOKEN_MULTIPLIER)
        if boosted < DEEP_QA_MIN_OUTPUT_TOKENS:
            boosted = DEEP_QA_MIN_OUTPUT_TOKENS
        cap = ceiling if isinstance(ceiling, int) and ceiling >= 1 else 65536
        return min(boosted, cap)
    return effective


def is_continue_query(query: str | None) -> bool:
    q = (query or "").strip().lower()
    return q in CONTINUE_TRIGGER_WORDS


def build_continue_query(user_query: str | None) -> str:
    """组装续写用户指令。空/「继续写」用默认指令；其它视为补充要求。"""
    q = (user_query or "").strip()
    if is_continue_query(q):
        return DEFAULT_CONTINUE_INSTRUCTION
    return f"{DEFAULT_CONTINUE_INSTRUCTION}\n\n补充要求：{q}"


def done_frame_extras(
    *,
    track: str,
    truncated: bool,
    continue_writing: bool = False,
) -> dict[str, Any]:
    """done 帧扩展字段（向后兼容：旧客户端可忽略未知键）。"""
    extras: dict[str, Any] = {
        "prompt_track": track,
        "truncated": truncated,
        "continue_supported": True,
        "response_mode": "continue" if continue_writing else "normal",
    }
    if truncated:
        extras["continue_hint"] = (
            "回答可能因输出上限被截断，可点击「继续写」补全，或调大设置中的输出上限"
        )
    return extras
