"""CLI 命令实现 — Typer 框架。

命令一览：
    doc2mind ingest ./docs/                       # 摄入文档
    doc2mind search "查询词"                      # 搜索
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
from typing import Optional

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
    return


def _open_store() -> tuple[VectorStore, "object"]:
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
    store, embedder = _open_store()
    try:
        retriever = Retriever(store=store, embedder=embedder)
        hits, stats = retriever.search(
            query=query, collection=collection, top_k=top_k
        )
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
    collection: Optional[str] = typer.Option(
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
    collection: Optional[str] = typer.Option(
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
        for name, (dc, cc) in sorted(s.collections.items()):
            table.add_row(name, str(dc), str(cc))
        console.print(table)


@app.command()
def convert(
    input_path: Path = typer.Argument(..., help="输入文件或目录。"),
    output: Optional[Path] = typer.Argument(
        None, help="输出文件（省略则输出到 stdout）。"
    ),
    format: str = typer.Option("md", "--format", "-f", help="目标格式：md/json/txt/html。"),
    out_dir: Optional[Path] = typer.Option(
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
    # 阶段 8 实现：uvicorn.run(app, host=host, port=port)
    from doc2mind.server.http import create_app

    rprint(f"[bold green]启动 HTTP 服务[/bold green] http://{host}:{port}")
    uvicorn.run(create_app(), host=host, port=port)


@app.command()
def mcp() -> None:
    """启动 MCP Server（stdio 传输，供 Cursor / Claude Desktop 等调用）。"""
    from doc2mind.server.mcp import run_mcp_server

    run_mcp_server()


if __name__ == "__main__":
    sys.exit(app())
