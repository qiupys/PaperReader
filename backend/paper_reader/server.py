"""FastAPI 服务：为 Web 前端与外部工具提供 REST API。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .chat import (
    ChatError,
    ChatMessage,
    build_chat_messages,
    chat_reply,
    digest_notes,
)
from .config import Config, load_config
from .extract import ExtractionError, extract_text
from .library import PaperInfo, find_paper, scan
from .summarizer import (
    SummarizeError,
    build_client,
    parse_summary,
    render_markdown,
    strip_reasoning,
    summarize_pdf,
    write_summary,
)

app = FastAPI(title="PaperReader", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

router = APIRouter(prefix="/api")


class ChatHistoryItem(BaseModel):
    role: str = "user"
    content: str = ""


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    history: list[ChatHistoryItem] = Field(default_factory=list)


class PaperMetaUpdate(BaseModel):
    starred: bool | None = None
    note: str | None = None
    clips: list[dict] | None = None
    digests: list[dict] | None = None


class ImportRequest(BaseModel):
    url: str = Field(min_length=1)
    filename: str | None = None


class DigestClipsRequest(BaseModel):
    clips: list[dict] = Field(default_factory=list)
    prior: str = ""  # 已有的整理笔记文本；非空时做「旧笔记 + 新摘录」合并整理


class RedigestRequest(BaseModel):
    text: str = Field(min_length=1)


# ---------- 论文元信息（星标 / 个人笔记）：项目根 paperreader_meta.json ----------

META_FILE_NAME = "paperreader_meta.json"


def _meta_path(cfg: Config) -> Path:
    return Path(cfg.papers_dir).parent / META_FILE_NAME


def _load_metas(cfg: Config) -> dict[str, dict]:
    path = _meta_path(cfg)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_metas(cfg: Config, metas: dict[str, dict]) -> None:
    path = _meta_path(cfg)
    path.write_text(
        json.dumps(metas, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _paper_or_404(stem: str) -> PaperInfo:
    cfg = load_config()
    info = find_paper(cfg.papers_dir, cfg.summaries_dir, stem)
    if info is None:
        raise HTTPException(status_code=404, detail=f"未找到论文: {stem}")
    root = Path(cfg.papers_dir).resolve()
    if not info.pdf_path.resolve().is_relative_to(root):
        raise HTTPException(status_code=404, detail="非法路径")
    return info


def _paper_dict(p: PaperInfo) -> dict:
    return {
        "stem": p.stem,
        "pdf_path": str(p.pdf_path),
        "summary_path": str(p.summary_path),
        "size": p.size,
        "mtime": p.mtime,
        "read": p.read,
        "title": p.title,
    }


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/papers")
def list_papers() -> dict:
    cfg = load_config()
    papers = scan(cfg.papers_dir, cfg.summaries_dir)
    n_read = sum(1 for p in papers if p.read)
    return {
        "papers": [_paper_dict(p) for p in papers],
        "stats": {"total": len(papers), "read": n_read, "unread": len(papers) - n_read},
    }


@router.get("/papers/{stem:path}/summary")
def get_summary(stem: str) -> dict:
    info = _paper_or_404(stem)
    if not info.summary_path.is_file():
        return {"stem": info.stem, "read": False, "title": None, "markdown": None}
    markdown = info.summary_path.read_text(encoding="utf-8")
    # 思考型模型的原始推理过程仅存档，不在界面展示
    return {
        "stem": info.stem,
        "read": True,
        "title": info.title,
        "markdown": strip_reasoning(markdown),
    }


@router.delete("/papers/{stem:path}/summary")
def delete_summary(stem: str) -> dict:
    """删除 summary，将论文标记回未读。"""
    info = _paper_or_404(stem)
    if info.summary_path.is_file():
        info.summary_path.unlink()
    return {"stem": info.stem, "read": False}


@router.post("/papers/{stem:path}/summarize")
def summarize(stem: str, force: bool = False) -> dict:
    info = _paper_or_404(stem)
    if info.read and not force:
        raise HTTPException(status_code=409, detail="summary 已存在（force=true 重新生成）")
    cfg = load_config()
    try:
        result = summarize_pdf(info.pdf_path, cfg)
    except SummarizeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except ExtractionError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    write_summary(info.summary_path, result.markdown)
    return {
        "stem": info.stem,
        "read": True,
        "title": result.summary.title,
        "summary_path": str(info.summary_path),
        "pages": result.pages,
        "chars": result.chars,
        # 思考型模型的原始推理过程仅存档，不在界面展示
        "markdown": strip_reasoning(result.markdown),
    }


def _sse(payload: dict) -> str:
    """编码为 SSE 事件帧。"""
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


@router.post("/papers/{stem:path}/summarize/stream")
def summarize_stream(stem: str, force: bool = False):
    """流式生成 Summary：SSE 推送增量文本，结束时返回解析渲染后的完整 markdown。"""
    info = _paper_or_404(stem)
    if info.read and not force:
        raise HTTPException(status_code=409, detail="summary 已存在（force=true 重新生成）")
    cfg = load_config()
    try:
        extracted = extract_text(info.pdf_path, max_chars=cfg.max_chars)
    except ExtractionError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    from .summarizer import SYSTEM_PROMPT, build_user_prompt

    client = build_client(cfg)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": build_user_prompt(extracted.text, info.pdf_path.name),
        },
    ]

    def gen():
        try:
            stream = client.chat.completions.create(
                model=cfg.model, messages=messages, stream=True
            )
            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                text = getattr(delta, "content", None) or ""
                reasoning = getattr(delta, "reasoning_content", None) or (
                    (delta.model_extra or {}).get("reasoning_content")
                    if getattr(delta, "model_extra", None)
                    else None
                )
                if reasoning:
                    reasoning_parts.append(reasoning)  # 思考过程仅存档，不推送给前端
                if text:
                    content_parts.append(text)
                    yield _sse({"type": "delta", "text": text})
            parsed = parse_summary("".join(content_parts))
            markdown = render_markdown(
                parsed,
                reasoning="".join(reasoning_parts) or None,
                model=cfg.model,
                source_pdf=info.pdf_path.name,
            )
            write_summary(info.summary_path, markdown)
            yield _sse(
                {
                    "type": "done",
                    "stem": info.stem,
                    "read": True,
                    "title": parsed.title,
                    "markdown": strip_reasoning(markdown),
                }
            )
        except SummarizeError as e:
            yield _sse({"type": "error", "message": str(e)})
        except Exception as e:  # noqa: BLE001
            yield _sse({"type": "error", "message": f"生成失败：{e}"})

    return StreamingResponse(
        gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


@router.get("/papers/{stem:path}/text")
def get_text(stem: str) -> dict:
    info = _paper_or_404(stem)
    cfg = load_config()
    try:
        extracted = extract_text(info.pdf_path, max_chars=cfg.max_chars)
    except ExtractionError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {
        "stem": info.stem,
        "pages": extracted.pages,
        "chars": extracted.chars,
        "truncated": extracted.truncated,
        "text": extracted.text,
    }


@router.post("/papers/{stem:path}/chat")
def chat(stem: str, body: ChatRequest) -> dict:
    """基于单篇论文上下文与 LLM 继续多轮对话（history 由前端保存并回传）。"""
    info = _paper_or_404(stem)
    cfg = load_config()
    summary_md = (
        info.summary_path.read_text(encoding="utf-8")
        if info.summary_path.is_file()
        else None
    )
    history = [ChatMessage(role=m.role, content=m.content) for m in body.history]
    try:
        reply = chat_reply(
            info.pdf_path,
            cfg,
            message=body.message,
            history=history,
            summary_markdown=summary_md,
        )
    except ChatError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except ExtractionError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {"stem": info.stem, "reply": reply, "model": cfg.model}


@router.post("/papers/{stem:path}/chat/stream")
def chat_stream(stem: str, body: ChatRequest):
    """流式对话：SSE 推送回答增量（思考过程不推送），结束时 done。"""
    info = _paper_or_404(stem)
    cfg = load_config()
    summary_md = (
        info.summary_path.read_text(encoding="utf-8")
        if info.summary_path.is_file()
        else None
    )
    history = [ChatMessage(role=m.role, content=m.content) for m in body.history]
    try:
        messages = build_chat_messages(
            info.pdf_path,
            cfg,
            message=body.message,
            history=history,
            summary_markdown=summary_md,
        )
    except ChatError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except ExtractionError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    client = build_client(cfg)

    def gen():
        try:
            stream = client.chat.completions.create(
                model=cfg.model, messages=messages, stream=True
            )
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                text = getattr(delta, "content", None) or ""
                if text:  # reasoning_content 不推送：思考过程不进对话
                    yield _sse({"type": "delta", "text": text})
            yield _sse({"type": "done"})
        except Exception as e:  # noqa: BLE001
            yield _sse({"type": "error", "message": f"对话失败：{e}"})

    return StreamingResponse(
        gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


@router.get("/meta")
def get_metas() -> dict:
    """全部论文元信息（星标 / 笔记），key 为 stem。"""
    cfg = load_config()
    return {"metas": _load_metas(cfg)}


@router.put("/papers/{stem:path}/meta")
def update_meta(stem: str, body: PaperMetaUpdate) -> dict:
    """更新论文元信息：星标、手写笔记、对话摘录（clips）、AI 整理（digests）。"""
    info = _paper_or_404(stem)
    cfg = load_config()
    metas = _load_metas(cfg)
    entry = metas.get(info.stem) if isinstance(metas.get(info.stem), dict) else {}
    clips = entry.get("clips") if isinstance(entry.get("clips"), list) else []
    digests = entry.get("digests") if isinstance(entry.get("digests"), list) else []
    entry = {
        "starred": bool(entry.get("starred")),
        "note": str(entry.get("note") or ""),
        "clips": clips,
        "digests": digests,
    }
    if body.starred is not None:
        entry["starred"] = body.starred
    if body.note is not None:
        entry["note"] = body.note
    if body.clips is not None:
        entry["clips"] = body.clips
    if body.digests is not None:
        entry["digests"] = body.digests
    metas[info.stem] = entry
    _save_metas(cfg, metas)
    return {"stem": info.stem, "meta": entry}


@router.post("/papers/{stem:path}/digest-clips")
def digest_clips(stem: str, body: DigestClipsRequest) -> dict:
    """把追加的对话摘录交给 LLM，整理成一篇结构化笔记；如有旧笔记则合并整理。"""
    import uuid
    from datetime import datetime

    from .chat import DIGEST_CLIPS_INSTRUCTION, MERGE_DIGEST_INSTRUCTION

    info = _paper_or_404(stem)
    cfg = load_config()
    lines: list[str] = []
    for c in body.clips:
        if not isinstance(c, dict):
            continue
        q = str(c.get("question") or "").strip()
        a = str(c.get("answer") or "").strip()
        if q:
            lines.append(f"**问**：{q}")
        if a:
            lines.append(f"**答**：{a}")
    clips_text = "\n\n".join(lines)
    prior = (body.prior or "").strip()
    if prior:
        content = (
            "## 已有的整理笔记\n\n" + prior + "\n\n## 新增的对话摘录\n\n" + clips_text
        )
        instruction = MERGE_DIGEST_INSTRUCTION
    else:
        content = clips_text
        instruction = DIGEST_CLIPS_INSTRUCTION
    try:
        text = digest_notes(cfg, content, instruction)
    except ChatError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {
        "digest": {
            "id": uuid.uuid4().hex[:8],
            "ts": datetime.now().isoformat(timespec="seconds"),
            "text": text,
        }
    }


@router.post("/papers/{stem:path}/redigest")
def redigest(stem: str, body: RedigestRequest) -> dict:
    """重新整理一段已有笔记文本。"""
    import uuid
    from datetime import datetime

    from .chat import REDIGEST_INSTRUCTION

    info = _paper_or_404(stem)
    cfg = load_config()
    try:
        text = digest_notes(cfg, body.text, REDIGEST_INSTRUCTION)
    except ChatError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {
        "digest": {
            "id": uuid.uuid4().hex[:8],
            "ts": datetime.now().isoformat(timespec="seconds"),
            "text": text,
        }
    }


@router.post("/papers/import")
def import_paper(body: ImportRequest) -> dict:
    """从 URL 下载 PDF 入库（支持 arXiv abs/pdf 链接自动转换）。"""
    import httpx
    import urllib.parse

    from .summarizer import USER_AGENT

    cfg = load_config()
    url = body.url.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=422, detail="仅支持 http/https 链接")

    # arXiv 页面链接（/abs/<id>）自动转为 PDF 直链
    m = re.match(
        r"(?:https?://)?(?:www\.)?arxiv\.org/(?:abs|pdf)/([^\s/?#]+?)(?:\.pdf)?(?:[/?#].*)?$",
        url,
    )
    if m:
        url = f"https://arxiv.org/pdf/{m.group(1)}"

    proxy = (cfg.proxy or "").strip()
    if proxy and "://" not in proxy:
        proxy = "http://" + proxy
    try:
        resp = httpx.Client(
            proxy=proxy or None,
            timeout=120.0,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ).get(url)
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502, detail=f"下载失败：远端返回 HTTP {e.response.status_code}"
        ) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"下载失败：{e}") from e

    content = resp.content
    if len(content) < 100:
        raise HTTPException(status_code=422, detail="下载内容为空或过小，请检查链接")
    if len(content) > 200 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件超过 200 MB，暂不支持")
    if not content.startswith(b"%PDF"):
        ctype = resp.headers.get("content-type", "")
        if "pdf" not in ctype.lower():
            raise HTTPException(
                status_code=422, detail="链接返回的不是 PDF 文件（指向了网页？）"
            )

    # 文件名：请求指定 > Content-Disposition > URL 最后一段
    filename = (body.filename or "").strip()
    if not filename:
        cd = resp.headers.get("content-disposition", "")
        cm = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?", cd, re.I)
        if cm:
            filename = urllib.parse.unquote(cm.group(1).strip())
    if not filename:
        path = urllib.parse.urlparse(url).path
        filename = path.rstrip("/").split("/")[-1] or "paper.pdf"
    filename = urllib.parse.unquote(filename)
    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename).strip(". ")
    if not filename or filename == ".pdf":
        filename = "paper.pdf"
    if len(filename) > 120:
        filename = filename[:115] + ".pdf"

    dest = Path(cfg.papers_dir) / filename
    if dest.exists():
        raise HTTPException(
            status_code=409, detail=f"papers/ 下已存在同名文件：{filename}"
        )

    dest.write_bytes(content)
    return {
        "stem": filename.removesuffix(".pdf"),
        "filename": filename,
        "size": len(content),
    }


@router.get("/search")
def search_fulltext(q: str) -> dict:
    """跨所有 Summary 全文搜索，返回命中论文与片段（大小写不敏感）。"""
    cfg = load_config()
    query = q.strip()
    if not query:
        return {"query": query, "results": []}
    needle = query.lower()
    results: list[dict] = []
    for info in scan(cfg.papers_dir, cfg.summaries_dir):
        if not info.read:
            continue
        try:
            text = info.summary_path.read_text(encoding="utf-8")
        except OSError:
            continue
        body = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.DOTALL)
        pos = body.lower().find(needle)
        if pos == -1:
            continue
        start = max(0, pos - 60)
        end = min(len(body), pos + len(query) + 100)
        snippet = body[start:end].replace("\n", " ").strip()
        prefix = "…" if start > 0 else ""
        suffix = "…" if end < len(body) else ""
        results.append(
            {
                "stem": info.stem,
                "title": info.title,
                "snippet": f"{prefix}{snippet}{suffix}",
            }
        )
        if len(results) >= 50:
            break
    return {"query": query, "results": results}


@router.get("/papers/{stem:path}/pdf")
def get_pdf(stem: str) -> FileResponse:
    info = _paper_or_404(stem)
    return FileResponse(
        info.pdf_path,
        media_type="application/pdf",
        filename=info.pdf_path.name,
        # inline：浏览器内嵌预览（iframe / 新标签页），而不是触发下载
        content_disposition_type="inline",
    )


app.include_router(router)


def _mount_frontend() -> None:
    candidates = [
        Path.cwd() / "frontend" / "dist",
        Path(__file__).resolve().parents[2] / "frontend" / "dist",
    ]
    for dist in candidates:
        if (dist / "index.html").is_file():
            app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
            return


_mount_frontend()
