"""Normalize model output before it reaches the chat UI or history store."""

from __future__ import annotations

import logging
import re
from collections import Counter

logger = logging.getLogger(__name__)

_OPEN_TAG = re.compile(r"<(?:think|thinking|reasoning|analysis|reflection)\s*>", re.I)
_CLOSE_TAG = re.compile(r"</(?:think|thinking|reasoning|analysis|reflection)\s*>", re.I)
_OPEN_SENTINEL = re.compile(r"<\|(?:thinking|analysis|reasoning)\|>", re.I)
_CLOSE_SENTINEL = re.compile(r"<\|end(?:_of_)?(?:thinking|analysis|reasoning)\|>", re.I)


def sanitize_model_text(text: str | None) -> str:
    """Remove explicit reasoning blocks while preserving normal answer text."""
    stream = OutputSanitizer()
    return stream.feed(text) + stream.flush()


def detect_degenerate_answer(text: str | None) -> tuple[bool, str]:
    """一次性检测完整回答是否为工具调用 dump / 严重退化。

    返回 (是否垃圾, 原因)。非流式路径在落库前调用，垃圾回答直接拒绝。
    """
    if not text or not text.strip():
        return False, ""
    guard = AnswerGuard()
    chunk = 400
    for i in range(0, len(text), chunk):
        out = guard.feed(text[i : i + chunk])
        if guard.should_abort:
            break
        del out  # 仅关心判定结果，不关心可见切片
    if not guard.should_abort:
        guard.flush()
    return guard.is_garbage, guard.reason


class OutputSanitizer:
    """Incrementally remove tagged reasoning blocks from a token stream."""

    _MAX_TAG_LENGTH = 40
    # 隐藏态保护上限：推理块开标签出现后若迟迟等不到闭合标签，
    # 最多吞掉这么多字符就强制退出隐藏（见下方 hidden 分支说明）
    _MAX_HIDDEN_CHARS = 20000

    def __init__(self) -> None:
        self._pending = ""
        self._hidden = False
        self._hidden_chars = 0

    def feed(self, text: str | None) -> str:
        if not text:
            return ""
        self._pending += text
        visible: list[str] = []

        while self._pending:
            if self._hidden:
                close = self._find_close(self._pending)
                if close is not None:
                    self._pending = self._pending[close.end() :]
                    self._hidden = False
                    self._hidden_chars = 0
                    continue

                # 保护：闭合标签的形态比想象的杂（如全角变体 <｜end▁of▁thinking｜>、
                # 模型自己造的 </Reflection> 等）。一旦闭合标签匹配不上，隐藏态就
                # 永远不会结束，整篇正文都会被当成推理链吞掉，前端只剩几个字。
                # 吞掉的量超过上限时强制退出隐藏——宁可漏出推理文本，也不能丢正文。
                self._hidden_chars += max(0, len(self._pending) - self._MAX_TAG_LENGTH)
                if self._hidden_chars >= self._MAX_HIDDEN_CHARS:
                    logger.warning(
                        "推理块闭合标签缺失（已吞 %d 字符），强制退出隐藏态以免丢失正文",
                        self._hidden_chars,
                    )
                    self._hidden = False
                    self._hidden_chars = 0
                    continue

                self._pending = self._pending[-self._MAX_TAG_LENGTH :]
                break

            opening = self._find_open(self._pending)
            if opening is None:
                keep = min(len(self._pending), self._MAX_TAG_LENGTH)
                emit_len = len(self._pending) - keep
                if emit_len > 0:
                    visible.append(self._pending[:emit_len])
                    self._pending = self._pending[emit_len:]
                break

            if opening.start() > 0:
                visible.append(self._pending[: opening.start()])
            self._pending = self._pending[opening.end() :]
            self._hidden = True
            self._hidden_chars = 0

        return "".join(visible)

    def flush(self) -> str:
        if self._hidden:
            self._pending = ""
            return ""
        text = self._pending
        self._pending = ""
        return text

    @staticmethod
    def _find_open(text: str) -> re.Match[str] | None:
        matches = [m for m in (_OPEN_TAG.search(text), _OPEN_SENTINEL.search(text)) if m]
        return min(matches, key=lambda m: m.start()) if matches else None

    @staticmethod
    def _find_close(text: str) -> re.Match[str] | None:
        matches = [m for m in (_CLOSE_TAG.search(text), _CLOSE_SENTINEL.search(text)) if m]
        return min(matches, key=lambda m: m.start()) if matches else None


class ThinkingMetaFilter:
    """流式清洗主模型推理链中的格式 meta 指令，保留实质推理。

    增量 feed：按句缓冲，丢弃含 Provide answer / No extra formatting 等
    编排口吻的完整句；未闭合残句在 flush 时再判一次。

    真实故障（2026-09-12 GPT 问答截图）：nemotron-3-super 的思考链几乎
    全是「如何遵守 system prompt」的英文自我编排，例如
    We must answer based on general knowledge / According to rule 3 /
    Let's produce. / Probably acceptable. —— 对用户零价值，却整屏泄漏。
    """

    # 整句丢弃（推理里的自我格式/编排指令，不是给用户看的内容）
    _DROP_SENTENCE = re.compile(
        r"(?i)("
        # 原有：格式 meta
        r"provide\s+answer|no\s+extra\s+formatting|"
        r"the\s+user\s+didn['’]?t\s+specify|just\s+ask\b|"
        r"we\s+need\s+to\s+answer\s*:|"
        r"provide\s+detailed\s+introduction|"
        # 扩展：自我编排 / 规则遵从 / 收尾口令（nemotron 实测泄漏）
        r"we\s+must\s+(answer|state|follow|cite|clarify|say)|"
        r"we\s+should\s+(say|not|follow|output|add|note)|"
        r"according\s+to\s+rule|"
        r"do\s+not\s+fabricate|"
        r"use\s+provided\s+knowledge|"
        r"the\s+local\s+knowledge\s+slices|"
        r"probably\s+(acceptable|not\s+needed)|"
        r"let['’]?\s*s\s+produce|"
        r"thus\s+answer\s*:|"
        r"no\s+actions\s+line|"
        r"architecture\s+insights?\s+unless|"
        r"follow\s+style\s*:|"
        r"could\s+add\s+a\s+brief|"
        r"we\s+can\s+still\s+give\s+answer|"
        # 2026-09 GPT/豆包 截图实测泄漏句式
        r"not\s+needed\s+unless|"
        r"so\s+answer\s+directly|"
        r"provide\s+brief\s+description|"
        r"no\s+need\s+for\s+actions|"
        r"note\s+lack\s+of\s+source|"
        r"answer\s+based\s+on\s+general\s+knowledge|"
        r"provide\s+brief|"
        r"no\s+need\s+for\s+"
        r")"
    )
    # 泛化编排句式（2026-09-13 豆包问答截图）：不再逐句枚举，改为按
    # 「自我编排语义骨架」匹配——主语 we/I + 模态动词 + 元指令动词，
    # 或孤立的收尾口令。骨架命中的英文句几乎不可能是给用户的实质内容。
    _DROP_SKELETON = re.compile(
        r"(?i)("
        r"none\s+of\s+(these|them|the\s+\w+)\s+(are|is|seem\w*)\s+about|"
        r"provide\s+(a\s+)?(concise|brief|simple|short|one\s+paragraph)\s+|"
        r"we\s+(need|must|should|can|could|will)\s+(to\s+)?be\s+(concise|simple|brief|short)|"
        r"(must|should|need\s+to)\s+not\s+(fabricate|invent|hallucinate|output|mention|cite|make\s+up)|"
        r"we\s+must\s+not\b|"
        r"so\s+we\s+(can|could|will|must|should)\s+(say|state|conclude|answer|note)|"
        r"maybe\s+(one|a)\s+(paragraph|sentence|line)|"
        r"\bproceed\b[\s.!?]*$|"
        r"\bproceeding\b|"
        r"\bproceed\s+with\b"
        r")"
    )
    # 句末边界：西文句号/问号/叹号/换行，或中文句读
    # nemotron 实测大量 "GPT.Use" 无空格粘连，lookbehind 零宽空白仍可切开
    _SENTENCE_END = re.compile(r"(?<=[.!?。！？\n])\s*")
    _CJK_RE = re.compile(r"[一-鿿]")

    @classmethod
    def _is_droppable(cls, sentence: str) -> bool:
        """匹配 meta 模式且中文实质内容很少 → 整句丢弃。

        「We should say: 本地资料中未找到关于 GPT 的信息.」这类句子里
        中文是给用户看的实质判断，不能因为英文前缀整句吞掉。
        """
        if not (cls._DROP_SENTENCE.search(sentence) or cls._DROP_SKELETON.search(sentence)):
            return False
        return len(cls._CJK_RE.findall(sentence)) < 8

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, token: str | None) -> str:
        if not token:
            return ""
        self._buf += token
        parts = self._SENTENCE_END.split(self._buf)
        # 最后一段可能是未写完的句，留在缓冲
        if len(parts) == 1:
            return ""
        *complete, self._buf = parts
        kept = [p for p in complete if p and not self._is_droppable(p)]
        return "".join(kept)

    def flush(self) -> str:
        tail = self._buf
        self._buf = ""
        if not tail or self._is_droppable(tail):
            return ""
        return tail


# 工具调用 JSON 常见键（DocMind 自身无 function calling，模型幻觉出的 schema 也在此覆盖）
# 刻意排除 "function"/"parameters" 等过泛的键——技术问答里极常见，会误杀正常代码示例
_TOOL_CALL_KEY_RE = re.compile(
    r'"(?:query|region|max_results|pages|arguments|tool_calls|'
    r'tool_call_id|search_query|search_terms)"\s*:',
    re.I,
)
_FUNC_CALL_NAME_RE = re.compile(
    r'"(?:name|function|tool)"\s*:\s*"(?:web_?search|search|browse|fetch|'
    r'google_?search|tavily|bing|duckduckgo)"',
    re.I,
)


class AnswerGuard:
    """流式正文完整性守卫：拦截工具调用 JSON 幻觉与重复退化。

    真实故障（2026-09-12 手动实测）：nemotron-3-super 把系统提示误解成
    tool-calling，连续输出约 165 个 {"query","region","max_results","pages"}
    对象作为「回答」，无正文、无引用，并逐渐退化成 "video video video"
    堆叠。DocMind 的 OutputSanitizer 只剥 think 标签，完全拦不住这类输出。

    策略：
    1. 探针：JSON 开场且含工具键 → 整段抑制并判定垃圾；
       散文开场则短确认后立即放行（降低首 token 延迟）
    2. 流式跟踪滚动窗口，工具键密度过高 → 同上
    3. n-gram 重复率过高 → 退化，要求中止生成
    """

    # JSON 开场探针窗口：这么多字符内完成「是否工具调用 dump」判定
    _PROBE_CHARS = 280
    # 散文开场确认长度：见到这么多非空白散文就放行，不再等满探针
    # 真实中文短句约 15-25 字即可确认不是 JSON；再长只会拖高首 token 延迟
    _PROSE_CONFIRM_CHARS = 16
    # 探针内至少出现这么多个工具键，才认定为工具调用 dump
    _PROBE_KEY_MIN = 2
    # 滚动窗口内工具键密度阈值（键数 / 千字符）。真实 dump ≈8 键/千字符；
    # 正常技术问答偶发 1-2 个同名键，3.0 给足余量又拦得住 dump
    _MARKER_DENSITY = 3.0
    _MARKER_ABS_MIN = 6      # 绝对次数下限；dump 单对象约 4 键，2 个对象即触发
    # 重复检测窗口与阈值
    _REPEAT_WINDOW = 360
    _REPEAT_GRAM = 12
    _REPEAT_MIN_COUNT = 8

    def __init__(self) -> None:
        self._probe = ""
        self._probe_done = False
        self._suppress = False
        self._garbage = False
        self._abort = False
        self._reason = ""
        # 滚动近窗：同时服务工具键密度与 n-gram 重复检测
        self._tail = ""
        self._marker_hits = 0
        self._total_chars = 0
        self.suppressed_chars = 0

    @property
    def is_garbage(self) -> bool:
        """正文已被判定为工具调用 dump 或严重退化，不应展示/落库。"""
        return self._garbage

    @property
    def should_abort(self) -> bool:
        """调用方应立即中断生成（继续烧 token 毫无意义）。"""
        return self._abort

    @property
    def reason(self) -> str:
        return self._reason

    def feed(self, text: str | None) -> str:
        """喂入一段可见正文，返回应当展示的文本（'' = 抑制该段）。"""
        if not text:
            return ""
        self._total_chars += len(text)

        if not self._probe_done:
            self._probe += text
            pending = self._probe
            stripped = pending.lstrip()
            if not stripped:
                # 还在空白前缀里
                if len(pending) < self._PROBE_CHARS:
                    return ""
            elif stripped[0] in "{[":
                # JSON 开场：等满探针再判，避免把工具 dump 放出去
                if len(pending) < self._PROBE_CHARS:
                    return ""
            else:
                # 散文开场：攒够确认字即放行，不拖到 280 字
                if len(stripped) < self._PROSE_CONFIRM_CHARS and len(pending) < self._PROBE_CHARS:
                    return ""
            self._probe = ""
            self._probe_done = True
            if self._judge_probe(pending):
                self._enter_garbage("tool_call_json")
                return ""
            return self._after_pass(pending)

        if self._suppress:
            self.suppressed_chars += len(text)
            self._observe(text)
            return ""

        return self._after_pass(text)

    def flush(self) -> str:
        """流结束：若探针未攒满则补判一次。"""
        if self._probe_done:
            return ""
        pending = self._probe
        self._probe = ""
        self._probe_done = True
        if self._judge_probe(pending):
            self._enter_garbage("tool_call_json")
            return ""
        return self._after_pass(pending)

    # ── 内部 ──────────────────────────────────────────────────────────────

    def _after_pass(self, text: str) -> str:
        """正常放行路径：继续跟踪工具键与重复，必要时升级为垃圾。"""
        self._observe(text)
        if self._suppress:
            self.suppressed_chars += len(text)
            return ""
        return text

    def _enter_garbage(self, reason: str) -> None:
        if not self._garbage:
            logger.warning(
                "正文完整性守卫触发：%s（已收 %d 字符）", reason, self._total_chars
            )
        self._garbage = True
        self._abort = True
        self._suppress = True
        self._reason = reason

    def _judge_probe(self, buf: str) -> bool:
        """探针判定：以 JSON 开场且含工具键 → 工具调用 dump。"""
        stripped = buf.lstrip()
        if not stripped:
            return False
        if stripped[0] not in "{[":
            # 正常散文开场；「散文引言 + 立刻进 JSON」交给滚动窗口跟踪
            return False
        keys = _TOOL_CALL_KEY_RE.findall(stripped)
        if len(keys) >= self._PROBE_KEY_MIN or _FUNC_CALL_NAME_RE.search(stripped):
            return True
        # 纯 JSON 但无工具键：可能是合法 JSON 回答（配置示例），放行
        return False

    def _observe(self, text: str) -> None:
        """更新滚动窗口并做密度/重复检测。抑制态与放行态共用。"""
        self._tail = (self._tail + text)[-self._REPEAT_WINDOW * 4 :]
        hits = len(_TOOL_CALL_KEY_RE.findall(text))
        if hits:
            self._marker_hits += hits
            window_keys = len(_TOOL_CALL_KEY_RE.findall(self._tail))
            window_len = max(len(self._tail), 1)
            density = window_keys * 1000.0 / window_len
            if window_keys >= self._MARKER_ABS_MIN and density >= self._MARKER_DENSITY:
                self._enter_garbage("tool_call_json_midstream")
                return

        if len(self._tail) < self._REPEAT_WINDOW:
            return
        window = self._tail[-self._REPEAT_WINDOW :]
        g = self._REPEAT_GRAM
        step = max(g // 2, 1)
        grams = [window[i : i + g] for i in range(0, len(window) - g + 1, step)]
        if not grams:
            return
        most_common, count = Counter(grams).most_common(1)[0]
        # 重复 gram 需有一定长度，避免正常短词误判
        if count >= self._REPEAT_MIN_COUNT and len(most_common.strip()) >= 6:
            self._enter_garbage(f"repetition:x{count}")
