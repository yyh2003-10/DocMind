import sqlite3
import sqlite_vec

conn = sqlite3.connect('E:/DocMind/tests/test_doc2mind.db')
conn.enable_load_extension(True)
sqlite_vec.load(conn)

print('=== chunks_meta content ===')
for r in conn.execute('SELECT id, content FROM chunks_meta ORDER BY id'):
    print(f'chunk {r[0]}: {r[1]!r}')
print()

print('=== bm25_index content ===')
for r in conn.execute('SELECT chunk_id, content FROM bm25_index LIMIT 3'):
    print(f'chunk_id={r[0]} content={r[1]!r}')
print()

# 中文 3-gram 直接探查
print('=== 中文 3-gram 直接探查 ===')
for q in ['"入嵌入模"', '"嵌入模型"', '"量向量存"', '"向量存储"']:
    try:
        cur = conn.execute(
            'SELECT chunk_id, bm25(bm25_index) AS score FROM bm25_index WHERE bm25_index MATCH ? LIMIT 5',
            [q],
        )
        rows = cur.fetchall()
        print(f'  {q}: rows={rows}')
    except Exception as e:
        print(f'  {q}: ERROR {type(e).__name__}: {e}')

# 试 2-gram 中文
print()
print('=== 中文 2-gram ===')
for q in ['"向量"', '"存储"', '"嵌入"']:
    try:
        cur = conn.execute(
            'SELECT chunk_id, bm25(bm25_index) AS score FROM bm25_index WHERE bm25_index MATCH ? LIMIT 5',
            [q],
        )
        rows = cur.fetchall()
        print(f'  {q}: rows={rows}')
    except Exception as e:
        print(f'  {q}: ERROR {type(e).__name__}: {e}')

# 单字符
print()
print('=== 单中文字符 ===')
for q in ['"量"', '"储"']:
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
