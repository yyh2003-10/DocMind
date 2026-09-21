# 引用门控调参对比（T3）

数据源：`docs/verification/citation_gate_calibration_2026-09-20.csv`（`tools/calibrate_citation_threshold.py`）

## 对比摘要（mock/真实库同构问题集）

| 阈值 | 库内概念平均 cite | 库外概念平均 cite | cross_check |
|---|---|---|---|
| 0.30 | 1.00 | 0.00 | 全部 True |
| **0.45（推荐默认）** | 1.00 | 0.00 | 全部 True |
| 0.55 | 1.00 | 0.00 | 全部 True |

库外「什么是豆包 / nemotron / 天气」在 0.30–0.55 下均不产生 cite（主题门 + 低分丢弃），符合 ISSUE-03 验收「无伪造 [n]」。

## 推荐默认

| 配置 | 推荐值 | 理由 |
|---|---|---|
| `citation_min_score` | **0.45** | 与 ISSUE-03 已上调默认一致；库外 cite=0，库内 cite 稳定；>0.60 在无重排库易误杀 |
| `citation_bg_score_ratio` | **0.6** | 中相关作背景、更低丢弃，缩短无效上下文 |
| `background_hit_limit` | **5** | 与 rag_top_k 同量级，防长背景拖慢生成 |

## 误杀风险

1. 同义改写无词汇重叠 → 高分命中**降为背景**而非丢弃（`topic_demoted`），可被模型用作背景但不占 `[n]`。
2. 短 query 无显著 token → 主题门停用，只靠分数门。
3. 门控自身异常 → 回退**全部可引用**（FR-06），宁松勿丢。

## 真实库回归

`tests/test_real_kb_citation_gate.py`（integration）：库不存在时 skip，不静默通过。库内路径见 calibration CSV。
