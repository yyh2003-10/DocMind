"""Normalize model output before it reaches the chat UI or history store."""

from __future__ import annotations

import re


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

    def __init__(self) -> None:
        self._pending = ""
        self._hidden = False

    def feed(self, text: str | None) -> str:
        if not text:
            return ""
        self._pending += text
        visible: list[str] = []

        while self._pending:
            if self._hidden:
                close = self._find_close(self._pending)
                if close is None:
                    self._pending = self._pending[-self._MAX_TAG_LENGTH :]
                    break
                self._pending = self._pending[close.end() :]
                self._hidden = False
                continue

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
