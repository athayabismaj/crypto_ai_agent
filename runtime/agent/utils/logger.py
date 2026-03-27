import copy
import logging
from typing import Any


class AgentLogger:
    """
    Wrapper di atas logging.Logger standar.
    Semua method menerima **kwargs untuk structured context.
    """

    def __init__(self, name: str, context: dict[str, Any] | None = None):
        self._logger = logging.getLogger(name)
        self._context = context or {}

    def bind(self, **ctx) -> "AgentLogger":
        """Return logger baru dengan context yang sudah di-bind."""
        new_context = copy.deepcopy(self._context)
        new_context.update(ctx)
        return AgentLogger(self._logger.name, new_context)

    def _log(self, level: int, msg: str, **ctx) -> None:
        """Internal log method."""
        if not self._logger.isEnabledFor(level):
            return

        merged_ctx = copy.deepcopy(self._context)
        merged_ctx.update(ctx)

        # In actual implementation, we might pass ctx as `extra` to the standard logger
        self._logger.log(level, msg, extra={"structured_ctx": merged_ctx})

    def debug(self, msg: str, **ctx) -> None:
        self._log(logging.DEBUG, msg, **ctx)

    def info(self, msg: str, **ctx) -> None:
        self._log(logging.INFO, msg, **ctx)

    def warning(self, msg: str, **ctx) -> None:
        self._log(logging.WARNING, msg, **ctx)

    def error(self, msg: str, **ctx) -> None:
        self._log(logging.ERROR, msg, **ctx)

    def critical(self, msg: str, **ctx) -> None:
        self._log(logging.CRITICAL, msg, **ctx)


def get_logger(name: str) -> AgentLogger:
    """Factory function. Panggil satu kali per modul di level module."""
    return AgentLogger(name)
