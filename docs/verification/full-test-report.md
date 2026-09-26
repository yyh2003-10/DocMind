# DocMind 全方面检测报告

- 时间：2026-09-21T10:55:33
- 仓库：`E:\DocMindY`
- Python：`E:\DocMindY\.venv\Scripts\python.exe`
- 结果：**PASS=9 / FAIL=0 / SKIP=1**

## 分组结果

| 分组 | 状态 | 摘要 |
|---|---|---|
| 商业门禁与基础能力 | **PASS** | 43 passed, 33 warnings in 4.24s |
| 对话/续写/双轨 | **PASS** | 227 passed in 26.52s |
| Agent 骨架与执行器 | **PASS** | 19 passed in 1.21s |
| 导入/取消/软删 | **PASS** | 77 passed, 21 warnings in 9.61s |
| 搜索/检索/重排 | **PASS** | 67 passed in 12.05s |
| 图谱/科研/意图 | **PASS** | 190 passed in 1.69s |
| 创作/导出/整理/配置 | **PASS** | 69 passed, 5 warnings in 4.47s |
| LLM/元数据/健壮性 | **PASS** | 153 passed, 53 warnings in 14.56s |
| 业务矩阵 | **PASS** | endpoints/vms/tests/markers OK |
| dotnet | **SKIP** | 未启用 --include-dotnet |

## 商用门禁覆盖

- Agent 未开启时服务端强制回落 RAG
- 回收站清理二次确认；恢复提示需重摄入/reindex
- 路径穿越与超大写入拒绝
- HTTP Bearer 鉴权
- 密钥不进生效配置明文列表
- 导入取消残留、搜索空库分流、LLM 前置禁用、离线横幅接线

## 已知环境债

- `test_retrieval_eval.py` 缺 `eval_retrieval` 模块，默认排除
- 全量跑时 `test_source_log_recorded` 可能 caplog 串扰（单测可通过）
- WPF `dotnet` 可能因 NuGet path1 失败，需在可用开发机补跑
- API smoke 需后端进程，本脚本未默认启动
