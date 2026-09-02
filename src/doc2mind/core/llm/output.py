"""Normalize model output before it reaches the chat UI or history store."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_OPEN_TAG = re.compile(r"<(?:think|thinking|reasoning|analysis|reflection)\s*>", re.I)
_CLOSE_TAG = re.compile(r"</(?:think|thinking|reasoning|analysis|reflection)\s*>", re.I)
_OPEN_SENTINEL = re.compile(r"<\|(?:thinking|analysis|reasoning)\|>", re.I)
_CLOSE_SENTINEL = re.compile(r"<\|end(?:_of_)?(?:thinking|analysis|reasoning)\|>", re.I)


def sanitize_model_text(text: str | None) -> str:
    """Remove explicit reasoning blocks while preserving normal answer text."""
    stream = OutputSanitizer()
    return stream.feed(text) + stream.flush()


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
