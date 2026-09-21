# T0 合并策略（agent-p0 → 主仓）

## 范围

- 源：`E:\DocMindY-worktrees\agent-p0` 分支 `feat/t3-citation-gate-timing`（基于 `feat/agent-p0-p1`）
- 目标：主仓 `E:\DocMindY`（`debug`，含大量未提交改动）——**禁止** `checkout --force` / `reset --hard` 覆盖

## 建议合并步骤（集成 Agent）

1. 在主仓创建整合分支，不直接推 `debug`：
   ```powershell
   git -C E:\DocMindY checkout -b integrate/agent-p0-t3
   ```
2. 合并 worktree 分支：
   ```powershell
   git -C E:\DocMindY merge --no-ff feat/t3-citation-gate-timing
   ```
3. 冲突优先保留：主仓未提交的 WPF/模型文件；后端冲突以 worktree 的 `rag.py`/`config.py`/`http.py` 门控与 timing 为准（本任务已测）。
4. 合并后回归（本机 venv）：
   ```powershell
   $env:PYTHONPATH="E:\DocMindY\src"
   E:\DocMindY\.venv\Scripts\python.exe -m pytest tests/test_context_and_citation_gates.py tests/test_confidence_citation.py tests/test_rag.py tests/test_dispatch_t0_t8_acceptance.py tests/test_config.py -q
   E:\DocMindY\.venv\Scripts\python.exe scripts/run_full_verification.py --repo E:\DocMindY --python E:\DocMindY\.venv\Scripts\python.exe
   ```
5. 契约台账：dispatch README 中 T3 状态 `done-worktree` → 合并后改 `done`。

## 已知不应被覆盖的主仓痕迹

- 主仓 `debug` 大量未提交 WPF/文档；合并策略记录在本文件，不静默丢弃。
- worktree 中未跟踪的 `tmp_*.py`、`tmp-biz-test.db*` **不纳入合并**。

## 合并后应进入主仓的关键文件

- `src/doc2mind/core/rag.py`（门控 + stage timing + 慢提示）
- `src/doc2mind/core/config.py`
- `src/doc2mind/core/llm/base.py` / `openai_impl.py`（tool-calling）
- `src/doc2mind/core/search/provider.py`
- `src/doc2mind/core/agent/runtine/chat_agent.py` → `runtime/chat_agent.py`
- `src/doc2mind/server/http.py`
- `tests/test_dispatch_t0_t8_acceptance.py` 等
- `docs/api.md`、`docs/verification/*`、`tools/calibrate_citation_threshold.py`
- WPF：`AppSettings.cs`、`SettingsViewModel.cs`、`SettingsView.xaml`、`ChatViewModel.cs`、`Doc2kbApiService.cs`、`IDoc2kbApiService.cs`

## 回归基线（worktree 已跑）

| 组 | 结果 |
|---|---|
| citation/config/confidence | PASS |
| rag 全量相关 | PASS |
| commercial/contract/stream | PASS |
| dispatch acceptance（T2-T8） | 见任务收尾粘贴 |
