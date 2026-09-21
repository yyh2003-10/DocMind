# T5 模型延迟诊断与推荐策略

> 依据 ISSUE-05 + 「中后端均匀提速」。观测字段已在 T3 done 帧落地；本页给产品侧推荐口径。

## 分阶段耗时字段（done 帧 / evidence.timing）

| 字段 | 含义 | 健康参考（本地/中端 API） |
|---|---|---|
| `stage_retrieval_ms` | 本地检索 + 引用门控 | < 800ms（小库）/ < 3s（大库+重排） |
| `stage_web_ms` | 联网搜索与精读 | 0（关闭）或 ≤ `web_search_timeout`（默认 36s） |
| `stage_tool_ms` | 避坑/Agent 工具执行 | 通常 < 500ms |
| `stage_first_token_ms` | 生成首 token（TTFT）；非流式 = -1 | < 5s 理想；≥ 30s 触发慢模型提示 |
| `stage_generation_ms` | 生成总时长 | 与输出长度相关 |
| `stage_total_ms` | 全链路 | = 本轮 elapsed_ms |

慢模型提示：首 token 等待超过 `llm_first_token_slow_ms`（默认 30000）后，状态流每约 5s 输出「正在生成回答…（已等待 Ns），可关闭联网/换模型/继续等待」，N 单调递增，最多 12 次。

## 不均衡诊断

| 现象 | 可能原因 | 操作建议 |
|---|---|---|
| 检索快、TTFT 极慢 | 超大 MoE / 远程慢链路 | 设置中换 gpt-oss / qwen 中等规模；关联网减上下文 |
| 联网 30s+、生成很快 | 多引擎反爬挂起 | 调低 `web_search_timeout`；T6 换 search_provider |
| 检索+门控很长 | 大库重排/扩展开启 | 关 query_expansion；降 rerank_recall |
| 总耗时长但各段都不长 | 队列/代理缓冲 | 检查 SSE no-cache；缩短历史 |

## 模型选用建议

| 场景 | 推荐 |
|---|---|
| 日常知识库问答 | 中等规模（gpt-oss-20b / qwen2.5-7b / deepseek-chat） |
| 长文创作 | 输出上限更大的模型 + delivery 轨 |
| 本地离线 | Ollama 量化模型；接受更高 TTFT |
| 避免 | 未调优的超大 MoE 做默认对话模型 |

## 超时错误文案（已实现）

失败时建议包含：检查模型服务 / **关闭联网** / **设置中换更快模型** / 减少知识库数量。

## 回归

- 快速模型：`stage_first_token_ms` < 阈值时不出现「模型响应较慢」
- 见 `tests/test_dispatch_t0_t8_acceptance.py::test_slow_model_waiting_hints_increase`
