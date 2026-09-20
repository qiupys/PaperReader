"""PaperReader：论文阅读器（已读/未读管理 + LLM summary 生成）。"""

__version__ = "0.1.0"

from .config import Config, load_config
from .library import PaperInfo, scan

__all__ = ["Config", "load_config", "PaperInfo", "scan", "__version__"]
