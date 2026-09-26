"""本地 AI 环境与大模型服务智能探测（Ollama / LM Studio / 本地 GGUF 模型资产）。

用于在设置页为用户提供「一键免配置绑定本地最佳方案」的零门槛体验。
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Any

import httpx

from doc2mind.core.model_bundles import (
    _OLLAMA_INSTALL_URL,
    MODEL_BUNDLES,
    detect_tier,
    get_bundle,
)

# 运行时常驻地址（未探测到运行服务时，"自定义地址"入口给用户的默认值）
OLLAMA_DEFAULT_BASE_URL = "http://127.0.0.1:11434"
LM_STUDIO_DEFAULT_BASE_URL = "http://127.0.0.1:1234/v1"


def _registry_uninstall_location(display_name_matches: tuple[str, ...]) -> str | None:
    """从 Windows 注册表卸载项里找安装位置/主 exe（跨盘符自定义安装路径的唯一可靠来源）。"""
    try:
        import winreg
    except ImportError:
        return None
    hives = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    )
    for hive, sub in hives:
        try:
            key = winreg.OpenKey(hive, sub)
        except OSError:
            continue
        try:
            for i in range(winreg.QueryInfoKey(key)[0]):
                try:
                    entry = winreg.EnumKey(key, i)
                    with winreg.OpenKey(key, entry) as k2:
                        disp = winreg.QueryValueEx(k2, "DisplayName")[0].lower()
                        if not any(n in disp for n in display_name_matches):
                            continue
                        for value_name in ("DisplayIcon", "InstallLocation"):
                            try:
                                raw = winreg.QueryValueEx(k2, value_name)[0]
                            except OSError:
                                continue
                            cand = raw.strip().strip('"')
                            if not cand:
                                continue
                            if os.path.isfile(cand):
                                return cand
                            if os.path.isdir(cand):
                                for name in os.listdir(cand):
                                    if name.lower().endswith(".exe"):
                                        return os.path.join(cand, name)
                except OSError:
                    continue
        finally:
            key.Close()
    return None


def _find_runtime_install(name: str) -> dict[str, Any]:
    """探测运行时是否已安装（不要求服务在跑）。

    探测链：PATH（shutil.which）→ 常见默认安装目录 → Windows 注册表卸载项 →
    用户数据目录启发式（LM Studio 的 ~/.lmstudio；自定义安装路径时路径未知，
    标记 installed 但 path 为空，交给"自定义地址"入口兜底）。

    返回 {installed, path, version}；path 可为 None（已装但位置未定位到）。
    """
    info: dict[str, Any] = {"installed": False, "path": None, "version": None}

    if name == "ollama":
        cli_names = ("ollama",)
        default_dirs = (
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Ollama" / "ollama.exe",
            Path(os.environ.get("ProgramW6432", r"C:\Program Files")) / "Ollama" / "ollama.exe",
        )
        registry_names = ("ollama",)
        data_dir_hint: Path | None = None
    else:  # lm_studio
        cli_names = ("lms",)
        default_dirs = (
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "LM Studio" / "LM Studio.exe",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "LM Studio" / "LM Studio.exe",
            Path(os.environ.get("ProgramW6432", r"C:\Program Files")) / "LM Studio" / "LM Studio.exe",
        )
        registry_names = ("lm studio",)
        data_dir_hint = Path.home() / ".lmstudio"

    # 1. PATH
    for cli in cli_names:
        found = shutil.which(cli)
        if found:
            info["installed"] = True
            info["path"] = found
            break

    # 2. 常见默认目录
    if not info["installed"]:
        for d in default_dirs:
            if d.parent and d.parent.exists() and d.is_file():
                info["installed"] = True
                info["path"] = str(d)
                break

    # 3. 注册表卸载项（自定义盘符安装路径的唯一可靠来源）
    if not info["installed"]:
        reg = _registry_uninstall_location(registry_names)
        if reg:
            info["installed"] = True
            info["path"] = reg

    # 4. 用户数据目录启发式（已装但路径定位不到）
    if not info["installed"] and data_dir_hint is not None and data_dir_hint.is_dir():
        info["installed"] = True
        info["path"] = None

    return info


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


def scan_local_gguf_models(extra_dirs: list[str] | None = None) -> list[dict[str, Any]]:
    """扫描本地常见目录下的 GGUF 模型文件（不写死盘符，干净电脑零误报）。

    候选根：枚举所有存在盘符的 `<盘>:\\models`、`<盘>:\\llama`
    + `~/.cache/lm-studio/models` + 环境变量 DOC2MIND_GGUF_DIRS（分号分隔）追加。
    """
    candidate_roots: list[Path] = []

    # 枚举所有已存在盘符（A-Z），不再写死 F:/E:/D:
    import string
    for letter in string.ascii_uppercase:
        drive = Path(f"{letter}:\\")
        if not drive.exists():
            continue
        candidate_roots.append(drive / "models")
        candidate_roots.append(drive / "llama")

    candidate_roots.append(Path.home() / ".cache" / "lm-studio" / "models")

    # 用户/管理员可用环境变量追加自定义目录（分号分隔）
    extra = extra_dirs or [
        s.strip() for s in os.environ.get("DOC2MIND_GGUF_DIRS", "").split(";") if s.strip()
    ]
    candidate_roots.extend(Path(s) for s in extra)

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
    """综合探测本地所有 AI 服务与模型资产，产出全套自动配置方案。

    核心原则：**推荐永不依赖本机已有环境** —— 即使 Ollama / LM Studio 都不存在
    （干净电脑），也返回完整 5 档 bundle 推荐（全部标 runtime_missing），
    服务探测只在推荐条目上叠加"就绪/模型缺失"标记。
    """
    ollama_task = detect_ollama()
    lm_studio_task = detect_lm_studio()

    ollama_res, lm_studio_res = await asyncio.gather(ollama_task, lm_studio_task)
    local_ggufs = await asyncio.to_thread(scan_local_gguf_models)

    # --- 安装态探测（不要求服务在跑；引导下载前必须先确认是否已装） ---
    ollama_install = await asyncio.to_thread(_find_runtime_install, "ollama")
    lm_studio_install = await asyncio.to_thread(_find_runtime_install, "lm_studio")
    if ollama_res["running"]:
        ollama_install["installed"] = True  # 服务在跑必然已装（兜底 PATH 探测失败的情形）
    if lm_studio_res.get("running"):
        lm_studio_install["installed"] = True
    ollama_res["installed"] = ollama_install["installed"]
    ollama_res["install_path"] = ollama_install["path"]
    ollama_res["version"] = ollama_install["version"]
    lm_studio_res["installed"] = lm_studio_install["installed"]
    lm_studio_res["install_path"] = lm_studio_install["path"]
    lm_studio_res["version"] = lm_studio_install["version"]

    installed_models = set(ollama_res.get("chat_models") or [])
    installed_models.update(lm_studio_res.get("models") and
                            [m["name"] for m in lm_studio_res["models"]] or [])
    installed_lower = {m.lower() for m in installed_models}
    ollama_running = bool(ollama_res["running"])
    lm_running = bool(lm_studio_res.get("running"))

    # --- 档位判定（多级降级，干净电脑也能拿到档位） ---
    tier_info = await asyncio.to_thread(detect_tier)
    current_tier = str(tier_info["tier"])

    # 硬件描述：用探测到的实际硬件，不硬编码型号
    if tier_info.get("gpu_name"):
        hw_desc = f"检测到 {tier_info['gpu_name']}"
        if tier_info.get("vram_gb"):
            hw_desc += f" ({tier_info['vram_gb']}GB 显存)"
    elif tier_info.get("ram_gb"):
        hw_desc = f"纯 CPU 模式（内存 {tier_info['ram_gb']}GB）"
    else:
        hw_desc = "纯 CPU 模式"

    def _bundle_state(b: Any) -> tuple[str, str]:
        """四态：
        ready            运行时在跑且已装该档推荐模型
        model_missing    运行时在跑但该档模型未拉
        installed_stopped 运行时已安装但服务未启动（引导启动，而非下载）
        runtime_missing  运行时未安装（引导下载，附 installer_url）

        匹配规则：模型 tag 归一化（去符号小写）后互为包含即视为同款——
        兼容官方 tag（qwen3:8b）、魔搭 tag（modelscope.cn/Qwen/Qwen3-8B-GGUF:Q4_K_M）
        等不同来源命名；"Qwen38bGGUF" 与 "qwen3:8b" 归一化后互不包含，
        再用较短的归一化串做子串兜底。
        """
        import re as _re

        def _norm(s: str) -> str:
            return _re.sub(r"[^a-z0-9]", "", s.lower())

        if ollama_running or lm_running:
            for opt in b.chat_models:
                nid = _norm(opt.model_id)
                for inst in installed_models:
                    ninst = _norm(inst)
                    if nid and ninst and (nid == ninst or nid in ninst or ninst in nid):
                        return "ready", inst  # 返回实际安装的完整 tag（魔搭/官方源命名都可能）
            return "model_missing", ""
        if ollama_install["installed"] or lm_studio_install["installed"]:
            return "installed_stopped", ""
        return "runtime_missing", ""

    recommendations: list[dict[str, Any]] = []

    # 1. 五档整套搭配（永远全部返回 —— 干净电脑的主路径）
    for b in MODEL_BUNDLES:
        rec_model = b.chat_models[0] if b.chat_models else None
        state, installed_tag = _bundle_state(b)
        is_current = b.tier == current_tier
        # 已装未启动时给出启动指引文本，避免误导用户去下载
        if state == "installed_stopped":
            start_hint = []
            if ollama_install["installed"]:
                start_hint.append("Ollama（未启动）")
            if lm_studio_install["installed"]:
                start_hint.append("LM Studio（未启动）")
            stop_hint = "已检测到本机安装 " + " / ".join(start_hint) + "，启动并开启 Local Server 后点「重新探测」，无需下载"
        else:
            stop_hint = ""
        recommendations.append({
            "id": f"bundle_{b.tier}",
            "kind": "bundle",
            "title": b.display_name,
            "tier": b.tier,
            "is_current_tier": is_current,
            "state": state,
            "provider": "openai",
            "base_url": ollama_res["base_url"] if ollama_running else OLLAMA_DEFAULT_BASE_URL,
            "api_key": "",
            "model": installed_tag if (state == "ready" and installed_tag) else (rec_model.model_id if rec_model else ""),
            "embed_model": b.embed_model,
            "rerank_model": b.rerank_model,
            "rerank_enabled": b.rerank_enabled,
            "pull_command": rec_model.pull_command if rec_model else "",
            "installer_url": _OLLAMA_INSTALL_URL if state == "runtime_missing" else "",
            "install_hint": stop_hint,
            "chat_options": [
                {
                    "model_id": opt.model_id,
                    "display_name": opt.display_name,
                    "size_gb": opt.size_gb,
                    "pull_command": opt.pull_command,
                    "recommended": opt.recommended,
                    "note": opt.note,
                }
                for opt in b.chat_models
            ],
            "description": b.description + (f"（{hw_desc}）" if is_current else ""),
            "badge": "当前档位" if is_current else ("就绪" if state == "ready" else ("需拉模型" if state == "model_missing" else ("已装未启动" if state == "installed_stopped" else ""))),
        })

    # 2. 若 LM Studio 正在运行：叠加直连当前已加载模型的快捷条目
    if lm_running:
        model_name = lm_studio_res.get("default_chat_model") or "local-model"
        recommendations.append({
            "id": "lm_studio",
            "kind": "service",
            "title": "LM Studio 极速本地方案",
            "provider": "openai",
            "base_url": lm_studio_res["base_url"],
            "api_key": "lm-studio",
            "model": model_name,
            "description": f"已自动连接 LM Studio (端口 1234)，当前模型: {model_name}",
            "badge": "就绪",
        })

    # 3. 若 Ollama 正在运行：叠加直连当前模型的快捷条目
    if ollama_running:
        model_name = ollama_res.get("default_chat_model") or "qwen2.5"
        recommendations.append({
            "id": "ollama",
            "kind": "service",
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
        "tier": tier_info,
        "bundle_version": get_bundle("minimal") and 1 or 1,  # 保持接口稳定；版本随 model_bundles.BUNDLE_VERSION
        "recommendations": recommendations,
    }
