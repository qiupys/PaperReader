"""论文库扫描与已读/未读判定。

已读判定标准（唯一标准）：
  papers/<stem>.pdf 存在对应 summaries/<stem>.md 即视为已读。
  支持子目录：papers/sub/name.pdf -> summaries/sub/name.md。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_FRONTMATTER_TITLE_RE = re.compile(r"^title:\s*(.+)$", re.MULTILINE)
_HEADING_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


@dataclass
class PaperInfo:
    """一篇论文的库内信息。"""

    stem: str  # 相对 papers 目录、去掉 .pdf 后缀的路径，如 "2024/attention"
    pdf_path: Path
    summary_path: Path  # 期望的 summary 路径（可能尚不存在）
    size: int
    mtime: float
    read: bool
    title: str | None = None


def summary_path_for(papers_dir: Path, summaries_dir: Path, pdf_path: Path) -> Path:
    rel = pdf_path.relative_to(papers_dir).with_suffix(".md")
    return summaries_dir / rel


def read_title(summary_path: Path) -> str | None:
    """从 summary markdown 提取标题：优先 frontmatter 的 title，其次首个一级标题。"""
    try:
        head = Path(summary_path).read_text(encoding="utf-8", errors="ignore")[:2048]
    except OSError:
        return None
    m = _FRONTMATTER_TITLE_RE.search(head)
    if m:
        raw = m.group(1).strip()
        if len(raw) >= 2 and raw[0] in "\"'" and raw[-1] == raw[0]:
            raw = raw[1:-1]
        return raw or None
    m = _HEADING_RE.search(head)
    if m:
        return m.group(1).strip() or None
    return None


def scan(papers_dir: Path, summaries_dir: Path) -> list[PaperInfo]:
    """扫描论文库，返回按修改时间倒序的论文列表。"""
    papers_dir = Path(papers_dir)
    summaries_dir = Path(summaries_dir)
    papers_dir.mkdir(parents=True, exist_ok=True)
    summaries_dir.mkdir(parents=True, exist_ok=True)

    infos: list[PaperInfo] = []
    for pdf_path in papers_dir.rglob("*.pdf"):
        if pdf_path.name.startswith("."):
            continue
        spath = summary_path_for(papers_dir, summaries_dir, pdf_path)
        stat = pdf_path.stat()
        infos.append(
            PaperInfo(
                stem=pdf_path.relative_to(papers_dir).with_suffix("").as_posix(),
                pdf_path=pdf_path,
                summary_path=spath,
                size=stat.st_size,
                mtime=stat.st_mtime,
                read=spath.is_file(),
                title=read_title(spath) if spath.is_file() else None,
            )
        )
    infos.sort(key=lambda p: p.mtime, reverse=True)
    return infos


def find_paper(papers_dir: Path, summaries_dir: Path, key: str) -> PaperInfo | None:
    """按 stem / 文件名（含或不含 .pdf 后缀）查找论文。"""
    key = key.strip().removesuffix(".pdf")
    if not key:
        return None
    for info in scan(papers_dir, summaries_dir):
        if info.stem == key or info.stem.endswith("/" + key):
            return info
    return None
