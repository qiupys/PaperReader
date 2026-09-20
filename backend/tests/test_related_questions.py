from __future__ import annotations

from paper_reader.chat import _parse_related_questions


def test_parse_plain_array():
    text = '["方法 X 与 Y 的区别是什么？", "实验里 BLEU 提升多少？", "局限有哪些？"]'
    assert _parse_related_questions(text) == [
        "方法 X 与 Y 的区别是什么？",
        "实验里 BLEU 提升多少？",
        "局限有哪些？",
    ]


def test_parse_fenced_and_wrapped():
    text = '好的：\n```json\n["问题一？", "问题二？", "问题三？"]\n```\n以上。'
    assert _parse_related_questions(text) == ["问题一？", "问题二？", "问题三？"]


def test_parse_trailing_comma_tolerated():
    text = '["a？", "b？", "c？",]'
    assert _parse_related_questions(text) == ["a？", "b？", "c？"]


def test_parse_garbage_returns_empty():
    assert _parse_related_questions("抱歉，无法生成。") == []
    assert _parse_related_questions("") == []
    assert _parse_related_questions('{"a": 1}') == []


def test_parse_caps_at_three_and_truncates():
    text = '["一？", "二？", "三？", "四？"]'
    assert len(_parse_related_questions(text)) == 3
    long = '["' + "长" * 80 + '？"]'
    assert len(_parse_related_questions(long)[0]) == 50
