import json
import logging
from datetime import datetime

from runtime.agent.utils.logger import get_logger  # type: ignore
from runtime.agent.utils.structured_logger import (  # type: ignore
    JSONFormatter,
    setup_structured_logging,
)


def test_agent_logger_bind():
    log = get_logger("test_logger").bind(trade_id="123")
    assert log._context == {"trade_id": "123"}

    # Test logging invocation does not crash
    log.info("Test message", detail="val")
    log.debug("Debug msg")
    log.warning("Warn msg")
    log.error("Error msg")
    log.critical("Crit msg")


def test_json_formatter():
    formatter = JSONFormatter(mode="test_mode")

    # Create a mock LogRecord
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="A message",
        args=(),
        exc_info=None,
    )
    import dataclasses

    @dataclasses.dataclass
    class Data:
        x: int

    class NonSerializable:
        pass

    record.structured_ctx = {  # type: ignore
        "trade_id": 123,
        "dt": datetime(2025, 1, 1),
        "exc": Exception("Error"),
        "dc": Data(x=5),
        "ns": NonSerializable(),
    }

    output = formatter.format(record)
    data = json.loads(output)

    assert data["message"] == "A message"
    assert data["level"] == "INFO"
    assert data["trade_id"] == 123
    assert data["mode"] == "test_mode"
    assert "Exception" in data["exc"] or "Error" in data["exc"]
    assert data["dc"]["x"] == 5


def test_setup_structured_logging(tmp_path):
    log_file = tmp_path / "test.log"
    setup_structured_logging(str(log_file), log_format="json")

    log = get_logger("test_log").bind(ctx="hello")
    log.info("Test")

    # Allow file to be written
    with open(log_file) as f:
        line = f.readline()
        assert "Test" in line
        data = json.loads(line)
        assert data["ctx"] == "hello"
