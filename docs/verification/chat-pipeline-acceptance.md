# 对话链路验收标准（2026-09-13）

适用范围：WPF 对话页一次完整问答（知识型问题，如「你知道什么是gpt吗」）。
验收方式：API 探针 + 一次真实对话的思考流检查，**全部通过才算修好**。

## A. 配置生效（进程级）

| ID | 标准 | 探针 |
|---|---|---|
| A1 | 后端运行时 `rerank_model == BAAI/bge-reranker-base` | `GET /v1/config` |
| A2 | WPF 注入的 `DOC2MIND_RERANK_MODEL` 不是 Xenova/bge-reranker-v2-m3 | 重启后端后 `GET /v1/config` |
| A3 | 加载中的 Python 源是仓库 `E:\DocMindY\src\doc2mind` | `doc2mind.__file__` |

## B. 检索链路（一次真实对话）

| ID | 标准 | 判定 |
|---|---|---|
| B1 | 思考流**不得**出现「检索降级：重排模型不可用」 | 否决项 |
| B2 | 思考流出现「已重排精排」或命中分块带重排相关度 | 必须 |
| B3 | 联网超时文案为「>18s」且超时后若已有结果则「保留已完成的 N 条」，而非「>12s」整段丢弃 | 必须 |
| B4 | 无关本地切片不得伪装成已引用（可「未作引用」，但不得乱编 [n]） | 必须 |

## C. 性能与元信息

| ID | 标准 | 判定 |
|---|---|---|
| C1 | 思考总耗时记录完整（可读到秒级） | 观察 |
| C2 | 思考区不泄露英文规划原始 prompt（meta-leak） | 观察（本次不修，另开 issue） |

## 验收命令

```powershell
# 1) 配置探针
$token = (Get-Content "$env:LOCALAPPDATA\doc2mind\server.token").Trim()
Invoke-RestMethod -Uri http://127.0.0.1:8765/v1/config -Headers @{Authorization="Bearer $token"} |
  Select-Object rerank_model, rerank_enabled

# 2) 代码探针：超时应为 18.0
Select-String -Path E:\DocMindY\src\doc2mind\core\rag.py -Pattern "18\.0|>18s"

# 3) 对话探针：观察思考流是否仍有「检索降级」/「>12s」
```

## Done 定义

- A1–A3 全绿
- B1 必须不出现；B2 必须出现；B3 超时文案与部分保留行为正确
- 在本文件追加一次实测记录（时间、耗时、通过项）

---

## 实测记录

### 2026-09-14 00:05 · 探针 `tmp/chat_acceptance_probe.py`

**场景**：`POST /v1/chat/stream`，query=`你知道什么是gpt吗`，`enableWebSearch=true`，`ragMode=hybrid`

| ID | 结果 | 证据 |
|---|---|---|
| A1 | ✅ | `GET /v1/config` → `rerank_model=BAAI/bge-reranker-base` |
| A2 | ✅ | 启动 env `DOC2MIND_RERANK_MODEL=BAAI/bge-reranker-base`；进程 7688 |
| A3 | ✅ | `doc2mind.__file__ = E:\DocMindY\src\doc2mind\...` |
| B1 | ✅ | `has_degrade_any=False`，无「检索降级」 |
| B2 | ✅ | 状态帧：`命中 0 个分块（已重排精排）（相关度偏低或主题不符，降为背景）` |
| B3 | ✅（文案）/ ⚠️（结果） | 出现 `联网搜索超时（>18s）`，不再出现 `>12s`；但 worker 未在 18s 内写出任何结果，未触发「保留已完成」 |
| B4 | ✅ | `total_chunks=0`、`sources=0`，无伪造引用 |
| C1 | ✅ | `elapsed_ms=56721` |
| C2 | ⚠️ 未修 | 思考区英文 meta-leak 仍在（另开 issue） |

**结论**：重排链路修复已验收通过；联网搜索在本机网络下 18s 内仍可能完全无结果（引擎/反爬慢），属独立待修项。

---

## 联网搜索改造验收（2026-09-14）

### 改造点

1. `WebSearchService.search(..., deadline=)`：绝对单调时间戳；到点带已完成结果返回
2. 线程池 `shutdown(wait=False)`：不再被慢引擎拖死（`with` 块默认 wait=True 是超时失效根因）
3. worker deadline 比外层早 0.5s + 收割已完成 future
4. 够数提前收工（渠道 ≥4 / 变体 ≥8）
5. 实体约束 + 相关性门槛 **前置到精读之前**（避免精读《你》歌词页吃光预算）
6. 剩余预算 <5s 跳过精读，直接返回标题/摘要候选
7. 通识短问补「纯核心实体」查询变体（`什么是gpt` → `gpt`）
8. 摘要级兜底：未精读但高相关候选可进入上下文（标注「仅摘要」）

### 单测

- `tests/test_web_search_deadline.py`：deadline 0.6s 内返回快通道结果（不等 2s 慢通道）
- `test_web_search_deadline` + `test_rag` + `test_config`：**92+ 全绿**

### 实测（query=`你知道什么是gpt吗`）

| 指标 | 改造前 | 改造后 |
|---|---|---|
| 联网超时 | 12s 整段丢弃 | 预算内完成，无超时杀进程 |
| 原始候选 | 0 | 10 |
| 实体过滤 | 精读 9 篇无关《你》页 | `9 → 0（须命中 gpt）`，未再精读垃圾页 |
| 总耗时 | 44–56s | **~33s** |
| 伪造引用 | 库内 5 条噪声 | 0（诚实无引用） |
| 重排 | 降级 | 已重排精排 |

### 仍存在的环境限制

本机网络下 Bing/DDG/Wikipedia 常挂起，百度对「什么是gpt」召回的是无关中文页（被实体约束正确丢弃）。因此通识题**可能仍拿不到联网引用**——这是引擎可达性/质量问题，不是超时丢弃问题。

后续可选：接入可配置的搜索 API（SerpAPI/Tavily/博查）作主通道，公有引擎作兜底。
