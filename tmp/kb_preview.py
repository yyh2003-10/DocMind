"""存量知识库只读预览（无 LLM）：集合概览 + 未打标文档 + 纯向量疑似重复扫描。

不写库（除 VectorStore.open 的幂等 schema 迁移加列外零写入）、不调 LLM。
"""
import sys

sys.path.insert(0, r"E:\DocMindY\src")

from doc2mind.core.config import get_settings
from doc2mind.core.embedder import get_embedder
from doc2mind.core.store.sqlite_vec import VectorStore

settings = get_settings()
print(f"db: {settings.db_path}")
embedder = get_embedder(settings)
store = VectorStore(settings.db_path, embedder.dimension)
store.open()

try:
    stats = store.get_stats()
    print("\n== 集合概览 ==")
    for name, (docs, chunks, size) in sorted(stats.collections.items()):
        print(f"  {name}: {docs} docs, {chunks} chunks, {size / 1024:.0f} KB")

    docs = store.list_documents(limit=10000)
    real = [d for d in docs if d.source != "__collection_placeholder__"]
    notes = [d for d in real if d.source.startswith("note:")]
    unenriched = [d for d in real if not d.enriched_at]
    print(f"\n== 文档现状 ==")
    print(f"  有效文档 {len(real)} 篇，其中经验笔记(note:) {len(notes)} 条，尚无 AI 标签/摘要 {len(unenriched)} 篇")

    print("\n== 疑似重复候选（向量相似分 >= 0.85，同集合内，未经 LLM 判定）==")
    seen = set()
    total_pairs = 0
    listed = 0
    for cname in sorted(stats.collections.keys()):
        cdocs = [
            d for d in store.list_documents(collection=cname, limit=10000)
            if d.source != "__collection_placeholder__" and d.chunk_count > 0
        ]
        by_source = {d.source: d for d in cdocs}
        for doc in cdocs:
            chunks = store.list_chunks_by_document(doc.id, limit=1)
            if not chunks or not chunks[0].content.strip():
                continue
            try:
                qv = embedder.embed_query(chunks[0].content[:1500])
            except Exception as e:  # noqa: BLE001
                print(f"  [embed 失败] {doc.source}: {e}")
                continue
            try:
                hits = store.vector_search(qv, top_k=3, collection=cname)
            except Exception as e:  # noqa: BLE001
                print(f"  [检索失败] {doc.source}: {e}")
                continue
            for cid, dist in hits:
                score = 1.0 / (1.0 + float(dist))
                if score < 0.85:
                    continue
                ch = store.get_chunks([cid])
                if not ch:
                    continue
                other = by_source.get(ch[0].source)
                if other is None or other.id == doc.id:
                    continue
                key = frozenset((doc.id, other.id))
                if key in seen:
                    continue
                seen.add(key)
                total_pairs += 1
                if listed < 25:
                    listed += 1
                    print(f"  [{cname}] {score:.3f}  {doc.source[:48]}  <->  {other.source[:48]}")
    print(f"  候选对总数: {total_pairs}")
finally:
    store.close()
