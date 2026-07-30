import sqlite3
import sqlite_vec

conn = sqlite3.connect('E:/DocMind/tests/test_doc2mind.db')
conn.enable_load_extension(True)
sqlite_vec.load(conn)

# 整段中文 query
print('=== 整段中文 4 chars ===')
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

# 拆开单个空格分隔
print()
print('=== 整段无引号 ===')
for q in ['向量存储架构', '向量存储', '嵌入模型']:
    try:
        cur = conn.execute(
            'SELECT chunk_id, bm25(bm25_index) AS score FROM bm25_index WHERE bm25_index MATCH ? LIMIT 5',
            [q],
        )
        rows = cur.fetchall()
        print(f'  {q!r}: rows={rows}')
    except Exception as e:
        print(f'  {q!r}: ERROR {type(e).__name__}: {e}')

# 检查 sqlite3 版本与 FTS5 trigram 支持
print()
print('=== sqlite version ===')
print(conn.execute('SELECT sqlite_version()').fetchone()[0])

conn.close()
