import os
import tempfile
from unittest.mock import patch

import pytest  # type: ignore
import yaml  # type: ignore
from pydantic import ValidationError  # type: ignore

from runtime.agent.core.config import load_config  # type: ignore


def test_load_default_config():
    with patch.dict(os.environ, {}, clear=True):
        config = load_config(yaml_path="not_exist.yaml")
        assert config.mode == "paper"
        assert config.market_type == "spot"


def test_yaml_override():
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.dump({"mode": "shadow", "initial_equity": 5000.0}, f)
        temp_path = f.name

    try:
        with patch.dict(os.environ, {}, clear=True):
            config = load_config(yaml_path=temp_path)
            assert config.mode == "shadow"
            assert config.initial_equity == 5000.0
    finally:
        os.remove(temp_path)


def test_env_override():
    env_vars = {
        "MODE": "live",
        "INITIAL_EQUITY": "12345.6",
        "SYMBOLS": "ETHUSDT, BNBUSDT",
        "LLM_ENABLED": "true",
    }
    with patch.dict(os.environ, env_vars, clear=True):
        config = load_config(yaml_path="not_exist.yaml")
        assert config.mode == "live"
        assert config.initial_equity == 12345.6
        assert config.symbols == ["ETHUSDT", "BNBUSDT"]
        assert config.llm_enabled is True


def test_env_override_false():
    env_vars = {"LLM_ENABLED": "false"}
    with patch.dict(os.environ, env_vars, clear=True):
        config = load_config(yaml_path="not_exist.yaml")
        assert config.llm_enabled is False


def test_validation_error():
    with patch.dict(os.environ, {"MARKET_TYPE": "invalid"}, clear=True):
        with pytest.raises(ValidationError):
            load_config(yaml_path="not_exist.yaml")


def test_timeframe_validation_error():
    with patch.dict(os.environ, {"TIMEFRAME": "1s"}, clear=True):
        with pytest.raises(ValidationError):
            load_config(yaml_path="not_exist.yaml")


def test_global_max_leverage_spot():
    with patch.dict(os.environ, {"MARKET_TYPE": "spot", "GLOBAL_MAX_LEVERAGE": "5"}, clear=True):
        with pytest.raises(ValidationError):
            load_config(yaml_path="not_exist.yaml")
