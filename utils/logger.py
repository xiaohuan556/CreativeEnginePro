"""
CreativeEnginePro - 统一日志系统
基于 Python logging，支持文件和控制台双输出
"""
import logging
import sys
from pathlib import Path


def setup_logger(
    name: str = "CreativeEnginePro",
    log_file: str = None,
    level: int = logging.WARNING,
) -> logging.Logger:
    """创建预配置的 logger 实例"""
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(level)

    # 控制台输出
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(logging.Formatter(
        "[%(levelname)s] %(name)s: %(message)s"
    ))
    logger.addHandler(console)

    # 文件输出（可选）
    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d: %(message)s"
        ))
        logger.addHandler(fh)

    return logger


# 默认 logger（模块级使用）
_logger = None


def get_logger(name: str = "CreativeEnginePro") -> logging.Logger:
    """获取或创建 logger"""
    global _logger
    if _logger is None:
        _logger = setup_logger(name)
    return _logger
