from __future__ import annotations

from pathlib import Path

from paper_reader.library import find_paper, read_title, scan, summary_path_for


def _touch(path: Path, content: str = "%PDF-1.4 fake") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_read_status_by_summary_existence(tmp_path):
    """已读的唯一标准：summaries/ 下存在对应 .md 文件。"""
    papers, summaries = tmp_path / "papers", tmp_path / "summaries"
    _touch(papers / "a.pdf")
    _touch(papers / "sub" / "b.pdf")
    _touch(summaries / "a.md", "# 标题A\n")

    infos = {p.stem: p for p in scan(papers, summaries)}
    assert infos["a"].read is True
    assert infos["sub/b"].read is False
    assert infos["a"].summary_path == summaries / "a.md"
    assert infos["sub/b"].summary_path == summaries / "sub" / "b.md"


def test_summary_creation_flips_status(tmp_path):
    papers, summaries = tmp_path / "papers", tmp_path / "summaries"
    _touch(papers / "x.pdf")
    assert scan(papers, summaries)[0].read is False
    _touch(summaries / "x.md", "# t\n")
    assert scan(papers, summaries)[0].read is True


def test_summary_path_for_nested(tmp_path):
    pdf = tmp_path / "papers" / "2024" / "attention.pdf"
    assert summary_path_for(tmp_path / "papers", tmp_path / "s", pdf) == tmp_path / "s" / "2024" / "attention.md"


def test_find_paper_matches_stem_and_suffix(tmp_path):
    papers, summaries = tmp_path / "papers", tmp_path / "summaries"
    _touch(papers / "attention.pdf")
    assert find_paper(papers, summaries, "attention") is not None
    assert find_paper(papers, summaries, "attention.pdf") is not None
    assert find_paper(papers, summaries, "nonexistent") is None


def test_read_title_frontmatter_wins(tmp_path):
    f = _touch(tmp_path / "s1.md", '---\ntitle: "Deep Title: A Study"\n---\n\n# Other\n')
    assert read_title(f) == "Deep Title: A Study"
    f2 = _touch(tmp_path / "s2.md", "# Plain Heading\n")
    assert read_title(f2) == "Plain Heading"
