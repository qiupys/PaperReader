"""调用 OpenAI 兼容 API 生成论文 summary（含 Agent 思考），并渲染为 Markdown。

summary markdown 结构（与「已读」判定配套的固定模板）：
  - frontmatter：title / authors / institutions / venue / year / model / generated_at
  - 正文：摘要、核心技术方案、实验效果、结论、Agent 思考（+ 原始推理过程）
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx
from openai import OpenAI

from .config import Config
from .extract import ExtractedText, extract_text

USER_AGENT = "paper-reader/0.1.0"

# 进程级复用：客户端与 会话ID 各保留一份
_CLIENT_CACHE: dict[tuple, OpenAI] = {}
_SESSION_ID = f"paper-reader-{uuid.uuid4().hex[:16]}"

SYSTEM_PROMPT = (
    "你是一位资深的科研论文阅读助手。你的任务是通读论文全文，"
    "输出结构化的中文总结（专有名词、论文名称、数据集与指标名可保留英文）。"
    "严格遵守：只输出一个 JSON 对象，不要输出任何解释性文字或 markdown 代码块。"
)

USER_PROMPT_TEMPLATE = """请阅读以下论文全文，输出一个 JSON 对象，字段要求如下：

{{
  "title": "论文标题（保留英文原标题）",
  "authors": ["作者姓名列表，按原文顺序"],
  "institutions": ["作者机构列表，尽量具体到院系/实验室"],
  "venue": "发表期刊或会议（如 NeurIPS 2017），未知则为空字符串",
  "year": "发表年份（字符串），未知则为空字符串",
  "abstract": "摘要：3-5 句话概括研究问题、动机与主要贡献",
  "core_method": "核心技术方案：分 2-4 个自然段呈现——先整体框架与流程，再关键模块/算法设计（可用要点列表），最后总结主要创新点",
  "experiments": "实验效果：先用一段说明实验设置（数据集、对比基线、评估指标），再用要点列表呈现关键量化结果，指出相对基线的提升幅度",
  "conclusion": "结论：分段或用列表呈现论文结论、局限性、作者提出的未来方向",
  "agent_reflection": "你作为阅读 agent 的批判性思考：用分组要点呈现——优点、不足、贡献是否显著与实验证据是否充分、适合哪些读者、值得延伸阅读的相关方向"
}}

注意：各字段的内容请分段/分点组织（可用 Markdown 语法），保持段落与层次，不要把所有内容挤进单一长段落；JSON 字符串内的换行必须转义。

=== 论文全文开始 ===
{paper_text}
=== 论文全文结束 ===

提示：论文文件名为 {pdf_name}。再次强调：只输出 JSON 对象本身。"""


@dataclass
class PaperSummary:
    title: str = "Untitled"
    authors: list[str] = field(default_factory=list)
    institutions: list[str] = field(default_factory=list)
    venue: str = ""
    year: str = ""
    abstract: str = ""
    core_method: str = ""
    experiments: str = ""
    conclusion: str = ""
    agent_reflection: str = ""


@dataclass
class SummarizeResult:
    summary: PaperSummary
    reasoning: str | None  # 思考型模型返回的推理过程
    markdown: str
    model: str
    pages: int
    chars: int


class SummarizeError(Exception):
    """生成 summary 失败。"""


def build_user_prompt(paper_text: str, pdf_name: str) -> str:
    return USER_PROMPT_TEMPLATE.format(paper_text=paper_text, pdf_name=pdf_name)


def _normalize_str_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [p.strip() for p in re.split(r"[,，;；、]", value) if p.strip()]
    return []


def parse_summary(content: str) -> PaperSummary:
    """从模型输出解析 JSON（容忍代码块包裹与前后多余文本）。"""
    text = content.strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise SummarizeError(
            f"模型输出中未找到 JSON 对象。原始输出前 500 字：\n{content[:500]}"
        )
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        raise SummarizeError(
            f"模型输出 JSON 解析失败: {e}\n原始输出前 500 字：\n{content[:500]}"
        ) from e
    if not isinstance(data, dict):
        raise SummarizeError("模型输出的 JSON 不是对象")

    title = str(data.get("title") or "").strip() or "Untitled"
    return PaperSummary(
        title=title,
        authors=_normalize_str_list(data.get("authors")),
        institutions=_normalize_str_list(data.get("institutions")),
        venue=str(data.get("venue") or "").strip(),
        year=str(data.get("year") or "").strip(),
        abstract=str(data.get("abstract") or "").strip(),
        core_method=str(data.get("core_method") or data.get("method") or "").strip(),
        experiments=str(data.get("experiments") or data.get("results") or "").strip(),
        conclusion=str(data.get("conclusion") or "").strip(),
        agent_reflection=str(
            data.get("agent_reflection") or data.get("reflection") or ""
        ).strip(),
    )


def _q(value: str) -> str:
    """以 JSON 字符串形式输出，同时是合法的 YAML 标量，避免引号/冒号破坏 frontmatter。"""
    return json.dumps(value, ensure_ascii=False)


def render_markdown(
    summary: PaperSummary,
    reasoning: str | None = None,
    model: str = "",
    source_pdf: str = "",
) -> str:
    generated_at = datetime.now().isoformat(timespec="seconds")
    lines: list[str] = [
        "---",
        f"title: {_q(summary.title)}",
        f"authors: {json.dumps(summary.authors, ensure_ascii=False)}",
        f"institutions: {json.dumps(summary.institutions, ensure_ascii=False)}",
        f"venue: {_q(summary.venue)}",
        f"year: {_q(summary.year)}",
        f"model: {_q(model)}",
        f"generated_at: {_q(generated_at)}",
        f"source_pdf: {_q(source_pdf)}",
        "---",
        "",
        f"# {summary.title}",
        "",
    ]

    meta_rows: list[tuple[str, str]] = []
    if summary.authors:
        meta_rows.append(("作者", "；".join(summary.authors)))
    if summary.institutions:
        meta_rows.append(("机构", "；".join(summary.institutions)))
    source = "，".join(x for x in (summary.venue, summary.year) if x)
    if source:
        meta_rows.append(("来源", source))
    for label, value in meta_rows:
        lines.append(f"> **{label}**：{value}  ")
    if meta_rows:
        lines.append("")

    lines += [
        "## 摘要",
        "",
        summary.abstract or "（未提取到）",
        "",
        "## 核心技术方案",
        "",
        summary.core_method or "（未提取到）",
        "",
        "## 实验效果",
        "",
        summary.experiments or "（未提取到）",
        "",
        "## 结论",
        "",
        summary.conclusion or "（未提取到）",
        "",
        "## Agent 思考",
        "",
        summary.agent_reflection or "（模型未提供）",
        "",
    ]
    if reasoning and reasoning.strip():
        lines += ["### 原始推理过程", "", reasoning.strip(), ""]
    return "\n".join(lines)


def _normalize_proxy(proxy: str) -> str:
    """容忍用户只写 host:port，补全为 http:// 代理 URL。"""
    p = (proxy or "").strip()
    if p and "://" not in p:
        p = "http://" + p
    return p


REASONING_HEADING = "### 原始推理过程"

_STRIP_REASONING_RE = re.compile(
    rf"\n?{re.escape(REASONING_HEADING)}.*?(?=\n#{{1,6}}\s|\Z)", re.DOTALL
)


def strip_reasoning(markdown: str) -> str:
    """从 summary markdown 中删除「原始推理过程」整段（到下一个标题或文末）。

    思考型模型的原始推理过程只落盘存档，不在 Web 界面展示。
    """
    cleaned = _STRIP_REASONING_RE.sub("", markdown)
    # 兜底：兼容标题写法略有出入的历史文件（如多空格 / 前缀无空格）
    cleaned = re.sub(
        r"\n?#{1,6}\s*原始推理过程\s*.*?(?=\n#{1,6}\s|\Z)", "", cleaned, flags=re.DOTALL
    )
    return cleaned.rstrip() + "\n" if cleaned.strip() else cleaned


def build_client(cfg: Config) -> OpenAI:
    """按配置构造 OpenAI 兼容客户端（含 User-Agent / 会话头 / 可选代理）。

    同一配置复用客户端实例：省去每次请求重建连接池 / TLS 握手的开销。
    """
    key = (cfg.api_base, cfg.api_key, _normalize_proxy(cfg.proxy), cfg.timeout)
    cached = _CLIENT_CACHE.get(key)
    if cached is not None:
        return cached
    # 部分 OpenAI 兼容网关（如 OpenCode Go）要求客户端标识自身并提供会话 ID
    default_headers = {
        "User-Agent": USER_AGENT,
        "x-opencode-session": _SESSION_ID,
    }
    proxy = _normalize_proxy(cfg.proxy)
    http_client = None
    if proxy:
        http_client = httpx.Client(proxy=proxy, timeout=cfg.timeout)
    client = OpenAI(
        base_url=cfg.api_base,
        api_key=cfg.api_key,
        timeout=cfg.timeout,
        default_headers=default_headers,
        http_client=http_client,
    )
    if len(_CLIENT_CACHE) >= 4:
        _CLIENT_CACHE.clear()
    _CLIENT_CACHE[key] = client
    return client


def summarize_pdf(
    pdf_path: Path | str,
    cfg: Config,
    client: OpenAI | None = None,
) -> SummarizeResult:
    """提取 PDF 文本 -> 调用 LLM -> 解析 -> 渲染 markdown。client 参数供测试注入。"""
    pdf_path = Path(pdf_path)
    if not cfg.api_key:
        raise SummarizeError(
            "未配置 API Key：请设置环境变量 PAPERREADER_API_KEY（或 OPENAI_API_KEY），"
            "或在 paperreader.toml / ~/.config/paperreader/config.toml 中配置 api_key"
        )
    extracted: ExtractedText = extract_text(pdf_path, max_chars=cfg.max_chars)
    if client is None:
        client = build_client(cfg)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(extracted.text, pdf_path.name)},
    ]
    try:
        resp = client.chat.completions.create(model=cfg.model, messages=messages)
    except Exception as e:  # noqa: BLE001
        raise SummarizeError(f"调用 LLM API 失败: {e}") from e

    msg = resp.choices[0].message
    content = msg.content or ""
    reasoning = getattr(msg, "reasoning_content", None)
    if not reasoning and getattr(msg, "model_extra", None):
        reasoning = msg.model_extra.get("reasoning_content")
    if not content.strip():
        raise SummarizeError("模型返回内容为空，可尝试重试或更换模型")

    parsed = parse_summary(content)
    markdown = render_markdown(
        parsed, reasoning=reasoning, model=cfg.model, source_pdf=pdf_path.name
    )
    return SummarizeResult(
        summary=parsed,
        reasoning=reasoning if isinstance(reasoning, str) else None,
        markdown=markdown,
        model=cfg.model,
        pages=extracted.pages,
        chars=extracted.chars,
    )


def write_summary(summary_path: Path | str, markdown: str) -> Path:
    summary_path = Path(summary_path)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(markdown, encoding="utf-8")
    return summary_path
