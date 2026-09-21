#!/usr/bin/env python3
"""DocMind 全方面检测运行器（跨平台，Windows PowerShell 友好）。

用法:
  python scripts/run_full_verification.py
  python scripts/run_full_verification.py --repo E:\\DocMindY-worktrees\\agent-p0
  python scripts/run_full_verification.py --include-dotnet
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def default_repo() -> Path:
    cand = Path(r"E:\DocMindY-worktrees\agent-p0")
    if (cand / "src" / "doc2mind").is_dir():
        return cand
    return Path(__file__).resolve().parents[1]


def run_pytest(python: str, repo: Path, name: str, files: list[str]) -> dict:
    tests = repo / "tests"
    paths = [str(tests / f) for f in files if (tests / f).is_file()]
    if not paths:
        return {"group": name, "status": "SKIP", "detail": "no test files"}
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["DOC2MIND_DISABLE_AUTH"] = "1"
    print(f"==> {name} ({len(paths)} files)", flush=True)
    p = subprocess.run(
        [python, "-m", pytest_bin(), *paths, "-q", "--tb=line"],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out = (p.stdout or "") + (p.stderr or "")
    last = ""
    for line in out.splitlines():
        if "passed" in line or "failed" in line or "error" in line or "skipped" in line:
            last = line.strip()
    status = "PASS" if p.returncode == 0 else "FAIL"
    print(f"[{status}] {name} — {last}", flush=True)
    if p.returncode != 0:
        print(out[-4000:], flush=True)
    return {"group": name, "status": status, "detail": last or f"rc={p.returncode}"}


def pytest_bin() -> str:
    return "pytest"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=str(default_repo()))
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--include-dotnet", action="store_true")
    ap.add_argument("--skip-full", action="store_true")
    ap.add_argument("--skip-retrieval-eval", action="store_true", default=True)
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    python = args.python
    tests = repo / "tests"
    results: list[dict] = []

    groups: list[tuple[str, list[str]]] = [
        (
            "商业门禁与基础能力",
            [
                "test_commercial_gates.py",
                "test_foundations_upgrade.py",
                "test_contract_p0_gates.py",
                "test_business_matrix.py",
                "test_http_foundation_api.py",
                "test_http_dto_contract.py",
                "test_auth_middleware.py",
            ],
        ),
        (
            "对话/续写/双轨",
            [
                "test_rag.py",
                "test_prompt_policy.py",
                "test_continue_merge.py",
                "test_wpf_p0_contract.py",
                "test_stream_resilience.py",
                "test_rag_agent_planning.py",
                "test_chat_store.py",
                "test_web_search.py",
            ],
        ),
        (
            "Agent 骨架与执行器",
            [
                "test_agent_chat_stream.py",
                "test_agent_runtime.py",
                "test_agent_executors.py",
            ],
        ),
        (
            "导入/取消/软删",
            [
                "test_fc01_import_cancel.py",
                "test_cancel_contracts.py",
                "test_soft_delete.py",
                "test_ingest_stage_progress.py",
                "test_p0_foundation.py",
                "test_extractor.py",
            ],
        ),
        (
            "搜索/检索/重排",
            [
                "test_fc07_search_empty.py",
                "test_bm25_ranking.py",
                "test_rerank.py",
                "test_confidence_citation.py",
                "test_contextual_retrieval.py",
                "test_context_and_citation_gates.py",
                "test_dispatch_t0_t8_acceptance.py",
                "test_http_config_new_fields.py",
            ],
        ),
        (
            "图谱/科研/意图",
            [
                "test_arbiter.py",
                "test_intent_classifier.py",
                "test_research_context.py",
                "test_research_citation_support.py",
                "test_research_graph_topic.py",
            ],
        ),
        (
            "创作/导出/整理/配置",
            [
                "test_creative_api.py",
                "test_creator_exporters.py",
                "test_pptx_p0_quality.py",
                "test_pptx_inspector.py",
                "test_curator.py",
                "test_config.py",
            ],
        ),
        (
            "LLM/元数据/健壮性",
            [
                "test_llm_providers.py",
                "test_metadata.py",
                "test_model_registry.py",
                "test_llm_output.py",
                "test_robustness_fixes.py",
                "test_weak_model_resilience.py",
                "test_web_search_deadline.py",
            ],
        ),
    ]

    print(f"Repo: {repo}")
    print(f"Python: {python}")
    print(f"Time: {datetime.now().isoformat(timespec='seconds')}")
    print()

    for name, files in groups:
        results.append(run_pytest(python, repo, name, files))

    # 全量
    if not args.skip_full:
        ignore = tests / "test_retrieval_eval.py"
        env = os.environ.copy()
        env["PYTHONPATH"] = str(repo / "src") + os.pathsep + env.get("PYTHONPATH", "")
        env["DOC2MIND_DISABLE_AUTH"] = "1"
        print("==> 全量 pytest（排除 retrieval_eval）", flush=True)
        cmd = [python, "-m", "pytest", str(tests), "-q", "--tb=line"]
        if ignore.is_file():
            cmd += ["--ignore", str(ignore)]
        p = subprocess.run(cmd, cwd=str(repo), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = (p.stdout or "") + (p.stderr or "")
        last = ""
        for line in out.splitlines():
            if any(k in line for k in ("passed", "failed", "skipped", "error")):
                last = line.strip()
        status = "PASS" if p.returncode == 0 else "FAIL"
        print(f"[{status}] 全量 pytest — {last}", flush=True)
        if p.returncode != 0:
            print(out[-5000:], flush=True)
        results.append({"group": "全量 pytest", "status": status, "detail": last})

    # 业务矩阵
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo / "src") + os.pathsep + str(repo) + os.pathsep + env.get("PYTHONPATH", "")
    env["DOC2MIND_DISABLE_AUTH"] = "1"
    print("==> 业务矩阵结构检测", flush=True)
    p = subprocess.run(
        [python, "-c", "import tests.test_business_matrix as m; m.test_all_required_endpoints_exist(); m.test_all_required_viewmodels_exist(); m.test_all_required_tests_exist(); m.test_feature_markers_present(); m.test_mcp_handler_count_at_least_20(); print('business matrix OK')"],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    ok = p.returncode == 0 and "OK" in (p.stdout or "")
    results.append(
        {
            "group": "业务矩阵",
            "status": "PASS" if ok else "FAIL",
            "detail": "endpoints/vms/tests/markers OK" if ok else ((p.stdout or "") + (p.stderr or ""))[:300],
        }
    )
    print(f"[{'PASS' if ok else 'FAIL'}] 业务矩阵")

    # dotnet
    if args.include_dotnet:
        csproj = repo / "DocMind" / "DocMind.csproj"
        print("==> dotnet build", flush=True)
        p = subprocess.run(
            ["dotnet", "build", str(csproj), "-v", "q", "--nologo"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        status = "PASS" if p.returncode == 0 else "FAIL"
        detail = "ok" if p.returncode == 0 else ((p.stdout or "") + (p.stderr or "")).strip().splitlines()[-3:]
        print(f"[{status}] dotnet build — {detail}")
        results.append({"group": "dotnet build", "status": status, "detail": str(detail)[:200]})
        if p.returncode == 0:
            tproj = repo / "DocMind.Tests" / "DocMind.Tests.csproj"
            if tproj.is_file():
                print("==> dotnet test", flush=True)
                p2 = subprocess.run(
                    ["dotnet", "test", str(tproj), "-v", "q", "--nologo"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                st = "PASS" if p2.returncode == 0 else "FAIL"
                dl = ((p2.stdout or "") + (p2.stderr or "")).strip().splitlines()[-2:]
                print(f"[{st}] dotnet test — {dl}")
                results.append({"group": "dotnet test", "status": st, "detail": str(dl)[:200]})
    else:
        results.append({"group": "dotnet", "status": "SKIP", "detail": "未启用 --include-dotnet"})

    # 报告
    report_dir = repo / "docs" / "verification"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = report_dir / "full-test-report.md"
    n_pass = sum(1 for r in results if r["status"] == "PASS")
    n_fail = sum(1 for r in results if r["status"] == "FAIL")
    n_skip = sum(1 for r in results if r["status"] == "SKIP")
    lines = [
        "# DocMind 全方面检测报告",
        "",
        f"- 时间：{datetime.now().isoformat(timespec='seconds')}",
        f"- 仓库：`{repo}`",
        f"- Python：`{python}`",
        f"- 结果：**PASS={n_pass} / FAIL={n_fail} / SKIP={n_skip}**",
        "",
        "## 分组结果",
        "",
        "| 分组 | 状态 | 摘要 |",
        "|---|---|---|",
    ]
    for r in results:
        detail = str(r["detail"]).replace("|", "/").replace("\n", " ")[:180]
        lines.append(f"| {r['group']} | **{r['status']}** | {detail} |")
    lines += [
        "",
        "## 商用门禁覆盖",
        "",
        "- Agent 未开启时服务端强制回落 RAG",
        "- 回收站清理二次确认；恢复提示需重摄入/reindex",
        "- 路径穿越与超大写入拒绝",
        "- HTTP Bearer 鉴权",
        "- 密钥不进生效配置明文列表",
        "- 导入取消残留、搜索空库分流、LLM 前置禁用、离线横幅接线",
        "",
        "## 已知环境债",
        "",
        "- `test_retrieval_eval.py` 缺 `eval_retrieval` 模块，默认排除",
        "- 全量跑时 `test_source_log_recorded` 可能 caplog 串扰（单测可通过）",
        "- WPF `dotnet` 可能因 NuGet path1 失败，需在可用开发机补跑",
        "- API smoke 需后端进程，本脚本未默认启动",
        "",
    ]
    report.write_text("\n".join(lines), encoding="utf-8")
    print()
    print(f"报告: {report}")
    print(f"PASS={n_pass} FAIL={n_fail} SKIP={n_skip}")
    return 1 if n_fail > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
