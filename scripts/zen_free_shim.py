"""
zen_free_shim.py — OpenAI 兼容 -> OpenCode 免费模型 转换层

把 PaperReader（OpenAI SDK 客户端）的请求翻译给本地 opencode serve，
从而使用 opencode 免费档模型（如 mimo-v2.6-flash-free）。

端口: 8766
接口:
  GET  /v1/models                 免费模型列表（动态拉取 zen 网关，缓存 5 分钟；?refresh=1 强制刷新）
  POST /v1/chat/completions       OpenAI 格式（支持 stream=True，整段下发）
  GET  /health                    健康检查
启动时若发现 opencode serve(4096) 不在，会自动以脱离会话方式拉起。

日志: 全部输出到 stderr，由启动脚本重定向到 logs/zen_free_shim.log；
      包含每次调用的模型、提示词长度、耗时、回复长度与完整错误堆栈。
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import uuid

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse

# Windows Proactor 事件循环噪音：客户端中途断开连接时，
# _call_connection_lost 会抛 ConnectionResetError，纯日志噪音，静音处理。
if sys.platform == "win32":
    from asyncio.proactor_events import _ProactorBasePipeTransport

    _orig_ccl = _ProactorBasePipeTransport._call_connection_lost

    def _quiet_call_connection_lost(self, exc=None):  # noqa: ANN001, ANN202
        try:
            _orig_ccl(self, exc)
        except (ConnectionResetError, ConnectionAbortedError):
            pass

    _ProactorBasePipeTransport._call_connection_lost = _quiet_call_connection_lost

# ---------- 常量 ----------

OC_PORT = 4096
SHIM_PORT = 8766
OC_EXE = r"C:\Users\viruser.v-desktop\miniconda3\node_modules\opencode-ai\bin\opencode.exe"
# 相对本文件定位：本文件位于 <根>/scripts/ 下，根 = 仓库根或工作区根
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OC_LOG = os.path.join(_BASE, "logs", "opencode_serve.log")
OC_CWD = os.path.join(_BASE, "oc_workspace")
ZEN_BASE = "https://opencode.ai/zen/v1"  # 免费档端点（订阅端点是 /zen/go/v1）
ZEN_PROXY = "http://127.0.0.1:10808"  # 出站走 v2rayN
MODELS_TTL = 300  # 模型列表缓存秒数
# 单次 LLM 调用超时（秒）：免费档上游偶发卡死，超时即取消并清理会话。
# 可用环境变量 OC_CALL_TIMEOUT 覆盖。
CALL_TIMEOUT = float(os.environ.get("OC_CALL_TIMEOUT", "300"))

FALLBACK_MODELS = [
    "mimo-v2.6-flash-free",
    "nemotron-3-ultra-free",
    "nemotron-3.5-lightning-free",
    "longcat-2.5-preview-free",
    "ling-3.0-flash-fin-free",
    "muse-spark-1.3-contributor-free",
    "space-bunny-free",
]

# ---------- 日志 ----------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("shim")

# ---------- 状态 ----------

oc = httpx.Client(trust_env=False, timeout=1800)  # 本地 opencode，无代理
_models_cache: dict = {"ids": [], "ts": 0.0}


def ensure_oc_serve() -> None:
    """确认 opencode serve 存活，否则以 DETACHED_PROCESS 拉起并等就绪。"""
    base = f"http://127.0.0.1:{OC_PORT}"
    try:
        if oc.get(f"{base}/doc", timeout=3).status_code == 200:
            log.info("opencode serve 已在运行 (:%s)", OC_PORT)
            return
    except Exception:
        pass
    env = os.environ.copy()
    env["HTTP_PROXY"] = ZEN_PROXY
    env["HTTPS_PROXY"] = ZEN_PROXY
    env["NO_PROXY"] = "localhost,127.0.0.1"
    os.makedirs(OC_CWD, exist_ok=True)
    os.makedirs(os.path.dirname(OC_LOG), exist_ok=True)
    detached = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    with open(OC_LOG, "ab") as logf:
        subprocess.Popen(
            [OC_EXE, "serve", "--port", str(OC_PORT), "--hostname", "127.0.0.1"],
            stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            creationflags=detached, env=env, cwd=OC_CWD,
        )
    for i in range(30):
        time.sleep(1)
        try:
            if oc.get(f"{base}/doc", timeout=3).status_code == 200:
                log.info("opencode serve 已启动 (:%s, %ss)", OC_PORT, i + 1)
                return
        except Exception:
            pass
    raise RuntimeError("opencode serve 启动失败")


def fetch_free_models(force: bool = False) -> list[str]:
    """从 zen 网关动态拉取免费模型列表（-free 后缀），缓存 MODELS_TTL 秒。"""
    now = time.time()
    if not force and _models_cache["ids"] and now - _models_cache["ts"] < MODELS_TTL:
        return _models_cache["ids"]
    try:
        r = httpx.Client(
            trust_env=False, proxy=ZEN_PROXY, timeout=15
        ).get(f"{ZEN_BASE}/models")
        r.raise_for_status()
        ids = sorted(
            m.get("id") if isinstance(m, dict) else str(m)
            for m in r.json().get("data", [])
        )
        ids = [i for i in ids if i and (i.endswith("-free") or i == "big-pickle")]
        if ids:
            _models_cache["ids"] = ids
            _models_cache["ts"] = now
            log.info("模型列表已刷新：%d 个免费模型", len(ids))
            return ids
        raise RuntimeError("网关返回空列表")
    except Exception as e:  # noqa: BLE001
        log.warning("拉取模型列表失败（%s），使用内置回退列表", e)
        return _models_cache["ids"] or FALLBACK_MODELS


def flatten_messages(messages: list[dict]) -> str:
    label = {"system": "System", "user": "User", "assistant": "Assistant", "tool": "Tool"}
    segs = []
    for m in messages:
        role = label.get(m.get("role", "user"), "User")
        content = m.get("content", "")
        if isinstance(content, list):  # 多模态数组：取文本段
            content = "\n".join(
                p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
            )
        if str(content).strip():
            segs.append(f"{role}: {content}")
    return "\n\n".join(segs)


class CallTimeout(RuntimeError):
    """单次模型调用超时（已清理会话）。"""


def ask_opencode(prompt: str, model: str) -> str:
    """建一次性会话 -> 发消息（限时）-> 抽取 assistant 文本 -> 删会话。

    超时或失败都会尽力删除会话，避免 opencode 里积累僵尸会话。
    """
    base = f"http://127.0.0.1:{OC_PORT}"
    sid = oc.post(f"{base}/session", json={"title": "shim"}).json()["id"]
    log.info("opencode 会话 %s 已创建（model=%s, prompt=%d 字符, 超时 %ds）",
             sid, model, len(prompt), int(CALL_TIMEOUT))
    try:
        t0 = time.time()
        try:
            r = oc.post(
                f"{base}/session/{sid}/message",
                json={
                    "parts": [{"type": "text", "text": prompt}],
                    "model": {"providerID": "opencode", "modelID": model},
                    "agent": "build",
                },
                timeout=CALL_TIMEOUT,
            )
        except httpx.TimeoutException:
            dt = time.time() - t0
            log.error("模型 %s 超时：%.0fs 内未返回（上限 %ds），会话 %s 已取消",
                      model, dt, int(CALL_TIMEOUT), sid)
            raise CallTimeout(
                f"模型 {model} 在 {int(CALL_TIMEOUT)} 秒内未返回，已自动取消。"
                f"免费档上游偶发拥堵，可重试或在前端换一个更快的模型。"
            ) from None
        r.raise_for_status()
        data = r.json()
        dt = time.time() - t0
        err = (data.get("info") or {}).get("error")
        if err:
            msg = json.dumps(err, ensure_ascii=False)[:400]
            log.error("opencode 上游错误（%.1fs）: %s", dt, msg)
            raise RuntimeError(f"opencode 上游错误: {msg}")
        texts = [p.get("text", "") for p in data.get("parts") or [] if p.get("type") == "text"]
        if not texts:
            log.error("opencode 返回中无文本输出（%.1fs, parts=%s）", dt,
                      [p.get("type") for p in data.get("parts") or []])
            raise RuntimeError("opencode 返回中无文本输出")
        reply = "\n".join(texts).strip()
        log.info("opencode 回复完成（%.1fs, 回复 %d 字符）", dt, len(reply))
        return reply
    finally:
        try:
            oc.delete(f"{base}/session/{sid}")
        except Exception:
            pass


def openai_chunk(model: str, delta: dict, finish: str | None) -> str:
    return "data: " + json.dumps({
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }, ensure_ascii=False) + "\n\n"


app = FastAPI()


@app.get("/v1/models")
def models(refresh: bool = False):
    ids = fetch_free_models(force=refresh)
    log.info("GET /v1/models -> %d 个模型%s", len(ids), "（强制刷新）" if refresh else "")
    return {"object": "list", "data": [{"id": m, "object": "model"} for m in ids]}


@app.post("/v1/chat/completions")
async def chat_completions(body: dict):
    raw_model = (body.get("model") or "").split("/")[-1]
    free = fetch_free_models()
    model = raw_model if raw_model in free else (free[0] if free else FALLBACK_MODELS[0])
    if raw_model and raw_model != model:
        log.warning("请求模型 %r 不在免费列表中，改用 %s", raw_model, model)
    n_msg = len(body.get("messages") or [])
    prompt = flatten_messages(body.get("messages") or [])
    stream = bool(body.get("stream"))
    log.info("=> chat/completions model=%s stream=%s messages=%d prompt=%d 字符",
             model, stream, n_msg, len(prompt))
    t0 = time.time()
    try:
        text = ask_opencode(prompt, model)
    except CallTimeout as e:
        log.warning("chat/completions 超时取消（%.1fs）: %s", time.time() - t0, e)
        return JSONResponse(
            {"error": {"message": str(e), "type": "timeout"}},
            status_code=504,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("chat/completions 失败（%.1fs）", time.time() - t0)
        return JSONResponse(
            {"error": {"message": str(e), "type": "shim_error"}},
            status_code=502,
        )
    dt = time.time() - t0
    log.info("<= chat/completions 完成 model=%s 耗时 %.1fs 回复 %d 字符", model, dt, len(text))

    if stream:
        def gen():
            yield openai_chunk(model, {"role": "assistant", "content": ""}, None)
            yield openai_chunk(model, {"content": text}, None)
            yield openai_chunk(model, {}, "stop")
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    return JSONResponse({
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    })


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    log.info("zen_free_shim 启动中（端口 %s）…", SHIM_PORT)
    ensure_oc_serve()
    uvicorn.run(app, host="127.0.0.1", port=SHIM_PORT, log_level="info")
