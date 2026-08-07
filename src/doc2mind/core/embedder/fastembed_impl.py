"""fastembed 嵌入实现 — ONNX 本地嵌入（core，默认）。

模型：`BAAI/bge-small-zh-v1.5`（~35MB，首次自动下载到用户缓存目录）
特性：
- 默认 CPU 推理，无需 PyTorch / CUDA
- 若检测到可用 GPU（CUDA / DirectML），自动切换 ONNX GPU provider 加速
- 内置 tokenizer（tokenizers 库）
- 自动归一化（bge 系列需 `query:` / `passage:` 前缀，但 small-zh-v1.5 不强制）

性能基准（i5-1240P，CPU）：
- bge-small-zh-v1.5：~200 texts/s，512 维
- bge-base-zh-v1.5：~80 texts/s，768 维
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Iterator, Sequence

from doc2mind.core.chunker.base import Chunk
from doc2mind.core.config import Settings
from doc2mind.core.embedder.base import Embedder, EmbedderError

logger = logging.getLogger("doc2mind.embedder.fastembed")


# 已知模型 → 维度映射（避免每次都构造 embedder 探测）
_MODEL_DIM: dict[str, int] = {
    "BAAI/bge-small-en-v1.5": 384,
    "BAAI/bge-small-zh-v1.5": 512,
    "BAAI/bge-base-en-v1.5": 768,
    "BAAI/bge-base-zh-v1.5": 768,
    "BAAI/bge-large-en-v1.5": 1024,
    "BAAI/bge-large-zh-v1.5": 1024,
    "sentence-transformers/all-MiniLM-L6-v2": 384,
    "nomic-ai/nomic-embed-text-v1": 768,
}


def _select_providers() -> list[str] | None:
    """按优先级挑选可用的 ONNX Runtime provider。

    优先级：CUDA（NVIDIA 独立显卡，且 cu13 运行时就绪）→ DirectML
    （Windows 无 CUDA 工具链时的 GPU 路径）→ 默认 CPU。只返回当前
    onnxruntime 实际可用的 provider，避免把不可用的 provider 传给
    fastembed 导致启动失败。

    Windows 注意：onnxruntime-gpu 1.28 是 CUDA 13 构建。若环境只有 cu12
    运行时（如与 paddle cu12 共存），盲目注册 nvidia/*/bin 并启用 CUDA
    会让 onnxruntime 加载到错误版本的 cudnn，在 C 层直接崩溃（进程退出、
    Python 无法捕获），比"找不到 cudnn 抛异常回退 CPU"更糟。
    因此这里先预检 cu13 运行时是否就绪，缺失则不用 CUDA。
    """
    try:
        import onnxruntime as ort
    except ImportError:
        return None

    try:
        available = set(ort.get_available_providers())
    except Exception:  # noqa: BLE001 — 探测失败一律回退 CPU
        return None

    if "CUDAExecutionProvider" in available:
        from doc2mind.core.nvidia_runtime import (
            cuda_runtime_ready,
            register_nvidia_dll_dirs,
        )

        if cuda_runtime_ready():
            # 运行时就绪才注册 nvidia DLL 目录（让 onnxruntime 找得到 cu13 cudnn）
            register_nvidia_dll_dirs()
            logger.info("嵌入推理使用 GPU provider: CUDAExecutionProvider")
            return ["CUDAExecutionProvider"]
        logger.warning(
            "检测到 onnxruntime 需要 CUDA 13 运行时，但未找到 cudart64_13.dll，"
            "嵌入回退 CPU。安装 nvidia-cublas-cu13 / nvidia-cuda-runtime-cu13 "
            "后可启用 GPU。"
        )
    if "DmlExecutionProvider" in available:
        logger.info("嵌入推理使用 GPU provider: DmlExecutionProvider")
        return ["DmlExecutionProvider"]
    return None


# 国内网络直连 HuggingFace 常超时，下载失败后自动用该镜像重试一次
HF_MIRROR = "https://hf-mirror.com"


def _is_download_error(e: Exception) -> bool:
    """粗判异常是否属于"模型下载/网络"类错误（用于触发镜像重试与引导提示）。"""
    msg = str(e).lower()
    markers = (
        "connecttimeout", "connect timeout", "connectionerror",
        "connection timed out", "connection reset", "connection aborted",
        "winerror 10060", "winerror 10061", "winerror 10054",
        "timed out", "timeout", "failed to resolve", "could not resolve",
        "cannot resolve", "socket", "tls", "ssl", "network",
        "remote end closed", "resolve host", "repository not found",
        "404 client error", "client error", "eof",
    )
    return any(m in msg for m in markers)


def _download_error_message(model: str, err: Exception) -> str:
    """把模型下载失败转成新手可操作的中文提示（而不是原始网络异常堆栈）。"""
    from doc2mind.core.config import get_settings

    cache_dir = get_settings().embed_cache_dir
    return (
        f"嵌入模型 {model} 下载失败（首次使用需联网下载约 90MB）。\n"
        f"原始错误：{err}\n"
        "解决办法（按顺序尝试）：\n"
        "  1. 确认网络可用；程序默认从 hf-mirror.com 镜像下载，\n"
        "     若镜像也不通，可换用其他镜像：\n"
        "     命令提示符(cmd)执行：set DOC2MIND_HF_ENDPOINT=https://hf-mirror.com\n"
        "     PowerShell 执行：  $env:DOC2MIND_HF_ENDPOINT=\"https://hf-mirror.com\"\n"
        "     然后重新启动 DocMind 或重跑命令。\n"
        "  2. 如开了代理/VPN，请检查是否拦截了下载连接。\n"
        f"  3. 模型缓存目录：{cache_dir}，\n"
        "     可把该目录从网络正常的机器整体拷贝过来（保留目录结构）。"
    )


def is_model_cached() -> bool:
    """嵌入模型是否已下载到本地缓存（供启动时的首启提示判断）。"""
    from doc2mind.core.config import get_settings

    cache_dir = get_settings().embed_cache_dir
    if not cache_dir.is_dir():
        return False
    for pkg in cache_dir.glob("models--*"):
        snap = pkg / "snapshots"
        if not snap.is_dir():
            continue
        for rev in snap.iterdir():
            if not rev.is_dir():
                continue
            for fname in ("model_optimized.onnx", "model.onnx"):
                if (rev / fname).is_file():
                    return True
    return False


def first_run_hint() -> str:
    """首次使用引导：模型未缓存时提示下载与镜像配置（新手友好）。

    返回空字符串表示模型已就绪、无需提示；否则返回一段可打印的中文提示。
    """
    if is_model_cached():
        return ""
    from doc2mind.core.config import get_settings

    model = get_settings().embed_model
    return (
        f"嵌入模型 {model} 尚未下载（首次使用需联网下载约 90MB），"
        "程序会自动从镜像 hf-mirror.com 下载，请保持联网并稍候。\n"
        "如果下载失败，请设置环境变量 "
        "DOC2MIND_HF_ENDPOINT=https://hf-mirror.com 后重试。"
    )


class FastEmbedEmbedder(Embedder):
    """fastembed ONNX 嵌入实现。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._impl = None  # 惰性初始化
        self._model = settings.embed_model
        self._dim = settings.embed_dim
        self._providers = _select_providers()

    # --- 惰性加载 ---
    def _ensure_loaded(self) -> None:
        """首次调用时加载模型（避免导入时下模型）。"""
        if self._impl is not None:
            return

        # 镜像/端点必须在 import fastembed 之前设置：
        # huggingface_hub 在导入时就缓存 HF_ENDPOINT，事后改环境变量不生效。
        # 优先级：系统环境变量 HF_ENDPOINT > 配置 DOC2MIND_HF_ENDPOINT > 默认镜像。
        if not os.environ.get("HF_ENDPOINT"):
            os.environ["HF_ENDPOINT"] = self.settings.hf_endpoint or HF_MIRROR

        try:
            from fastembed import TextEmbedding
        except ImportError as e:
            raise EmbedderError(
                "fastembed 未安装。请运行：pip install fastembed"
            ) from e

        kwargs: dict[str, object] = {
            "model_name": self._model,
            "max_length": 512,
            "cache_dir": str(self.settings.embed_cache_dir),
        }

        # 本地模型目录：fastembed 的 specific_model_path 直接使用该目录的
        # ONNX + tokenizer 文件，跳过网络下载（model_name 仍须是内置名，
        # 决定 model_file 文件名与 tokenizer 结构）。
        if self.settings.embed_model_path:
            local_dir = Path(self.settings.embed_model_path)
            if not local_dir.is_dir():
                raise EmbedderError(
                    f"本地模型目录不存在: {self.settings.embed_model_path}"
                )
            kwargs["specific_model_path"] = str(local_dir)
            logger.info("嵌入使用本地模型目录: %s", local_dir)

        try:
            self._load_impl(TextEmbedding, kwargs)
        except Exception as e:  # noqa: BLE001
            # 模型下载/加载失败：再试一次（覆盖瞬时网络抖动）
            if _is_download_error(e):
                logger.warning("嵌入模型下载/加载失败，重试一次: %s", e)
                try:
                    self._load_impl(TextEmbedding, kwargs)
                except Exception as e2:  # noqa: BLE001
                    raise EmbedderError(_download_error_message(self._model, e2)) from e2
            else:
                raise EmbedderError(f"加载 fastembed 模型失败 ({self._model}): {e}") from e

    def _load_impl(self, TextEmbedding, kwargs: dict[str, object]) -> None:
        """构造 TextEmbedding 并 probe 验证：GPU 可用则 GPU，否则回退 CPU。"""
        providers = self._providers or ["CPUExecutionProvider"]
        kwargs = dict(kwargs)
        kwargs["providers"] = providers
        try:
            self._impl = TextEmbedding(**kwargs)
            # 用 probe 实际跑一次推理，验证 provider 真正可用
            probe = next(self._impl.embed(["probe"]))
        except Exception as gpu_err:  # noqa: BLE001
            if providers != ["CPUExecutionProvider"]:
                logger.warning(
                    "GPU 嵌入推理失败 (%s)，回退 CPU: %s",
                    providers, gpu_err,
                )
                kwargs["providers"] = ["CPUExecutionProvider"]
                self._impl = TextEmbedding(**kwargs)
                probe = next(self._impl.embed(["probe"]))
            else:
                raise
        # 探测真实维度
        self._dim = int(probe.shape[0])
        _MODEL_DIM[self._model] = self._dim
        logger.info(
            "fastembed 加载完成: model=%s dim=%d providers=%s",
            self._model, self._dim,
            kwargs["providers"],
        )

    # --- 公开属性 ---
    @property
    def dimension(self) -> int:
        """向量维度。"""
        return self._dim

    @property
    def model_name(self) -> str:
        """模型名。"""
        return self._model

    # --- 嵌入方法 ---
    def embed(self, chunks: Sequence[Chunk]) -> Iterator:
        """批量嵌入，逐批 yield 向量。"""
        self._ensure_loaded()
        texts = [c.content for c in chunks]
        try:
            yield from self._impl.embed(texts, batch_size=self.settings.embed_batch_size)
        except Exception as e:  # noqa: BLE001
            raise EmbedderError(f"嵌入失败: {e}") from e

    def embed_query(self, text: str) -> "object":
        """嵌入单条查询。"""
        self._ensure_loaded()
        try:
            return next(self._impl.query_embed([text]))
        except Exception as e:  # noqa: BLE001
            raise EmbedderError(f"查询嵌入失败: {e}") from e

    def embed_texts(self, texts: Sequence[str]) -> Iterator:
        """嵌入纯文本列表（重建索引用）。"""
        self._ensure_loaded()
        try:
            yield from self._impl.embed(list(texts), batch_size=self.settings.embed_batch_size)
        except Exception as e:  # noqa: BLE001
            raise EmbedderError(f"嵌入失败: {e}") from e


def get_model_dim(model_name: str) -> int | None:
    """查询已知模型的维度。"""
    return _MODEL_DIM.get(model_name)
