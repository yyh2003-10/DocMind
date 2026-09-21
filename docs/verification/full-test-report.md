# DocMind 全方面检测报告

- 时间：2026-09-21T10:52:57
- 仓库：`E:\DocMindY`
- Python：`E:\DocMindY\.venv\Scripts\python.exe`
- 结果：**PASS=7 / FAIL=1 / SKIP=2**

## 分组结果

| 分组 | 状态 | 摘要 |
|---|---|---|
| 商业门禁与基础能力 | **PASS** | 11 passed, 17 warnings in 2.36s |
| 对话/续写/双轨 | **PASS** | 214 passed in 27.96s |
| Agent 骨架与执行器 | **SKIP** | no test files |
| 导入/取消/软删 | **PASS** | 72 passed, 21 warnings in 12.27s |
| 搜索/检索/重排 | **PASS** | 64 passed in 13.05s |
| 图谱/科研/意图 | **PASS** | 190 passed in 1.68s |
| 创作/导出/整理/配置 | **PASS** | 69 passed, 5 warnings in 4.42s |
| LLM/元数据/健壮性 | **PASS** | 153 passed, 53 warnings in 14.03s |
| 业务矩阵 | **FAIL** | Traceback (most recent call last):   File "<string>", line 1, in <module> ModuleNotFoundError: No module named 'tests.test_business_matrix'  |
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
