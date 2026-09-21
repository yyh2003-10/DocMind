from doc2mind.core.llm.output import (
    AnswerGuard,
    OutputSanitizer,
    ThinkingMetaFilter,
    detect_degenerate_answer,
    sanitize_model_text,
)
from doc2mind.core.rag import _should_run_pitfall_advisor


def test_sanitize_model_text_removes_tagged_reasoning() -> None:
    text = "<think>internal plan</think>Final answer"
    assert sanitize_model_text(text) == "Final answer"


def test_stream_sanitizer_handles_tags_split_across_chunks() -> None:
    sanitizer = OutputSanitizer()
    visible = "".join(
        sanitizer.feed(chunk)
        for chunk in ["<thi", "nk>hidden", "</thi", "nking>Visible"]
    ) + sanitizer.flush()
    assert visible == "Visible"


def test_sanitize_model_text_preserves_normal_angle_brackets() -> None:
    assert sanitize_model_text("Use <value> as input") == "Use <value> as input"


def test_unclosed_reasoning_tag_does_not_swallow_the_whole_answer() -> None:
    """闭合标签形态不匹配时，隐藏态必须自行退出，不能把整篇正文吞掉。

    真实故障：模型输出变体闭合标签（如全角 <｜end▁of▁thinking｜>）时，
    隐藏态永不结束，前端气泡里只剩标题、代码块全没了。
    """
    sanitizer = OutputSanitizer()
    body = "\n".join(f"line {i}: 正文内容" for i in range(4000))
    visible = sanitizer.feed("<think>推理过程\n" + body) + sanitizer.flush()

    assert "line 3000: 正文内容" in visible
    assert len(visible) > 10000


def test_pitfall_advisor_is_not_run_for_every_query() -> None:
    assert not _should_run_pitfall_advisor("什么是雷达")
    assert _should_run_pitfall_advisor("启动时报错，如何排错")


class TestThinkingMetaFilter:
    def test_drops_format_instruction_sentences(self) -> None:
        f = ThinkingMetaFilter()
        text = (
            'We need to answer: "AS 与 AtomCode 关系?" '
            "Based on the knowledge graph relationships: AS related_to AtomCode. "
            "Provide answer in Chinese, concise, maybe a sentence. "
            "No extra formatting? The user didn't specify format, just ask. "
            "Provide answer."
        )
        out = "".join(f.feed(ch) for ch in text) + f.flush()
        assert "Provide answer" not in out
        assert "No extra formatting" not in out
        assert "We need to answer" not in out
        assert "Based on the knowledge graph" in out

    def test_streaming_tokens_reassemble_sentences(self) -> None:
        f = ThinkingMetaFilter()
        chunks = [
            "用户在问产品关系。AS ",
            "uses AtomCode。\n",
            "Provide answer in ",
            "Chinese, concise.\n",
            "结论：二者为调用关系。",
        ]
        out = "".join(f.feed(c) for c in chunks) + f.flush()
        assert "用户在问产品关系" in out
        assert "uses AtomCode" in out
        assert "Provide answer" not in out
        assert "结论：二者为调用关系" in out

    def test_drops_nemotron_gpt_screenshot_phrases(self) -> None:
        f = ThinkingMetaFilter()
        text = (
            "Not needed unless user asks for next steps. "
            "So answer directly, note lack of source. "
            "Provide brief description. "
            "No need for actions. "
            "根据通用知识，GPT 是大语言模型。"
        )
        out = "".join(f.feed(ch) for ch in text) + f.flush()
        assert "Provide brief" not in out
        assert "No need for actions" not in out
        assert "Not needed unless" not in out
        assert "So answer directly" not in out
        assert "GPT 是大语言模型" in out

    def test_flush_keeps_substantive_tail(self) -> None:
        f = ThinkingMetaFilter()
        # 句末标点会在 feed 阶段切出完整句；flush 只处理未收尾残句
        assert "AtomCode" in f.feed("根据图谱，AS 调用了 AtomCode。")
        assert f.flush() == ""
        f2 = ThinkingMetaFilter()
        assert f2.feed("根据图谱，AS 调用了 AtomCode") == ""
        assert "AtomCode" in f2.flush()

    def test_drops_nemotron_gpt_meta_cot(self) -> None:
        """复现 2026-09-12 GPT 问答截图：英文自我编排推理链不得外泄。"""
        f = ThinkingMetaFilter()
        leak = (
            "Provide detailed introduction of GPT.Use provided knowledge?"
            "The local knowledge slices are about DocMind, not about GPT."
            "We must answer based on general knowledge but must cite sources if we have."
            "According to rule 3: if no basis, must say knowledge base does not have, do not fabricate with citation."
            "We should say: 本地资料中未找到关于 GPT 的信息."
            "Probably acceptable.We must follow style: concise, no unnecessary length."
            "We should not output ACTIONS unless user asks for next steps."
            "Let's produce."
        )
        out = "".join(f.feed(ch) for ch in leak) + f.flush()
        assert "We must answer" not in out
        assert "According to rule" not in out
        assert "Let's produce" not in out
        assert "Use provided knowledge" not in out
        # 中文实质判断句应保留
        assert "本地资料中未找到关于 GPT" in out

    def test_drops_doubao_screenshot_meta_cot(self) -> None:
        """复现 2026-09-13 豆包问答截图：泛化编排句式不得外泄。"""
        f = ThinkingMetaFilter()
        leak = (
            "None of these are about Doubao AI. "
            "Provide concise answer. "
            "We need to be concise, simple answer, maybe one paragraph. "
            "We must not fabricate citations. "
            "So we can say: 豆包是字节跳动的大语言模型系列. "
            "We must not output JSON or tool calls. "
            "Proceed."
        )
        out = "".join(f.feed(ch) for ch in leak) + f.flush()
        assert "None of these" not in out
        assert "Provide concise" not in out
        assert "maybe one paragraph" not in out
        assert "must not fabricate" not in out
        assert "must not output JSON" not in out
        assert "Proceed" not in out
        # 中文实质判断句应保留
        assert "豆包是字节跳动" in out


# ── AnswerGuard：工具调用 JSON / 重复退化拦截 ─────────────────────────────

_REAL_TOOL_DUMP_SAMPLE = (
    '{\n  "query": "主动式立式动平衡机 夹爪 动平衡 要求",\n'
    '  "region": "cn-zh",\n  "max_results": 10,\n  "pages": 1\n}\n'
    '{\n  "query": "active vertical balancing machine chuck balancing requirements",\n'
    '  "region": "us-en",\n  "max_results": 10,\n  "pages": 1\n}\n'
    '{\n  "query": "夹爪 动平衡 校准 立式 平衡机",\n'
    '  "region": "cn-zh",\n  "max_results": 10,\n  "pages": 1\n}\n'
)


def _stream_guard(chunks: list[str]) -> tuple[str, AnswerGuard]:
    guard = AnswerGuard()
    out: list[str] = []
    for c in chunks:
        piece = guard.feed(c)
        if piece:
            out.append(piece)
        if guard.should_abort:
            return "".join(out), guard
    tail = guard.flush()
    if tail and not guard.should_abort:
        out.append(tail)
    return "".join(out), guard


class TestAnswerGuardToolCallDump:
    def test_real_world_dump_is_fully_suppressed(self) -> None:
        """复现 2026-09-12 手动实测故障：整段工具调用 JSON 不得外泄。"""
        chunks = [
            _REAL_TOOL_DUMP_SAMPLE[i : i + 80]
            for i in range(0, len(_REAL_TOOL_DUMP_SAMPLE), 80)
        ]
        visible, guard = _stream_guard(chunks)
        assert visible == ""
        assert guard.is_garbage
        assert guard.should_abort
        assert "tool_call" in guard.reason

    def test_probe_emits_normal_prose_after_buffer(self) -> None:
        """正常回答在探针攒满后完整放出，不丢字。"""
        body = "夹爪自身的动平衡要求通常参照 ISO 1940，等级多取 G6.3 或更严。" * 8
        chunks = [body[i : i + 50] for i in range(0, len(body), 50)]
        visible, guard = _stream_guard(chunks)
        assert not guard.is_garbage
        assert visible == body

    def test_short_normal_answer_flushed_intact(self) -> None:
        """短于探针窗口的正常回答在 flush 时原样放出。"""
        visible, guard = _stream_guard(["你好，有什么可以帮你？"])
        assert visible == "你好，有什么可以帮你？"
        assert not guard.is_garbage

    def test_prose_then_tool_dump_midstream(self) -> None:
        """开场是散文，随后陷入工具调用 JSON —— 靠滚动窗口拦住。

        中流检测有固有滞后（散文已放行），故不断言 0 泄漏，
        而是断言：判定垃圾、中止、且绝大部分 dump 被吞掉。
        """
        intro = "好的，我来帮你搜索一下关于夹爪动平衡的要求：\n\n"
        dump = _REAL_TOOL_DUMP_SAMPLE * 6
        body = intro + dump
        chunks = [body[i : i + 120] for i in range(0, len(body), 120)]
        visible, guard = _stream_guard(chunks)
        assert guard.is_garbage
        assert guard.should_abort
        assert "tool_call" in guard.reason
        # 绝大部分工具调用 JSON 不得外泄（允许开场散文 + 极少量早期泄漏）
        total_max_results = body.count('"max_results"')
        leaked = visible.count('"max_results"')
        assert leaked < total_max_results * 0.25, f"泄漏过多: {leaked}/{total_max_results}"

    def test_legitimate_json_config_is_not_flagged(self) -> None:
        """以 JSON 开场但无工具键的合法配置示例应放行。"""
        cfg = (
            '{\n  "name": "my-app",\n  "version": "1.0.0",\n'
            '  "port": 8080,\n  "debug": false\n}\n'
            "以上是推荐的配置结构，端口可按环境调整。\n"
            "更多说明：请确保配置文件位于项目根目录，并纳入版本管理。"
        )
        chunks = [cfg[i : i + 60] for i in range(0, len(cfg), 60)]
        visible, guard = _stream_guard(chunks)
        assert not guard.is_garbage
        assert "my-app" in visible

    def test_code_block_with_query_key_not_flagged_when_sparse(self) -> None:
        """正文中间偶发一个 query 字段（代码示例）不应误杀。"""
        body = (
            "# 如何构造检索请求\n\n"
            "下面给出一个请求示例：\n\n"
            '```json\n{"query": "chuck balance", "limit": 5}\n```\n\n'
            "注意 limit 不宜过大，否则延迟会明显上升。"
            "另外请根据实际业务调整超时与重试策略，避免雪崩。"
            "生产环境建议加上缓存层，命中率通常可达 30% 以上。"
        )
        chunks = [body[i : i + 80] for i in range(0, len(body), 80)]
        visible, guard = _stream_guard(chunks)
        assert not guard.is_garbage
        assert "chuck balance" in visible

    def test_function_key_in_code_examples_not_flagged(self) -> None:
        """技术问答里密集出现 "function" 键不得误杀（已从工具键白名单剔除）。"""
        snippets = []
        for i in range(12):
            snippets.append(
                f'示例 {i}：{{"function": "handler_{i}", "timeout": 30}}，'
                "该配置用于注册服务端处理函数。"
            )
        body = "# OpenAPI 回调配置\n\n" + "\n\n".join(snippets) + (
            "\n\n以上示例覆盖了常见回调场景，生产环境请按网关文档调整超时。"
        )
        chunks = [body[i : i + 80] for i in range(0, len(body), 80)]
        visible, guard = _stream_guard(chunks)
        assert not guard.is_garbage
        assert "handler_0" in visible

    def test_prose_releases_before_full_probe(self) -> None:
        """散文开场应在短确认后放行，不必等满 280 字探针（降低首 token 延迟）。"""
        body = "夹爪动平衡通常参照 ISO 1940 选取等级。"  # 明显短于 280
        guard = AnswerGuard()
        out = guard.feed(body)
        # 短于探针但在确认长度以上时，应在同一次 feed 放出
        assert out == body
        assert not guard.is_garbage

    def test_leading_whitespace_only_keeps_buffering(self) -> None:
        """纯空白前缀仍在缓冲，不提前放行。"""
        guard = AnswerGuard()
        assert guard.feed("   \n\n  ") == ""
        assert not guard.is_garbage


class TestAnswerGuardRepetition:
    def test_video_video_loop_is_aborted(self) -> None:
        """复现退化尾段：'video video video' 无限堆叠。"""
        prefix = "关于夹爪平衡的说明："
        loop = "线上培训视频教程课程" * 40
        body = prefix + loop
        chunks = [body[i : i + 60] for i in range(0, len(body), 60)]
        visible, guard = _stream_guard(chunks)
        assert guard.is_garbage
        assert guard.should_abort
        assert "repetition" in guard.reason

    def test_normal_technical_prose_not_flagged_as_repeat(self) -> None:
        body = (
            "伺服驱动器的开关电源需要关注母线电压、纹波与保持时间。"
            "再生电阻匹配不当会导致过压报警。编码器电池电压过低会丢失绝对位置。"
            "建议将 P0.063 纳入预测性维护看板，并按班次巡检 24V 电源质量。"
            "断电移动功能在 Z 轴重力负载场景必须开启，且前置条件缺一不可。"
        ) * 3
        chunks = [body[i : i + 70] for i in range(0, len(body), 70)]
        visible, guard = _stream_guard(chunks)
        assert not guard.is_garbage
        assert "伺服驱动器" in visible


class TestDetectDegenerateAnswer:
    def test_full_dump_detected(self) -> None:
        bad, reason = detect_degenerate_answer(_REAL_TOOL_DUMP_SAMPLE * 4)
        assert bad
        assert reason

    def test_normal_answer_passes(self) -> None:
        good = (
            "主动式立式动平衡机夹爪自身残余不平衡量通常按 ISO 1940 选取等级，"
            "精密场合多用 G2.5，一般场合 G6.3。校准时应先做夹爪空载平衡，"
            "再引入标准转子复核。[1]\n\n[ACTIONS: [\"👉 查看 ISO 1940 计算表\"]]"
        )
        bad, reason = detect_degenerate_answer(good)
        assert not bad
        assert reason == ""

    def test_empty_and_none_not_flagged(self) -> None:
        assert detect_degenerate_answer("") == (False, "")
        assert detect_degenerate_answer(None) == (False, "")
        assert detect_degenerate_answer("   \n  ") == (False, "")
