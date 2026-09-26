# 功能评估与测试契约（知识库基础能力）

> 目的：保证功能「稳定、可靠、完整」落地——每项能力必须同时具备
> **实现代码、自动化回归、可运行评估、验收门槛**。新增/改动基础能力时按本契约执行。

## 1. 三层评估体系

| 层 | 工具 | 输入 | 用途 | 门槛 |
|---|---|---|---|---|
| **合成/确定性回归** | `python tools/eval_retrieval.py`（含 `--zh`） | 假嵌入 + 合成语料 | CI 卡回归，防算法退化 | MRR≥0.70 / R@1≥0.40 / R@3≥0.80（中文基准） |
| **本库自检索** | `python tools/eval_library.py` 或 `POST /v1/eval/library` | 真实 VectorStore 抽样 | 回答「我的库准不准」 | SelfRecall@1≥0.50、@3≥0.75、MRR≥0.55 |
| **人工标注集（可选）** | 本地 JSON gold set | 用户 relevant 标注 | 精调 semantic_floor / 权重 | 按项目定义 |

## 2. 推荐检索配置（F0）

- 预设：`doc2mind.core.config.RECOMMENDED_RETRIEVAL_PRESET`
- 预览：`recommended_retrieval_preview(s)` / `GET /v1/config/retrieval-recommended`
- 应用：`apply_recommended_retrieval(s)` / `POST /v1/config/retrieval-recommended` / `doc2mind config --recommended-retrieval [--apply]`
- **契约**：
  1. 出厂 `Settings()` 默认值保持向后兼容（不静默改老用户行为）；
  2. 仅显式「应用推荐」时写回并 `save_settings`；
  3. `apply` 幂等：已对齐时 changes 为空；
  4. 白名单 `RECOMMENDED_RETRIEVAL_FIELDS` 外字段禁止被该入口修改。

## 3. 本库评估（F1）

- 核心：`doc2mind.core.eval_library.evaluate_library(store, embedder, ...)`
- CLI：`tools/eval_library.py`；HTTP：`POST /v1/eval/library`
- **自检索定义**：从 chunk 正文截取查询 → 期望命中该 `chunk_id`。
- **契约**：
  1. 抽样必须 `seed` 可复现；
  2. `embedder is None` 时降级 BM25 并在 `metrics.degraded` 标记，不得静默当全量精度；
  3. 空库 / 无分块：`gate_passed=false` + `degraded_reason`，CLI 退出码 1；
  4. 门槛未过：CLI 退出码 2，便于脚本/CI；
  5. 报告必须含 `health`（文档数/分块数/维度一致性）与 `recommendations`（可操作）。

## 4. 新功能落地检查单（DoD）

每个基础能力 PR 必须勾满：

- [ ] 代码在 `src/doc2mind/`，无死分支
- [ ] 单元/集成测试在 `tests/`，全绿
- [ ] 若影响检索/入库：跑 `eval_retrieval.py --zh` 与（若有库）`eval_library.py`
- [ ] HTTP/MCP/CLI/WPF 至少一条用户路径可触达（无「后端有、前端无」空转）
- [ ] 文档：`docs/api.md` 或功能契约 / CHANGELOG 同步
- [ ] 破坏性变更：默认关、开关可回滚、老库迁移幂等

## 5. 建议的持续集成卡点

```powershell
# 1) 单元 + 集成
python -m pytest tests/ -q

# 2) 确定性检索门槛（免联网）
python tools/eval_retrieval.py --zh
# 期望 exit 0

# 3) WPF
dotnet test DocMind.Tests -v q

# 4)（可选，本机有库时）本库自检索
python tools/eval_library.py --sample 20
# exit 0 通过；2 表示需应用推荐配置或 reindex
```

## 6. 角色分工

| 角色 | 职责 |
|---|---|
| 开发改动 | 按 DoD 自测 + 补测试 |
| 评估件 | 算法/参数变更必须有 A/B 数字（合成或本库） |
| 发版 | 上表 1–3 全绿才打 tag |
| 用户侧 | 设置页/CLI 一键应用推荐；质量看板可调 eval |

## 7. 已知边界（明确不在本契约内）

- 晚分块（Late Chunking）：已判定 ROI 不足，见 foundation-upgrade C3 ADR
- 人工标注 UI：后续批次，先用 JSON gold set
- 分布式/多机评估：单机本地库即可
