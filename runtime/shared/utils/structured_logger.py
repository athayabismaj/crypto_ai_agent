"""
structured_logger.py — JSON Formatting System 
Menulis log dalam format JSON satu baris per entry untuk Prometheus/Grafana.
"""

import json
import logging
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any

from runtime.shared.utils.logger import AgentLogger
from runtime.shared.utils.time_utils import utcnow

# Konfigurasi via file Config, tapi fallback hardcoded untuk sementara
LOG_FORMAT = "json"  # json | text

class StructuredLogger(AgentLogger):
    def __init__(self, name: str, ctx: dict[str, Any] = None):
        super().__init__(name, ctx)
        self._mode = "prod"

    def _serialize(self, msg: str, level: str, **ctx: Any) -> str:
        ctx_all = {**self._ctx, **ctx}
        
        # Serialize specific types safely
        for k, v in list(ctx_all.items()):
            if isinstance(v, datetime):
                ctx_all[k] = v.isoformat()
            elif isinstance(v, Exception):
                ctx_all[k] = str(v)
            elif is_dataclass(v):
                ctx_all[k] = asdict(v)
            else:
                try:
                    # just verify if its json serializable
                    json.dumps(v)
                except Exception:
                    ctx_all[k] = repr(v)

        record = {
            "timestamp": utcnow().isoformat(),
            "level": level,
            "logger": self._logger.name,
            "message": msg,
            "mode": self._mode,
            **ctx_all
        }
        
        if LOG_FORMAT == "json":
            try:
                return json.dumps(record)
            except Exception:
                return super()._format(msg, ctx) # fallback ke format txt
        else:
            return super()._format(msg, ctx)

    def debug(self, msg: str, **ctx: Any) -> None:
        self._logger.debug(self._serialize(msg, "DEBUG", **ctx))

    def info(self, msg: str, **ctx: Any) -> None:
        self._logger.info(self._serialize(msg, "INFO", **ctx))

    def warning(self, msg: str, **ctx: Any) -> None:
        self._logger.warning(self._serialize(msg, "WARNING", **ctx))

    def error(self, msg: str, **ctx: Any) -> None:
        self._logger.error(self._serialize(msg, "ERROR", **ctx))

    def critical(self, msg: str, **ctx: Any) -> None:
        self._logger.critical(self._serialize(msg, "CRITICAL", **ctx))

    def bind(self, **ctx: Any) -> 'StructuredLogger':
        new_ctx = {**self._ctx, **ctx}
        return StructuredLogger(self._logger.name, new_ctx)

# Global register for structured logger as well
_structured_loggers: dict[str, StructuredLogger] = {}

def get_structured_logger(name: str) -> StructuredLogger:
    if name not in _structured_loggers:
        _structured_loggers[name] = StructuredLogger(name)
    return _structured_loggers[name]
