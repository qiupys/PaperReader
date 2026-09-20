"""配置加载。优先级：环境变量 > 项目 paperreader.toml > 用户配置 > 默认值。

所有配置均可通过环境变量覆盖：
  PAPERREADER_API_BASE / OPENAI_BASE_URL
  PAPERREADER_API_KEY  / OPENAI_API_KEY
  PAPERREADER_MODEL
  PAPERREADER_MAX_CHARS / PAPERREADER_TIMEOUT
  PAPERREADER_PAPERS_DIR / PAPERREADER_SUMMARIES_DIR
  PAPERREADER_PROXY
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_API_BASE = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_MODEL = "glm-4.5"
DEFAULT_MAX_CHARS = 200_000
DEFAULT_TIMEOUT = 900.0

PROJECT_CONFIG_NAME = "paperreader.toml"
USER_CONFIG_PATH = Path.home() / ".config" / "paperreader" / "config.toml"

_TOML_KEYS = (
    "api_base",
    "api_key",
    "model",
    "max_chars",
    "timeout",
    "papers_dir",
    "summaries_dir",
    "proxy",
)


@dataclass
class Config:
    """运行配置。papers_dir / summaries_dir 为 None 时自动探测项目根目录。"""

    api_base: str = DEFAULT_API_BASE
    api_key: str = ""
    model: str = DEFAULT_MODEL
    max_chars: int = DEFAULT_MAX_CHARS
    timeout: float = DEFAULT_TIMEOUT
    papers_dir: Path | None = None
    summaries_dir: Path | None = None
    proxy: str = ""  # LLM 出站代理，如 http://127.0.0.1:10808，空为直连


def _apply_toml(cfg: Config, path: Path, base: Path | None = None) -> None:
    if not path.is_file():
        return
    with path.open("rb") as f:
        data = tomllib.load(f)
    for key in _TOML_KEYS:
        if key in data:
            value = data[key]
            if key.endswith("_dir"):
                p = Path(str(value)).expanduser()
                # 项目配置中的相对路径以配置文件所在目录为基准
                if not p.is_absolute() and base is not None:
                    p = base / p
                value = p
            setattr(cfg, key, value)


def _apply_env(cfg: Config) -> None:
    mapping = {
        "api_base": ("PAPERREADER_API_BASE", "OPENAI_BASE_URL"),
        "api_key": ("PAPERREADER_API_KEY", "OPENAI_API_KEY"),
        "model": ("PAPERREADER_MODEL",),
        "max_chars": ("PAPERREADER_MAX_CHARS",),
        "timeout": ("PAPERREADER_TIMEOUT",),
        "papers_dir": ("PAPERREADER_PAPERS_DIR",),
        "summaries_dir": ("PAPERREADER_SUMMARIES_DIR",),
        "proxy": ("PAPERREADER_PROXY",),
    }
    for key, names in mapping.items():
        for name in names:
            value = os.environ.get(name)
            if value:
                setattr(cfg, key, Path(value).expanduser() if key.endswith("_dir") else value)
                break
    if isinstance(cfg.max_chars, str):
        cfg.max_chars = int(cfg.max_chars)
    if isinstance(cfg.timeout, str):
        cfg.timeout = float(cfg.timeout)


def _find_project_config() -> Path | None:
    """从 cwd 向上（最多 4 层）查找项目配置 paperreader.toml。"""
    cwd = Path.cwd()
    for d in (cwd, *cwd.parents[:3]):
        p = d / PROJECT_CONFIG_NAME
        if p.is_file():
            return p
    return None


def _detect_root() -> Path:
    """项目根：优先 paperreader.toml 所在目录，其次向上找包含 papers/ 的目录。"""
    cfg_path = _find_project_config()
    if cfg_path is not None:
        return cfg_path.parent
    cwd = Path.cwd()
    for d in (cwd, *cwd.parents[:3]):
        if (d / "papers").is_dir():
            return d
    return cwd


def load_config() -> Config:
    cfg = Config()
    _apply_toml(cfg, USER_CONFIG_PATH)
    project_cfg = _find_project_config()
    if project_cfg is not None:
        _apply_toml(cfg, project_cfg, base=project_cfg.parent)
    _apply_env(cfg)
    if cfg.papers_dir is None:
        cfg.papers_dir = _detect_root() / "papers"
    if cfg.summaries_dir is None:
        cfg.summaries_dir = cfg.papers_dir.parent / "summaries"
    return cfg


def mask_key(key: str) -> str:
    if not key:
        return "<未配置>"
    if len(key) <= 10:
        return key[:2] + "***"
    return key[:6] + "..." + key[-4:]


def config_asdict(cfg: Config) -> dict:
    return {
        "api_base": cfg.api_base,
        "api_key": mask_key(cfg.api_key),
        "model": cfg.model,
        "max_chars": cfg.max_chars,
        "timeout": cfg.timeout,
        "papers_dir": str(cfg.papers_dir or ""),
        "summaries_dir": str(cfg.summaries_dir or ""),
        "proxy": cfg.proxy or "（直连）",
    }
