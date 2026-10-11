# PostgreSQL Standard schema v1

`deploy/postgres/schema.sql` 固化 Standard 后端的第一版数据契约。集合以
`(tenant_id, name)` 复合键隔离。`PostgresBatchWriter` 已实现文档包原子替换与
分批 chunks/embeddings 写入；连接池、完整 `KnowledgeStore` 读写契约、权限过滤
和 snapshot 导入仍待 adapter 实现。

关键约定：

- `tenant_id`、`collection` 和 `department_id` 是查询范围的一部分，不能只在应用层过滤。
- `document_version`、`embedding_model` 和 `index_generation` 记录内容与索引代次；重建索引不覆盖旧代次。
- `deleted_at` 使用软删除；活跃文档的 `(tenant_id, collection, source)` 由部分唯一索引保证唯一。
- `knowledge_chunks.id` 接受显式 ID，允许从 SQLite snapshot 保留 chunk 标识；默认值使用 sequence 生成新 ID。
- `search_vector` 是 PostgreSQL 全文检索派生列；向量存储单独放在 `knowledge_chunk_embeddings`，以便模型/维度切换。
- `vector` 列暂不固定维度。部署选择 embedding 模型后，adapter 负责校验 `embedding_dim` 并创建相应的 ANN 索引。
- `PostgresBatchWriter.replace_document(...)` 在一个事务内替换活跃同源文档、批量插入 chunks 与 embeddings；chunk ID 由 `RETURNING` 关联，embedding 不依赖返回行顺序。失败会整笔回滚。软删除 ID 和跨 tenant ID 冲突会被拒绝。

当前验证边界：本机没有 Docker/psql，schema 已做文本级约束检查，尚未连接真实 PostgreSQL 执行。
