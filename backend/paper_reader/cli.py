"""CLI 入口：paper list / show / summarize / serve / config。"""

from __future__ import annotations

import json as _json

import typer
from rich.console import Console
from rich.table import Table

from .config import config_asdict, load_config
from .extract import ExtractionError
from .library import find_paper, scan
from .summarizer import SummarizeError, summarize_pdf, write_summary

app = typer.Typer(
    name="paper",
    help="论文阅读器：管理已读/未读，调用 LLM 生成含 Agent 思考的 summary。",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


def _fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


@app.command("list")
def list_papers(
    read: bool = typer.Option(False, "--read", help="只看已读"),
    unread: bool = typer.Option(False, "--unread", help="只看未读"),
    as_json: bool = typer.Option(False, "--json", help="以 JSON 输出"),
) -> None:
    """列出论文库中的论文及已读/未读状态（已读 = 存在对应 summary markdown）。"""
    cfg = load_config()
    papers = scan(cfg.papers_dir, cfg.summaries_dir)
    if read:
        papers = [p for p in papers if p.read]
    elif unread:
        papers = [p for p in papers if not p.read]

    if as_json:
        typer.echo(
            _json.dumps(
                {
                    "papers": [
                        {"stem": p.stem, "read": p.read, "title": p.title, "size": p.size}
                        for p in papers
                    ],
                    "stats": {
                        "total": len(papers),
                        "read": sum(1 for p in papers if p.read),
                        "unread": sum(1 for p in papers if not p.read),
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    table = Table(title=f"论文库（{cfg.papers_dir}）", title_style="bold")
    table.add_column("状态", justify="center")
    table.add_column("论文", style="cyan")
    table.add_column("标题", style="white")
    table.add_column("大小", justify="right", style="dim")
    for p in papers:
        title = p.title or "—"
        if len(title) > 48:
            title = title[:47] + "…"
        table.add_row(
            "[green]✅ 已读[/green]" if p.read else "[yellow]⬜ 未读[/yellow]",
            p.stem,
            title,
            _fmt_size(p.size),
        )
    console.print(table)
    n_read = sum(1 for p in papers if p.read)
    console.print(
        f"[bold]共 {len(papers)} 篇 · 已读 {n_read} · 未读 {len(papers) - n_read}[/bold]"
    )


@app.command()
def show(
    stem: str = typer.Argument(..., help="论文名（stem 或 xxx.pdf）"),
) -> None:
    """查看某篇论文的 summary（输出 Markdown 原文）。"""
    cfg = load_config()
    info = find_paper(cfg.papers_dir, cfg.summaries_dir, stem)
    if info is None:
        typer.secho(f"未找到论文: {stem}", fg=typer.colors.RED)
        raise typer.Exit(1)
    if not info.read:
        typer.secho(
            f"《{info.stem}》还未读（无对应 summary）。先运行: paper summarize {info.stem}",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(1)
    typer.echo(info.summary_path.read_text(encoding="utf-8"))


@app.command()
def summarize(
    pdf: str = typer.Argument(..., help="论文名（stem / xxx.pdf / 子目录路径）"),
    force: bool = typer.Option(False, "--force", "-f", help="summary 已存在时重新生成"),
) -> None:
    """生成 summary：作者机构、摘要、核心技术方案、实验效果、结论 + Agent 思考。"""
    cfg = load_config()
    info = find_paper(cfg.papers_dir, cfg.summaries_dir, pdf)
    if info is None:
        typer.secho(f"未找到论文: {pdf}", fg=typer.colors.RED)
        raise typer.Exit(1)
    if info.read and not force:
        typer.secho(
            f"已存在 summary: {info.summary_path}（使用 --force 重新生成）",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(0)

    with console.status(
        f"[bold cyan]调用 {cfg.model} 阅读论文（提取全文 -> 分析 -> 生成 summary，可能需要几分钟）…[/bold cyan]",
        spinner="dots",
    ):
        try:
            result = summarize_pdf(info.pdf_path, cfg)
        except (ExtractionError, SummarizeError) as e:
            typer.secho(str(e), fg=typer.colors.RED)
            raise typer.Exit(1) from e
        write_summary(info.summary_path, result.markdown)

    typer.secho(f"✅ 已生成 summary: {info.summary_path}", fg=typer.colors.GREEN)
    typer.secho(
        f"   标题: {result.summary.title}（{result.pages} 页 / 提取 {result.chars} 字符）",
        fg=typer.colors.GREEN,
    )


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="监听地址"),
    port: int = typer.Option(8765, help="监听端口"),
) -> None:
    """启动 API 服务（供 Web 界面使用，或供其他工具调用）。"""
    import uvicorn

    from .server import app as api_app

    typer.echo(f"API: http://{host}:{port}/api/papers")
    typer.echo(
        f"Web: http://{host}:{port}/ （需已构建 frontend/dist；开发时在 frontend/ 下运行 npm run dev）"
    )
    uvicorn.run(api_app, host=host, port=port, log_level="info")


@app.command("config")
def show_config() -> None:
    """查看当前生效的配置（API Key 已脱敏）。"""
    cfg = load_config()
    for key, value in config_asdict(cfg).items():
        typer.echo(f"{key:14s} = {value}")
    if not cfg.api_key:
        typer.secho(
            "⚠ 未配置 api_key，paper summarize / Web 生成功能不可用",
            fg=typer.colors.YELLOW,
        )


if __name__ == "__main__":
    app()
