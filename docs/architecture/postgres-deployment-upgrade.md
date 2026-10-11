# PostgreSQL Standard 部署与升级流程

本文定义 Standard 后端的可重复部署、升级、回滚和验收边界。它是运行手册，不代表当前仓库已经完成真实 PostgreSQL 验收。

## 首次部署

1. 准备 Docker、持久化卷和专用数据库密码；生产环境不要使用 compose 文件中的开发密码。
2. 启动数据库并等待健康检查：

   ```text
   docker compose -f deploy/postgres/docker-compose.yml up -d
   docker compose -f deploy/postgres/docker-compose.yml ps
   ```

3. 使用 `deploy/postgres/schema.sql` 初始化 Standard v1 schema。
4. 先运行只读检查，再导入数据：

   ```text
   python -m doc2mind.cli import --backend postgres \
     --tenant-id <tenant> --dsn <dsn> <snapshot.zip>
   ```

   模型不一致时导入器会跳过向量缓存并报告 `needs_reindex=true`；完成导入后必须使用目标 embedding 模型重建索引。

## 升级门禁

升级前必须保留旧库只读副本和 snapshot，并记录 `knowledge_schema_meta.schema_version`、embedding 模型与 index generation。

1. 在维护窗口执行数据库备份并验证备份文件可读。
2. 在预生产库应用幂等迁移；迁移脚本只允许增量添加表、列或索引，不直接删除数据。
3. 运行 Python 全量回归、PostgreSQL 集成回归和 `scripts/benchmark_postgres.py` 的小规模基线。
4. 新建 index generation，完成后再切换读取 generation；旧 generation 保留到观察窗口结束。
5. 观察错误率、查询 p95、导入失败率和数据库锁等待。

## 回滚

- 应用回滚：切回上一版本应用和上一 index generation，不删除新 generation 数据。
- 数据回滚：从升级前备份恢复到隔离实例，验证后再切换连接；不要在生产库上直接执行不可逆 DELETE。
- 迁移失败：保持旧应用可读，记录失败迁移和事务日志，修复后重新执行幂等迁移。

## 责任边界

- compose、schema 和本手册提供可重复的部署骨架。
- PostgreSQL `KnowledgeStore` adapter 现已实现代码与 fake-connection 契约测试；真实 PostgreSQL/pgvector 集成、权限策略、在线 reindex 和真实容量门禁仍需后续验收或实现。
- 任何“百万级 p95”结论必须来自固定硬件、数据量、并发和查询矩阵的实测报告；脚本输出本身不构成性能承诺。
