from __future__ import annotations

from pathlib import Path

import pytest

from paper_reader import summarizer
from paper_reader.config import Config, _apply_env, _apply_toml, config_asdict, load_config
from paper_reader.summarizer import build_client


def test_project_config_found_from_subdirectory(tmp_path, monkeypatch):
    """在项目子目录运行时，向上找到根目录的 paperreader.toml，相对路径以配置所在目录为基准。"""
    (tmp_path / "papers").mkdir()
    (tmp_path / "paperreader.toml").write_text(
        'api_key = "sk-test"\nmodel = "glm-5.3"\npapers_dir = "mypapers"\n',
        encoding="utf-8",
    )
    sub = tmp_path / "backend"
    sub.mkdir()
    monkeypatch.chdir(sub)

    cfg = load_config()
    assert cfg.api_key == "sk-test"
    assert cfg.model == "glm-5.3"
    assert cfg.papers_dir == tmp_path / "mypapers"
    assert cfg.summaries_dir == tmp_path / "summaries"


def test_defaults_without_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "papers").mkdir()
    cfg = load_config()
    assert cfg.api_key == ""
    assert cfg.papers_dir == tmp_path / "papers"
    assert cfg.summaries_dir == tmp_path / "summaries"


def test_env_overrides(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "papers").mkdir()
    monkeypatch.setenv("PAPERREADER_API_KEY", "sk-env")
    monkeypatch.setenv("PAPERREADER_MODEL", "glm-5.3-flash")
    cfg = load_config()
    assert cfg.api_key == "sk-env"
    assert cfg.model == "glm-5.3-flash"


# ---------- extra_headers：任意 OpenAI 兼容网关的通用接入 ----------


@pytest.fixture(autouse=True)
def _clear_client_cache():
    summarizer._CLIENT_CACHE.clear()
    yield
    summarizer._CLIENT_CACHE.clear()


def _write_toml(tmp_path, body):
    p = tmp_path / "paperreader.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_extra_headers_from_toml(tmp_path):
    cfg = Config()
    _apply_toml(
        cfg,
        _write_toml(
            tmp_path, 'api_base = "https://api.x.com/v1"\n\n[extra_headers]\n"X-A" = "1"\n'
        ),
    )
    assert cfg.extra_headers == {"X-A": "1"}


def test_extra_headers_default_empty():
    assert Config().extra_headers == {}


def test_extra_headers_from_env(monkeypatch):
    monkeypatch.setenv("PAPERREADER_EXTRA_HEADERS", '{"X-B": "2", "User-Agent": "app/9"}')
    cfg = Config()
    _apply_env(cfg)
    assert cfg.extra_headers == {"X-B": "2", "User-Agent": "app/9"}


def test_extra_headers_env_invalid_json(monkeypatch):
    monkeypatch.setenv("PAPERREADER_EXTRA_HEADERS", "{not json")
    with pytest.raises(ValueError):
        _apply_env(Config())


def test_client_no_opencode_header_for_standard_base():
    cfg = Config(api_base="https://open.bigmodel.cn/api/paas/v4", api_key="k")
    client = build_client(cfg)
    assert "x-opencode-session" not in client.default_headers


def test_client_auto_opencode_session_header():
    cfg = Config(api_base="https://opencode.ai/zen/go/v1", api_key="k")
    client = build_client(cfg)
    assert client.default_headers.get("x-opencode-session")


def test_client_extra_headers_merged_and_override():
    cfg = Config(
        api_base="https://api.x.com/v1",
        api_key="k",
        extra_headers={"X-Gateway-Auth": "abc", "User-Agent": "custom/1.0"},
    )
    client = build_client(cfg)
    assert client.default_headers.get("X-Gateway-Auth") == "abc"
    assert client.default_headers.get("User-Agent") == "custom/1.0"


def test_client_cache_respects_extra_headers():
    base = dict(api_base="https://api.x.com/v1", api_key="k")
    c1 = build_client(Config(**base))
    c2 = build_client(Config(**base))
    assert c1 is c2  # 同配置复用实例
    c3 = build_client(Config(**base, extra_headers={"X-A": "1"}))
    assert c3 is not c1  # headers 不同视为不同配置


def test_asdict_masks_extra_headers_values():
    text = config_asdict(Config(extra_headers={"X-Token": "sk-very-secret-token-value"}))
    assert "sk-very-secret-token-value" not in str(text)
    assert "X-Token" in text["extra_headers"]
