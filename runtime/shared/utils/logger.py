"""
logger.py — Standard Logger wrapper
Menambahkan konteks ke logger dan standar formatting agar konsisten
"""

import logging
from typing import Any


class AgentLogger:
    """Wrapper standard logger agar bisa bind context."""
    def __init__(self, name: str, ctx: dict[str, Any] = None):
        self._logger = logging.getLogger(name)
        self._ctx = ctx or {}

    def _format(self, msg: str, kwargs: dict[str, Any]) -> str:
        ctx_all = {**self._ctx, **kwargs}
        if not ctx_all:
            return msg
        ctx_str = " ".join(f"{k}={v}" for k, v in ctx_all.items())
        return f"{msg} | {ctx_str}"

    def debug(self, msg: str, **ctx: Any) -> None:
        self._logger.debug(self._format(msg, ctx))

    def info(self, msg: str, **ctx: Any) -> None:
        self._logger.info(self._format(msg, ctx))

    def warning(self, msg: str, **ctx: Any) -> None:
        self._logger.warning(self._format(msg, ctx))

    def error(self, msg: str, **ctx: Any) -> None:
        self._logger.error(self._format(msg, ctx))

    def critical(self, msg: str, **ctx: Any) -> None:
        self._logger.critical(self._format(msg, ctx))

    def bind(self, **ctx: Any) -> 'AgentLogger':
        new_ctx = {**self._ctx, **ctx}
        return AgentLogger(self._logger.name, new_ctx)

# Gunakan global dict untuk tidak instance multiple logger jika sama
_loggers: dict[str, AgentLogger] = {}

def get_logger(name: str) -> AgentLogger:
    if name not in _loggers:
        _loggers[name] = AgentLogger(name)
    return _loggers[name]
