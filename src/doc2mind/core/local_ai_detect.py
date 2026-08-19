"""本地 AI 环境与大模型服务智能探测（Ollama / LM Studio / 本地 GGUF 模型资产）。

用于在设置页为用户提供「一键免配置绑定本地最佳方案」的零门槛体验。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx


async def detect_ollama(base_url: str = "http://127.0.0.1:11434") -> dict[str, Any]:
    """探测本地 Ollama 服务及其可用模型。"""
    url = f"{base_url.rstrip('/')}/api/tags"
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                models = []
                chat_models = []
                embed_models = []
                for m in data.get("models", []):
                    name = m.get("name", "")
                    size_gb = round(m.get("size", 0) / (1024**3), 2)
                    model_item = {"name": name, "size_gb": size_gb}
                    models.append(model_item)
                    if "embed" in name.lower() or "bge" in name.lower():
                        embed_models.append(name)
                    else:
                        chat_models.append(name)
                return {
                    "running": True,
                    "base_url": base_url,
                    "models": models,
                    "chat_models": chat_models,
                    "embed_models": embed_models,
                    "default_chat_model": chat_models[0] if chat_models else (models[0]["name"] if models else "llama3.2"),
                    "default_embed_model": embed_models[0] if embed_models else None,
                }
    except Exception:
        pass
    return {
        "running": False,
        "base_url": base_url,
        "models": [],
        "chat_models": [],
        "embed_models": [],
        "default_chat_model": None,
        "default_embed_model": None,
    }


async def detect_lm_studio(base_url: str = "http://127.0.0.1:1234/v1") -> dict[str, Any]:
    """探测本地 LM Studio 服务及其当前加载/可用模型。"""
    url = f"{base_url.rstrip('/')}/models"
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                models = []
                for m in data.get("data", []):
                    model_id = m.get("id", "")
                    if model_id:
                        models.append({"name": model_id, "size_gb": 0.0})
                return {
                    "running": True,
                    "base_url": base_url,
                    "models": models,
                    "default_chat_model": models[0]["name"] if models else None,
                }
    except Exception:
        pass
    return {
        "running": False,
        "base_url": base_url,
        "models": [],
        "default_chat_model": None,
    }


def scan_local_gguf_models() -> list[dict[str, Any]]:
    """快速扫描本地常见目录下的 GGUF 模型文件。"""
    candidate_roots: list[Path] = [
        Path(r"F:\models"),
        Path(r"F:\llama"),
        Path(r"E:\models"),
        Path(r"D:\models"),
        Path(r"C:\models"),
        Path.home() / ".cache" / "lm-studio" / "models",
    ]

    found: list[dict[str, Any]] = []
    seen_paths: set[str] = set()

    for root in candidate_roots:
        if not root.exists() or not root.is_dir():
            continue
        try:
            # 扫描最多 3 层子目录
            for path in root.rglob("*.gguf"):
                path_str = str(path.resolve())
                if path_str in seen_paths:
                    continue
                seen_paths.add(path_str)
                try:
                    sz_gb = round(path.stat().st_size / (1024**3), 2)
                    found.append({
                        "name": path.stem,
                        "filename": path.name,
                        "path": path_str,
                        "size_gb": sz_gb,
                        "dir": str(path.parent.resolve()),
                    })
                except Exception:
                    pass
                if len(found) >= 50:
                    break
        except Exception:
            pass

    return sorted(found, key=lambda x: x["size_gb"], reverse=True)


async def get_local_ai_environment() -> dict[str, Any]:
    """综合探测本地所有 AI 服务与模型资产，产出全套自动配置方案。"""
    ollama_task = detect_ollama()
    lm_studio_task = detect_lm_studio()

    ollama_res, lm_studio_res = await asyncio.gather(ollama_task, lm_studio_task)
    local_ggufs = await asyncio.to_thread(scan_local_gguf_models)

    recommendations: list[dict[str, Any]] = []

    # 1. 若 LM Studio 正在运行
    if lm_studio_res["running"]:
        model_name = lm_studio_res.get("default_chat_model") or "local-model"
        recommendations.append({
            "id": "lm_studio",
            "title": "LM Studio 极速本地方案",
            "provider": "openai",
            "base_url": lm_studio_res["base_url"],
            "api_key": "lm-studio",
            "model": model_name,
            "description": f"已自动连接 LM Studio (端口 1234)，当前模型: {model_name}，RTX 2060 显卡全速加速",
            "badge": "推荐",
        })

    # 2. 若 Ollama 正在运行
    if ollama_res["running"]:
        model_name = ollama_res.get("default_chat_model") or "qwen2.5"
        recommendations.append({
            "id": "ollama",
            "title": "Ollama 一体化本地方案",
            "provider": "ollama",
            "base_url": ollama_res["base_url"],
            "api_key": "",
            "model": model_name,
            "description": f"已自动连接 Ollama (端口 11434)，可用模型: {model_name}，内置 CUDA 引擎加速",
            "badge": "就绪",
        })

    return {
        "ollama": ollama_res,
        "lm_studio": lm_studio_res,
        "local_gguf_models": local_ggufs,
        "local_gguf_count": len(local_ggufs),
        "recommendations": recommendations,
    }
