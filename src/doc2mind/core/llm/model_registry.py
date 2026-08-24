"""模型规格智能注册表与动态容量规划引擎。

本模块提供大模型（LLM）的规格元数据感知能力，包括：
1. 精确型号规格字典（涵盖 OpenAI、Anthropic、DeepSeek、Qwen、Gemini、GLM、Moonshot 等）；
2. 启发式特征提取器（智能识别 8k/32k/128k/1m 上下文与 r1/o1/reasoner 推理模型）；
3. 动态预算分配算法（根据模型上下文容量自动规划检索注入量与最大输出上限）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSpec:
    """大模型规格元数据。"""

    model_id: str
    display_name: str
    context_window: int  # 总输入上下文容量（tokens）
    max_output_tokens: int  # 单次最大输出上限（tokens）
    is_reasoning_model: bool = False  # 是否为深度思考/推理模型（DeepSeek-R1 / o1 / QwQ 等）
    recommended_rag_top_k: int = 5  # 推荐 RAG 检索引用 chunk 数
    description: str = ""  # 简要说明

    @property
    def summary_text(self) -> str:
        """UI 显示的规格摘要，如 '上下文 128K · 输出上限 8K'。"""
        ctx_k = self.context_window // 1000 if self.context_window >= 1000 else self.context_window
        ctx_unit = "M" if ctx_k >= 1000 else "K"
        ctx_display = f"{ctx_k / 1000:.1f}M" if ctx_unit == "M" else f"{ctx_k}K"
        out_k = self.max_output_tokens // 1000 if self.max_output_tokens >= 1000 else self.max_output_tokens
        out_display = f"{out_k}K" if self.max_output_tokens >= 1000 else f"{self.max_output_tokens}"

        tags = [f"上下文 {ctx_display}", f"最大输出 {out_display}"]
        if self.is_reasoning_model:
            tags.append("🧠 深度思考模型")
        return " · ".join(tags)


# 精确型号知识库（持续维护主流厂商最新官方规格）
_KNOWN_MODEL_SPECS: dict[str, ModelSpec] = {
    # ===== DeepSeek 系列 =====
    "deepseek-chat": ModelSpec(
        model_id="deepseek-chat",
        display_name="DeepSeek-V3",
        context_window=65536,
        max_output_tokens=8192,
        is_reasoning_model=False,
        recommended_rag_top_k=8,
        description="DeepSeek-V3 超大规模通用模型，性价比极高",
    ),
    "deepseek-v3": ModelSpec(
        model_id="deepseek-v3",
        display_name="DeepSeek-V3",
        context_window=65536,
        max_output_tokens=8192,
        is_reasoning_model=False,
        recommended_rag_top_k=8,
        description="DeepSeek-V3 官方通用模型",
    ),
    "deepseek-reasoner": ModelSpec(
        model_id="deepseek-reasoner",
        display_name="DeepSeek-R1",
        context_window=65536,
        max_output_tokens=8192,
        is_reasoning_model=True,
        recommended_rag_top_k=6,
        description="DeepSeek-R1 深度推理模型，具备强悍的数学与代码反思能力",
    ),
    "deepseek-r1": ModelSpec(
        model_id="deepseek-r1",
        display_name="DeepSeek-R1",
        context_window=65536,
        max_output_tokens=8192,
        is_reasoning_model=True,
        recommended_rag_top_k=6,
        description="DeepSeek-R1 官方推理模型",
    ),

    # ===== 阿里通义千问 (Qwen) 系列 =====
    "qwen-turbo": ModelSpec(
        model_id="qwen-turbo",
        display_name="通义千问 Turbo",
        context_window=1000000,
        max_output_tokens=8192,
        is_reasoning_model=False,
        recommended_rag_top_k=15,
        description="Qwen 百万级长文本极速模型",
    ),
    "qwen-plus": ModelSpec(
        model_id="qwen-plus",
        display_name="通义千问 Plus",
        context_window=131072,
        max_output_tokens=8192,
        is_reasoning_model=False,
        recommended_rag_top_k=10,
        description="Qwen 综合能力增强模型",
    ),
    "qwen-max": ModelSpec(
        model_id="qwen-max",
        display_name="通义千问 Max",
        context_window=32768,
        max_output_tokens=8192,
        is_reasoning_model=False,
        recommended_rag_top_k=6,
        description="Qwen 旗舰超强能力模型",
    ),
    "qwen-long": ModelSpec(
        model_id="qwen-long",
        display_name="通义千问 Long",
        context_window=10000000,
        max_output_tokens=6000,
        is_reasoning_model=False,
        recommended_rag_top_k=20,
        description="Qwen 千万级超长文档解析模型",
    ),
    "qwen-2.5-72b-instruct": ModelSpec(
        model_id="qwen-2.5-72b-instruct",
        display_name="Qwen 2.5 72B",
        context_window=131072,
        max_output_tokens=8192,
        is_reasoning_model=False,
        recommended_rag_top_k=10,
        description="开源最强通用模型之一",
    ),
    "qwen-2.5-32b-instruct": ModelSpec(
        model_id="qwen-2.5-32b-instruct",
        display_name="Qwen 2.5 32B",
        context_window=131072,
        max_output_tokens=8192,
        is_reasoning_model=False,
        recommended_rag_top_k=10,
    ),
    "qwq-32b-preview": ModelSpec(
        model_id="qwq-32b-preview",
        display_name="QwQ 32B Preview",
        context_window=32768,
        max_output_tokens=8192,
        is_reasoning_model=True,
        recommended_rag_top_k=6,
        description="通义千问深度推理模型",
    ),

    # ===== OpenAI 系列 =====
    "gpt-4o": ModelSpec(
        model_id="gpt-4o",
        display_name="GPT-4o",
        context_window=128000,
        max_output_tokens=16384,
        is_reasoning_model=False,
        recommended_rag_top_k=10,
        description="OpenAI 旗舰全能多模态模型",
    ),
    "gpt-4o-mini": ModelSpec(
        model_id="gpt-4o-mini",
        display_name="GPT-4o mini",
        context_window=128000,
        max_output_tokens=16384,
        is_reasoning_model=False,
        recommended_rag_top_k=10,
        description="高性价比极速小模型",
    ),
    "o1": ModelSpec(
        model_id="o1",
        display_name="OpenAI o1",
        context_window=200000,
        max_output_tokens=100000,
        is_reasoning_model=True,
        recommended_rag_top_k=8,
        description="OpenAI 顶级推理模型",
    ),
    "o3-mini": ModelSpec(
        model_id="o3-mini",
        display_name="OpenAI o3-mini",
        context_window=200000,
        max_output_tokens=100000,
        is_reasoning_model=True,
        recommended_rag_top_k=8,
        description="OpenAI 最新高速深度推理模型",
    ),
    "gpt-4-turbo": ModelSpec(
        model_id="gpt-4-turbo",
        display_name="GPT-4 Turbo",
        context_window=128000,
        max_output_tokens=4096,
        is_reasoning_model=False,
        recommended_rag_top_k=8,
    ),
    "gpt-3.5-turbo": ModelSpec(
        model_id="gpt-3.5-turbo",
        display_name="GPT-3.5 Turbo",
        context_window=16385,
        max_output_tokens=4096,
        is_reasoning_model=False,
        recommended_rag_top_k=4,
    ),

    # ===== Anthropic Claude 系列 =====
    "claude-3-7-sonnet": ModelSpec(
        model_id="claude-3-7-sonnet",
        display_name="Claude 3.7 Sonnet",
        context_window=200000,
        max_output_tokens=64000,
        is_reasoning_model=True,
        recommended_rag_top_k=12,
        description="Anthropic 最新混合推理旗舰模型",
    ),
    "claude-3-5-sonnet": ModelSpec(
        model_id="claude-3-5-sonnet",
        display_name="Claude 3.5 Sonnet",
        context_window=200000,
        max_output_tokens=8192,
        is_reasoning_model=False,
        recommended_rag_top_k=12,
        description="最强代码与长文写作模型之一",
    ),
    "claude-3-5-haiku": ModelSpec(
        model_id="claude-3-5-haiku",
        display_name="Claude 3.5 Haiku",
        context_window=200000,
        max_output_tokens=8192,
        is_reasoning_model=False,
        recommended_rag_top_k=8,
    ),
    "claude-3-opus": ModelSpec(
        model_id="claude-3-opus",
        display_name="Claude 3 Opus",
        context_window=200000,
        max_output_tokens=4096,
        is_reasoning_model=False,
        recommended_rag_top_k=8,
    ),

    # ===== Google Gemini 系列 =====
    "gemini-2.0-flash": ModelSpec(
        model_id="gemini-2.0-flash",
        display_name="Gemini 2.0 Flash",
        context_window=1048576,
        max_output_tokens=8192,
        is_reasoning_model=False,
        recommended_rag_top_k=15,
        description="Google 新一代超快百万上下文模型",
    ),
    "gemini-1.5-pro": ModelSpec(
        model_id="gemini-1.5-pro",
        display_name="Gemini 1.5 Pro",
        context_window=2097152,
        max_output_tokens=8192,
        is_reasoning_model=False,
        recommended_rag_top_k=20,
        description="Google 200 万超大上下文旗舰模型",
    ),
    "gemini-1.5-flash": ModelSpec(
        model_id="gemini-1.5-flash",
        display_name="Gemini 1.5 Flash",
        context_window=1048576,
        max_output_tokens=8192,
        is_reasoning_model=False,
        recommended_rag_top_k=15,
    ),

    # ===== 智谱 GLM 系列 =====
    "glm-4-plus": ModelSpec(
        model_id="glm-4-plus",
        display_name="GLM-4 Plus",
        context_window=131072,
        max_output_tokens=4096,
        is_reasoning_model=False,
        recommended_rag_top_k=10,
    ),
    "glm-4-long": ModelSpec(
        model_id="glm-4-long",
        display_name="GLM-4 Long",
        context_window=1000000,
        max_output_tokens=4096,
        is_reasoning_model=False,
        recommended_rag_top_k=15,
    ),
    "glm-4-flash": ModelSpec(
        model_id="glm-4-flash",
        display_name="GLM-4 Flash",
        context_window=131072,
        max_output_tokens=4096,
        is_reasoning_model=False,
        recommended_rag_top_k=8,
    ),

    # ===== Moonshot (Kimi) 系列 =====
    "moonshot-v1-8k": ModelSpec(
        model_id="moonshot-v1-8k",
        display_name="Kimi 8K",
        context_window=8192,
        max_output_tokens=4096,
        is_reasoning_model=False,
        recommended_rag_top_k=3,
    ),
    "moonshot-v1-32k": ModelSpec(
        model_id="moonshot-v1-32k",
        display_name="Kimi 32K",
        context_window=32768,
        max_output_tokens=4096,
        is_reasoning_model=False,
        recommended_rag_top_k=6,
    ),
    "moonshot-v1-128k": ModelSpec(
        model_id="moonshot-v1-128k",
        display_name="Kimi 128K",
        context_window=131072,
        max_output_tokens=4096,
        is_reasoning_model=False,
        recommended_rag_top_k=10,
    ),
}


def get_model_spec(model_name: str | None, provider: str = "") -> ModelSpec:
    """获取指定大模型的规格元数据。

    匹配策略：
    1. 精确匹配：从已知模型库中寻找完全一致的 ID（不区分大小写与常见前缀）；
    2. 启发式匹配：解析型号中的后缀（如 -128k、-32k）与推理特征（r1、o1、reasoner、thinking）；
    3. 厂商默认兜底：根据 provider（openai/anthropic/gemini/ollama）退回安全默认值。
    """
    raw_name = (model_name or "").strip()
    if not raw_name:
        return _get_default_fallback_spec(provider, "default-model")

    clean_id = raw_name.lower()
    # 剥离前缀（如 'openai/gpt-4o' 或 'deepseek-ai/DeepSeek-V3'）
    if "/" in clean_id:
        clean_id = clean_id.split("/")[-1]

    # 1. 精确匹配
    if clean_id in _KNOWN_MODEL_SPECS:
        return _KNOWN_MODEL_SPECS[clean_id]

    # 模糊别名匹配（去除常见日期后缀，如 'claude-3-5-sonnet-20241022' -> 'claude-3-5-sonnet'）
    base_id = re.sub(r"-\d{8}$", "", clean_id)
    base_id = re.sub(r"-\d{4}$", "", base_id)
    if base_id in _KNOWN_MODEL_SPECS:
        return _KNOWN_MODEL_SPECS[base_id]

    # 2. 启发式特征提取
    is_reasoning = bool(
        re.search(r"(reasoner|r1|o1|o3|thinking|qwq|zero|cot|deepseek-r)", clean_id)
    )

    # 上下文窗口识别
    context_window = 65536  # 默认 64K
    if re.search(r"(10m|10000k)", clean_id):
        context_window = 10000000
    elif re.search(r"(2m|2000k)", clean_id):
        context_window = 2097152
    elif re.search(r"(1m|1000k)", clean_id):
        context_window = 1048576
    elif re.search(r"(512k)", clean_id):
        context_window = 524288
    elif re.search(r"(256k)", clean_id):
        context_window = 262144
    elif re.search(r"(128k)", clean_id):
        context_window = 131072
    elif re.search(r"(64k)", clean_id):
        context_window = 65536
    elif re.search(r"(32k)", clean_id):
        context_window = 32768
    elif re.search(r"(16k)", clean_id):
        context_window = 16384
    elif re.search(r"(8k)", clean_id):
        context_window = 8192
    elif re.search(r"(4k)", clean_id):
        context_window = 4096

    # 最大输出上限推导
    if is_reasoning:
        max_output = 32768 if "o1" in clean_id or "o3" in clean_id else 8192
    elif "claude-3-7" in clean_id:
        max_output = 64000
    elif "gpt-4o" in clean_id:
        max_output = 16384
    elif context_window <= 8192:
        max_output = 4096
    else:
        max_output = 8192

    # RAG 推荐引用数量
    if context_window >= 1000000:
        recommended_rag_top_k = 15
    elif context_window >= 128000:
        recommended_rag_top_k = 10
    elif context_window <= 8192:
        recommended_rag_top_k = 3
    else:
        recommended_rag_top_k = 6

    return ModelSpec(
        model_id=raw_name,
        display_name=raw_name,
        context_window=context_window,
        max_output_tokens=max_output,
        is_reasoning_model=is_reasoning,
        recommended_rag_top_k=recommended_rag_top_k,
        description=f"自动推断规格：{context_window // 1000}K 上下文",
    )


def _get_default_fallback_spec(provider: str, model_id: str) -> ModelSpec:
    """按 provider 退回安全基线规格。"""
    p = (provider or "").lower()
    if "gemini" in p:
        return ModelSpec(model_id=model_id, display_name=model_id, context_window=1048576, max_output_tokens=8192)
    if "anthropic" in p:
        return ModelSpec(model_id=model_id, display_name=model_id, context_window=200000, max_output_tokens=8192)
    if "ollama" in p:
        return ModelSpec(model_id=model_id, display_name=model_id, context_window=32768, max_output_tokens=4096)
    return ModelSpec(model_id=model_id, display_name=model_id, context_window=65536, max_output_tokens=8192)


def calculate_dynamic_rag_budget(
    spec: ModelSpec,
    history_tokens: int = 0,
    system_prompt_tokens: int = 1500,
) -> dict[str, int]:
    """根据模型真实规格，自适应计算当前请求的各项容量预算。

    计算公式：
    最大可用资料预算 = 总上下文 - 预留输出空间 - 系统提示词 - 历史多轮 - 安全缓冲区
    """
    safety_margin = 1000  # 安全缓冲 token
    reserved_output = min(spec.max_output_tokens, 8192)
    # 对于小上下文模型（<= 16K），收缩预留输出
    if spec.context_window <= 16384:
        reserved_output = min(spec.max_output_tokens, 3072)

    available_input_space = (
        spec.context_window
        - reserved_output
        - system_prompt_tokens
        - history_tokens
        - safety_margin
    )

    # 资料预算下限 1500 tokens，上限根据模型能力动态调节
    rag_token_budget = max(1500, available_input_space)

    # 针对超大上下文模型（如 128K/1M），将单次 RAG 资料注入限制在合理高密度区间（如 32000 tokens），
    # 避免引发 "Lost in the Middle"（注意力迷失）问题。
    rag_token_budget = min(rag_token_budget, 32000)

    return {
        "context_window": spec.context_window,
        "max_output_tokens": spec.max_output_tokens,
        "rag_token_budget": rag_token_budget,
        "recommended_top_k": spec.recommended_rag_top_k,
    }
