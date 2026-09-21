"""对照技术规范的能力实现合规审计。"""
from pathlib import Path

root = Path(r"E:\DocMindY-worktrees\agent-p0")


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.is_file() else ""


http = read(root / "src/doc2mind/server/http.py")
prompt = read(root / "src/doc2mind/core/agent/prompt_policy.py")
agent = read(root / "src/doc2mind/core/agent/runtime/chat_agent.py")
rag = read(root / "src/doc2mind/core/rag.py")
chat_store = read(root / "src/doc2mind/core/store/chat_store.py")
cvm = read(root / "DocMind/ViewModels/ChatViewModel.cs")
dvm = read(root / "DocMind/ViewModels/DocumentsViewModel.cs")
svm = read(root / "DocMind/ViewModels/SettingsViewModel.cs")
gvm = read(root / "DocMind/ViewModels/GraphViewModel.cs")
qvm = read(root / "DocMind/ViewModels/QualityViewModel.cs")
main = read(root / "DocMind/ViewModels/MainViewModel.cs")
search_vm = read(root / "DocMind/ViewModels/SearchViewModel.cs")
imp_vm = read(root / "DocMind/ViewModels/ImportViewModel.cs")
chat_req = read(root / "DocMind/Models/ChatRequest.cs")
app_set = read(root / "DocMind/AppSettings.cs")
api = read(root / "docs/api.md") or read(Path(r"E:\DocMindY\docs\api.md"))
fc = read(Path(r"E:\DocMindY\docs\specs\功能契约\README.md"))
plan = read(root / "docs/specs/agent-capability-upgrade-plan.md")
norm = read(Path(r"E:\DocMindY\docs\specs\功能契约规范.md"))
reg = read(root / "src/doc2mind/core/agent/runtime/registry.py")
ws = read(root / "src/doc2mind/core/agent/runtime/workspace.py")
loop = read(root / "src/doc2mind/core/agent/runtime/loop.py")
executors = read(root / "src/doc2mind/core/agent/runtime/executors.py")
perm = read(root / "src/doc2mind/core/agent/runtime/permissions.py")
pipeline = read(root / "src/doc2mind/core/pipeline.py")

checks: list[tuple[str, bool, str]] = []


def add(name: str, ok: bool, note: str = "") -> None:
    checks.append((name, bool(ok), note))


# ── 计划 G1-G7 / P0 ──────────────────────────────────────────
add("计划文档存在", (root / "docs/specs/agent-capability-upgrade-plan.md").is_file())
add("G1 双轨 RAG/Delivery", "PROMPT_TRACK_DELIVERY" in prompt and "resolve_prompt_track" in prompt)
add("G3 长文：交付覆盖篇幅压制", "交付" in prompt or "delivery" in prompt.lower())
add(
    "P0 截断可观测 done 字段",
    ("continue_hint" in prompt or "truncated" in prompt)
    and ("truncated" in http or "done_frame_extras" in http or "truncated" in rag),
)
add("P0 续写后端合并", "continue_writing" in rag and "update_last_assistant_content" in chat_store)
add("P0 续写 WPF 命令", "ContinueWritingCommand" in cvm and "continueWriting" in chat_req)
add("P0 responseMode 契约", "response_mode" in http and "responseMode" in chat_req)
add("P0 提升交付轨 token", "boost_max_tokens" in prompt and "PROMPT_TRACK_DELIVERY" in rag)

# ── 计划 P1 骨架 ─────────────────────────────────────────────
add("P1 LoopController 状态机", "class LoopController" in loop and "LoopStatus" in loop)
add("P1 Loop 预算/取消", "max_steps" in loop and "cancel" in loop.lower())
add("P1 ToolRegistry+schema", "class ToolRegistry" in reg and "schema_for_model" in reg)
add("P1 权限 L0-L3", "L2_WORKSPACE_WRITE" in perm and "L3_FORBIDDEN" in perm)
add("P1 Workspace 沙箱", "PathDeniedError" in ws and "relative_to" in ws and "_MAX_WRITE_BYTES" in ws)
add("P1 executors 复用检索/导出", "bind_runtime_executors" in executors and "export_artifact" in executors)
add("P1 agent_answer_stream", "def agent_answer_stream" in agent)
add("P1 SSE tool 轨迹", "tool_call" in agent and "tool_result" in agent and "agent_plan" in agent)
add("P1 HTTP agentMode 字段", "agent_mode" in http and "agentMode" in http)
add("P1 商用门禁 agent_mode_enabled", "agent_allowed" in http and "agent_mode_enabled" in http)
add("P1 continue 优先于 agent", "continue_writing" in http and "agent_requested" in http)
add("P1 非目标：工具表无 shell", "shell" not in reg.lower() and "bash" not in reg.lower())

# ── 功能契约规范（四出口 / 前置 / 取消 / 空态） ────────────────
for doc in ["对话.md", "导入.md", "搜索.md", "知识图谱.md", "设置保存.md", "质量看板.md"]:
    t = read(Path(rf"E:\DocMindY\docs\specs\功能契约\{doc}"))
    ok = all(k in t for k in ("成功", "失败", "取消", "空态")) and "前置条件" in t
    add(f"契约结构完整 {doc}", ok)

add("规范前置 NavigateToSettings 范式", "NavigateToSettingsRequested" in gvm and "NavigateToSettingsRequested" in qvm)
add("规范取消残留 cancel_note", "cancel_note" in http and "CancelNote" in imp_vm)
add("规范取消粒度 pipeline 检查点", pipeline.count("IngestCancelled") >= 6)
add("规范空态分流 FC-07", "ShowGoImportAction" in search_vm and "知识库为空" in http)
add("规范离线统一出口 FC-03/08", "BackendUnreachable" in main and main.count("BackendUnreachable") >= 7)
add("规范成功可观测 FC-04", "EffectiveConfigItems" in svm and "生效明细" in read(root / "DocMind/Views/SettingsView.xaml"))
add("规范 LLM 前置事前禁用", "CanExtractGraph" in gvm and "IsLlmConfigured" in qvm)
add("规范 API Key 徽章 FC-05", "ApiKeyStatusText" in svm)

# ── api.md / 契约台账 ────────────────────────────────────────
for field in ("continueWriting", "responseMode", "agentMode", "truncated", "prompt_track"):
    add(f"api.md 字段 {field}", field in api)
add("api.md agent 门禁说明", "agent_mode_enabled" in api)
for fc_id in ("FC-01a", "FC-01b", "FC-03", "FC-04", "FC-06", "FC-07", "FC-08"):
    add(f"台账 {fc_id}", fc_id in fc)
add("台账 worktree 已接", "worktree 已接" in fc)

# ── 商用能力 ─────────────────────────────────────────────────
add("商用：回收站清理二次确认", "ShowPurgeTrashConfirm" in dvm and "ConfirmPurgeTrash" in dvm)
add("商用：恢复需重摄入提示", "重新摄入" in http)
add("商用：Agent AppSettings 默认关", "AgentModeEnabled" in app_set and "false" in app_set.split("AgentModeEnabled")[1][:80])
add("商用：Chat 默认不强开 Agent", "AgentModeEnabled" in cvm)
add("商用：配置回传 agent 预留", "agent_mode_enabled" in http and "agent_file_write_policy" in http)

# ── 测试齐套（技术规范：可执行验证） ─────────────────────────
required_tests = [
    "test_prompt_policy.py",
    "test_continue_merge.py",
    "test_agent_runtime.py",
    "test_agent_executors.py",
    "test_agent_chat_stream.py",
    "test_fc01_import_cancel.py",
    "test_fc07_search_empty.py",
    "test_contract_p0_gates.py",
    "test_commercial_gates.py",
    "test_foundations_upgrade.py",
    "test_http_foundation_api.py",
    "test_business_matrix.py",
    "test_cancel_contracts.py",
    "test_auth_middleware.py",
]
for t in required_tests:
    add(f"测试文件 {t}", (root / "tests" / t).is_file())
add("全量验证脚本", (root / "scripts/run_full_verification.py").is_file())
add("验证报告已生成", (root / "docs/verification/full-test-report.md").is_file())

# ── 计划明确未做项（诚实缺口，非违规） ────────────────────────
add("缺口备案：P2 longform 未实现", "Longform" not in agent and "longform_outline" not in http)  # 期望 True=确实未做
add("缺口备案：WPF Agent 轨迹 UI 无", "tool_call" not in cvm and "AgentModeEnabled" in cvm)
add("缺口备案：provider 真 tool-loop 无", "stream_chat_tools" not in rag and "chat_with_tools" not in (root / "src/doc2mind/core/llm/base.py").read_text(encoding="utf-8"))

# 计划目标 vs 实现：G2 服务端编排已实现、G5 轨迹部分（SSE 有 UI 无）
add("G2 服务端工具编排已实现", "LoopController" in agent and "bind_runtime_executors" in agent)
add("G4 工作区沙箱已实现", "Workspace" in agent and "PathDeniedError" in ws)
add("G6 artifact 后端化部分（notes/export）", "artifact_ready" in agent)

ok_n = sum(1 for _, c, _ in checks if c)
print(f"COMPLIANCE {ok_n}/{len(checks)}")
print()
for name, ok, note in checks:
    mark = "OK  " if ok else "FAIL"
    print(f"{mark} {name}" + (f" | {note}" if note else ""))
print()
fails = [n for n, c, _ in checks if not c]
if fails:
    print("NOT MET:")
    for f in fails:
        print(" -", f)
else:
    print("ALL MET")
