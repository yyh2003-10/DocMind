# ISSUE-03 弱相关引用门控调参与回归

| 字段 | 内容 |
|---|---|
| ID | T3 |
| 优先级 | **P0** |
| 角色 | 检索/后端 |
| 依赖 | T0 |
| 状态 | ready |

## 背景

用户：「弱相关的内容为什么也可以上引用」。  
已加 `_hit_supports_query` 主题门控，`citation_min_score` 默认 0.35→0.45。需调参与真实库回归。

## 目标

1. 弱相关/偏题切片**不占 [n]**，或仅作背景。  
2. 真正相关资料仍稳定被引用。  
3. 空命中时文案诚实（不硬编引用）。

## 验收标准

- [x] 单测：与 query 无词汇重叠的高分切片不进 cite_hits（`test_high_score_no_lexical_overlap_not_cited`）
- [x] 真实库：问库外概念 → 无伪造 [n] 或明确「无依据」（calibration CSV 库外 cite=0；status 诚实提示）
- [x] 真实库：问库内概念 → 引用切片正文与问题相关（库内 cite_avg=1.0 + off-topic 用例）
- [x] 配置项 `citation_min_score` 可调且生效（done/evidence.citation_gate + 状态流过滤计数）
- [x] `test_confidence_citation.py` / `test_rag.py` 相关用例通过（本轮相关组 201 passed）
- [x] 记录：门控误杀风险与建议默认值（`citation_gate_calibration_2026-09-20.md`）

## 建议实现点

- `rag.py`：`_hit_supports_query`、`partition_citation_hits`、cite/bg/discard 分流
- `config.py`：`citation_min_score` / `citation_bg_score_ratio` / `background_hit_limit`
- 状态流增加「低分丢弃 / 主题不符降为背景」计数

## 产出

- 调参前后对比表（问题 → 引用数/来源）：`docs/verification/citation_gate_calibration_2026-09-20.csv`
- 推荐默认阈值与原因：同目录 `.md`（0.45 / 0.6 / bg_limit=5）

