"""重排器（Reranker）— 检索后精排，提升 RAG 相关性。

为什么需要重排：
    首轮召回用 BM25 + 向量余弦 + RRF 融合，排序只看"排名位置"、不衡量
    query 与每片文档的真实语义相关度；BM25 字面匹配还会把"共享词汇但跑题"
    的切片顶上来。重排器（cross-encoder）对 (query, 文档) 逐对打分，能纠正
    召回阶段的排序偏差，是 RAG 相关性提升最大的单点改动。

降级契约：
    重排模型不可用（未安装 fastembed / 首次下载失败 / 推理异常）时，
    get_reranker() 返回 None 或 rerank() 抛 RerankerError，调用方（Retriever）
    保留原始 RRF 排序并标记 degraded，绝不阻断检索。
"""

from __future__ import annotations

from doc2mind.core.reranker.base import Reranker, RerankerError
from doc2mind.core.reranker.factory import get_reranker

__all__ = ["Reranker", "RerankerError", "get_reranker"]
