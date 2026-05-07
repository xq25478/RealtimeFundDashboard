"""日志工具"""

import logging
from rich.logging import RichHandler


_configured = False


def get_logger(name: str = "fund_advisor") -> logging.Logger:
    """获取一个使用 Rich 渲染的 logger"""
    global _configured
    if not _configured:
        logging.basicConfig(
            level=logging.INFO,
            format="%(message)s",
            datefmt="[%X]",
            handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
        )
        _configured = True
    return logging.getLogger(name)
