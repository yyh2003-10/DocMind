# PostgreSQL / pgvector 开发环境

这是 Standard 后端的本地基础设施脚手架；PostgreSQL adapter 已接入代码，但仍需真实 PostgreSQL/pgvector 验收。

## 启动

在安装 Docker Desktop 的机器上执行：

```text
docker compose -f deploy/postgres/docker-compose.yml up -d
docker compose -f deploy/postgres/docker-compose.yml ps
```

默认连接参数：

```text
host=127.0.0.1
port=55432
database=docmind
user=docmind
password=docmind-dev-only
```

可通过环境变量覆盖默认值：`DOCMIND_POSTGRES_PORT`、
`DOCMIND_POSTGRES_DB`、`DOCMIND_POSTGRES_USER`、
`DOCMIND_POSTGRES_PASSWORD`。

## 当前边界

- 镜像使用 PostgreSQL 16 与 pgvector；初始化脚本启用 `vector` 扩展并创建 Standard v1 schema。
- 数据卷名为 `docmind-postgres-data`，停止容器不会删除数据。
- PostgreSQL 写入仍由 `PostgresBatchWriter` 提供原子替换与分批写入；`PostgresKnowledgeStore` 已实现并由工厂按 `storage_backend="postgres"` 显式构造。缺少 DSN 或不兼容 deployment profile 时会明确报错，不会回退到 SQLite。
- SQLite snapshot 导入切片已实现为 `import_snapshot`：事务内追加 collections/documents/chunks/向量缓存，保留源 document/chunk ID，校验 manifest 计数、文档/集合/chunk 引用、chunk_count 和向量模型；目标模型不匹配时跳过向量并报告需要 reindex，失败或冲突整批回滚。导入显式 chunk ID 后会推进 `knowledge_chunk_id_seq`，避免后续默认 ID 碰撞。
- 调用方先用 `connect_postgres(dsn)` 建立 psycopg 3 连接，再调用 `PostgresBatchWriter(embedding_model=..., embedding_dim=..., tenant_id=...).replace_document(connection, document, chunks, embeddings)`；写入器校验向量维度/有限数值，chunk 与 embedding 数量必须一致，并根据活跃同源文档递增 `document_version`。
- PostgreSQL 驱动是可选依赖，安装方式为 `pip install -e ".[postgres]"`；Lite 默认安装不引入驱动。
- `PostgresKnowledgeStore` 的读写、向量/词法检索、tenant 过滤、软删除、健康检查与工厂选择已有 fake-connection 契约测试；真实 SQL/pgvector 行为仍需真实集成验收。当前机器没有 Docker CLI 或 PostgreSQL 服务，因此尚未运行 PostgreSQL 集成测试。
- 配好 `DOCMIND_TEST_POSTGRES_DSN` 指向专用测试库后，可运行 `python -m pytest tests/test_postgres_batch_writer_integration.py -q` 做真实写入/读取验收；该测试会创建后清理自己的唯一 tenant/document。
- 同一 DSN 还可运行 `python -m pytest tests/test_postgres_store_integration.py -q` 验收 `PostgresKnowledgeStore` 的文档、chunk、向量/词法检索和软删除闭环；测试会创建后清理自己的唯一 tenant/document。
- 配好 `DOCMIND_TEST_POSTGRES_DSN` 指向已执行 `deploy/postgres/schema.sql` 的专用测试库后，可运行 `python -m pytest tests/test_postgres_snapshot.py -q` 验收 snapshot 导入、ID 保留和序列推进；测试会创建后清理自己的唯一 tenant/document。
- `scripts/benchmark_postgres.py` 提供 Standard 压测入口；默认生成 100 万 chunk，使用 `DOCMIND_BENCH_POSTGRES_DSN` 或 `--dsn`，支持 `--mode vector|lexical|hybrid`、`--concurrency`、`--queries` 和 `--keep-data`。脚本默认清理自己的唯一 tenant，输出 JSON；未在本机运行，因为当前开发机没有 PostgreSQL/Docker。
- 本仓库当前开发机没有 Docker CLI，因此未在本机启动验收；在具备 Docker 的环境执行上面的健康检查后，再把路线图项目标记为完成。
