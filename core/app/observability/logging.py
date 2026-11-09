"""Application file logging with rotation and credential-safe formatting. / 轮转应用文件日志。"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_application_logging(root: Path) -> None:
    """Install rotating application and error handlers once. / 安装轮转应用与错误日志处理器。

    Args:
        root: Application log directory. / 应用日志目录。
    """
    root.mkdir(parents=True, exist_ok=True)
    base = logging.getLogger()
    if any(getattr(handler, "_slothy_application", False) for handler in base.handlers):
        return
    level = getattr(logging, os.environ.get("SLOTHY_LOG_LEVEL", "INFO").upper(), logging.INFO)
    base.setLevel(level)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    application = RotatingFileHandler(
        root / "slothy.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    application.setLevel(level)
    application.setFormatter(formatter)
    application._slothy_application = True
    errors = RotatingFileHandler(
        root / "errors.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    errors.setLevel(logging.ERROR)
    errors.setFormatter(formatter)
    errors._slothy_application = True
    base.addHandler(application)
    base.addHandler(errors)
