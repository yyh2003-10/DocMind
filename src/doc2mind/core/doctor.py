"""DocMind 系统健康体检与自愈诊断器 (Doctor)。

提供全面的环境状态检测、连通性测试、依赖诊断与一键修复建议。
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from doc2mind.core.config import _user_config_dir, _user_data_dir, get_settings
from doc2mind.core.embedder.fastembed_impl import (
    get_embed_providers,
    is_model_cached,
)
from doc2mind.core.system_env import (
    _ocr_available,
    get_gpu_diagnosis,
)


@dataclass
class DiagnosticCheck:
    """单个诊断项结果。"""

    name: str
    category: str  # "python", "storage", "embedder", "hardware", "network", "llm"
    status: str  # "ok", "warning", "error", "info"
    message: str
    detail: str | None = None
    fix_suggestion: str | None = None


@dataclass
class DoctorReport:
    """系统体检总报告。"""

    checks: list[DiagnosticCheck] = field(default_factory=list)
    overall_status: str = "ok"  # "ok", "warning", "error"
    score: int = 100  # 0 ~ 100
    summary: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_status": self.overall_status,
            "score": self.score,
            "summary": self.summary,
            "timestamp": self.timestamp,
            "checks": [
                {
                    "name": c.name,
                    "category": c.category,
                    "status": c.status,
                    "message": c.message,
                    "detail": c.detail,
                    "fix_suggestion": c.fix_suggestion,
                }
                for c in self.checks
            ],
        }


def _check_network_url(url: str, timeout: float = 3.0) -> tuple[bool, float, str]:
    """测试指定 URL 连通性，返回 (成功, 延迟ms, 描述)。"""
    start = time.perf_counter()
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "DocMind-Doctor/1.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed = (time.perf_counter() - start) * 1000.0
            return (True, elapsed, f"HTTP {resp.status} ({elapsed:.0f}ms)")
    except Exception as e:  # noqa: BLE001
        elapsed = (time.perf_counter() - start) * 1000.0
        return (False, elapsed, str(e))


def run_diagnostics(check_network: bool = True) -> DoctorReport:
    """执行全系统体检，生成结构化报告。"""
    report = DoctorReport()
    settings = get_settings()

    # 1. Python 环境检测
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    is_venv = hasattr(sys, "real_prefix") or (
        hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix
    )
    report.checks.append(
        DiagnosticCheck(
            name="Python 运行环境",
            category="python",
            status="ok",
            message=f"Python {py_ver} ({'虚拟环境 venv' if is_venv else '系统全局环境'})",
            detail=f"解释器路径: {sys.executable}",
        )
    )

    # 2. 存储与 SQLite 向量库读写检测
    try:
        data_dir = _user_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        config_dir = _user_config_dir()
        config_dir.mkdir(parents=True, exist_ok=True)

        test_db = data_dir / "_doctor_test.db"
        con = sqlite3.connect(test_db)
        con.execute("CREATE TABLE _t (id INT, val TEXT)")
        con.execute("INSERT INTO _t VALUES (1, 'ok')")
        con.commit()
        con.close()
        test_db.unlink(missing_ok=True)

        report.checks.append(
            DiagnosticCheck(
                name="本地存储读写",
                category="storage",
                status="ok",
                message="用户数据与配置目录读写正常",
                detail=f"数据目录: {data_dir}\n配置目录: {config_dir}",
            )
        )
    except Exception as e:  # noqa: BLE001
        report.checks.append(
            DiagnosticCheck(
                name="本地存储读写",
                category="storage",
                status="error",
                message=f"目录读写失败: {e}",
                fix_suggestion="请检查用户主目录或 %LOCALAPPDATA% 目录权限。",
            )
        )

    # 3. 嵌入模型与缓存检测
    cached = is_model_cached()
    if cached:
        report.checks.append(
            DiagnosticCheck(
                name="嵌入模型 (FastEmbed ONNX)",
                category="embedder",
                status="ok",
                message=f"模型 [{settings.embed_model}] 已就绪（本地缓存已命中）",
                detail=f"缓存路径: {settings.embed_cache_dir}",
            )
        )
    else:
        report.checks.append(
            DiagnosticCheck(
                name="嵌入模型 (FastEmbed ONNX)",
                category="embedder",
                status="warning",
                message=f"模型 [{settings.embed_model}] 尚未下载（将在首次搜索/导入时自动拉取，~35MB-90MB）",
                detail=f"缓存路径: {settings.embed_cache_dir}",
                fix_suggestion=f"可提前运行 `doc2mind model download {settings.embed_model}` 预下载。",
            )
        )

    # 4. 硬件与 GPU 加速检测
    gpu_diag = get_gpu_diagnosis()
    providers = get_embed_providers()
    if gpu_diag.get("gpu_available", False):
        report.checks.append(
            DiagnosticCheck(
                name="GPU 硬件加速",
                category="hardware",
                status="ok",
                message=f"已启用 GPU 加速 ({gpu_diag.get('gpu_provider')})",
                detail=f"显卡: {gpu_diag.get('gpu_name', 'NVIDIA')}\n可用 Provider: {', '.join(providers)}",
            )
        )
    elif gpu_diag.get("has_nvidia_gpu", False):
        report.checks.append(
            DiagnosticCheck(
                name="GPU 硬件加速",
                category="hardware",
                status="info",
                message=f"检测到 NVIDIA 显卡 ({gpu_diag.get('gpu_name')})，当前运行在 CPU 模式",
                detail=f"推荐安装方案: {gpu_diag.get('recommended_path', 'cuda12')}",
                fix_suggestion="如需开启 GPU 提速，可到【设置 → 环境自检】勾选一键安装 GPU 扩展包。",
            )
        )
    else:
        report.checks.append(
            DiagnosticCheck(
                name="硬件推理模式",
                category="hardware",
                status="ok",
                message="CPU 纯本地推理模式（轻量稳定，占用内存 < 150MB）",
                detail=f"可用 Provider: {', '.join(providers)}",
            )
        )

    # 5. 扩展能力检测（OCR & PDF）
    ocr_ok = _ocr_available()
    if ocr_ok:
        report.checks.append(
            DiagnosticCheck(
                name="OCR 图片文字识别",
                category="extension",
                status="ok",
                message="PaddleOCR 引擎已安装就绪",
            )
        )
    else:
        report.checks.append(
            DiagnosticCheck(
                name="OCR 图片文字识别",
                category="extension",
                status="info",
                message="未安装 PaddleOCR（扫描件与图片文字识别可选扩展）",
                fix_suggestion="若需提取图片/扫描件文字，可在设置页安装 OCR 扩展。",
            )
        )

    # 6. 大模型配置检测
    if settings.llm_provider and settings.llm_provider != "none":
        has_key = bool(settings.llm_api_key or os.getenv("DOC2MIND_LLM_API_KEY"))
        if settings.llm_provider == "ollama":
            report.checks.append(
                DiagnosticCheck(
                    name="大模型 (LLM) 配置",
                    category="llm",
                    status="ok",
                    message=f"已配置本地 Ollama (模型: {settings.llm_model or '默认'})",
                    detail=f"地址: {settings.llm_base_url or 'http://localhost:11434'}",
                )
            )
        elif has_key:
            report.checks.append(
                DiagnosticCheck(
                    name="大模型 (LLM) 配置",
                    category="llm",
                    status="ok",
                    message=f"已配置 {settings.llm_provider.upper()} API (模型: {settings.llm_model or '默认'})",
                    detail=f"Base URL: {settings.llm_base_url or '默认地址'}",
                )
            )
        else:
            report.checks.append(
                DiagnosticCheck(
                    name="大模型 (LLM) 配置",
                    category="llm",
                    status="warning",
                    message=f"已选择 {settings.llm_provider}，但尚未配置 API Key",
                    fix_suggestion="请到【设置 → 大模型对话】填写 API Key 并点击保存。",
                )
            )
    else:
        report.checks.append(
            DiagnosticCheck(
                name="大模型 (LLM) 配置",
                category="llm",
                status="info",
                message="尚未配置大模型（知识检索与格式转换仍可正常使用，AI 总结对话需配置）",
                fix_suggestion="到【设置 → 大模型对话】可一键选择 DeepSeek / 硅基流动 / 通义千问 / Ollama 等预设。",
            )
        )

    # 7. 网络镜像连通性测试 (可选)
    if check_network:
        # 清华 pip 镜像
        pip_ok, pip_ms, pip_msg = _check_network_url("https://pypi.tuna.tsinghua.edu.cn/simple", 3.0)
        report.checks.append(
            DiagnosticCheck(
                name="清华 Pip 软件镜像",
                category="network",
                status="ok" if pip_ok else "warning",
                message=f"连通性: {'良好' if pip_ok else '不可达'} ({pip_msg})",
                detail="https://pypi.tuna.tsinghua.edu.cn/simple",
                fix_suggestion=None if pip_ok else "请检查本地网络或网络代理。",
            )
        )

        # HF 模型镜像
        hf_ok, hf_ms, hf_msg = _check_network_url("https://hf-mirror.com", 3.0)
        report.checks.append(
            DiagnosticCheck(
                name="HuggingFace 模型镜像 (hf-mirror.com)",
                category="network",
                status="ok" if hf_ok else "warning",
                message=f"连通性: {'良好' if hf_ok else '不可达'} ({hf_msg})",
                detail="https://hf-mirror.com",
                fix_suggestion=None if hf_ok else "若下载模型超时，可设置环境变量 DOC2MIND_HF_ENDPOINT 或手动放置模型文件。",
            )
        )

    # 汇总评分与状态
    errors = sum(1 for c in report.checks if c.status == "error")
    warnings = sum(1 for c in report.checks if c.status == "warning")
    if errors > 0:
        report.overall_status = "error"
        report.score = max(30, 100 - errors * 30 - warnings * 10)
        report.summary = f"检测到 {errors} 个阻断项，请根据修复建议调整环境。"
    elif warnings > 0:
        report.overall_status = "warning"
        report.score = max(70, 100 - warnings * 10)
        report.summary = f"系统核心功能正常，有 {warnings} 个优化项建议完善。"
    else:
        report.overall_status = "ok"
        report.score = 100
        report.summary = "系统环境完美就绪，所有核心组件均处于最佳工作状态！"

    return report
