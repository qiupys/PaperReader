"""call_llm 重试逻辑测试：5xx/429/网络异常做指数退避重试，4xx 直接抛出。

上游 LLM API 偶发 500（如 Anthropic 网关 Internal server error）是笔记整理
失败的真实案例（2026-09-20 用户报告），重试可让此类瞬时错误自愈。
"""

import pytest

from paper_reader.summarizer import LLM_MAX_ATTEMPTS, call_llm


class _FakeHTTPError(Exception):
    """模拟 openai SDK 的 APIStatusError（带 status_code 属性）。"""

    def __init__(self, status: int):
        super().__init__(f"HTTP {status}")
        self.status_code = status


class _FakeCompletions:
    def __init__(self, script, result="ok"):
        self._script = list(script)
        self._result = result
        self.calls = 0

    def create(self, model=None, messages=None):  # noqa: ARG002 —— 签名对齐 SDK
        self.calls += 1
        if self._script:
            item = self._script.pop(0)
            if item is not None:
                raise item
        return self._result


class _Chat:
    def __init__(self, completions):
        self.completions = completions


class _FakeClient:
    def __init__(self, script, result="ok"):
        self.chat = _Chat(_FakeCompletions(script, result))


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """重试退避不真睡，保持测试快速。"""
    import paper_reader.summarizer as s

    monkeypatch.setattr(s.time, "sleep", lambda _seconds: None)


MSGS = [{"role": "user", "content": "hi"}]


def test_retry_success_after_5xx():
    client = _FakeClient([_FakeHTTPError(500), _FakeHTTPError(502)], result="done")
    assert call_llm(client, model="m", messages=MSGS) == "done"
    assert client.chat.completions.calls == 3


def test_retry_on_429_and_connection_error():
    client = _FakeClient([_FakeHTTPError(429), RuntimeError("connection reset")])
    assert call_llm(client, model="m", messages=MSGS) == "ok"
    assert client.chat.completions.calls == 3


def test_no_retry_on_4xx():
    client = _FakeClient([_FakeHTTPError(401)])
    with pytest.raises(_FakeHTTPError):
        call_llm(client, model="m", messages=MSGS)
    assert client.chat.completions.calls == 1


def test_exhausted_retries_raises_last_error():
    client = _FakeClient([_FakeHTTPError(500)] * (LLM_MAX_ATTEMPTS + 1))
    with pytest.raises(_FakeHTTPError):
        call_llm(client, model="m", messages=MSGS)
    assert client.chat.completions.calls == LLM_MAX_ATTEMPTS


def test_first_try_success_no_retry():
    client = _FakeClient([], result="immediate")
    assert call_llm(client, model="m", messages=MSGS) == "immediate"
    assert client.chat.completions.calls == 1
