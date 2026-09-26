# 真实检索评估集

> 目的：用**真实用户 query + 已标注相关分块**替换合成玩具集，给改检索/模型/档案时的回归锚点。

## 文件

| 文件 | 用途 |
|---|---|
| `real_v1.template.jsonl` | 50 条题面模板（分层配比），`relevant` 待人工填写 |
| `real_v1.jsonl` | **正式评估集**（由模板填写后生成；git 可提交） |
| `demo_corpus.jsonl` | 离线演示语料（无真实库时 harness 自动用） |

## 行格式（JSONL，一行一条）

```json
{
  "id": "q001",
  "query": "气缸缸径怎么选",
  "collection": "manuals",
  "type": "zh_2char",
  "relevant": ["manuals/a.pdf#12", "manuals/b.md#3"],
  "negatives": ["manuals/c.pdf#40"],
  "notes": "短词必须 BM25 也能中"
}
```

- **键格式**：`{source}#{chunk_index}`；`source` 与库中 `documents.source` 一致（笔记为 `note:标题`）
- **type**：`zh_2char` / `entity_exact` / `paraphrase` / `multi_hop` / `negation_noise` / `long_tail` / `ood`
- **ood** 不计入 Recall，单独统计 top1 误命中率

## 运行

```powershell
# 离线演示（demo 语料 + 确定性嵌入）
python tools/eval_real_set.py --eval-set eval/real_v1.template.jsonl

# 连真实库（后端已入库；用生产嵌入）
python tools/eval_real_set.py --eval-set eval/real_v1.jsonl --live

# 只标定 floor 建议
python tools/eval_real_set.py --eval-set eval/real_v1.jsonl --live --calibrate-floor
```

## 标注节奏

1. 从真实库/用户口述写 50 条 query  
2. 人工标 1–3 个 `relevant`，2–3 个 `negatives`  
3. 先锁基线（不卡 FAIL），再逐步收紧门槛  
4. 每月从 query 日志补 10 条（有日志之后）

## 门禁建议

| 指标 | 第一版 | 目标 |
|---|---|---|
| MRR@5 | ≥ 当前 − 0.02 | ≥ 0.70 |
| Recall@3 | ≥ 当前 − 0.03 | ≥ 0.75 |
| zh_2char R@3 | ≥ 0.80 | 1.0 |
| ood top1 误命中 | 记录 | ≤ 0.15 |
---

# 意图路由评估集（intent_v1）

> 目的：给「对话问答 AI」的意图路由（创作 / 科研 / 问答 / 排障 / 闲聊）做回归锚点，
> 防止新增科研写作链路后误判创作意图或科研意图。

## 文件

| 文件 | 用途 |
|---|---|
| `intent_v1.jsonl` | 意图路由标注集（46 条，分层配比，常驻回归） |
| `intent_v1.baseline.json` | 首版跑分基线（工具自动沉淀） |

## 行格式（JSONL，一行一条）

```json
{
  "id": "r001",
  "query": "把这些文献的观点对比一下",
  "history": [],                          // 多轮场景非空，与 query 联合路由
  "expected": "research",                 // greeting|question|task|troubleshoot|creative|research|summary
  "expected_sub": "compare",              // creative→persona；research→review/compare/draft
  "kind": "ambiguous_creative_research"   // 样本分层标记
}
```

## 分层配比（design 6.2.1）

- 四类场景正向各 6：问答 / 排障 / 科研 / 创作
- 闲聊 + 问候 4（含「随便聊聊」「今天天气怎么样」）
- 泛问叩边 4（「总结一下这篇文章」「对比 A 和 B 的区别」）
- **创作⇄科研叩边 8+**（含「把这些文献整理成一份对比报告」变体、多轮反复表达创作诉求、纯对比问答）
- 科研细分 6（综述 / 观点对比 / 带引用草稿各 2）

## 跑分工具与指标

```powershell
# 离线跑分（默认不依赖 LLM API；规则决策面）
python tools/eval_intent_routing.py --eval-set eval/intent_v1.jsonl

# 沉淀/对比基线（任一指标倒退 exit 1，CI 可回归）
python tools/eval_intent_routing.py --eval-set eval/intent_v1.jsonl --baseline eval/intent_v1.baseline.json

# 连真实 LLM 规划复核（可选）
python tools/eval_intent_routing.py --eval-set eval/intent_v1.jsonl --live

# 打印不一致样本明细
python tools/eval_intent_routing.py --eval-set eval/intent_v1.jsonl --show-errors
```

四指标口径（spec 6.1-A①-④）：

| 指标 | 口径 | 首版门槛 |
|---|---|---|
| route_acc（路由正确率） | 全体 expected 一致率 | ≥ 0.90 |
| creative_hit（创作命中） | expected=creative 中识别为 creative | ≥ 0.85 |
| research_hit（科研命中） | expected=research 中识别为 research | ≥ 0.80 |
| creative_fp（非创作误判） | expected≠creative 中被误判为 creative | ≤ 0.10 |

## 首版基线（2026-09-15，offline）

- route_acc = 97.83%
- creative_hit = 100.00%
- research_hit = 100.00%
- creative_fp = 0.00%

**注意**：叩边样本是常驻回归集，今后任何路由改动必须四指标全绿；
标注需人工核对，不得以模型自标冒充人工标注。
---

# 科研引用支撑评估集（research_v1）

> 目的：给「对话问答 AI」的科研写作子链路（文献集合判定 + 文献限定检索 + 图谱 topic 注入 + 引用支撑验证）做回归锚点，
> 验证 verify_citation_support 的判定逻辑（设计文档决策 D-1）与样例标签一致，且文献支撑率不倒退。

## 文件

| 文件 | 用途 |
|---|---|
| `research_v1.jsonl` | 科研引用支撑标注集（15 条，review / compare / draft 各 5 条，常驻回归） |
| `research_v1.baseline.json` | 首版跑分基线（工具自动沉淀） |

## 行格式（JSONL，一行一条）

```json
{
  "id": "r01",
  "task_type": "review",
  "query": "请基于我的文献集合，综述一下 X 框架在 Y 场景下的效果",
  "sources": [
    {"kind": "literature", "snippet": "X 框架在 Y 场景下准确率达到 82%..."},
    {"kind": "web", "snippet": "通用经验：小样本场景下优先选用..."}
  ],
  "reply": "本次综述聚焦「X 框架在 Y 场景下的效果」，以下结论均来自所选文献集合。[1][2] ...",
  "labels": {
    "key_sentences": {
      "以下结论均来自所选文献集合。[1][2]": "supported",
      "X 框架在 Y 场景下的准确率约 82%... [1]": "supported",
      "现有检索到的文献均未包含关于 X 的具体实验数据...": "uncited"
    },
    "supported": 3,
    "external_only": 0,
    "uncited": 1
  },
  "note": "含免责声明窗口：文献集合未包含具体实验数据的陈述不带引用，属提示句不计入关键陈述"
}
```

- **kind**: `literature` = 所选文献集合原作切片（literature=True）；`web` = 外部来源（source_type="web"，literature=False）
- **key_sentences**: 关键陈述句片段 → 分类（supported / external_only / uncited）。句片段用双向包含匹配判定内核输出
- **supported / external_only / uncited**: 三类关键陈述计数，用于校验判定内核统计不倒退
- **note**: 样本设计意图与免责说明

## 分层配比（design 决策 D-1）

- review（文献综述）5 条：含争议观点、免责声明窗口、零命中场景
- compare（观点对比）5 条：含分号拆分、外部来源引用、混合场景
- draft（写作草稿）5 条：含推论性陈述、通用性背景、零关键陈述

## 跑分工具与指标

```powershell
# 离线跑分（完全离线，不调用 LLM / 检索）
python tools/eval_research_support.py --data eval/research_v1.jsonl

# 沉淀/对比基线（支撑率倒退 exit 1，CI 可回归）
python tools/eval_research_support.py --data eval/research_v1.jsonl --baseline eval/baselines/research_v1.baseline.json

# 打印不一致样本明细
python tools/eval_research_support.py --data eval/research_v1.jsonl --show-errors

# 落盘本次结果为新基线
python tools/eval_research_support.py --data eval/research_v1.jsonl --out eval/baselines/research_v1.baseline.json
```

三指标口径（design 决策 D-1）：

| 指标 | 口径 | 首版门槛 |
|---|---|---|
| support_rate（文献支撑率） | supported / key_statement_count | ≥ 0.70 |
| label_consistency（标签一致率） | 标签句命中数 / 标签句总数 | ≥ 0.95 |
| invalid_refs（越界引用编号） | 答案中引用编号超出 sources.index 范围 | 0 |

## 首版基线（2026-09-16，offline）

- support_rate = 76.92%（≥ 0.70 ✅）
- label_consistency = 100.00%（45/45 ✅）
- invalid_refs = 0 ✅

**注意**：样例集是判定内核的回归锚点，今后任何 verify_citation_support / split_sentences / audit_answer_citations 改动必须三指标全绿；
标注需人工核对，不得以模型自标冒充人工标注。
