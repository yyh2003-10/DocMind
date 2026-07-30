import sqlite3
import sqlite_vec

# 模拟 VectorStore 的 connection 配置
conn = sqlite3.connect(
    'E:/DocMind/tests/test_doc2mind.db',
    check_same_thread=False,
    isolation_level=None,  # 手动事务
)
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")
conn.execute("PRAGMA foreign_keys=ON")
# 不加载 vec0 —— 模拟 _fts_conn

print('=== 不加载 vec0，中文 BM25 ===')
for q in ['"向量存储架构"', '"向量存储"', '"嵌入模型"']:
    try:
        cur = conn.execute(
            'SELECT chunk_id, bm25(bm25_index) AS score FROM bm25_index WHERE bm25_index MATCH ? LIMIT 5',
            [q],
        )
        rows = cur.fetchall()
        print(f'  {q}: rows={rows}')
    except Exception as e:
        print(f'  {q}: ERROR {type(e).__name__}: {e}')

conn.close()

print()
print('=== 加载 vec0 后，中文 BM25（对照）===')
conn2 = sqlite3.connect(
    'E:/DocMind/tests/test_doc2mind.db',
    check_same_thread=False,
    isolation_level=None,
)
conn2.execute("PRAGMA journal_mode=WAL")
conn2.enable_load_extension(True)
sqlite_vec.load(conn2)
for q in ['"向量存储架构"', '"向量存储"', '"嵌入模型"']:
    try:
        cur = conn2.execute(
            'SELECT chunk_id, bm25(bm25_index) AS score FROM bm25_index WHERE bm25_index MATCH ? LIMIT 5',
            [q],
        )
        rows = cur.fetchall()
        print(f'  {q}: rows={rows}')
    except Exception as e:
        print(f'  {q}: ERROR {type(e).__name__}: {e}')
conn2.close()
