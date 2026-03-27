import dataclasses
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from runtime.agent.utils.time_utils import utcnow  # type: ignore


class JSONFormatter(logging.Formatter):
    """Formatter untuk mengubah log record ke JSON."""

    def __init__(self, mode: str = "unknown"):
        super().__init__()
        self.mode = mode

    def format(self, record: logging.LogRecord) -> str:
        ctx = getattr(record, "structured_ctx", {})

        # Required fields
        log_obj = {
            "timestamp": utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "mode": self.mode,
        }

        # Add context fields
        for k, v in ctx.items():
            log_obj[k] = self._serialize(v)

        if record.exc_info:
            log_obj["error"] = self.formatException(record.exc_info)  # type: ignore

        return json.dumps(log_obj)

    def _serialize(self, value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Exception):
            return str(value)
        if dataclasses.is_dataclass(value):
            return dataclasses.asdict(value)
        try:
            # Test if it's serializable
            json.dumps(value)
            return value
        except (TypeError, ValueError):
            return repr(value)


def setup_structured_logging(
    log_file: str = "logs/agent.log",
    log_format: str = "json",
    level: str = "INFO",
    mode: str = "unknown",
) -> None:
    """Configure root logger with appropriate formatters and handlers."""
    root_logger = logging.getLogger()

    # Remove existing handlers
    for handler in root_logger.handlers[:]:  # type: ignore
        root_logger.removeHandler(handler)

    num_level = getattr(logging, level.upper(), logging.INFO)
    root_logger.setLevel(num_level)

    # Setup handlers (Console + File)
    handlers = []

    # Console Handler
    console = logging.StreamHandler()
    handlers.append(console)

    # File Handler
    if log_file:
        file_path = Path(log_file)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(str(file_path))
        handlers.append(file_handler)  # type: ignore

    # Standard formats
    text_formatter = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    json_formatter = JSONFormatter(mode=mode)

    formatter = json_formatter if log_format.lower() == "json" else text_formatter

    for h in handlers:
        h.setFormatter(formatter)
        root_logger.addHandler(h)
