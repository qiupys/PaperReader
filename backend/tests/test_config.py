from __future__ import annotations

from pathlib import Path

from paper_reader.config import load_config


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
