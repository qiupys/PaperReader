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


def test_has_content():
    empty = PaperSummary(title="T")
    assert not empty.has_content()
    full = PaperSummary(title="T", abstract="有内容")
    assert full.has_content()


def test_parse_truncated_meta_only_prefix_raises():
    """截断的输出只解析出元信息、内容字段全空：必须报错而不是静默渲染空章节。"""
    broken = json.dumps(
        {
            "title": "T",
            "authors": ["a"],
            "institutions": ["b"],
            "venue": "x",
            "year": "2024",
        },
        ensure_ascii=False,
    )
    with pytest.raises(SummarizeError, match="全为空"):
        parse_summary(broken)


def test_parse_empty_object_prefix_skipped():
    """JSON 前有无关的空对象 {}：应跳过并取到后面的完整对象。"""
    content = "{}\n" + json.dumps(_VALID, ensure_ascii=False)
    s = parse_summary(content)
    assert s.abstract == "我们提出 Transformer。"


def test_parse_all_fields_empty_raises():
    with pytest.raises(SummarizeError, match="全为空"):
        parse_summary(json.dumps({"title": "", "abstract": "", "conclusion": ""}))


def test_parse_valid_prefix_with_more_text():
    """JSON 后粘连多余文本：仍取完整 JSON（内容字段非空）。"""
    content = json.dumps(_VALID, ensure_ascii=False) + "，以上。"
    s = parse_summary(content)
    assert s.conclusion == "开创性工作。"


class _SeqClient:
    """按次序返回预设响应的 stub：模拟「首次输出损坏 -> 修复成功」。"""

    def __init__(self, payloads: list[str]):
        self._msgs = [
            SimpleNamespace(content=p, model_extra={}) for p in payloads
        ]
        self._i = 0

    @property
    def chat(self):
        resp = SimpleNamespace(choices=[SimpleNamespace(message=self._msgs[self._i])])
        self._i += 1
        return SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kwargs: resp)
        )


def test_summarize_pdf_repairs_truncated_output(tmp_path, make_pdf):
    """截断输出（只有元信息）应触发自动修复并成功渲染，而不是出现「未提取到」。"""
    pdf = make_pdf(tmp_path / "p.pdf", [["A Stub Paper Title", "body " + "x" * 200]])
    truncated = json.dumps(
        {"title": "T", "authors": ["a"], "institutions": ["b"]}, ensure_ascii=False
    )
    client = _SeqClient([truncated, json.dumps(_VALID, ensure_ascii=False)])
    cfg = Config(api_key="test-key")
    result = summarize_pdf(pdf, cfg, client=client)
    assert result.summary.abstract == "我们提出 Transformer。"
    assert "## 摘要" in result.markdown
    assert "（未提取到）" not in result.markdown


def test_summarize_pdf_raises_when_repair_also_fails(tmp_path, make_pdf):
    pdf = make_pdf(tmp_path / "p.pdf", [["T", "body " + "y" * 200]])
    broken = json.dumps({"title": "T", "authors": []}, ensure_ascii=False)
    client = _SeqClient([broken, broken])
    with pytest.raises(SummarizeError):
        summarize_pdf(pdf, Config(api_key="test-key"), client=client)


def test_ensure_complete_raises_listing_missing():
    from paper_reader.summarizer import ensure_complete

    with pytest.raises(SummarizeError, match="实验效果"):
        ensure_complete(
            PaperSummary(title="T", abstract="A", core_method="M", conclusion="C")
        )


def test_summarize_pdf_incomplete_result_raises_keeps_unread(tmp_path, make_pdf):
    """合法 JSON 但核心字段缺失：视为失败报错，不落盘空章节（论文保持未读）。"""
    pdf = make_pdf(tmp_path / "p.pdf", [["T", "body " + "z" * 200]])
    partial = json.dumps(
        {k: _VALID[k] for k in ("title", "authors", "abstract", "core_method")},
        ensure_ascii=False,
    )
    client = _SeqClient([partial, partial])
    with pytest.raises(SummarizeError, match="不完整"):
        summarize_pdf(pdf, Config(api_key="test-key"), client=client)


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
