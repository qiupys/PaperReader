"""论文对话：基于单篇论文上下文与 LLM 继续多轮问答。

上下文来源（按可用性拼接）：
  - summary markdown（剥离「原始推理过程」后的部分）
  - PDF 提取文本（pypdf，截断到 max_chars 以控制 token）
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from openai import OpenAI

from .config import Config
from .extract import ExtractionError, extract_text
from .summarizer import build_client, strip_reasoning

CHAT_SYSTEM_TEMPLATE = """你是一位资深的科研论文阅读助手，正在与用户就一篇论文进行深入讨论。
请基于下面提供的论文资料回答问题：回答准确、简洁、有依据；资料中没有的信息请明确说明，
不要编造。涉及论文之外的知识时，可结合你的专业知识作答，但需指出这不是论文原文内容。
回答使用中文（专有名词、论文名称、数据集与指标名可保留英文），支持 Markdown 格式。
引用论文原文作为依据时，尽量在对应句子末尾标注页码，格式如 [p.3]
（页码取自资料中的「【第 N 页】」标记）；无法确定页码时不要标注，绝不要编造页码。

=== 论文资料开始 ===
{context}
=== 论文资料结束 ===
"""

MAX_HISTORY_MESSAGES = 20
MAX_CHAT_CONTEXT_CHARS = 80_000


class ChatError(Exception):
    """对话调用失败。"""


@dataclass
class ChatMessage:
    role: str  # "user" | "assistant"
    content: str


def build_context(
    pdf_path: Path,
    cfg: Config,
    summary_markdown: str | None,
) -> str:
    """拼接论文上下文：summary（如有）+ PDF 提取文本。"""
    parts: list[str] = []
    if summary_markdown and summary_markdown.strip():
        parts.append("## 已生成的结构化 Summary\n\n" + strip_reasoning(summary_markdown))
    try:
        extracted = extract_text(pdf_path, max_chars=MAX_CHAT_CONTEXT_CHARS)
        note = "\n\n（注：论文较长，仅节选部分全文。）" if extracted.truncated else ""
        parts.append(
            "## 论文全文节选\n\n" + extracted.text + note
        )
    except ExtractionError:
        if not parts:
            raise ChatError(f"无法提取论文内容: {pdf_path.name}") from None
        # summary 存在但 PDF 提取失败：仅用 summary 也能对话
    return "\n\n---\n\n".join(parts)


DIGEST_CLIPS_INSTRUCTION = """你是一位论文阅读助手。下面是用户阅读一篇论文时逐条保存的对话摘录（问 + 答）。
请把它们提炼归纳成一篇精炼的 Markdown 笔记：按主题重新组织分点，合并重复与相近的内容，
保留关键数据、结论与重要细节；是对内容的归纳提炼，而不是逐条复述问答。
开头第一行写 `## 对话整理`，第二行写斜体的整理日期（使用系统消息中提供的整理日期，不要自己编造）。
"""

MERGE_DIGEST_INSTRUCTION = """你是一位论文阅读助手。用户阅读一篇论文时已有一篇整理笔记，之后又追加保存了新的对话摘录（问 + 答）。
请把「已有的整理笔记」与「新增的对话摘录」融合重写成一篇更完整、浑然一体的 Markdown 笔记：
以主题为线索重新组织结构，把新摘录中的信息点自然合并进对应主题，去重、互补、修正；
不要机械拼接两部分，也不要逐条复述对话；保留全部关键数据与结论。
开头第一行写 `## 对话整理`，第二行写斜体的整理日期（使用系统消息中提供的整理日期，不要自己编造）。
"""

REDIGEST_INSTRUCTION = """你是一位论文阅读助手。请重新整理下面这篇论文笔记：
更精炼、结构更清晰（Markdown 分点 / 小节），保留全部关键信息与数据，不要新增内容、不要编造；
保留原第一行标题，第二行写斜体的整理日期（使用系统消息中提供的整理日期）。
注意：笔记正文中不要出现「整理日期」这几个字以外的任何元信息。
"""


def digest_notes(
    cfg: Config,
    content: str,
    instruction: str,
    client: OpenAI | None = None,
) -> str:
    """把一段笔记内容交给 LLM 按指定指令重新整理。"""
    if not cfg.api_key:
        raise ChatError("未配置 API Key，无法整理笔记")
    content = (content or "").strip()
    if not content:
        raise ChatError("没有可整理的内容")
    if client is None:
        client = build_client(cfg)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    messages = [
        {"role": "system", "content": f"{instruction}\n\n整理日期：{now}"},
        {
            "role": "user",
            "content": f"=== 笔记内容开始 ===\n\n{content}\n\n=== 笔记内容结束 ===",
        },
    ]
    try:
        resp = client.chat.completions.create(model=cfg.model, messages=messages)
    except Exception as e:  # noqa: BLE001
        raise ChatError(f"调用 LLM API 失败: {e}") from e
    out = (resp.choices[0].message.content or "").strip()
    # 兜底：剥离模型误复制进正文的「整理日期：…」行
    out = re.sub(r"^\s*整理日期[:：].*\n?", "", out).strip()
    if not out:
        raise ChatError("模型返回内容为空，请重试")
    return out


def build_chat_messages(
    pdf_path: Path | str,
    cfg: Config,
    message: str,
    history: list[ChatMessage] | None = None,
    summary_markdown: str | None = None,
) -> list[dict[str, str]]:
    """构建一轮对话的完整消息列表（system + 历史 + 本轮提问），供普通/流式端点共用。"""
    pdf_path = Path(pdf_path)
    if not cfg.api_key:
        raise ChatError(
            "未配置 API Key：请设置 PAPERREADER_API_KEY（或 OPENAI_API_KEY），"
            "或在 paperreader.toml 中配置 api_key"
        )
    message = (message or "").strip()
    if not message:
        raise ChatError("消息内容为空")

    context = build_context(pdf_path, cfg, summary_markdown)
    messages: list[dict[str, str]] = [
        {"role": "system", "content": CHAT_SYSTEM_TEMPLATE.format(context=context)}
    ]
    # 只保留最近 N 轮，防止上下文无限膨胀
    for m in (history or [])[-MAX_HISTORY_MESSAGES:]:
        role = m.role if m.role in ("user", "assistant") else "user"
        content = (m.content or "").strip()
        if content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message})
    return messages


def chat_reply(
    pdf_path: Path | str,
    cfg: Config,
    message: str,
    history: list[ChatMessage] | None = None,
    summary_markdown: str | None = None,
    client: OpenAI | None = None,
) -> str:
    """发起一轮对话，返回模型回复（不包含思考过程）。"""
    messages = build_chat_messages(
        pdf_path, cfg, message, history=history, summary_markdown=summary_markdown
    )
    if client is None:
        client = build_client(cfg)

    try:
        resp = client.chat.completions.create(model=cfg.model, messages=messages)
    except Exception as e:  # noqa: BLE001
        raise ChatError(f"调用 LLM API 失败: {e}") from e

    msg = resp.choices[0].message
    content = (msg.content or "").strip()
    if not content:
        # 思考型模型偶尔只返回 reasoning_content：给出明确提示而非空白
        reasoning = getattr(msg, "reasoning_content", None) or (
            (msg.model_extra or {}).get("reasoning_content") if msg.model_extra else None
        )
        raise ChatError(
            "模型返回内容为空" + ("（仅输出了思考过程，请重试）" if reasoning else "，请重试")
        )
    return content
