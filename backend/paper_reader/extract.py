"""PDF 文本提取（基于 pypdf）。

提取结果带「【第 N 页】」分页标记：页码与 PDF 阅读器页码一致（1 起），
为 LLM 提供原文的分页结构参照。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

MIN_TEXT_CHARS = 50

# 提取结果缓存：同一 PDF（路径+mtime+截断长度）只提取一次，对话/摘要请求免重复解析
_CACHE: dict[tuple[str, int, int], "ExtractedText"] = {}
_CACHE_MAX = 8


class ExtractionError(Exception):
    """PDF 文本提取失败。"""


@dataclass
class ExtractedText:
    text: str
    pages: int
    chars: int  # 原始提取字符数（截断前）
    truncated: bool


def extract_text(pdf_path: Path | str, max_chars: int = 200_000) -> ExtractedText:
    """提取 PDF 全文文本（含分页标记）。超出 max_chars 时保留首部 70% + 尾部 30%。"""
    pdf_path = Path(pdf_path)
    if not pdf_path.is_file():
        raise ExtractionError(f"PDF 文件不存在: {pdf_path}")

    try:
        mtime = int(pdf_path.stat().st_mtime)
    except OSError as e:  # noqa: BLE001
        raise ExtractionError(f"无法读取 PDF 文件信息: {pdf_path.name}") from e

    key = (str(pdf_path), mtime, max_chars)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    try:
        reader = PdfReader(pdf_path)
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as e:  # noqa: BLE001
                raise ExtractionError(f"PDF 已加密，无法读取: {pdf_path.name}") from e
        pages = len(reader.pages)
        parts: list[str] = []
        for no, page in enumerate(reader.pages, start=1):
            try:
                piece = (page.extract_text() or "").strip()
            except PdfReadError:
                piece = ""
            if piece:
                parts.append(f"【第 {no} 页】\n{piece}")
    except ExtractionError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ExtractionError(f"读取 PDF 失败（{pdf_path.name}）: {e}") from e

    full = "\n\n".join(parts)
    if len(full.strip()) < MIN_TEXT_CHARS:
        raise ExtractionError(
            f"未能从 {pdf_path.name} 提取到足够文本（可能是扫描版/图片型 PDF，暂不支持 OCR）"
        )

    if len(full) <= max_chars:
        result = ExtractedText(text=full, pages=pages, chars=len(full), truncated=False)
    else:
        head = int(max_chars * 0.7)
        tail = max_chars - head
        text = (
            full[:head]
            + "\n\n[...为适应上下文长度，论文中间部分已截断...]\n\n"
            + full[-tail:]
        )
        result = ExtractedText(text=text, pages=pages, chars=len(full), truncated=True)

    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = result
    return result
