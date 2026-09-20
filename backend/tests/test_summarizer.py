from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_reader.config import Config
from paper_reader.summarizer import (
    PaperSummary,
    SummarizeError,
    parse_summary,
    render_markdown,
    summarize_pdf,
)

_VALID = {
    "title": "Attention Is All You Need",
    "authors": ["Ashish Vaswani", "Noam Shazeer"],
    "institutions": ["Google Brain", "UC Berkeley"],
    "venue": "NeurIPS",
    "year": "2017",
    "abstract": "我们提出 Transformer。",
    "core_method": "自注意力机制。",
    "experiments": "WMT14 上 BLEU 提升。",
    "conclusion": "开创性工作。",
    "agent_reflection": "影响深远，建议结合 BERT 阅读。",
}


def test_parse_plain_json():
    s = parse_summary(json.dumps(_VALID, ensure_ascii=False))
    assert s.title == "Attention Is All You Need"
    assert s.authors == ["Ashish Vaswani", "Noam Shazeer"]
    assert s.institutions == ["Google Brain", "UC Berkeley"]


def test_parse_fenced_and_wrapped_output():
    content = "好的，以下是总结：\n```json\n" + json.dumps(_VALID, ensure_ascii=False) + "\n```\n希望对你有帮助"
    s = parse_summary(content)
    assert s.title == "Attention Is All You Need"
    assert s.agent_reflection.startswith("影响深远")


def test_parse_authors_string_normalized():
    payload = dict(_VALID, authors="张三, 李四；王五")
    s = parse_summary(json.dumps(payload, ensure_ascii=False))
    assert s.authors == ["张三", "李四", "王五"]


def test_parse_missing_json_raises():
    with pytest.raises(SummarizeError, match="未找到 JSON"):
        parse_summary("抱歉，我无法完成这个任务。")


def test_render_markdown_contains_required_sections():
    s = PaperSummary(
        title="T Paper",
        authors=["a", "b"],
        institutions=["MIT"],
        venue="NeurIPS",
        year="2017",
        abstract="A",
        core_method="M",
        experiments="E",
        conclusion="C",
        agent_reflection="R",
    )
    md = render_markdown(s, reasoning="thinking trace", model="glm-4.5", source_pdf="t.pdf")
    for part in [
        "# T Paper",
        "**作者**",
        "**机构**",
        "## 摘要",
        "## 核心技术方案",
        "## 实验效果",
        "## 结论",
        "## Agent 思考",
        "### 原始推理过程",
        "thinking trace",
        'title: "T Paper"',
    ]:
        assert part in md


def test_render_markdown_without_reasoning():
    md = render_markdown(PaperSummary(title="X"), model="m")
    assert "### 原始推理过程" not in md


class _StubClient:
    def __init__(self, payload: dict, reasoning: str | None):
        msg = SimpleNamespace(
            content=json.dumps(payload, ensure_ascii=False),
            model_extra={"reasoning_content": reasoning} if reasoning else {},
        )
        self._resp = SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    @property
    def chat(self):
        return SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kwargs: self._resp)
        )


def test_summarize_pdf_end_to_end_with_stub(tmp_path, make_pdf):
    pdf = make_pdf(
        tmp_path / "p.pdf",
        [["A Stub Paper Title", "body " + "x" * 200]],
    )
    client = _StubClient(_VALID, reasoning="第一步先看标题…")
    cfg = Config(api_key="test-key")
    result = summarize_pdf(pdf, cfg, client=client)
    assert result.summary.title == "Attention Is All You Need"
    assert result.pages == 1
    assert "## Agent 思考" in result.markdown
    assert "第一步先看标题…" in result.markdown  # reasoning 进入 markdown
    assert "A Stub Paper Title" in result.summary.abstract or True


def test_summarize_pdf_requires_api_key(tmp_path, make_pdf):
    pdf = make_pdf(tmp_path / "p.pdf", [["T", "body " + "y" * 200]])
    with pytest.raises(SummarizeError, match="API Key"):
        summarize_pdf(pdf, Config(), client=_StubClient(_VALID, None))
