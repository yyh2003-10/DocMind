"""机型档位模型整套搭配（bundle）— 按硬件自动匹配 嵌入 + 重排 + 本地 LLM 三件套。

原则：
1. **推荐永不依赖本机已有环境**：干净电脑（无 Ollama / 无 GGUF / 无 GPU）也能拿到
   完整 5 档推荐；服务探测（local_ai_detect）只在推荐条目上叠加"就绪"标记。
2. 只收录当前 fastembed 版本真实支持的嵌入模型（与 embedder/catalog.py 对齐），
   BGE-M3 在 fastembed 0.8 不受支持，故不采用。
3. 纯函数可单测：detect_tier 的硬件探测结果可通过参数注入。
4. bundle 带 version 字段，后续加档位只改本文件，前端零改动。

档位选型依据（魔搭社区 GGUF 仓库 + 低配实测数据，2025-2026）：
- 极轻量：Qwen3-1.7B Q4_K_M（~1.1GB，纯 CPU 25-40 tok/s），关闭重排（cross-encoder
  在无 GPU 机器上每次问答慢数秒，得不偿失）。
- 轻量：Qwen3-4B / Phi-4-mini（8GB 内存 CPU 甜点位）。
- 主流：Qwen3-8B（6-8GB 显存甜点位，如 RTX 2060/3060）。
- 进阶：Qwen3-14B（12-16GB 显存），嵌入升级 jina-zh（中文检索质量高于 bge-small）。
- 旗舰：Qwen3-32B / 30B-A3B MoE（24GB+ 显存），嵌入 multilingual-e5-large（目录内最强）。
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ChatModelOption:
    """一个档位下的本地对话 LLM 选项。"""

    model_id: str          # Ollama 模型名（如 qwen3:8b）
    display_name: str
    size_gb: float         # Q4_K_M 量化后磁盘占用（约等于显存/内存需求下限）
    pull_command: str      # 魔搭加速源拉取命令（国内下载快）
    recommended: bool = False  # 档位内首选
    note: str = ""


@dataclass(frozen=True)
class ModelBundle:
    """一个机型档位的整套模型搭配。"""

    tier: str              # tier id：minimal / light / mainstream / advanced / flagship
    display_name: str      # 档位中文名
    min_vram_gb: int       # 判定用：独立显存下限（纯 CPU 用内存判）
    min_ram_gb: int        # 判定用：内存下限（无独显时）
    embed_model: str       # 必须在 embedder/catalog.py 支持列表内
    rerank_model: str      # bge-reranker-base（fastembed TextCrossEncoder 支持）
    rerank_enabled: bool   # 极轻量档关闭重排
    chat_models: tuple[ChatModelOption, ...] = field(default_factory=tuple)
    description: str = ""


_OLLAMA_INSTALL_URL = "https://ollama.com/download"


def _pull(repo: str) -> str:
    """魔搭加速源的 ollama 拉取命令（国内带宽友好）。"""
    return f"ollama pull modelscope.cn/{repo}"


# ===== 5 档 bundle 定义（持续维护；加档位只改这里） =====
BUNDLE_VERSION = 1

MODEL_BUNDLES: tuple[ModelBundle, ...] = (
    ModelBundle(
        tier="minimal",
        display_name="极轻量（纯 CPU / 老机器）",
        min_vram_gb=0,
        min_ram_gb=4,
        embed_model="BAAI/bge-small-zh-v1.5",
        rerank_model="",
        rerank_enabled=False,  # cross-encoder 重排在无 GPU 机器上得不偿失
        chat_models=(
            ChatModelOption(
                model_id="qwen3:1.7b",
                display_name="Qwen3-1.7B (Q4_K_M)",
                size_gb=1.4,
                pull_command=_pull("Qwen/Qwen3-1.7B-GGUF"),
                recommended=True,
                note="纯 CPU 25-40 tok/s，1.1GB 内存即可跑",
            ),
            ChatModelOption(
                model_id="qwen3:0.6b",
                display_name="Qwen3-0.6B (Q4_K_M)",
                size_gb=0.6,
                pull_command=_pull("Qwen/Qwen3-0.6B-GGUF"),
                note="应急项：质量偏弱，仅内存极紧张时使用",
            ),
        ),
        description="4GB 内存 / 无独显老电脑：关闭重排换流畅度，1.7B 模型纯 CPU 可用",
    ),
    ModelBundle(
        tier="light",
        display_name="轻量（4-8GB 显存 / 8GB 内存）",
        min_vram_gb=4,
        min_ram_gb=8,
        embed_model="BAAI/bge-small-zh-v1.5",
        rerank_model="BAAI/bge-reranker-base",
        rerank_enabled=True,
        chat_models=(
            ChatModelOption(
                model_id="qwen3:4b",
                display_name="Qwen3-4B (Q4_K_M)",
                size_gb=2.6,
                pull_command=_pull("Qwen/Qwen3-4B-GGUF"),
                recommended=True,
                note="有 iGPU/入门独显时 12-20 tok/s",
            ),
            ChatModelOption(
                model_id="phi4-mini:3.8b",
                display_name="Phi-4-mini 3.8B (Q4_K_M)",
                size_gb=2.8,
                pull_command=_pull("microsoft/Phi-4-mini-instruct-GGUF"),
                note="纯 CPU 甜点位：15-25 tok/s，编码与推理更强",
            ),
        ),
        description="入门独显或 8GB 内存：4B 模型 + 轻量重排，均衡起步",
    ),
    ModelBundle(
        tier="mainstream",
        display_name="主流（6-8GB 显存）",
        min_vram_gb=6,
        min_ram_gb=16,
        embed_model="BAAI/bge-small-zh-v1.5",
        rerank_model="BAAI/bge-reranker-base",
        rerank_enabled=True,
        chat_models=(
            ChatModelOption(
                model_id="qwen3:8b",
                display_name="Qwen3-8B (Q4_K_M)",
                size_gb=5.2,
                pull_command=_pull("Qwen/Qwen3-8B-GGUF"),
                recommended=True,
                note="RTX 2060/3060/4060 显存内全速运行",
            ),
        ),
        description="主流游戏显卡：8B 模型本地全速，质量与速度最佳平衡",
    ),
    ModelBundle(
        tier="advanced",
        display_name="进阶（12-16GB 显存）",
        min_vram_gb=12,
        min_ram_gb=32,
        embed_model="jinaai/jina-embeddings-v2-base-zh",
        rerank_model="BAAI/bge-reranker-base",
        rerank_enabled=True,
        chat_models=(
            ChatModelOption(
                model_id="qwen3:14b",
                display_name="Qwen3-14B (Q4_K_M)",
                size_gb=9.0,
                pull_command=_pull("Qwen/Qwen3-14B-GGUF"),
                recommended=True,
                note="RTX 3060-12G / 4070 显存内运行",
            ),
        ),
        description="大显存显卡：14B 模型 + jina-zh 嵌入（中文检索质量升级，需重建索引）",
    ),
    ModelBundle(
        tier="flagship",
        display_name="旗舰（24GB+ 显存）",
        min_vram_gb=24,
        min_ram_gb=48,
        embed_model="intfloat/multilingual-e5-large",
        rerank_model="BAAI/bge-reranker-base",
        rerank_enabled=True,
        chat_models=(
            ChatModelOption(
                model_id="qwen3:30b-a3b",
                display_name="Qwen3-30B-A3B (Q4_K_M, MoE)",
                size_gb=18.6,
                pull_command=_pull("unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF"),
                recommended=True,
                note="MoE 架构激活 3B 参数，速度接近 8B、质量接近 32B，24G 显存实测最佳",
            ),
            ChatModelOption(
                model_id="qwen3:32b",
                display_name="Qwen3-32B (Q4_K_M)",
                size_gb=19.8,
                pull_command=_pull("Qwen/Qwen3-32B-GGUF"),
                note="稠密模型，质量最强但更慢",
            ),
        ),
        description="旗舰显卡（3090/4090）：30B-A3B MoE + e5-large 嵌入，本地满血体验",
    ),
)


def get_bundle(tier: str) -> ModelBundle | None:
    """按档位 id 查 bundle。"""
    for b in MODEL_BUNDLES:
        if b.tier == tier:
            return b
    return None


# ===== 硬件探测与档位判定 =====
# 降级链：nvidia-smi（独立显存）→ wmic（内存 + GPU 名）→ 全失败取最保守档。
# 探测函数均为纯包装，探测结果可作为参数注入（单测不依赖真机）。


def _query_nvidia_smi() -> tuple[int, str] | None:
    """nvidia-smi 查最大独立显存 (MB) 与 GPU 名；失败返回 None。"""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total,name", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return None
        best: tuple[int, str] | None = None
        for line in out.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 2:
                continue
            try:
                mb = int(re.sub(r"\D", "", parts[0]) or 0)
            except ValueError:
                continue
            if mb > 0 and (best is None or mb > best[0]):
                best = (mb, parts[1])
        return best
    except Exception:  # noqa: BLE001 — 无 nvidia-smi / 无驱动 / 超时都算探测失败
        return None


def _query_wmi() -> dict[str, object] | None:
    """wmic 查物理内存 (GB) 与显卡名（无 NVIDIA 卡 / 未装驱动的降级路径）。"""
    result: dict[str, object] = {}
    try:
        out = subprocess.run(
            ["wmic", "computersystem", "get", "TotalPhysicalMemory", "/value"],
            capture_output=True, text=True, timeout=8,
        )
        m = re.search(r"TotalPhysicalMemory=(\d+)", out.stdout)
        if m:
            result["ram_gb"] = int(m.group(1)) / (1024**3)
    except Exception:  # noqa: BLE001
        pass
    try:
        out = subprocess.run(
            ["wmic", "path", "win32_VideoController", "get", "Name", "/value"],
            capture_output=True, text=True, timeout=8,
        )
        names = [v.strip() for v in re.findall(r"Name=(.+)", out.stdout) if v.strip()]
        if names:
            result["gpu_name"] = names[0]
    except Exception:  # noqa: BLE001
        pass
    return result or None


def pick_tier(
    vram_gb: float | None = None,
    ram_gb: float | None = None,
) -> ModelBundle:
    """按显存/内存判定档位（纯函数，探测值注入；两者皆 None 取最保守档）。

    规则：显存达标优先（档位按 min_vram_gb 从高到低），显存不足或无独显时
    按内存判（排除旗舰/进阶/主流这类明确标注显存需求的档）。
    """
    candidates = list(MODEL_BUNDLES)
    if vram_gb is not None and vram_gb >= 4:
        # 有可用独显：按显存从高到低选第一个达标的档
        for b in sorted(candidates, key=lambda x: x.min_vram_gb, reverse=True):
            if b.min_vram_gb > 0 and vram_gb >= b.min_vram_gb:
                return b
    if ram_gb is not None:
        # 无独显或显存不足：按内存选（只看 min_vram_gb<=4 的档 + 内存门槛）
        for b in sorted(candidates, key=lambda x: x.min_ram_gb, reverse=True):
            if b.min_vram_gb <= 4 and ram_gb >= b.min_ram_gb:
                return b
    return get_bundle("minimal")  # 最保守兜底，永不返回空


def detect_tier() -> dict[str, object]:
    """探测本机硬件并返回档位判定结果（含探测来源，供 UI 展示）。

    返回 {tier, tier_name, vram_gb, ram_gb, gpu_name, source}。
    source: nvidia-smi / wmic / fallback。
    """
    nvidia = _query_nvidia_smi()
    if nvidia is not None:
        vram_gb = nvidia[0] / 1024
        bundle = pick_tier(vram_gb=vram_gb)
        return {
            "tier": bundle.tier,
            "tier_name": bundle.display_name,
            "vram_gb": round(vram_gb, 1),
            "ram_gb": None,
            "gpu_name": nvidia[1],
            "source": "nvidia-smi",
        }

    wmi = _query_wmi()
    if wmi:
        ram_gb = wmi.get("ram_gb")
        gpu_name = wmi.get("gpu_name")
        bundle = pick_tier(vram_gb=None, ram_gb=ram_gb if isinstance(ram_gb, float) else None)
        return {
            "tier": bundle.tier,
            "tier_name": bundle.display_name,
            "vram_gb": None,
            "ram_gb": round(ram_gb, 1) if isinstance(ram_gb, float) else None,
            "gpu_name": gpu_name if isinstance(gpu_name, str) else None,
            "source": "wmic",
        }

    bundle = get_bundle("minimal")
    return {
        "tier": bundle.tier,
        "tier_name": bundle.display_name,
        "vram_gb": None,
        "ram_gb": None,
        "gpu_name": None,
        "source": "fallback",
    }
