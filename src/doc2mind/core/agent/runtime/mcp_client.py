"""对话侧只读 MCP 客户端（T9 / ISSUE-09）。

仅挂载 search/fetch 类外部工具；默认关闭。密钥与命令不写入日志明文。
MCP server 挂了时降级为内置检索/RAG，不阻断对话。
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_SECRET_RE = re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[=:]\s*\S+")


def mask_cmd(cmd: str | list[str] | None) -> str:
    """脱敏命令行/密钥，仅保留程序名。"""
    if not cmd:
        return ""
    if isinstance(cmd, list):
        return str(cmd[0]) if cmd else ""
    first = str(cmd).strip().split()[0] if str(cmd).strip() else ""
    return first


def _scrub(text: str) -> str:
    return _SECRET_RE.sub(lambda m: m.group(1) + "=***", text or "")


@dataclass
class McpServerConfig:
    name: str
    transport: str = "stdio"  # stdio | http
    command: str = ""
    args: list[str] = field(default_factory=list)
    url: str = ""
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = False
    # 只读工具白名单（工具名子串匹配，如 search / fetch / query）
    tool_whitelist: list[str] = field(
        default_factory=lambda: ["search", "fetch", "query", "lookup"]
    )
    # 禁止写/危险能力（子串）
    tool_blacklist: list[str] = field(
        default_factory=lambda: ["write", "delete", "shell", "exec", "send", "create", "update", "drop"]
    )


@dataclass
class McpToolResult:
    server: str
    tool: str
    ok: bool
    content: str = ""
    error: str | None = None
    elapsed_ms: int = 0
    source: str = "mcp"


def _tool_allowed(tool_name: str, cfg: McpServerConfig) -> bool:
    t = (tool_name or "").lower()
    if not t:
        return False
    if any(b in t for b in cfg.tool_blacklist):
        return False
    if not cfg.tool_whitelist:
        return False
    return any(w in t for w in cfg.tool_whitelist)


class ReadOnlyMcpClient:
    """极简只读 MCP 客户端：stdio JSON-RPC tools/list + tools/call。

    商用约束：仅在 agent_mode_enabled 且配置显式 enabled 时加载；
    未审计 server 不加载；写类工具一律拒绝。
    """

    def __init__(self, servers: list[McpServerConfig] | None = None, *, enabled: bool = False):
        self.enabled = bool(enabled)
        self.servers = [s for s in (servers or []) if s.enabled]
        self._procs: dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()
        self.last_error: str | None = None

    @classmethod
    def from_settings(cls, s: Any) -> "ReadOnlyMcpClient":
        """从 Settings 解析 MCP 配置；agent 总开关关闭时不加载。"""
        if not bool(getattr(s, "agent_mode_enabled", False)):
            return cls([], enabled=False)
        if not bool(getattr(s, "mcp_client_enabled", False)):
            return cls([], enabled=False)
        raw = getattr(s, "mcp_servers_json", None) or "[]"
        try:
            items = json.loads(raw) if isinstance(raw, str) else list(raw)
        except Exception as ex:  # noqa: BLE001
            logger.warning("mcp_servers_json 解析失败，已禁用 MCP client: %s", _scrub(str(ex)))
            return cls([], enabled=False)
        servers: list[McpServerConfig] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            servers.append(
                McpServerConfig(
                    name=str(item.get("name") or "mcp"),
                    transport=str(item.get("transport") or "stdio"),
                    command=str(item.get("command") or ""),
                    args=list(item.get("args") or []),
                    url=str(item.get("url") or ""),
                    env=dict(item.get("env") or {}),
                    enabled=bool(item.get("enabled", False)),
                    tool_whitelist=list(item.get("tool_whitelist") or McpServerConfig().tool_whitelist),
                    tool_blacklist=list(item.get("tool_blacklist") or McpServerConfig().tool_blacklist),
                )
            )
        return cls(servers, enabled=True)

    def close(self) -> None:
        with self._lock:
            for p in self._procs.values():
                try:
                    p.terminate()
                except Exception:  # noqa: BLE001
                    pass
            self._procs.clear()

    def _spawn(self, cfg: McpServerConfig) -> subprocess.Popen | None:
        if cfg.transport != "stdio" or not cfg.command:
            return None
        env = os.environ.copy()
        # 密钥来自配置 env，不打印
        env.update({k: str(v) for k, v in (cfg.env or {}).items()})
        try:
            proc = subprocess.Popen(
                [cfg.command, *(cfg.args or [])],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=env,
                text=True,
            )
            return proc
        except Exception as ex:  # noqa: BLE001
            self.last_error = _scrub(str(ex))
            logger.warning("MCP server %s 启动失败（命令脱敏=%s）: %s", cfg.name, mask_cmd(cfg.command), self.last_error)
            return None

    def _rpc(self, cfg: McpServerConfig, method: str, params: dict[str, Any] | None = None) -> Any:
        with self._lock:
            proc = self._procs.get(cfg.name)
            if proc is None or proc.poll() is not None:
                proc = self._spawn(cfg)
                if proc is None:
                    raise RuntimeError("mcp spawn failed")
                self._procs[cfg.name] = proc
        req = {"jsonrpc": "2.0", "id": int(time.time() * 1000) % 1_000_000, "method": method}
        if params is not None:
            req["params"] = params
        assert proc.stdin and proc.stdout
        proc.stdin.write(json.dumps(req) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("mcp empty response")
        data = json.loads(line)
        if "error" in data:
            raise RuntimeError(_scrub(str(data["error"])))
        return data.get("result")

    def list_tools(self, server_name: str | None = None) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        out: list[dict[str, Any]] = []
        for cfg in self.servers:
            if server_name and cfg.name != server_name:
                continue
            try:
                result = self._rpc(cfg, "tools/list", {})
                tools = (result or {}).get("tools") or []
                for t in tools:
                    name = str(t.get("name") or "")
                    if _tool_allowed(name, cfg):
                        out.append({"server": cfg.name, "name": name, "origin": "mcp", "inputSchema": t.get("inputSchema")})
            except Exception as ex:  # noqa: BLE001
                self.last_error = _scrub(str(ex))
                logger.debug("MCP tools/list 失败 server=%s: %s", cfg.name, self.last_error)
        return out

    def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout_ms: int = 8000,
    ) -> McpToolResult:
        t0 = time.perf_counter()
        if not self.enabled:
            return McpToolResult(server_name, tool_name, False, error="mcp disabled", source="mcp")
        cfg = next((s for s in self.servers if s.name == server_name), None)
        if cfg is None:
            return McpToolResult(server_name, tool_name, False, error="server not found", source="mcp")
        if not _tool_allowed(tool_name, cfg):
            logger.warning("拒绝非白名单 MCP 工具: server=%s tool=%s", server_name, tool_name)
            return McpToolResult(server_name, tool_name, False, error="tool not in whitelist", source="mcp")
        try:
            # 简化超时：调用同步 RPC；挂死时由上层 agent 预算兜底
            result = self._rpc(
                cfg,
                "tools/call",
                {"name": tool_name, "arguments": arguments or {}},
            )
            content = ""
            if isinstance(result, dict):
                parts = result.get("content") or []
                chunks = []
                for p in parts:
                    if isinstance(p, dict) and p.get("type") == "text":
                        chunks.append(str(p.get("text") or ""))
                    elif isinstance(p, str):
                        chunks.append(p)
                content = "\n".join(chunks)[:4000]
            else:
                content = _scrub(str(result))[:4000]
            return McpToolResult(
                server=cfg.name,
                tool=tool_name,
                ok=True,
                content=content,
                elapsed_ms=int((time.perf_counter() - t0) * 1000),
            )
        except Exception as ex:  # noqa: BLE001 —— 降级不抛
            self.last_error = _scrub(str(ex))
            logger.warning("MCP 工具失败 server=%s tool=%s（已降级）: %s", server_name, tool_name, self.last_error)
            return McpToolResult(
                server=cfg.name,
                tool=tool_name,
                ok=False,
                error=self.last_error,
                elapsed_ms=int((time.perf_counter() - t0) * 1000),
            )


def mcp_search_registry(client: ReadOnlyMcpClient) -> list[dict[str, Any]]:
    """给 ToolRegistry 用的只读 MCP 工具描述（origin=mcp）。"""
    return client.list_tools() if client and client.enabled else []
