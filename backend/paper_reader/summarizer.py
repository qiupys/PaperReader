"""调用 OpenAI 兼容 API 生成论文 summary（含 Agent 思考），并渲染为 Markdown。

summary markdown 结构（与「已读」判定配套的固定模板）：
  - frontmatter：title / authors / institutions / venue / year / model / generated_at
  - 正文：摘要、核心技术方案、实验效果、结论、Agent 思考（+ 原始推理过程）
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

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

注意：各字段的内容请分段/分点组织（可用 Markdown 语法），保持段落与层次，不要把所有内容挤进单一长段落；JSON 字符串内的换行必须转义。不要在内容中输出 [p.N] 之类的页码标注。

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

    def has_content(self) -> bool:
        """摘要/方案/实验/结论四大内容字段是否至少有一个非空。"""
        return any(
            (getattr(self, f) or "").strip()
            for f in ("abstract", "core_method", "experiments", "conclusion")
        )


# Summary 落盘前的完整性要求：核心内容字段全非空，否则视为失败（论文保持未读）
REQUIRED_FIELDS = ("abstract", "core_method", "experiments", "conclusion")
REQUIRED_LABELS = {
    "abstract": "摘要",
    "core_method": "核心技术方案",
    "experiments": "实验效果",
    "conclusion": "结论",
}


def ensure_complete(summary: PaperSummary) -> None:
    """校验核心内容字段全部非空；缺失即抛错（调用方不得将空章节落盘为已读）。"""
    missing = [
        REQUIRED_LABELS[f]
        for f in REQUIRED_FIELDS
        if not (getattr(summary, f) or "").strip()
    ]
    if missing:
        raise SummarizeError(
            "生成结果不完整（缺少：" + "、".join(missing) + "），已放弃保存；"
            "论文保持未读，请重试或更换模型"
        )


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


def _summary_from_data(data: dict) -> PaperSummary:
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


def parse_summary(content: str) -> PaperSummary:
    """从模型输出解析 JSON（容忍代码块包裹、前后多余文本、尾逗号与多余花括号）。

    用 ``JSONDecoder.raw_decode`` 从每个 ``{`` 起点尝试解码：天然忽略 JSON
    后面粘连的多余文本，也不受字符串内花括号影响。只含元信息、内容字段
    （摘要/方案/实验/结论）全空的对象（典型：输出被截断后只剩元信息）不算
    解析成功，继续尝试其他起点；全部失败才报错。
    """
    text = content.strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    if "{" not in text:
        raise SummarizeError(
            f"模型输出中未找到 JSON 对象。原始输出前 500 字：\n{content[:500]}"
        )
    # 模型常见小毛病：数组/对象末尾多一个逗号
    text = re.sub(r",\s*([}\]])", r"\1", text)
    decoder = json.JSONDecoder()
    last_err = "未找到内容非空的 JSON 对象"
    for m in re.finditer(r"{", text):
        try:
            data, _ = decoder.raw_decode(text, m.start())
        except json.JSONDecodeError as e:
            last_err = str(e)
            continue
        if not isinstance(data, dict):
            continue
        parsed = _summary_from_data(data)
        if parsed.has_content():
            return parsed
        last_err = "解析结果内容字段全为空（疑似输出被截断）"
    raise SummarizeError(
        f"模型输出 JSON 解析失败（可能被截断）：{last_err}\n原始输出前 500 字：\n{content[:500]}"
    )


def repair_summary_json(
    cfg: Config, raw: str, client: OpenAI | None = None
) -> PaperSummary:
    """解析失败后的自动修复：把损坏的模型输出交回 LLM 修正为合法 JSON 再解析。"""
    if client is None:
        client = build_client(cfg)
    messages = [
        {
            "role": "system",
            "content": (
                "你是 JSON 修复器。用户给出的 JSON 已损坏（可能被截断或格式错误），"
                "请修复为完整合法的 JSON 对象并只输出该 JSON，"
                "不要任何解释、不要 markdown 代码块标记。"
                "保留原有字段与内容；无法恢复的字段补空字符串或空数组。"
            ),
        },
        {"role": "user", "content": raw[:20000]},
    ]
    try:
        resp = call_llm(client, model=cfg.model, messages=messages)
        text = (resp.choices[0].message.content or "").strip()
    except Exception as e:  # noqa: BLE001
        raise SummarizeError(f"JSON 自动修复请求失败：{e}") from e
    if not text:
        raise SummarizeError("JSON 自动修复返回为空")
    return parse_summary(text)


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


def _is_opencode_base(api_base: str) -> bool:
    """判断端点是否为 OpenCode 网关（其要求客户端提供会话 ID 头）。

    仅按域名识别，其他 OpenAI 兼容网关不附加任何专属头。
    """
    try:
        host = urlparse(api_base).hostname or ""
    except ValueError:
        return False
    return "opencode" in host.lower()


def build_client(cfg: Config) -> OpenAI:
    """按配置构造 OpenAI 兼容客户端（含可选代理与附加请求头）。

    标准接入只需 api_base + api_key + model 三项；网关专属头按域名自动附加
    （目前仅 OpenCode），cfg.extra_headers 可配置任意附加头且优先级最高。
    同一配置复用客户端实例：省去每次请求重建连接池 / TLS 握手的开销。
    """
    headers_extra = {str(k): str(v) for k, v in (cfg.extra_headers or {}).items()}
    key = (
        cfg.api_base,
        cfg.api_key,
        _normalize_proxy(cfg.proxy),
        cfg.timeout,
        tuple(sorted(headers_extra.items())),
    )
    cached = _CLIENT_CACHE.get(key)
    if cached is not None:
        return cached
    default_headers: dict[str, str] = {"User-Agent": USER_AGENT}
    if _is_opencode_base(cfg.api_base):
        default_headers["x-opencode-session"] = _SESSION_ID
    default_headers.update(headers_extra)
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


# 上游 LLM API 偶发 5xx/网络抖动时的重试参数：最多 3 次尝试（1 次原始 + 2 次重试）
LLM_MAX_ATTEMPTS = 3
LLM_RETRY_BASE_DELAY = 1.0


def _is_retryable_llm_error(exc: Exception) -> bool:
    """判断 LLM 调用异常是否值得重试。

    - 带 HTTP 状态码：仅 5xx（上游内部错误）与 429（限流）重试；
      4xx 的参数/鉴权/内容类错误重试也不会成功，直接抛出。
    - 不带状态码（连接失败、超时等网络异常）：重试。
    """
    status = getattr(exc, "status_code", None)
    return status is None or status == 429 or status >= 500


def call_llm(client: OpenAI, *, model: str, messages: list[dict]):
    """调用 chat completions，对上游瞬时错误（5xx/429/网络异常）做指数退避重试。

    所有非流式 LLM 调用统一走这里；重试仍失败时抛出最后一次的原始异常，
    由各调用方按自身语义包装（SummarizeError / ChatError / 静默降级）。
    """
    last_exc: Exception | None = None
    for attempt in range(1, LLM_MAX_ATTEMPTS + 1):
        try:
            return client.chat.completions.create(model=model, messages=messages)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if not _is_retryable_llm_error(exc) or attempt == LLM_MAX_ATTEMPTS:
                raise
            time.sleep(LLM_RETRY_BASE_DELAY * (2 ** (attempt - 1)))
    raise last_exc  # pragma: no cover —— 循环内必 return 或 raise，此处仅类型收窄


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
        resp = call_llm(client, model=cfg.model, messages=messages)
    except Exception as e:  # noqa: BLE001
        raise SummarizeError(f"调用 LLM API 失败: {e}") from e

    msg = resp.choices[0].message
    content = msg.content or ""
    reasoning = getattr(msg, "reasoning_content", None)
    if not reasoning and getattr(msg, "model_extra", None):
        reasoning = msg.model_extra.get("reasoning_content")
    if not content.strip():
        raise SummarizeError("模型返回内容为空，可尝试重试或更换模型")

    # 解析失败自动修复一轮（截断 / 多余文本 / 格式错误），修复仍失败才报错
    try:
        parsed = parse_summary(content)
    except SummarizeError:
        parsed = repair_summary_json(cfg, content, client=client)
    # 内容不完整（核心字段缺失）视为失败：不落盘，论文保持未读
    ensure_complete(parsed)
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
