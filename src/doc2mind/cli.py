"""CLI 命令实现 — Typer 框架。

命令一览：
    doc2mind ingest ./docs/                       # 摄入文档
    doc2mind search "查询词"                      # 搜索
    doc2mind chat "你的问题"                       # RAG 对话（支持交互模式）
    doc2mind list [--collection NAME]             # 列出文档
    doc2mind remove <path|doc_id>                 # 删除
    doc2mind stats [--collection NAME]            # 统计
    doc2mind convert <input> [output]             # 格式转换
    doc2mind serve                                # 启动 HTTP 服务 (extras)
    doc2mind mcp                                  # 启动 MCP Server
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer
from rich import print as rprint
from rich.console import Console
from rich.table import Table

from doc2mind import __version__
from doc2mind.core.config import get_settings
from doc2mind.core.converter import (
    SUPPORTED_FORMATS,
    ConversionError,
    convert_document,
)
from doc2mind.core.loader.detect import get_loader, is_supported
from doc2mind.core.logging_setup import setup_logging
from doc2mind.core.pipeline import ingest_path
from doc2mind.core.retriever.search import Retriever
from doc2mind.core.store.sqlite_vec import VectorStore

app = typer.Typer(
    name="doc2mind",
    help="轻量向量知识库工具 — 本地 ONNX 嵌入 + sqlite-vec 混合检索 + MCP 接口",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

console = Console()


def _fmt_size(n: int) -> str:
    """把字节数格式化为人类可读单位。"""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.2f} GB"


def _version_callback(value: bool) -> None:
    if value:
        rprint(f"doc2mind [bold]{__version__}[/bold]")
        raise typer.Exit()


@app.callback()
def main_callback(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="显示版本号并退出。",
    ),
) -> None:
    """doc2mind — 轻量向量知识库工具。"""
    # 所有子命令共用：日志落盘（数据目录 logs/ 下，轮转 5MB×3），幂等
    setup_logging()
    return


def _open_store() -> tuple[VectorStore, object]:
    """打开 store + embedder，返回 (store, embedder)。"""
    from doc2mind.core.embedder import get_embedder

    settings = get_settings()
    embedder = get_embedder(settings)
    store = VectorStore(settings.db_path, embedder.dimension)
    store.open()
    return store, embedder


@app.command()
def ingest(
    path: Path = typer.Argument(..., help="要摄入的文件或目录路径。"),
    collection: str = typer.Option("default", "--collection", "-c", help="集合名称。"),
    recursive: bool = typer.Option(
        False, "--recursive", "-r", help="目录递归扫描。"
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="强制重新摄入未变更的文件。"
    ),
) -> None:
    """摄入文档：解析 → 分块 → 嵌入 → 入库。"""
    if not path.exists():
        rprint(f"[red]error[/red] 路径不存在: {path}")
        raise typer.Exit(code=1)

    settings = get_settings()
    settings.ensure_dirs()

    with console.status("[bold green]摄入中...[/bold green]"):
        summary = ingest_path(
            path=path,
            settings=settings,
            collection=collection,
            recursive=recursive,
            force=force,
        )

    # 渲染结果表
    table = Table(title=f"摄入完成 ({summary.total_documents} 新, {summary.skipped} 跳过, {summary.failed} 失败)")
    table.add_column("文件", style="cyan")
    table.add_column("格式", style="magenta")
    table.add_column("分块", justify="right")
    table.add_column("耗时", justify="right")
    table.add_column("状态")
    for res in summary.results:
        status_color = {
            "ingested": "green",
            "skipped": "yellow",
            "updated": "green",
            "failed": "red",
        }.get(res.status, "white")
        table.add_row(
            res.source,
            res.format,
            str(res.chunk_count),
            f"{res.elapsed_ms}ms",
            f"[{status_color}]{res.status}[/{status_color}]"
            + (f" ({res.error})" if res.error else ""),
        )
    console.print(table)
    rprint(f"[bold]总分块数:[/bold] {summary.total_chunks}")


@app.command()
def search(
    query: str = typer.Argument(..., help="搜索查询词。"),
    collection: str = typer.Option("default", "--collection", "-c", help="集合名称。"),
    top_k: int = typer.Option(10, "--top-k", "-k", help="返回结果数量。"),
    format: str = typer.Option(
        "text", "--format", help="输出格式：text / json / md。"
    ),
) -> None:
    """混合检索：BM25 + 向量余弦，RRF 融合 Top-K。"""
    from doc2mind.core.embedder.base import EmbedderError
    from doc2mind.core.embedder.fastembed_impl import first_run_hint
    from doc2mind.core.retriever.search import RetrievalError

    store, embedder = _open_store()
    try:
        retriever = Retriever(store=store, embedder=embedder)
        hits, stats = retriever.search(
            query=query, collection=collection, top_k=top_k
        )
    except (EmbedderError, RetrievalError) as e:
        # 嵌入模型未就绪 / 检索失败：给出可操作提示，而不是裸堆栈
        rprint(f"[red]检索失败:[/red] {e}")
        hint = first_run_hint()
        if hint:
            rprint(f"[yellow]提示:[/yellow]\n{hint}")
        raise typer.Exit(code=1) from None
    finally:
        store.close()

    if not hits:
        rprint("[yellow]未找到相关内容[/yellow]")
        return

    if format == "json":
        payload = {
            "query": query,
            "total": len(hits),
            "elapsed_ms": stats.elapsed_ms,
            "hits": [
                {
                    "rank": h.rank,
                    "score": round(h.score, 4),
                    "match_type": h.match_type,
                    "vector_score": round(h.vector_score, 4),
                    "bm25_score": round(h.bm25_score, 4),
                    "source": h.chunk.source,
                    "format": h.chunk.format,
                    "page": h.chunk.page,
                    "heading": h.chunk.heading,
                    "content": h.chunk.content,
                }
                for h in hits
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    # text / md
    rprint(
        f"[bold]查询:[/bold] {query}  "
        f"[bold]结果:[/bold] {len(hits)}  "
        f"[bold]耗时:[/bold] {stats.elapsed_ms}ms"
    )
    for h in hits:
        score_pct = int(h.score * 100)
        rprint(
            f"\n[bold cyan]#{h.rank + 1}[/bold cyan] "
            f"[green]{score_pct}%[/green] "
            f"[magenta]{h.match_type}[/magenta] "
            f"({h.chunk.source}"
            + (f" p.{h.chunk.page}" if h.chunk.page else "")
            + ")"
        )
        if h.chunk.heading:
            rprint(f"[dim]§ {h.chunk.heading}[/dim]")
        # 内容预览（前 300 字符）
        preview = h.chunk.content[:300]
        if len(h.chunk.content) > 300:
            preview += "..."
        rprint(preview)


@app.command(name="list")
def list_docs(
    collection: str | None = typer.Option(
        None, "--collection", "-c", help="集合名称（默认全部）。"
    ),
    limit: int = typer.Option(50, "--limit", "-n", help="返回数量。"),
) -> None:
    """列出已摄入的文档。"""
    store, _ = _open_store()
    try:
        docs = store.list_documents(collection=collection, limit=limit)
    finally:
        store.close()

    if not docs:
        rprint("[yellow]知识库为空[/yellow]")
        return

    table = Table(title=f"文档列表 ({len(docs)})")
    table.add_column("ID", style="dim", max_width=12)
    table.add_column("文件", style="cyan")
    table.add_column("集合", style="magenta")
    table.add_column("格式", style="magenta")
    table.add_column("分块", justify="right")
    table.add_column("大小", justify="right")
    table.add_column("摄入时间", style="dim")
    for d in docs:
        table.add_row(
            d.id[:12],
            d.source,
            d.collection,
            d.format,
            str(d.chunk_count),
            _fmt_size(d.size_bytes),
            d.created_at[:19],
        )
    console.print(table)


@app.command()
def remove(
    target: str = typer.Argument(..., help="要删除的文件路径或文档 ID。"),
    collection: str = typer.Option("default", "--collection", "-c", help="集合名称。"),
) -> None:
    """从知识库中删除文档及其所有分块。"""
    store, _ = _open_store()
    try:
        # 优先按文档 ID 删
        if len(target) >= 12 and not Path(target).exists():
            n = store.delete_document(target)
            if n >= 0:
                rprint(f"[green]已删除[/green] {target} ({n} 个分块)")
                return

        # 按 source 文件名删
        source_name = Path(target).name if Path(target).exists() else target
        n = store.delete_by_source(source_name, collection)
        if n > 0:
            rprint(f"[green]已删除[/green] {source_name}")
        else:
            rprint(f"[yellow]未找到[/yellow] {source_name} (collection={collection})")
            raise typer.Exit(code=1)
    finally:
        store.close()


@app.command()
def stats(
    collection: str | None = typer.Option(
        None, "--collection", "-c", help="集合名称（默认全部）。"
    ),
) -> None:
    """显示知识库统计信息。"""
    store, _ = _open_store()
    try:
        s = store.get_stats()
    finally:
        store.close()

    rprint(f"[bold]文档总数:[/bold] {s.total_documents}")
    rprint(f"[bold]分块总数:[/bold] {s.total_chunks}")
    rprint(f"[bold]集合数:[/bold] {len(s.collections)}")
    if s.collections:
        table = Table(title="按集合分布")
        table.add_column("集合", style="cyan")
        table.add_column("文档数", justify="right")
        table.add_column("分块数", justify="right")
        table.add_column("大小", justify="right")
        # collections 值可能是 (doc, chunk) 或 (doc, chunk, size_bytes)，兼容两种
        for name, val in sorted(s.collections.items()):
            dc, cc = val[0], val[1]
            size = val[2] if len(val) > 2 else 0
            table.add_row(name, str(dc), str(cc), _fmt_size(size))
        console.print(table)


@app.command()
def convert(
    input_path: Path = typer.Argument(..., help="输入文件或目录。"),
    output: Path | None = typer.Argument(
        None, help="输出文件（省略则输出到 stdout）。"
    ),
    format: str = typer.Option("md", "--format", "-f", help="目标格式：md/json/txt/html。"),
    out_dir: Path | None = typer.Option(
        None, "--out", "-o", help="批量转换输出目录。"
    ),
) -> None:
    """格式互转：PDF / DOCX / XLSX / PPTX → MD / JSON / TXT / HTML。"""
    if format not in SUPPORTED_FORMATS:
        rprint(f"[red]error[/red] 不支持的格式: {format}，支持 {SUPPORTED_FORMATS}")
        raise typer.Exit(code=1)

    if not input_path.exists():
        rprint(f"[red]error[/red] 文件不存在: {input_path}")
        raise typer.Exit(code=1)

    # 单文件
    if input_path.is_file():
        if not is_supported(input_path):
            rprint(f"[red]error[/red] 不支持的文件格式: {input_path.suffix}")
            raise typer.Exit(code=1)

        try:
            loader = get_loader(input_path)
            doc = loader.extract(input_path)
            result = convert_document(doc, format)
        except ConversionError as e:
            rprint(f"[red]error[/red] {e}")
            raise typer.Exit(code=1) from e
        except Exception as e:  # noqa: BLE001
            rprint(f"[red]error[/red] 加载失败: {e}")
            raise typer.Exit(code=1) from e

        if output:
            output.write_text(result, encoding="utf-8")
            rprint(f"[green]已写入[/green] {output} ({len(result)} bytes)")
        else:
            print(result, end="")
        return

    # 目录批量
    if input_path.is_dir():
        if not out_dir:
            rprint("[red]error[/red] 批量转换需要 --out 输出目录")
            raise typer.Exit(code=1)
        out_dir.mkdir(parents=True, exist_ok=True)
        files = sorted(f for f in input_path.iterdir() if f.is_file() and is_supported(f))
        rprint(f"[bold]待转换:[/bold] {len(files)} 个文件")
        for f in files:
            try:
                loader = get_loader(f)
                doc = loader.extract(f)
                result = convert_document(doc, format)
                out_file = out_dir / f"{f.stem}.{format}"
                out_file.write_text(result, encoding="utf-8")
                rprint(f"  [green]✓[/green] {f.name} → {out_file.name}")
            except Exception as e:  # noqa: BLE001
                rprint(f"  [red]✗[/red] {f.name}: {e}")
        return


@app.command()
def chat(
    query: str | None = typer.Argument(None, help="提问内容（不传则进入交互模式）。"),
    collection: str = typer.Option("default", "--collection", "-c", help="检索集合。"),
    top_k: int = typer.Option(5, "--top-k", "-k", help="引用 chunk 数。"),
    chat_id: str | None = typer.Option(None, "--chat-id", help="会话 ID（多轮对话时传同一值）。"),
    collections: list[str] | None = typer.Option(None, "--collections", help="多选知识库集合（逗号分隔或多次传）。"),
) -> None:
    """基于知识库的 RAG 对话：检索相关文档 → 调用 LLM 生成回答。

    用法：
        doc2mind chat "项目架构是什么？"           # 单次问答
        doc2mind chat                              # 进入交互式多轮对话
        doc2mind chat --collections prj-a prj-b     # 多集合对话
    """
    from doc2mind.core.rag import RagError, rag_answer

    # 单次问答
    if query:
        _run_chat_once(query, collection, top_k, chat_id, collections)
        return

    # 交互式多轮对话
    rprint("[bold green]DocMind RAG 对话[/bold green]（输入 exit / quit 退出）")
    rprint("[dim]────────────────────────────────────────[/dim]")
    current_chat_id: str | None = chat_id
    while True:
        try:
            user_input = console.input("[bold cyan]你:[/bold cyan] ")
        except (EOFError, KeyboardInterrupt):
            rprint("\n[dim]已退出[/dim]")
            break
        user_input = user_input.strip()
        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "q"):
            rprint("[dim]已退出[/dim]")
            break

        try:
            result = rag_answer(
                query=user_input, collection=collection, top_k=top_k,
                chat_id=current_chat_id, collections=collections,
            )
        except RagError as e:
            rprint(f"[red]对话失败:[/red] {e}")
            continue

        # 首次问答后锁定 chat_id
        if current_chat_id is None:
            current_chat_id = result.chat_id

        _print_chat_result(result)


def _run_chat_once(query: str, collection: str, top_k: int, chat_id: str | None = None, collections: list[str] | None = None) -> None:
    """执行一次 RAG 对话并输出结果。"""
    from doc2mind.core.rag import RagError, rag_answer

    try:
        result = rag_answer(
            query=query, collection=collection, top_k=top_k, chat_id=chat_id,
            collections=collections,
        )
    except RagError as e:
        rprint(f"[red]对话失败:[/red] {e}")
        raise typer.Exit(code=1) from None

    _print_chat_result(result)


def _print_chat_result(result: Any) -> None:
    """打印 RAG 对话结果。"""
    # 引用来源
    if result.sources:
        rprint("[bold magenta]引用来源:[/bold magenta]")
        for s in result.sources:
            loc = s.source
            if s.page is not None:
                loc += f" p.{s.page}"
            if s.heading:
                loc += f"（{s.heading}）"
            rprint(f"  [{s.index}] {loc}  [dim]score={s.score}[/dim]")
        rprint()

    # 回答
    rprint(f"[bold green]DocMind:[/bold green] {result.answer}")

    # 元信息
    rprint(
        f"[dim]模型: {result.model} ({result.provider}) | "
        f"引用 {result.total_chunks} 块 | {result.elapsed_ms}ms[/dim]"
    )


@app.command()
def models() -> None:
    """列出可选的嵌入模型（含维度/语言/适用场景说明）。"""
    from doc2mind.core.embedder.catalog import (
        default_model,
        get_model_info,
        render_catalog_table,
    )

    rprint(render_catalog_table())
    current = get_settings().embed_model
    info = get_model_info(current)
    rprint(f"\n当前模型: [bold green]{current}[/bold green]"
           + (f"（{info.dim} 维）" if info else "（自定义模型）"))
    if info and not info.recommended:
        rprint(f"[dim]提示: 推荐 {default_model()}，中英文效果均衡且资源占用低。[/dim]")


# --- 模型管理子命令组：doc2mind model <list|download|use|add-local> ---
model_app = typer.Typer(
    help="嵌入模型管理：下载推荐模型 / 切换模型 / 登记本地模型",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
app.add_typer(model_app, name="model")


@model_app.command("list")
def model_list() -> None:
    """列出推荐模型与当前配置（等价 `doc2mind models`）。"""
    from doc2mind.core.embedder.catalog import (
        default_model,
        get_model_info,
        render_catalog_table,
    )

    rprint(render_catalog_table())
    s = get_settings()
    current = s.embed_model
    info = get_model_info(current)
    rprint(f"\n当前模型: [bold green]{current}[/bold green]"
           + (f"（{info.dim} 维）" if info else "（自定义模型）"))
    if s.embed_model_path:
        rprint(f"本地模型目录: [bold cyan]{s.embed_model_path}[/bold cyan]")
    if info and not info.recommended:
        rprint(f"[dim]提示: 推荐 {default_model()}，中英文效果均衡且资源占用低。[/dim]")


@model_app.command("download")
def model_download(
    name: str = typer.Argument(
        ..., help="推荐清单里的模型名（如 BAAI/bge-small-zh-v1.5）。"
    ),
) -> None:
    """下载推荐模型到本地缓存（首次联网，之后直接用缓存）。"""
    from doc2mind.core.embedder.catalog import download_recommended_model

    with console.status("[bold green]正在下载/校验模型...[/bold green]"):
        result = download_recommended_model(name)
    rprint(result)


@model_app.command("use")
def model_use(
    name: str = typer.Argument(..., help="要切换的模型名（推荐清单或 fastembed 支持的模型）。"),
) -> None:
    """切换嵌入模型并持久化（等价 `doc2mind config --set-model`）。"""
    from doc2mind.core.embedder.catalog import get_model_info

    info = get_model_info(name)
    if info is None:
        rprint(
            f"[yellow]注意:[/yellow] {name} 不在内置清单里，"
            "请确认该模型已被 fastembed 支持，否则加载会失败。"
        )
    settings = get_settings()
    settings.embed_model = name
    settings.embed_model_path = None  # 切换网络模型时清除本地模型指向
    _save_settings_or_warn(settings)
    rprint(f"[green]已切换嵌入模型:[/green] {name}")
    if info and info.dim != settings.embed_dim:
        rprint(
            "[yellow]提示:[/yellow] 新模型维度与旧模型不同，"
            "请对已有集合执行 `doc2mind reindex` 重建索引，否则检索会报错。"
        )


@model_app.command("add-local")
def model_add_local(
    path: Path = typer.Argument(..., help="本地 ONNX 模型目录（含 model.onnx + tokenizer.json）。"),
    model_name: str = typer.Option(
        None,
        "--model-name",
        "-n",
        help="对应的内置模型名（决定 tokenizer 结构与 model_file 文件名），"
             "默认用当前模型名。",
    ),
) -> None:
    """登记本地模型目录：直接用本地 ONNX 文件，无需联网下载。"""
    from doc2mind.core.embedder.catalog import validate_local_model_dir

    ok, message = validate_local_model_dir(path)
    if not ok:
        rprint(f"[red]本地模型不可用:[/red] {message}")
        raise typer.Exit(code=1)
    rprint(f"[green]{message}[/green]")

    settings = get_settings()
    settings.embed_model_path = str(path)
    if model_name:
        settings.embed_model = model_name
    _save_settings_or_warn(settings)
    rprint(f"[green]已登记本地模型目录:[/green] {path}")
    rprint(
        "[dim]说明: 使用本地模型时不再联网下载；若该模型与之前用的模型维度不同，"
        "请对已有集合执行 `doc2mind reindex` 重建索引。[/dim]"
    )


@app.command()
def config(
    show: bool = typer.Option(
        False, "--show", "-s", help="显示当前生效配置。"
    ),
    set_model: str | None = typer.Option(
        None, "--set-model", "-m", help="切换嵌入模型并持久化（下次启动仍生效）。"
    ),
    model: str | None = typer.Option(
        None, "--model", "-M", help="临时指定嵌入模型（仅本次进程，不持久化）。"
    ),
) -> None:
    """查看 / 切换嵌入模型配置。

    示例：
        doc2mind config --show               # 查看当前配置
        doc2mind config --set-model BAAI/bge-small-en-v1.5   # 切换模型（持久化）
        doc2mind config --model <名称>       # 临时用某个模型跑一次
    """
    from doc2mind.core.embedder.catalog import get_model_info, render_catalog_table

    if model:
        # 临时覆盖：仅本次进程
        set_settings_for_model(model)
        rprint(f"[green]已临时切换模型:[/green] {model}（仅本次进程，不持久化）")
        info = get_model_info(model)
        if info:
            rprint(f"[dim]{info.desc}[/dim]")
        return

    if set_model:
        # 校验模型是否在清单内（自定义模型放行，但提示）
        info = get_model_info(set_model)
        if info is None:
            rprint(
                f"[yellow]注意:[/yellow] {set_model} 不在内置清单里，"
                "请确认该模型已被 fastembed 支持，否则加载会失败。"
            )
        settings = get_settings()
        settings.embed_model = set_model
        _save_settings_or_warn(settings)
        rprint(f"[green]已保存嵌入模型:[/green] {set_model}")
        if info and info.dim != settings.embed_dim:
            rprint(
                "[yellow]提示:[/yellow] 新模型维度与旧模型不同，"
                "请对已有集合执行 `doc2mind reindex` 重建索引，否则检索会报错。"
            )
        return

    # 默认/--show：显示当前配置
    s = get_settings()
    rprint(f"[bold]嵌入模型:[/bold] {s.embed_model}")
    info = get_model_info(s.embed_model)
    if info:
        rprint(f"[bold]维度:[/bold] {info.dim}   [bold]大小:[/bold] {info.size_gb}G")
        rprint(f"[bold]说明:[/bold] {info.desc}")
    rprint(f"[bold]嵌入批大小:[/bold] {s.embed_batch_size}")
    rprint(f"[bold]分块参数:[/bold] max_tokens={s.chunk_max_tokens} "
           f"min_chars={s.chunk_min_chars} overlap={s.chunk_overlap_chars} "
           f"max_chars={s.chunk_max_chars}")
    rprint(f"[bold]检索:[/bold] top_k={s.search_top_k} rrf_k={s.rrf_k}")
    rprint(f"[bold]知识库文件:[/bold] {s.db_path}")
    rprint(f"[bold]模型缓存目录:[/bold] {s.embed_cache_dir}")
    rprint("\n可用模型清单：")
    rprint(render_catalog_table())


def set_settings_for_model(model: str) -> None:
    """临时覆盖全局配置中的嵌入模型（仅本次进程）。"""
    settings = get_settings()
    settings.embed_model = model


def _save_settings_or_warn(settings: object) -> None:
    """持久化配置；失败时明确警告（否则用户重启后才发现配置丢了）。"""
    from doc2mind.core.config import save_settings

    if not save_settings(settings):  # type: ignore[arg-type]
        rprint(
            "[red]警告:[/red] 配置已在本进程生效，但写入 config.toml 失败"
            "（磁盘满/权限不足），重启后将回退为之前的配置。"
        )


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="监听地址。"),
    port: int = typer.Option(8765, "--port", "-p", help="监听端口。"),
) -> None:
    """启动 FastAPI HTTP 服务（需要 `pip install doc2mind[server]`）。"""
    try:
        import uvicorn  # noqa: F401
    except ImportError:
        rprint(
            "[red]error[/red] server 依赖未安装。请运行："
            "[bold]pip install doc2mind[server][/bold]"
        )
        raise typer.Exit(code=1) from None
    # 首次使用引导：模型未下载时提示（新手友好）
    from doc2mind.core.embedder.fastembed_impl import first_run_hint
    from doc2mind.server.http import create_app

    hint = first_run_hint()
    if hint:
        rprint(f"[yellow]提示:[/yellow]\n{hint}")

    # 端口冲突处理：目标端口被占用时自动 +1 探测空闲端口（最多 +100），
    # 并把实际端口写入 server.port 状态文件，供 WPF 客户端读取跟随。
    actual_port = _find_free_port(host, port)
    if actual_port != port:
        rprint(
            f"[yellow]端口 {port} 被占用，自动改用 {actual_port}[/yellow]"
        )
    _write_server_port(actual_port)

    rprint(f"[bold green]启动 HTTP 服务[/bold green] http://{host}:{actual_port}")
    uvicorn.run(create_app(), host=host, port=actual_port)


def _find_free_port(host: str, port: int, max_tries: int = 100) -> int:
    """探测一个可监听的端口：从 `port` 起 +1 依次 bind 测试，返回第一个空闲端口。

    目的：8765 被其它程序占用时，后端不至于启动失败，而是顺延到 8766/8767…
    """
    import socket

    for candidate in range(port, port + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, candidate))
                return candidate
            except OSError:
                continue
    raise typer.Exit(
        code=1,
        message=f"[red]error[/red] 端口 {port}~{port + max_tries - 1} 均被占用，无法启动服务。",
    )


def _write_server_port(port: int) -> None:
    """把实际监听端口写入状态文件（WPF 客户端据此跟随端口变化）。失败静默。"""
    try:
        from doc2mind.core.config import server_port_file_path

        p = server_port_file_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(port), encoding="utf-8")
    except OSError:
        # 写失败不影响服务启动（WPF 仍会先探测默认端口）
        pass


@app.command()
def mcp() -> None:
    """启动 MCP Server（stdio 传输，供 Cursor / Claude Desktop 等调用）。"""
    from doc2mind.server.mcp import run_mcp_server

    run_mcp_server()


if __name__ == "__main__":
    sys.exit(app())
