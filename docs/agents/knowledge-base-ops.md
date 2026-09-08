# Knowledge Base Ops — 软删除 / 恢复 / 自动整理工作流

DocMind 知识库的删除与整理是**可逆优先**设计。本文档是 Agent 操作知识库的速查，完整契约见根目录 `AGENTS.md`「AI 自动整理（curate）— 必读契约」。

## 删除语义（软删除）

`remove_doc` / `DELETE /v1/documents/{id}` / `doc2mind remove` 现在都是**软删除**：

- `chunks_meta` / `vec_chunks` / `bm25_index` / `sparse_terms` / `chunk_entities` 物理删除（检索立即不可见）
- `documents` 行保留，`deleted_at` 置时间戳（30 天后悔期）
- 30 天内可 `restore_doc` 撤销；30 天后由 `purge_trash` 物理清除

**Agent 操作纪律：**

1. 删除前先确认用户意图——软删除虽可逆，但恢复的是**元数据**，chunks/向量需重新摄入才能检索。
2. 删除后提示用户"可用 `list_trash` + `restore_doc` 撤销"。
3. 重复删除已软删文档是幂等成功（返回 `already_deleted`），不是错误。

## 恢复工作流

用户误删后，按此顺序操作：

1. `list_trash` 查回收站，拿到准确的 `document_id` 和 `source`（绝对路径）。
2. `restore_doc(target=<document_id>)` 恢复元数据。
3. 告知用户：恢复后文档出现在列表/统计，但**检索不到**——需重新摄入同源文件或 `reindex` 重建向量。

**无法恢复的场景：** 软删后同 source 被重新摄入的活跃文档占用 → `restore_doc` 返回 `not_found`（部分唯一索引阻止两个同 source 文档并存）。这是合理行为，回收站条目等 30 天 GC 即可。

## 按文件名 vs 按 ID

- `documents.source` 存的是**绝对路径**（loader 解析后统一 `/` 分隔）。
- 按文件名删/恢复：传 basename（`alpha.md`），内部 `LIKE '%/alpha.md'` 后缀匹配。
- 按 ID：传 26 位 ULID。
- 找不到时优先用 `list_trash` / `list_docs` 拿准确 ID，不要反复试文件名。

## 自动整理（curate）与留痕

- 每次 `curate()` 跑完自动写 `curate_runs` 一行（actions / changed_doc_ids / dry_run / note）。
- Agent 主动跑 curate 时传 `note`（触发来源，如 `"agent_settle"`）便于回溯。
- 回答"刚才做了什么整理"前先 `list_curate_runs(days=7)`，不靠记忆。
- `dedup` / `consolidate` 永不静默跑：先 `dry_run=true` 预览，用户明确同意才落盘。
- `purge_trash` 是破坏性操作，仅用户明确同意后调用（默认清 30 天前的）。
