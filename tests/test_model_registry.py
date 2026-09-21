"""模型规格智能注册表与动态容量规划单元测试。"""

from __future__ import annotations

from doc2mind.core.llm.model_registry import get_model_spec


class TestModelSpecRegistry:
    def test_exact_matches(self) -> None:
        """测试主流知名型号精准匹配。"""
        # DeepSeek
        spec_ds_chat = get_model_spec("deepseek-chat")
        assert spec_ds_chat.context_window == 65536
        assert spec_ds_chat.max_output_tokens == 8192
        assert spec_ds_chat.is_reasoning_model is False

        spec_ds_r1 = get_model_spec("deepseek-reasoner")
        assert spec_ds_r1.context_window == 65536
        assert spec_ds_r1.max_output_tokens == 8192
        assert spec_ds_r1.is_reasoning_model is True

        # OpenAI
        spec_gpt4o = get_model_spec("gpt-4o")
        assert spec_gpt4o.context_window == 128000
        assert spec_gpt4o.max_output_tokens == 16384

        spec_o1 = get_model_spec("o1")
        assert spec_o1.context_window == 200000
        assert spec_o1.max_output_tokens == 100000
        assert spec_o1.is_reasoning_model is True

        # Qwen
        spec_qwen_turbo = get_model_spec("qwen-turbo")
        assert spec_qwen_turbo.context_window == 1000000
        assert spec_qwen_turbo.max_output_tokens == 8192

        # Anthropic
        spec_claude37 = get_model_spec("claude-3-7-sonnet")
        assert spec_claude37.context_window == 200000
        assert spec_claude37.max_output_tokens == 64000
        assert spec_claude37.is_reasoning_model is True

        # Gemini
        spec_gemini2 = get_model_spec("gemini-2.0-flash")
        assert spec_gemini2.context_window == 1048576

    def test_heuristic_pattern_extraction(self) -> None:
        """测试未知新模型的启发式特征提取。"""
        # 提取 128K 上下文
        spec_custom_128k = get_model_spec("my-custom-model-128k")
        assert spec_custom_128k.context_window == 131072
        assert spec_custom_128k.is_reasoning_model is False

        # 提取 8K 小上下文
        spec_small_8k = get_model_spec("internlm-8k")
        assert spec_small_8k.context_window == 8192
        assert spec_small_8k.max_output_tokens == 4096

        # 提取推理模型关键词
        spec_r1_custom = get_model_spec("deepseek-r1-distill-qwen-14b")
        assert spec_r1_custom.is_reasoning_model is True

        spec_thinking_custom = get_model_spec("qwen-2.5-thinking-exp")
        assert spec_thinking_custom.is_reasoning_model is True

    def test_fallback_specs(self) -> None:
        """测试空模型名或完全未知模型的安全基线。"""
        spec_empty = get_model_spec("", provider="openai")
        assert spec_empty.context_window >= 32768
        assert spec_empty.max_output_tokens >= 4096

        spec_gemini_fallback = get_model_spec("unknown-gemini-model", provider="gemini")
        assert spec_gemini_fallback.context_window >= 65536

    def test_summary_text(self) -> None:
        """测试规格摘要字符串生成。"""
        spec = get_model_spec("deepseek-reasoner")
        summary = spec.summary_text
        assert "上下文" in summary
        assert "最大输出" in summary
        assert "深度思考模型" in summary
