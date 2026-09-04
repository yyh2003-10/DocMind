"""jieba 中文分词工具 — FTS5/BM25 中文底料。

在启用 `bm25_jieba_enabled` 时，将文档内容与查询做 jieba 分词并按空格拼接，
配合 FTS5 `unicode61` tokenizer（按空白切分）即可对中文多字词做精确 BM25。
未启用时 `segment`/`segment_query` 原样返回文本，保持原 trigram 路径字节级一致。

商用安全：jieba 为 MIT 许可。
"""

from __future__ import annotations

import logging
import re as _re

logger = logging.getLogger(__name__)

_segment_init_done = False


def _ensure_segmenter() -> None:
    """幂等初始化 jieba（首次加载/建码表可能较慢；模块级缓存，线程安全）。"""
    global _segment_init_done
    if _segment_init_done:
        return
    try:
        import jieba  # noqa: PLC0415

        jieba.initialize()
        _segment_init_done = True
    except Exception as e:  # noqa: BLE001
        logger.warning("jieba 初始化失败，BM25 中文分词降级为原样文本: %s", e)


def segment(text: str, enabled: bool) -> str:
    """按 jieba 分词并用空格拼接；disabled 时原样返回（保持 trigram 路径一致）。"""
    if not enabled:
        return text
    if not text:
        return ""
    _ensure_segmenter()
    try:
        import jieba  # noqa: PLC0415

        return " ".join(jieba.cut(text))
    except Exception as e:  # noqa: BLE001
        logger.warning("jieba 分词失败，按原样返回: %s", e)
        return text


def segment_query(query: str, enabled: bool) -> str:
    """分词查询；disabled 或分词结果为空时回退原样（含空格切分）。"""
    if not enabled:
        return query
    seg = segment(query, True)
    return seg if seg and seg.strip() else query


# 稀疏向量（D2）保留的最小 token 长度：过滤单字中文/单字母等噪声，
# 避免其在几乎所有 chunk 上都出现，让稀疏向量丧失判别力。
_SPARSE_MIN_TOKEN_LEN = 2


def token_counts(text: str, enabled: bool) -> dict[str, int]:
    """返回文本的 token→计数（稀疏向量词库底料，D2）。

    enabled=True 用 jieba 分词；否则按 CJK 连续段 / 字母数字粗切。
    token 统一过滤掉长度 < _SPARSE_MIN_TOKEN_LEN 的（单字/单字母噪声），
    保证确定性、可回归。
    """
    if not text:
        return {}
    if enabled:
        _ensure_segmenter()
        try:
            import jieba  # noqa: PLC0415

            toks = [t for t in jieba.lcut(text) if t.strip()]
        except Exception as e:  # noqa: BLE001
            logger.warning("jieba 分词失败，稀疏向量粗切回退: %s", e)
            toks = _coarse_tokens(text)
    else:
        toks = _coarse_tokens(text)
    counts: dict[str, int] = {}
    for t in toks:
        if len(t) < _SPARSE_MIN_TOKEN_LEN:
            continue
        counts[t] = counts.get(t, 0) + 1
    return counts


def _coarse_tokens(text: str) -> list[str]:
    """无 jieba 时的粗切：连续 CJK/ASCII 词，按空白与标点切分。"""
    return [t for t in _re.split(r"[^\w\u4e00-\u9fff]+", text) if t]