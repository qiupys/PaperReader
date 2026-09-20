from __future__ import annotations

from pathlib import Path

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


@pytest.fixture
def make_pdf():
    """返回一个工厂：make_pdf(path, pages)，pages 为每行文本的二维列表。"""

    def _make(path: Path, pages: list[list[str]]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        c = canvas.Canvas(str(path), pagesize=A4)
        for lines in pages:
            y = 800
            for line in lines:
                c.drawString(72, y, line)
                y -= 18
            c.showPage()
        c.save()
        return path

    return _make
