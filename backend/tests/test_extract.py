from __future__ import annotations

import pytest

from paper_reader.extract import ExtractionError, extract_text


def test_extract_text_roundtrip(tmp_path, make_pdf):
    pdf = make_pdf(
        tmp_path / "p.pdf",
        [
            ["Attention Is All You Need", "Ashish Vaswani, Noam Shazeer"],
            ["Second page body content here"],
        ],
    )
    out = extract_text(pdf)
    assert out.pages == 2
    assert out.truncated is False
    assert "Attention Is All You Need" in out.text
    assert "Second page body content" in out.text


def test_extract_truncation_keeps_head_and_tail(tmp_path, make_pdf):
    page = [f"line {i:03d} unique marker text" for i in range(200)]
    pdf = make_pdf(tmp_path / "big.pdf", [page] * 5)
    out = extract_text(pdf, max_chars=3_000)
    assert out.truncated is True
    assert out.chars > 3_000
    assert out.text.startswith("line 000")
    assert "line 199" in out.text  # 尾部保留（结论通常在文末）
    assert "已截断" in out.text


def test_extract_missing_file(tmp_path):
    with pytest.raises(ExtractionError):
        extract_text(tmp_path / "nope.pdf")


def test_extract_too_little_text(tmp_path, make_pdf):
    pdf = make_pdf(tmp_path / "tiny.pdf", [["hi"]])
    with pytest.raises(ExtractionError, match="扫描版"):
        extract_text(pdf)
