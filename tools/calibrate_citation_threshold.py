"""引用门控阈值校准脚本（T3 / ISSUE-03）。

对固定问题集在 citation_min_score ∈ {0.30,0.45,0.55} 下跑 partition_citation_hits，
输出 CSV 对比表。默认只读真实库（不写库）。库不存在时使用内置 mock 命中集。

用法：
  $env:PYTHONPATH="src"; python tools/calibrate_citation_threshold.py --collection doc2mind
  python tools/calibrate_citation_threshold.py --dry-run --out docs/verification/xxx.csv
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from doc2mind.core.rag import partition_citation_hits  # noqa: E402


@dataclass
class Hit:
    content: str
    source: str
    rerank_score: float
    vector_score: float = 0.0
    bm25_score: float = 0.0
    score: float = 0.0


# 固定问题集：库内 / 库外 / 偏题边缘
QUESTIONS = [
    ("什么是挠度", "in_kb"),
    ("DocMind 引用门控默认阈值是多少", "in_kb"),
    ("知识库 curate 的风险分级是什么", "in_kb"),
    ("多轮对话失败轮为什么要写历史", "in_kb"),
    ("什么是豆包大模型", "out_of_kb"),
    ("nemotron 3 性能如何", "out_of_kb"),
    ("北京今天天气怎么样", "out_of_kb"),
    ("导入 PDF 时 OCR 失败怎么办", "edge"),
    ("点击新建对话导入文档", "edge"),
    ("BM25 短词检索有什么坑", "in_kb"),
]

# mock 命中（无真实库时）：按问题粗粒度构造
MOCK_HITS = {
    "什么是挠度": [
        Hit("挠度是指构件在荷载下的竖向位移。", "mech.md", 0.88),
        Hit("DocMind 快速上手：点击新建对话。", "guide.md", 0.93),
        Hit("菜单与快捷键说明。", "ui.md", 0.12),
    ],
    "DocMind 引用门控默认阈值是多少": [
        Hit("citation_min_score 默认 0.45，背景线 0.6 比例。", "config.md", 0.91),
        Hit("无关天气资讯。", "news.md", 0.08),
    ],
    "知识库 curate 的风险分级是什么": [
        Hit("enrich/categorize/extract 可静默；dedup/consolidate 必须 dry_run。", "curate.md", 0.9),
        Hit("随机菜谱。", "food.md", 0.05),
    ],
    "多轮对话失败轮为什么要写历史": [
        Hit("失败轮写入用户问题，避免下一句失忆。", "chat.md", 0.86),
        Hit("旅游攻略。", "travel.md", 0.07),
    ],
    "什么是豆包大模型": [
        Hit("DocMind 操作指南与快捷键。", "guide.md", 0.92),
        Hit("导入步骤说明。", "import.md", 0.4),
    ],
    "nemotron 3 性能如何": [
        Hit("本地知识库设置说明。", "settings.md", 0.85),
        Hit("OCR 参数说明。", "ocr.md", 0.2),
    ],
    "北京今天天气怎么样": [
        Hit("向量检索原理简介。", "vector.md", 0.3),
        Hit("主题无关列表。", "list.md", 0.05),
    ],
    "导入 PDF 时 OCR 失败怎么办": [
        Hit("OCR 失败可检查语言包与 dpi 配置。", "ocr-faq.md", 0.87),
        Hit("无关广告页。", "ad.md", 0.04),
    ],
    "点击新建对话导入文档": [
        Hit("点击新建对话导入文档，选择目录后摄入。", "guide.md", 0.95),
        Hit("挠度定义。", "mech.md", 0.55),
    ],
    "BM25 短词检索有什么坑": [
        Hit("BM25 短词可用 jieba 分词改善召回。", "bm25.md", 0.89),
        Hit("无关图表。", "chart.md", 0.06),
    ],
}


def load_real_hits(query: str, collection: str, top_k: int = 8) -> list[Hit]:
    """尝试从真实库检索；失败/空库回退 mock。"""
    db = Path(os.environ.get("DOC2MIND_DB_PATH") or (Path.home() / "AppData/Local/doc2mind/doc2mind.db"))
    if not db.exists():
        return list(MOCK_HITS.get(query, []))
    try:
        from doc2mind.core.config import get_settings
        from doc2mind.core.retriever import Retriever
        from doc2mind.core.store.sqlite_vec import VectorStore
        from doc2mind.core.embedder.factory import get_embedder

        s = get_settings()
        store = VectorStore(s.db_path)
        embedder = get_embedder(s)
        retriever = Retriever(store, embedder)
        hits, rstats = retriever.search(query=query, collection=collection, top_k=top_k, min_score=0.0)
        out = [
            Hit(
                content=h.chunk.content[:200],
                source=h.chunk.source,
                rerank_score=float(h.rerank_score) if h.rerank_score is not None else -1,
                vector_score=h.vector_score,
                bm25_score=h.bm25_score,
                score=h.score,
            )
            for h in hits
        ]
        store.close()
        return out or list(MOCK_HITS.get(query, []))
    except Exception:
        return list(MOCK_HITS.get(query, []))


def _as_partition_hits(hits: list[Hit]):
    class _C:
        def __init__(self, h: Hit):
            self.content = h.content
            self.heading = None
            self.source = h.source

    class _H:
        def __init__(self, h: Hit):
            self.chunk = _C(h)
            self.rerank_score = None if h.rerank_score < 0 else h.rerank_score
            self.vector_score = h.vector_score
            self.bm25_score = h.bm25_score
            self.score = h.score
            self.source = h.source

    return [_H(h) for h in hits]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", default="doc2mind")
    ap.add_argument("--thresholds", default="0.30,0.45,0.55")
    ap.add_argument("--out", default="")
    ap.add_argument("--dry-run", action="store_true", help="只读，不写库（本脚本本身只读）")
    args = ap.parse_args()
    thresholds = [float(x) for x in args.thresholds.split(",") if x.strip()]
    rows = []
    for q, kind in QUESTIONS:
        raw = load_real_hits(q, args.collection)
        hits = _as_partition_hits(raw)
        for thr in thresholds:
            part = partition_citation_hits(
                q, hits, citation_min_score=thr, top_k=5, reranked_usable=True
            )
            cite_src = "、".join(h.chunk.source for h in part.cite_hits[:4])
            rows.append({
                "query": q,
                "kind": kind,
                "citation_min_score": thr,
                "hit_count": part.gate["hit_count"],
                "cite_count": part.gate["cite_count"],
                "bg_count": part.gate["bg_count"],
                "discarded_count": part.gate["discarded_count"],
                "dropped_by_score": part.gate["dropped_by_score"],
                "dropped_by_topic": part.gate["dropped_by_topic"],
                "cross_check": part.gate["cross_check"],
                "cite_sources": cite_src,
                "topic_demoted": part.gate["topic_demoted_count"],
            })

    date = dt.date.today().isoformat()
    out = Path(args.out) if args.out else ROOT / "docs/verification" / f"citation_gate_calibration_{date}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {out} rows={len(rows)}")
    # 简表
    for thr in thresholds:
        subset = [r for r in rows if r["citation_min_score"] == thr]
        out_kb = [r for r in subset if r["kind"] == "out_of_kb"]
        in_kb = [r for r in subset if r["kind"] == "in_kb"]
        print(
            f"thr={thr:.2f}  in_kb.cite_avg={sum(r['cite_count'] for r in in_kb)/max(1,len(in_kb)):.2f}"
            f"  out.cite_avg={sum(r['cite_count'] for r in out_kb)/max(1,len(out_kb)):.2f}"
            f"  cross_ok={all(r['cross_check'] for r in subset)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
