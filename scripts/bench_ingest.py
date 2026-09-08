"""DocMind 摄入性能基准：串行 vs 并行（ingest_workers）× 首导 vs 重复导入。

用法（项目根目录）：
    python scripts/bench_ingest.py

输出四组耗时：
    seq-first  串行首导（ingest_workers=1，优化前基线）
    seq-reskip 同一库重复导入（验证去重前移：跳过解析/OCR/嵌入）
    par-first  并行首导（ingest_workers=4，两段式流水线）
    par-reskip 并行重复导入

注：真实嵌入模型首次加载约 1-3s，脚本先预热再计时；两组并行/串行
都用同一批文件、独立数据库，结果可直接对比。
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def _make_docs(count: int = 12, paragraphs: int = 30) -> Path:
    d = Path(tempfile.mkdtemp(prefix="docmind-bench-"))
    for i in range(count):
        lines = [f"# 文档 {i}"]
        for p in range(paragraphs):
            lines.append(
                f"第 {p} 段：DocMind 摄入性能基准内容，包含若干关键词与示例句子，"
                "用于真实分块与向量化，确保每个文件产出多个分块。"
            )
            lines.append("")
        (d / f"doc{i}.md").write_text("\n".join(lines), encoding="utf-8")
    return d


def _warm_embedder(settings) -> None:
    from doc2mind.core.embedder import get_embedder

    emb = get_embedder(settings)
    _ = list(emb.embed_texts(["预热"]))


def _run(settings, docs: Path, label: str) -> tuple[int, int, int]:
    from doc2mind.core.pipeline import ingest_path

    t0 = time.perf_counter()
    summary = ingest_path(path=docs, settings=settings, collection="bench")
    el = int((time.perf_counter() - t0) * 1000)
    print(f"{label:12s} {el:6d} ms  ingested={summary.total_documents} "
          f"chunks={summary.total_chunks} skipped={summary.skipped} failed={summary.failed}")
    return el, summary.total_chunks, summary.total_documents


def main() -> None:
    from doc2mind.core.config import Settings

    docs = _make_docs()
    print(f"样本: {docs}  共 {len(list(docs.iterdir()))} 个 markdown 文件\n")

    seq_settings = Settings(db_path=docs.parent / "seq.db", ingest_workers=1)
    _warm_embedder(seq_settings)
    seq_first, chunks1, docs1 = _run(seq_settings, docs, "seq-first")
    _run(seq_settings, docs, "seq-reskip")

    par_settings = Settings(db_path=docs.parent / "par.db", ingest_workers=4)
    _warm_embedder(par_settings)
    par_first, chunks2, docs2 = _run(par_settings, docs, "par-first")
    _run(par_settings, docs, "par-reskip")

    print("\n结论（首导提速）：")
    if seq_first > 0 and par_first > 0:
        ratio = seq_first / par_first
        print(f"  ingest_workers=4 相对串行: {ratio:.2f}x "
              f"({'提速' if ratio >= 1 else '反而变慢'})")
    print(f"  重复导入（去重前移）: seq {seq_first}ms → 跳过解析直返")
    assert docs1 == docs2 == 12
    assert chunks1 == chunks2, "并行与串行分块数应一致"


if __name__ == "__main__":
    main()
