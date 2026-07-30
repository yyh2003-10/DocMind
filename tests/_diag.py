import sqlite3
import sqlite_vec

conn = sqlite3.connect('E:/DocMind/tests/test_doc2mind.db')
conn.enable_load_extension(True)
sqlite_vec.load(conn)

print('=== BM25 中文（vec0 已加载）===')
try:
    cur = conn.execute(
        'SELECT chunk_id, bm25(bm25_index) AS score FROM bm25_index WHERE bm25_index MATCH ? LIMIT 3',
        ['"向量存储"'],
    )
    print(f'rows: {cur.fetchall()}')
except Exception as e:
    print(f'ERROR: {type(e).__name__}: {e}')

print()
print('=== chunks_meta schema ===')
for r in conn.execute("SELECT sql FROM sqlite_master WHERE name='chunks_meta'"):
    print(r[0])

print()
print('=== sample chunks_meta.id types ===')
for r in conn.execute('SELECT id, typeof(id) FROM chunks_meta LIMIT 3'):
    print(r)

print()
print('=== sample bm25_index.chunk_id types ===')
for r in conn.execute('SELECT chunk_id, typeof(chunk_id) FROM bm25_index LIMIT 3'):
    print(r)

print()
print('=== BM25 ASCII（对照）===')
try:
    cur = conn.execute(
        'SELECT chunk_id, bm25(bm25_index) AS score FROM bm25_index WHERE bm25_index MATCH ? LIMIT 3',
        ['"system"'],
    )
    print(f'rows: {cur.fetchall()}')
except Exception as e:
    print(f'ERROR: {type(e).__name__}: {e}')

conn.close()
